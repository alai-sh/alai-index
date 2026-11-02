# Copyright: (c) 2026, Jordan Borean (@jborean93) <jborean93@gmail.com>
# MIT License (see LICENSE or https://opensource.org/licenses/MIT)

"""An index as a tree of files on disk, and the operations that change it. The private state and the artifacts are the
source of truth, and every public file is derived from them, as README.md's "The tree" describes."""

from __future__ import annotations

import collections.abc as _c
import contextlib
import datetime as dt
import hashlib
import json
import logging
import os
import posixpath
import re
import tempfile
import typing as _t
import urllib.parse
import uuid

from alai_core import artifact, collection
from alai_core.index import CollectionEntry, CollectionIndex, Collections, CollectionVersion, Config
from alai_core.semver import Version, VersionReq

from . import _galaxy as galaxy
from . import _plugins as plugins

DEFAULT_INDEX = "index/{namespace}/{name}.jsonl"
DEFAULT_DL = "dl/{sha256-prefix}/{sha256-checksum}.tar.gz"
COLLECTIONS = "collections.json"
PLUGINS = "plugins.jsonl"
log = logging.getLogger("alai_index")
"""Progress, at info, and each decision, at debug. Warnings a caller should act on are ``Index.warnings``."""

PRIVATE = "private"
"""The private directory's name in the tree, when it isn't given."""


class Error(Exception):
    """A failure to report to the user, without a traceback."""


class Change(_t.NamedTuple):
    """A version a command was given, and what happened to it, as README.md's "Results" lists them."""

    name: str
    version: str | None
    """None for the whole collection."""
    result: str
    """``added``, ``exists``, ``changed``, ``unchanged``, ``removed``, ``extracted``, ``conflict``, ``failed`` or
    ``deferred``."""
    details: dict[str, object] | None = None
    """What there is to say about the result, by result."""


def _changed(old: CollectionVersion, new: CollectionVersion) -> dict[str, object]:
    """The fields that may change, as ``new`` has them, where they differ from ``old``."""
    return {k: getattr(new, k) for k in ("yanked", "deprecated") if getattr(new, k) != getattr(old, k)}


def _failed(name: str, version: str | None, kind: str, error: str) -> Change:
    return Change(name, version, "failed", {"kind": kind, "error": error})


SKIPPABLE_CHECKS = ("dependencies", "requirements")
"""The checks ``Index.add`` can skip, to copy versions as their origin published them. ``dependencies`` is a dependency
no version in the index meets, and ``requirements`` one ``deps`` or ``requires_ansible`` ansible-core can't read."""


def _files() -> dict[str, _t.Any]:
    """What a command changed, by where each file is, as ``--output-format json`` gives it."""
    groups = ("dl", "index", "private")
    return {"collections": [], "written": {g: [] for g in groups}, "deleted": {g: [] for g in groups}, "stale": []}


def now() -> str:
    """The time as the index writes it, RFC 3339 in UTC to the second."""
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def configure(
    root: str | os.PathLike[str],
    *,
    private: str | os.PathLike[str] | None = None,
    dl: str | os.PathLike[str] | None = None,
    index: str | None = None,
    dl_template: str | None = None,
    listing: bool | None = None,
    plugins: bool | None = None,
) -> Config:
    """Write ``config.json``, and the empty private listing, ``collections.json`` and ``plugins.jsonl`` it needs.

    Each option left as None keeps what ``config.json`` already says, or else takes the default: files by name, a
    listing and a plugin index. The ``dl`` template defaults to the artifacts' directory, ``dl``, as a path in the
    index, which it has to be in. Once the index has collections, ``config.json`` can't change, as their files would be
    left where the old one put them.

    Raises:
        Error: The config isn't valid, or the index has collections and it would change.
    """
    root = os.fspath(root)
    path = os.path.join(root, "config.json")
    tree = Index(root, private=private, dl=dl) if os.path.exists(path) else None
    current = tree.config if tree else None
    # An index's state is never made empty, which would make every collection look new to the next add.
    has_collections = tree is not None and len(tree._listing()) > 0
    if listing is None:
        listing = current is None or current.collections is not None
    if plugins is None:
        plugins = current is None or current.plugins is not None
    try:
        config = Config(
            index=index or (current.index if current else DEFAULT_INDEX),
            dl=dl_template or (current.dl if current else _derived_dl(root, dl)),
            collections=COLLECTIONS if listing else None,
            plugins=PLUGINS if plugins else None,
            moved_to=current.moved_to if current else None,
        )
    except ValueError as e:
        raise Error(str(e)) from None
    _check_layout(root, _private_dir(root, private), dl, config)
    if config != current:
        if has_collections:
            raise Error(f"{root} already has collections, which changing config.json would leave behind")
        _write(path, config.to_json().encode())

    tree = Index(root, private=private, dl=dl)
    if not os.path.exists(tree.listing_file):
        _write(tree.listing_file, Collections({}).to_json().encode())
    if config.collections and not os.path.exists(tree.file(config.collections)):
        _write(tree.file(config.collections), _read(tree.listing_file) or b"")
    if config.plugins and not os.path.exists(tree.file(config.plugins)):
        _write(tree.file(config.plugins), b"")
    return config


def _private_dir(root: str, private: str | os.PathLike[str] | None) -> str:
    return os.fspath(private) if private is not None else os.path.join(root, PRIVATE)


def _dl_dir(root: str, dl: str | os.PathLike[str] | None) -> str:
    return os.fspath(dl) if dl is not None else os.path.join(root, "dl")


def _derived_dl(root: str, dl: str | os.PathLike[str] | None) -> str:
    """The ``dl`` template for artifacts kept in ``dl``, as a path in the index."""
    if dl is None:
        return DEFAULT_DL
    try:
        rel = os.path.relpath(os.path.abspath(dl), os.path.abspath(root))
    except ValueError:  # Another drive on Windows.
        rel = os.pardir
    if rel == os.pardir or rel.startswith(os.pardir + os.sep):
        raise Error(
            f"{os.fspath(dl)} isn't in the index, so clients can't find artifacts there by a path in it. "
            + "--dl-template gives the URL they're published at"
        )
    prefix = "" if rel == os.curdir else rel.replace(os.sep, "/") + "/"
    return prefix + "{sha256-prefix}/{sha256-checksum}.tar.gz"


def _artifact_path(
    root: str, dl: str | os.PathLike[str] | None, config: Config, name: str, version: Version, sha256: str
) -> str:
    """Where an artifact is on disk. A ``dl`` template that's a path in the index puts it there, in the tree, and one
    on another host puts it in the artifacts' directory at the URL's path, as the host would have it."""
    url = config.artifact_path(name=name, version=version, sha256=sha256)
    parts = urllib.parse.urlsplit(url)
    if parts.scheme or parts.netloc:
        return _tree_path(_dl_dir(root, dl), urllib.parse.unquote(parts.path).lstrip("/"), url)
    if parts.query or parts.fragment:
        raise Error(f"{url!r} isn't a path in the index")
    return _tree_path(root, urllib.parse.unquote(parts.path), url)


def _within(path: str, top: str) -> bool:
    path, top = os.path.abspath(path), os.path.abspath(top)
    try:
        return os.path.commonpath([path, top]) == top
    except ValueError:  # Another drive on Windows.
        return False


def _check_layout(root: str, private: str, dl: str | os.PathLike[str] | None, config: Config) -> None:
    """Refuse a ``dl`` template that isn't by sha256, a layout that puts index files or artifacts in the private
    directory, which ``add`` would overwrite, and artifacts the ``dl`` template puts outside the artifacts' directory
    given."""
    # The spec lets a reader take artifacts by name and version. A writer that did would reuse a URL for other bytes
    # when a version taken down is published again, which a cache could go on serving.
    if "{sha256-checksum}" not in config.dl:
        raise Error(f"the dl template {config.dl!r} needs {{sha256-checksum}}, so an artifact's URL never changes")
    artifact = _artifact_path(root, dl, config, "ns.coll", Version("1.0.0"), "0" * 64)
    places = [artifact]
    index_ref = urllib.parse.urlsplit(config.index_path(name="ns.coll", sha256="0" * 64))
    if not (index_ref.scheme or index_ref.netloc):
        places.append(_tree_path(root, urllib.parse.unquote(index_ref.path), index_ref.geturl()))
    if any(_within(p, private) for p in places) or _within(private, _dl_dir(root, dl)):
        raise Error(f"index files and artifacts can't be kept in {private}, which holds the index's own state")
    if dl is not None and not _within(artifact, os.fspath(dl)):
        raise Error(f"the dl template {config.dl!r} puts artifacts in the index, not in {os.fspath(dl)}")


def _tree_path(root: str, rel: str, ref: str) -> str:
    norm = posixpath.normpath(rel)
    parts = norm.split("/")
    # A drive or a backslash would lead elsewhere on Windows.
    if rel.startswith("/") or ".." in parts or any(":" in p or "\\" in p for p in parts):
        raise Error(f"{ref!r} leads outside the index")
    return os.path.join(root, *parts)


def _target(text: str) -> tuple[str, str | None]:
    """``NS.NAME`` or ``NS.NAME:VERSION``, checked."""
    name, sep, version = text.partition(":")
    try:
        collection.check_name(name)
        if sep:
            Version(version)
    except ValueError as e:
        raise Error(f"{text!r}: {e}") from None
    return name, version if sep else None


def _raw_deps(info: collection.Metadata, where: str) -> list[tuple[str, str]]:
    """``MANIFEST.json``'s dependencies as written, even ones ansible-galaxy can't read."""
    raw: object = info.get("dependencies") or {}
    deps = _t.cast("dict[str, object]", raw) if isinstance(raw, dict) else None
    if deps is None or not all(isinstance(r, str) for r in deps.values()):
        raise Error(f"{where} has dependencies that aren't a mapping of names to requirements")
    return [(n, _t.cast(str, r)) for n, r in deps.items()]


def _readable_deps(line: CollectionVersion) -> list[tuple[str, VersionReq]]:
    """A line's dependencies, leaving out any whose requirement can't be read."""
    if line.problem is None:
        return list(line.deps)
    readable: list[tuple[str, VersionReq]] = []
    for dep in _t.cast("list[dict[str, str]]", _fields(line)["deps"]):
        with contextlib.suppress(ValueError):
            readable.append((dep["name"], VersionReq.from_galaxy(dep["req"])))
    return readable


def _met(req: VersionReq, lines: _c.Iterable[CollectionVersion]) -> bool:
    """Whether a version that isn't yanked meets the requirement."""
    return any(not line.yanked and req.contains(line.version) for line in lines)


def _fields(line: CollectionVersion) -> dict[str, object]:
    """The line as JSON, kept as read, even a requirement it can't read."""
    return json.loads(line.to_json(allow_unreadable=True))


def _line(name: str, data: _c.Mapping[str, object]) -> CollectionVersion:
    parsed = CollectionIndex.parse(json.dumps(data).encode(), name)
    if not parsed.lines:
        raise Error(f"{name} {data.get('vers')}: {'; '.join(why for _, why in parsed.invalid)}")
    return parsed.lines[0]


def _with(line: CollectionVersion, **changes: object) -> CollectionVersion:
    """The line with these fields changed, kept as read otherwise."""
    return _line(line.name, {**_fields(line), **{k: v for k, v in changes.items() if v is not None}})


def _state(line: CollectionVersion) -> dict[str, object]:
    """A version's line in the private state, what only the index knows about it."""
    state: dict[str, object] = {
        "v": 1,
        "version": line.version.text,
        "sha256": line.sha256,
        "published": line.published,
        "yanked": line.yanked,
    }
    if line.deprecated:
        state["deprecated"] = True
    return state


def _jsonl(lines: _c.Iterable[_c.Mapping[str, object]]) -> bytes:
    return "".join(json.dumps(line, separators=(",", ":")) + "\n" for line in lines).encode()


def _changes(name: str, before: list[CollectionVersion], after: list[CollectionVersion]) -> list[Change]:
    """A ``changed`` for each version in ``before`` whose fields that may change differ in ``after``."""
    old = {line.version.text: line for line in before}
    return [
        Change(name, line.version.text, "changed", changed)
        for line in after
        if line.version.text in old and (changed := _changed(old[line.version.text], line))
    ]


class _Mirrored(_t.NamedTuple):
    lines: list[CollectionVersion]
    extractions: dict[str, dict[str, object]]
    """Each version added's plugin extraction."""
    downloads: int


def _count(n: int, thing: str) -> str:
    return f"{n} {thing}" if n == 1 else f"{n} {thing}s"


def _read_session(path: str | os.PathLike[str]) -> dict[str, dict[str, object]]:
    """The listings a ``galaxy_mirror`` session file keeps, or none if there's no file yet."""
    data = _read(os.fspath(path))
    if data is None:
        return {}
    listings: object = None
    try:
        session: object = json.loads(data)
        if isinstance(session, dict) and _t.cast("dict[str, object]", session).get("v") == 1:
            listings = _t.cast("dict[str, object]", session).get("collections")
    except ValueError:
        pass
    if not isinstance(listings, dict):
        raise Error(f"{os.fspath(path)} isn't a galaxy-mirror session file")
    return _t.cast("dict[str, dict[str, object]]", listings)


def _served_sha256(detail: _c.Mapping[str, object]) -> str | None:
    """A version detail's ``artifact.sha256``, or None if the server didn't give one."""
    artifact = detail.get("artifact")
    sha256 = _t.cast("dict[str, object]", artifact).get("sha256") if isinstance(artifact, dict) else None
    return sha256 if isinstance(sha256, str) else None


def _valid_name(name: str) -> bool:
    try:
        collection.check_name(name)
    except ValueError:
        return False
    return True


def _rfc3339(value: object) -> str | None:
    """A server's timestamp as the index writes one, RFC 3339 in UTC to the second, or None if it isn't one."""
    if not isinstance(value, str):
        return None
    # The index keeps whole seconds, and Python 3.10 reads only 3 or 6 digits of a fraction.
    try:
        when = dt.datetime.fromisoformat(re.sub(r"\.\d+", "", value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=dt.timezone.utc)
    return when.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class _Staged(_t.NamedTuple):
    line: CollectionVersion
    data: bytes
    extraction: dict[str, object] | None
    """Its plugin extraction, or None for an index without a plugin index."""


class Index:
    """The index whose tree is at ``root``, with its private state in ``private``, ``<root>/private`` by default, and its
    artifacts in ``dl``, ``<root>/dl`` by default, for a ``dl`` template on another host.

    Raises:
        Error: ``config.json`` is missing or isn't valid, or a template leads into the private directory.
    """

    def __init__(
        self,
        root: str | os.PathLike[str],
        *,
        private: str | os.PathLike[str] | None = None,
        dl: str | os.PathLike[str] | None = None,
    ) -> None:
        self.root = os.fspath(root)
        self.private = _private_dir(self.root, private)
        self.dl = dl
        self.files = _files()
        """Every file the commands run on this index wrote, deleted or left stale, by group: ``dl``, artifacts on
        another host, relative to the artifacts' directory, ``index``, what's published with the index, relative to the
        root, in the order to publish it, and ``private``, relative to the private directory. ``stale`` is each index
        file found by sha256 that no listing names any more, to delete once copies of the old listing have expired."""
        self.warnings: list[str] = []
        """Each warning the commands run on this index gave."""
        path = os.path.join(self.root, "config.json")
        data = _read(path)
        if data is None:
            raise Error(f"{path} doesn't exist. 'alai-index config' makes one")
        try:
            self.config = Config.parse(data)
        except ValueError as e:
            raise Error(f"{path}: {e}") from None
        _check_layout(self.root, self.private, self.dl, self.config)

    @property
    def listing_file(self) -> str:
        """The private listing, every collection the index has."""
        return os.path.join(self.private, COLLECTIONS)

    def file(self, ref: str) -> str:
        """Where a file ``config.json`` names by a reference relative to the index root is in the tree.

        Raises:
            Error: The reference leads outside the tree or isn't a plain path.
        """
        parts = urllib.parse.urlsplit(ref)
        if parts.scheme or parts.netloc or parts.query or parts.fragment:
            raise Error(f"{ref!r} isn't a path in the index")
        return _tree_path(self.root, urllib.parse.unquote(parts.path), ref)

    def artifact_file(self, line: CollectionVersion) -> str:
        """Where a version's artifact is on disk."""
        return self._artifact_file(line.name, line.version, line.sha256)

    def _artifact_file(self, name: str, version: Version, sha256: str) -> str:
        return _artifact_path(self.root, self.dl, self.config, name, version, sha256)

    def state_file(self, name: str) -> str:
        """Where a collection's versions are in the private state."""
        namespace, short = name.split(".", 1)
        return os.path.join(self.private, "index", namespace, f"{short}.jsonl")

    def extractions_file(self, name: str) -> str:
        """Where a collection's plugin extractions are cached in the private directory."""
        namespace, short = name.split(".", 1)
        return os.path.join(self.private, "cache", "plugins", namespace, f"{short}.jsonl")

    def add(
        self,
        tarballs: _c.Sequence[str | os.PathLike[str]],
        /,
        *,
        published: str | None = None,
        skip: _c.Collection[str] = (),
        commit: bool = True,
    ) -> list[Change]:
        """Add collection artifacts, each version's line taken from its own ``MANIFEST.json`` and ``meta/runtime.yml``.

        Every tarball is checked before anything is written. A version the index has with the same artifact is
        skipped, and one it has with another is refused. Each dependency must be met by a version that isn't yanked,
        in the index or among ``tarballs``, and requirements must be ones ansible-core can read, unless ``skip`` says
        otherwise, when each is a warning instead.

        Args:
            tarballs: The artifacts.
            published: When they were first published, RFC 3339 in UTC, or None for now.
            skip: The checks in ``SKIPPABLE_CHECKS`` to warn about rather than refuse.
            commit: Write ``plugins.jsonl`` and ``collections.json`` too. Without, ``rebuild`` with the collections
                changed does it later, so a batch of changes writes them once.

        Returns:
            Each tarball's version, in the order given.

        Raises:
            Error: A tarball can't be added. Nothing is written.
        """
        published = published or now()
        listing = self._listing()
        with tempfile.TemporaryDirectory(prefix="alai-index-", ignore_cleanup_errors=True) as tmp:
            staged: list[_Staged] = []
            for i, tarball in enumerate(tarballs):
                log.info("reading %s", os.fspath(tarball))
                staged.append(self._stage(os.fspath(tarball), os.path.join(tmp, str(i)), published, skip))

        lines: dict[str, list[CollectionVersion]] = {}
        before: dict[str, list[CollectionVersion]] = {}
        results: list[Change] = []
        new: list[_Staged] = []
        for s in staged:
            name, vers = s.line.name, s.line.version.text
            if name not in lines:
                lines[name] = self._lines(name, listing)
                before[name] = list(lines[name])
            have = lines[name]
            existing = next((line for line in have if line.version == s.line.version), None)
            if existing is not None and existing.sha256 != s.line.sha256:
                raise Error(f"{name} {vers} is already in the index with another artifact, sha256 {existing.sha256}")
            results.append(Change(name, vers, "added" if existing is None else "exists", {"sha256": s.line.sha256}))
            if existing is None:
                have.append(s.line)
                new.append(s)

        others: dict[str, list[CollectionVersion]] = {}
        unmet: list[str] = []
        for s in new:
            for dep, req in _readable_deps(s.line):
                if dep not in lines and dep not in others:
                    others[dep] = self._lines(dep, listing)
                if not _met(req, lines[dep] if dep in lines else others[dep]):
                    unmet.append(
                        f"{s.line.name} {s.line.version.text} needs {dep} {req.text}, "
                        + "which no version in the index that isn't yanked meets"
                    )
        if unmet and "dependencies" not in skip:
            raise Error("; ".join(unmet))
        if not new:
            return results
        self.warnings.extend(unmet)
        for s in new:
            if s.extraction is not None and (error := s.extraction.get("error")):
                self.warnings.append(
                    f"extracting the plugins of {s.line.name} {s.line.version.text} failed, so the plugin index has "
                    + f"none of them. {error}"
                )

        touched = {s.line.name: lines[s.line.name] for s in new}
        plugin_index, extractions = self._plugin_state(before, touched, commit=commit)
        if extractions is not None:
            for s in new:
                extractions[s.line.name][s.line.version.text] = _t.cast("dict[str, object]", s.extraction)
        self._commit(
            touched,
            listing,
            artifacts=[(s.line, s.data) for s in new],
            extractions=extractions,
            plugin_index=plugin_index,
            public_listing=commit,
        )
        return results

    def set(
        self,
        targets: _c.Sequence[str],
        /,
        *,
        yanked: bool | None = None,
        deprecated: bool | None = None,
        commit: bool = True,
    ) -> list[Change]:
        """Change whether versions are yanked or deprecated, the only fields of a line that may change.

        Args:
            targets: Each ``NS.NAME``, for every version of the collection, or ``NS.NAME:VERSION``.
            yanked: Yank or unyank them, or None to leave it.
            deprecated: Deprecate them or not, or None to leave it.
            commit: As for ``add``.

        Returns:
            Each version the targets name, in order.

        Raises:
            Error: Nothing to change was given, or a target isn't in the index. Nothing is written.
        """
        if yanked is None and deprecated is None:
            raise Error("there's nothing to change")
        listing = self._listing()
        selected = self._select(targets, listing)
        lines = {name: self._lines(name, listing) for name, _ in selected}
        results: list[Change] = []
        touched: set[str] = set()
        for name, version in selected:
            i = next(i for i, line in enumerate(lines[name]) if line.version.text == version)
            old = lines[name][i]
            lines[name][i] = _with(old, yanked=yanked, deprecated=deprecated)
            if changed := _changed(old, lines[name][i]):
                results.append(Change(name, version, "changed", changed))
                touched.add(name)
            else:
                results.append(Change(name, version, "unchanged"))
        if touched:
            # Plugin lines count yanked and deprecated versions too, so they stay as they are.
            self._commit({name: lines[name] for name in touched}, listing, public_listing=commit)
        return results

    def remove(self, targets: _c.Sequence[str], /, *, commit: bool = True) -> list[Change]:
        """Take versions down: their lines, artifacts and extractions are deleted, and with a collection's last, its
        index file and listing entry.

        It's refused if a version that stays, and isn't yanked, would be left without a version it needs, which a
        takedown mustn't do to an index. Yank those first, or remove them too.

        Args:
            targets: Each ``NS.NAME``, for every version of the collection, or ``NS.NAME:VERSION``.
            commit: As for ``add``.

        Returns:
            Each version removed, in order.

        Raises:
            Error: A target isn't in the index, or the removal would leave a dependency unmet. Nothing is written.
        """
        listing = self._listing()
        selected = self._select(targets, listing)
        before = {name: self._lines(name, listing) for name, _ in selected}
        gone = [line for name, version in selected for line in before[name] if line.version.text == version]
        lines = {name: [line for line in have if line not in gone] for name, have in before.items()}

        unmet: list[str] = []
        for name in listing:
            for line in lines[name] if name in lines else self._lines(name, listing):
                if line.yanked or line.problem is not None:
                    continue
                for dep, req in line.deps:
                    # Only what the removal breaks. A mirror can hold a requirement nothing ever met.
                    if dep in lines and _met(req, before[dep]) and not _met(req, lines[dep]):
                        unmet.append(f"{line.name} {line.version.text} needs {dep} {req.text}")
        if unmet:
            raise Error(
                "removing them would leave versions that aren't yanked without a version they need. "
                + f"Yank these first, or remove them too. {', '.join(unmet)}"
            )

        plugin_index, extractions = self._plugin_state(before, lines, commit=commit)
        if extractions is not None:
            for name, found in extractions.items():
                extractions[name] = {line.version.text: found[line.version.text] for line in lines[name]}
        self._commit(
            lines, listing, extractions=extractions, plugin_index=plugin_index, removed=gone, public_listing=commit
        )
        return [Change(line.name, line.version.text, "removed", {"sha256": line.sha256}) for line in gone]

    def rebuild(
        self,
        targets: _c.Sequence[str] = (),
        /,
        *,
        everything: bool = False,
        files: _c.Collection[str] | None = None,
        from_artifacts: bool = False,
        allow_changes: bool = False,
    ) -> list[Change]:
        """Make public files again from what the index knows, or with ``from_artifacts``, from the artifacts too.

        ``index`` is the targets' index files and ``collections.json``. From what the index knows, each version's line
        is its private state with ``deps``, ``requires_ansible`` and ``size`` from the current index file. With
        ``from_artifacts``, those are read from its artifact. ``plugins`` is ``plugins.jsonl``, from the private
        extractions, which ``from_artifacts`` extracts again from each artifact first. Without targets or
        ``everything``, only ``collections.json`` and ``plugins.jsonl`` are made again.

        Args:
            targets: Each ``NS.NAME``, for every version of the collection, or ``NS.NAME:VERSION``.
            everything: Every collection, in place of targets.
            files: ``index``, ``plugins`` or both, or None for both, which leaves out ``plugins`` for an index without
                a plugin index.
            from_artifacts: Read the targets' artifacts, which must be on disk, rather than what the index knows.
            allow_changes: With ``from_artifacts``, write ``deps``, ``requires_ansible`` or ``size`` that differ from
                the index file's, which are meant to stay as they were published.

        Returns:
            Each version whose plugins were extracted again. ``files`` has the public files that changed.

        Raises:
            Error: The options conflict, a target isn't in the index, what's needed isn't on disk or doesn't match the
                state, or a field that's meant to stay would change. Nothing is written.
        """
        if targets and everything:
            raise Error("give collections or --all, not both")
        if from_artifacts and not (targets or everything):
            raise Error(
                "--from-artifacts reads every artifact it's given, so name collections, or --all for all of them"
            )
        chosen = set(files) if files is not None else {"index", "plugins"}
        if unknown := chosen - {"index", "plugins"}:
            raise Error(f"there are no {', '.join(sorted(unknown))} files to rebuild, only index and plugins")
        if self.config.plugins is None:
            if files is not None and "plugins" in chosen:
                raise Error("the index has no plugin index")
            chosen.discard("plugins")
        do_index, do_plugins = "index" in chosen, "plugins" in chosen

        listing = self._listing()
        if targets:
            selected = self._select(targets, listing)
        elif everything:
            selected = [(name, _t.cast(str, s["version"])) for name in listing for s in self._states(name, listing)]
        else:
            selected = []
        wanted = set(selected)

        lines: dict[str, list[CollectionVersion]] = {}
        extractions: dict[str, dict[str, dict[str, object]]] = {}
        results: list[Change] = []
        changes: list[str] = []
        with tempfile.TemporaryDirectory(prefix="alai-index-", ignore_cleanup_errors=True) as tmp:
            for i, name in enumerate(dict.fromkeys(name for name, _ in selected)):
                states = self._states(name, listing)
                cached: dict[str, CollectionVersion] = {}
                if do_index:
                    try:
                        cached = self._cached(name, listing)
                    except Error:
                        # An index file that can't be read is made from the artifacts, with nothing to compare.
                        if not from_artifacts:
                            raise
                found = self._extractions(name, states, partial=from_artifacts) if do_plugins else {}
                collection_lines: list[CollectionVersion] = []
                for n, state in enumerate(states):
                    version = _t.cast(str, state["version"])
                    again = from_artifacts and (name, version) in wanted
                    if again:
                        log.info("reading the artifact of %s %s", name, version)
                    dest = self._unpack(name, state, os.path.join(tmp, f"{i}-{n}")) if again else None
                    if do_index:
                        old = cached.get(version)
                        if dest is not None:
                            line, change = self._derive(name, state, dest, old)
                            changes.extend([change] if change else [])
                        elif old is None:
                            raise Error(
                                f"{name}'s index file doesn't have {version}, which its state does. "
                                + f"'alai-index rebuild {name} --files index --from-artifacts' makes it"
                            )
                        else:
                            line = self._from_state(name, state, old)
                        collection_lines.append(line)
                    if do_plugins and dest is not None:
                        found[version] = (
                            plugins.extraction(dest, version)
                            if isinstance(dest, str)
                            else plugins.failure(version, dest)
                        )
                        error = _t.cast("str | None", found[version].get("error"))
                        results.append(Change(name, version, "extracted", {"error": error} if error else None))
                        if error:
                            self.warnings.append(
                                f"extracting the plugins of {name} {version} failed, so the plugin index has none "
                                + f"of them. {error}"
                            )
                if do_index:
                    lines[name] = collection_lines
                if do_plugins:
                    versions = [_t.cast(str, s["version"]) for s in states]
                    if missing := [v for v in versions if v not in found]:
                        raise Error(
                            f"{name} has no plugin extraction for {', '.join(missing)}. "
                            + f"'alai-index rebuild {name} --files plugins --from-artifacts' makes it"
                        )
                    extractions[name] = {v: found[v] for v in versions}

        if changes and not allow_changes:
            raise Error(
                "the artifacts give fields that are meant to stay as they were published. "
                + f"--allow-changes writes them anyway. {'; '.join(changes)}"
            )
        plugin_index = None
        if do_plugins:
            if targets:
                plugin_index = self._plugin_state({}, ())[0]
            else:
                # Made from nothing, so a collection the index no longer has leaves no lines behind.
                plugin_index = b""
                for name in listing:
                    if name not in extractions:
                        extractions[name] = self._extractions(name, self._states(name, listing))
        self._commit(
            lines,
            listing,
            extractions=extractions if do_plugins else None,
            plugin_index=plugin_index,
            public_listing=do_index,
        )
        return results

    def galaxy_mirror(
        self,
        client: galaxy.GalaxyClient,
        targets: _c.Sequence[str] = (),
        /,
        *,
        everything: bool = False,
        limit: int | None = None,
        revalidate: bool = False,
        commit: bool = True,
        session: str | os.PathLike[str] | None = None,
    ) -> list[Change]:
        """Bring collections in line with a Galaxy server, through the v3 endpoints ansible-galaxy uses.

        For each collection, versions the server lists and the index doesn't have are added, oldest upload first, with
        ``published`` from the server's ``created_at``, and their artifacts checked against its sha256. They're copied as
        published, so the ``dependencies`` and ``requirements`` checks warn rather than refuse. A version is yanked only
        when its own page, or the collection's, is a 404, so a listing that's cut short can never yank anything, and a
        yanked one the server lists again is checked and unyanked. A version the index has is checked against the
        server's sha256 when it's yanked, its ``created_at`` differs from ``published``, or with ``revalidate``. Other
        bytes are a ``conflict``, never written. The collection's ``deprecated`` goes on every line.

        A failure is reported for its version, and the rest go on.

        Args:
            client: The server.
            targets: Each ``NS.NAME``.
            everything: Every collection the server lists or the index has, in place of targets.
            limit: The most versions to download. Those left, and the collections after, are ``deferred``.
            revalidate: Check every version the index has against the server's sha256.
            commit: As for ``add``.
            session: A file to carry on from, if it's there, and to write what's needed to carry on to. A collection
                ``limit`` leaves part done is kept there with what the server said of it, so the next run only
                downloads. It's for runs close together, against the same server, and its contents are internal to
                alai-index.

        Returns:
            What happened to each version.

        Raises:
            Error: The options conflict, a target isn't a collection name, or the server can't be listed.
        """
        if targets and everything:
            raise Error("give collections or --all, not both")
        if not targets and not everything:
            raise Error("name collections, or --all for every one")
        listing = self._listing()
        if everything:
            try:
                log.info("listing the collections on %s", client.server)
                served = [f"{c.get('namespace')}.{c.get('name')}" for c in client.collections()]
            except galaxy.GalaxyError as e:
                raise Error(f"the collections can't be listed. {e}") from None
            names = sorted({n for n in served if _valid_name(n)} | set(listing))
        else:
            names = []
            for text in targets:
                name, version = _target(text)
                if version is not None:
                    raise Error(f"{text!r}: galaxy-mirror takes collections, not versions")
                names.append(name)

        results: list[Change] = []
        lines: dict[str, list[CollectionVersion]] = {}
        new: dict[str, dict[str, dict[str, object]]] = {}
        downloads = 0
        listings = _read_session(session) if session is not None else {}
        # Checked before any download, as artifacts are written as they come.
        haves = {name: self._lines(name, listing) for name in names}
        plugin_index, extractions = self._plugin_state(haves, haves, commit=commit)
        with tempfile.TemporaryDirectory(prefix="alai-index-", ignore_cleanup_errors=True) as tmp:
            for i, (name, have) in enumerate(haves.items()):
                if limit is not None and downloads >= limit:
                    results.append(Change(name, None, "deferred"))
                    continue
                mirrored = self._mirror_collection(
                    client, name, have, revalidate, limit, downloads, tmp, i, results, listings
                )
                downloads += mirrored.downloads
                if mirrored.lines != have:
                    lines[name] = mirrored.lines
                if mirrored.extractions:
                    new[name] = mirrored.extractions
        if session is not None:
            data = {"v": 1, "collections": listings}
            _write(os.fspath(session), json.dumps(data, separators=(",", ":")).encode())

        if not lines:
            log.info("nothing to write")
            return results
        log.info("writing the changes to %s", _count(len(lines), "collection"))
        if extractions is not None:
            merged = {name: extractions[name] | found for name, found in new.items()}
            extractions = {
                name: {line.version.text: merged[name][line.version.text] for line in lines[name]} for name in new
            }
        self._commit(lines, listing, extractions=extractions, plugin_index=plugin_index, public_listing=commit)
        return results

    def _mirror_collection(
        self,
        client: galaxy.GalaxyClient,
        name: str,
        have: list[CollectionVersion],
        revalidate: bool,
        limit: int | None,
        downloads: int,
        tmp: str,
        n: int,
        results: list[Change],
        listings: dict[str, dict[str, object]],
    ) -> _Mirrored:
        """One collection's part of ``galaxy_mirror``. A collection left with versions to download keeps what the
        server said of it in ``listings``, for the session, and one in it is carried on from."""
        namespace, short = name.split(".", 1)
        lines = list(have)
        saved: dict[str, object] | None = None if revalidate else listings.get(name)
        if saved is None:
            log.info("%s, asking the server for its versions", name)
            try:
                served = client.collection(namespace, short)
                upstream = client.versions(namespace, short) if served is not None else []
            except galaxy.GalaxyError as e:
                results.append(_failed(name, None, e.kind, str(e)))
                self.warnings.append(f"mirroring {name} failed with {e.kind}. {e}")
                return _Mirrored(lines, {}, 0)

            if served is None:
                listings.pop(name, None)
                lines = [_with(line, yanked=True) for line in lines]
                results.extend(_changes(name, have, lines))
                return _Mirrored(lines, {}, 0)

            listed: dict[str, str | None] = {}
            for v in upstream:
                version = v.get("version")
                try:
                    Version(_t.cast(str, version))
                except (TypeError, ValueError):
                    self.warnings.append(
                        f"the server lists {version!r} for {name}, which isn't a version, so it's left out"
                    )
                    continue
                listed[_t.cast(str, version)] = _rfc3339(v.get("created_at"))
            saved = {
                "name": name,
                "updated_at": served.get("updated_at"),
                "deprecated": bool(served.get("deprecated")),
                "versions": listed,
            }
            resumed = False
        else:
            log.info("%s, carrying on from the versions the server listed earlier", name)
            listed = _t.cast("dict[str, str | None]", saved["versions"])
            resumed = True

        by_version = {line.version.text: i for i, line in enumerate(lines)}
        missing = sorted(set(listed) - set(by_version), key=lambda v: (listed[v] or "", Version(v)))
        log.info("%s has %s on the server, %d not in the index yet", name, _count(len(listed), "version"), len(missing))
        extractions: dict[str, dict[str, object]] = {}
        done = 0
        deferred = False
        # Oldest upload first, so a collection mirrored over several runs keeps its lines in publication order.
        for version in missing:
            if limit is not None and downloads + done >= limit:
                results.append(Change(name, version, "deferred"))
                deferred = True
                continue
            done += 1
            log.info("downloading %s %s, %d of %d", name, version, done, len(missing))
            staged = self._mirror_version(client, name, version, listed[version], os.path.join(tmp, f"{n}-{done}"))
            if isinstance(staged, Change):
                results.append(staged)
                details = staged.details or {}
                self.warnings.append(
                    f"mirroring {name} {version} failed with {details.get('kind')}. {details.get('error')}"
                )
                continue
            line, extraction = staged
            lines.append(line)
            if extraction is not None:
                extractions[version] = extraction
            results.append(Change(name, version, "added", {"sha256": line.sha256}))

        if deferred:
            if not resumed:
                listings[name] = saved
        else:
            listings.pop(name, None)
        if resumed:
            # Checked against the server when it was listed. The next fresh listing checks it again.
            return _Mirrored(lines, extractions, done)

        for version, i in by_version.items():
            line = lines[i]
            if version in listed:
                if line.yanked or listed[version] != line.published or revalidate:
                    lines[i] = self._mirror_recheck(client, name, line, results)
            elif not line.yanked:
                try:
                    client.version(namespace, short, version)
                except galaxy.NotFound:
                    lines[i] = _with(line, yanked=True)
                except galaxy.GalaxyError as e:
                    results.append(_failed(name, version, e.kind, str(e)))
                    self.warnings.append(f"mirroring {name} {version} failed with {e.kind}. {e}")

        deprecated = bool(saved["deprecated"])
        lines = [_with(line, deprecated=deprecated) if line.deprecated != deprecated else line for line in lines]
        # One entry for each version already in the index whose yanked or deprecated changed, with all it changed.
        results.extend(_changes(name, have, lines))
        return _Mirrored(lines, extractions, done)

    def _mirror_version(
        self, client: galaxy.GalaxyClient, name: str, version: str, created: str | None, tmp: str
    ) -> tuple[CollectionVersion, dict[str, object] | None] | Change:
        """Download and stage a version the index doesn't have, writing its artifact, or why it failed."""
        namespace, short = name.split(".", 1)
        try:
            detail = client.version(namespace, short, version)
            sha256, url = _served_sha256(detail), detail.get("download_url")
            if not isinstance(url, str) or not isinstance(sha256, str):
                return _failed(name, version, "bad_detail", "no download_url or artifact sha256")
            data = client.download(url)
        except galaxy.GalaxyError as e:
            return _failed(name, version, e.kind, str(e))
        if (got := hashlib.sha256(data).hexdigest()) != sha256:
            return _failed(name, version, "sha256_mismatch", f"got {got}, not {sha256}")
        os.makedirs(tmp)
        path = os.path.join(tmp, "download.tar.gz")
        _write(path, data)
        published = _rfc3339(detail.get("created_at")) or created or now()
        try:
            staged = self._stage(path, os.path.join(tmp, "stage"), published, SKIPPABLE_CHECKS)
        except Error as e:
            return _failed(name, version, "bad_artifact", str(e))
        if (staged.line.name, staged.line.version.text) != (name, version):
            what = f"{staged.line.name} {staged.line.version.text}"
            return _failed(name, version, "manifest_mismatch", f"its MANIFEST.json is for {what}")
        if staged.extraction is not None and (error := staged.extraction.get("error")):
            self.warnings.append(
                f"extracting the plugins of {name} {version} failed, so the plugin index has none of them. {error}"
            )
        # Written now rather than at the commit, so a long run holds no more than one artifact at a time.
        self._write("artifact", self.artifact_file(staged.line), data, name)
        return staged.line, staged.extraction

    def _mirror_recheck(
        self, client: galaxy.GalaxyClient, name: str, line: CollectionVersion, results: list[Change]
    ) -> CollectionVersion:
        """Check a version the index has against the server's sha256, unyanking it if it's served again."""
        namespace, short = name.split(".", 1)
        version = line.version.text
        try:
            detail = client.version(namespace, short, version)
        except galaxy.GalaxyError as e:
            results.append(_failed(name, version, e.kind, str(e)))
            self.warnings.append(f"mirroring {name} {version} failed with {e.kind}. {e}")
            return line
        sha256 = _served_sha256(detail)
        if sha256 != line.sha256:
            # Never written over: the caller decides what another artifact for a published version means.
            results.append(Change(name, version, "conflict", {"sha256": sha256, "url": detail.get("download_url")}))
        return _with(line, yanked=False) if line.yanked else line

    def _unpack(self, name: str, state: _c.Mapping[str, object], tmp: str) -> str | artifact.ExtractError:
        """Unpack a version's artifact, checked against its state, into ``tmp``. If alai-core refuses one it once
        took, why, which costs the version its plugins rather than stopping a rebuild."""
        version, sha256 = _t.cast(str, state["version"]), _t.cast(str, state["sha256"])
        path = self._artifact_file(name, Version(version), sha256)
        data = _read(path)
        if data is None:
            raise Error(f"{path} is missing, which rebuilding {name} {version} needs")
        if hashlib.sha256(data).hexdigest() != sha256:
            raise Error(f"{path} isn't the artifact {name} {version} has, its sha256 differs")
        os.makedirs(tmp)
        copy = os.path.join(tmp, "artifact.tar.gz")
        _write(copy, data)
        dest = os.path.join(tmp, "collection")
        try:
            artifact.extract(copy, dest)
        except artifact.ExtractError as e:
            return e
        return dest

    def _derive(
        self,
        name: str,
        state: _c.Mapping[str, object],
        dest: str | artifact.ExtractError,
        old: CollectionVersion | None,
    ) -> tuple[CollectionVersion, str | None]:
        """A version's line from its state and its unpacked artifact, kept as the artifact says even where ansible-galaxy
        couldn't read it, and what changed from ``old`` that's meant to stay."""
        version = _t.cast(str, state["version"])
        if not isinstance(dest, str):
            raise Error(f"{name} {version}'s artifact can't be unpacked, so its line can't be made from it. {dest}")
        where = f"{name} {version}'s artifact"
        try:
            meta = collection.Manifest.parse(_read(os.path.join(dest, "MANIFEST.json")) or b"", name=where)
            info = meta.collection_info
            if (info.get_fqcn(), info.get_version().text) != (name, version):
                raise Error(f"{where} is for {info.get_fqcn()} {info.get_version().text}")
            deps = _raw_deps(info, where)
            requires_ansible = None
            runtime_data = _read(os.path.join(dest, "meta", "runtime.yml"))
            if runtime_data is not None:
                value = collection.Runtime.parse(runtime_data, name=where, on_warning=None).requires_ansible
                requires_ansible = value if isinstance(value, str) else None
        except ValueError as e:
            raise Error(str(e)) from None
        size = os.path.getsize(os.path.join(os.path.dirname(dest), "artifact.tar.gz"))
        kept = {k: v for k, v in (_fields(old) if old is not None else {}).items() if k != "deprecated"}
        fields = {
            **kept,
            **{k: v for k, v in state.items() if k != "version"},
            "name": name,
            "vers": version,
            "deps": [{"name": n, "req": r} for n, r in deps],
            "size": size,
        }
        fields.pop("requires_ansible", None)
        if requires_ansible is not None:
            fields["requires_ansible"] = requires_ansible
        line = _line(name, fields)
        change = None
        if old is not None:
            was, now_ = _fields(old), _fields(line)
            differ = [k for k in ("deps", "requires_ansible", "size") if was.get(k) != now_.get(k)]
            if differ:
                change = f"{name} {version}: {', '.join(differ)}"
        return line, change

    def _from_state(self, name: str, state: _c.Mapping[str, object], cached: CollectionVersion) -> CollectionVersion:
        """A version's line from its state and the derived fields its current index file has."""
        if cached.sha256 != state["sha256"]:
            raise Error(
                f"{name}'s index file has another artifact for {state['version']} than its state does. "
                + f"'alai-index rebuild {name} --files index --from-artifacts' makes it"
            )
        return _with(
            cached, published=state["published"], yanked=state["yanked"], deprecated=state.get("deprecated", False)
        )

    def _select(self, targets: _c.Sequence[str], listing: Collections) -> list[tuple[str, str]]:
        """Each version the targets name, as its collection and version, from the private state."""
        selected: dict[tuple[str, str], None] = {}
        for text in targets:
            name, version = _target(text)
            versions = [_t.cast(str, s["version"]) for s in self._states(name, listing)]
            matches = [v for v in versions if version in (None, v)]
            if not matches:
                raise Error(f"{name} {version} isn't in the index" if version else f"{name} isn't in the index")
            selected.update(dict.fromkeys((name, v) for v in matches))
        return list(selected)

    def _plugin_state(
        self, before: dict[str, list[CollectionVersion]], touched: _c.Iterable[str], *, commit: bool = True
    ) -> tuple[bytes | None, dict[str, dict[str, dict[str, object]]] | None]:
        """``plugins.jsonl``, unless it's not to be written, and the touched collections' extractions, or None for each
        without a plugin index."""
        if self.config.plugins is None:
            return None, None
        path = self.file(self.config.plugins)
        plugin_index = _read(path) if commit else None
        if plugin_index is None and commit:
            raise Error(f"{path} is missing, which config.json says the index has")
        return plugin_index, {
            name: self._extractions(name, [_state(line) for line in before[name]]) for name in touched
        }

    def _stage(self, path: str, tmp: str, published: str, skip: _c.Collection[str]) -> _Staged:
        """Read and check an artifact, and make its line and extraction."""
        data = _read(path)
        if data is None:
            raise Error(f"{path} doesn't exist")
        # Extracted from a copy, so the bytes checked are the bytes hashed and written.
        os.makedirs(tmp)
        copy = os.path.join(tmp, "artifact.tar.gz")
        _write(copy, data)
        dest = os.path.join(tmp, "collection")
        try:
            artifact.extract(copy, dest)
        except artifact.ExtractError as e:
            raise Error(f"{path}: {e}") from None

        try:
            info = collection.Manifest.parse(_read(os.path.join(dest, "MANIFEST.json")) or b"", name=path)
            meta = info.collection_info
            name, version = meta.get_fqcn(), meta.get_version()
            where = f"{name} {version.text}"
            try:
                deps = [(n, r.text) for n, r in meta.get_dependencies().items()]
            except collection.MetadataError as e:
                if "requirements" not in skip:
                    raise
                self.warnings.append(f"{where} has a requirement ansible-core can't read. {e}")
                deps = _raw_deps(meta, where)
            requires_ansible = None
            runtime_data = _read(os.path.join(dest, "meta", "runtime.yml"))
            if runtime_data is not None:
                runtime = collection.Runtime.parse(runtime_data, name=f"{path}: meta/runtime.yml", on_warning=None)
                try:
                    if runtime.get_requires_ansible() is not None:
                        requires_ansible = str(runtime.requires_ansible)
                except collection.MetadataError as e:
                    if "requirements" not in skip:
                        raise
                    self.warnings.append(f"{where} has a requirement ansible-core can't read. {e}")
                    value = runtime.requires_ansible
                    requires_ansible = value if isinstance(value, str) else None
            line = CollectionVersion(
                name=name,
                version=version,
                sha256=hashlib.sha256(data).hexdigest(),
                size=len(data),
                published=published,
                deps=deps,
                requires_ansible=requires_ansible,
            )
        except ValueError as e:
            raise Error(str(e)) from None
        extraction = plugins.extraction(dest, line.version.text) if self.config.plugins is not None else None
        return _Staged(line, data, extraction)

    def _listing(self) -> Collections:
        """The private listing, every collection the index has."""
        data = _read(self.listing_file)
        if data is None:
            raise Error(f"{self.listing_file} is missing, which every index has. Is the private directory right?")
        try:
            return Collections.parse(data)
        except ValueError as e:
            raise Error(f"{self.listing_file}: {e}") from None

    def _states(self, name: str, listing: Collections) -> list[dict[str, object]]:
        """A collection's versions in the private state, in publication order, or none if the index doesn't have it."""
        if name not in listing:
            return []
        path = self.state_file(name)
        data = _read(path)
        if data is None:
            # Never taken as empty, which would publish the collection with only the versions added now.
            raise Error(f"{path} is missing, which the private listing has for {name}")
        states: list[dict[str, object]] = []
        for n, raw in enumerate(data.splitlines(), 1):
            if not raw.strip():
                continue
            try:
                state: object = json.loads(raw)
            except ValueError as e:
                raise Error(f"{path}: line {n}: {e}") from None
            state = _t.cast("dict[str, object]", state) if isinstance(state, dict) else {}
            if (
                state.get("v") != 1
                or not all(isinstance(state.get(k), str) for k in ("version", "sha256", "published"))
                or not isinstance(state.get("yanked"), bool)
            ):
                raise Error(f"{path}: line {n} isn't a version's state this alai-index reads")
            states.append(state)
        return states

    def _cached(self, name: str, listing: Collections) -> dict[str, CollectionVersion]:
        """The lines a collection's current index file has, by version, or none if it's missing."""
        entry = listing.get(name)
        if entry is None:
            return {}
        path = self.file(self.config.index_path(name=name, sha256=entry.sha256))
        data = _read(path)
        if data is None:
            return {}
        parsed = CollectionIndex.parse(data, name)
        if parsed.invalid or parsed.newer:
            problems = [f"line {n}: {why}" for n, why in parsed.invalid]
            if parsed.newer:
                problems.append(f"{parsed.newer} lines need a newer alai-index")
            raise Error(
                f"{path} has lines this alai-index can't read, {'; '.join(problems)}. "
                + f"'alai-index rebuild {name} --files index --from-artifacts' makes it"
            )
        return {line.version.text: line for line in parsed.lines}

    def _lines(self, name: str, listing: Collections) -> list[CollectionVersion]:
        """A collection's lines, from its state and its current index file, or none if the index doesn't have it.

        Raises:
            Error: The index file is missing or doesn't match the state.
        """
        states = self._states(name, listing)
        cached = self._cached(name, listing)
        if len(cached) != len(states) or any(s["version"] not in cached for s in states):
            raise Error(
                f"{name}'s index file doesn't match its state. "
                + f"'alai-index rebuild {name} --files index --from-artifacts' makes it"
            )
        return [self._from_state(name, s, cached[_t.cast(str, s["version"])]) for s in states]

    def _extractions(
        self, name: str, states: list[dict[str, object]], *, partial: bool = False
    ) -> dict[str, dict[str, object]]:
        """A collection's plugin extractions for the versions it has, by version, dropping any for versions it doesn't.
        With ``partial``, a missing file or extraction is left out rather than an error."""
        path = self.extractions_file(name)
        versions = [_t.cast(str, s["version"]) for s in states]
        data = _read(path)
        if data is None:
            if versions and not partial:
                raise Error(
                    f"{path} is missing, which {name}'s plugin lines need. "
                    + f"'alai-index rebuild {name} --files plugins --from-artifacts' makes it"
                )
            return {}
        try:
            parsed = plugins.read_extractions(data, path)
        except ValueError as e:
            raise Error(str(e)) from None
        missing = [v for v in versions if v not in parsed]
        if missing and not partial:
            raise Error(
                f"{path} has no extraction for {name} {', '.join(missing)}. "
                + f"'alai-index rebuild {name} --files plugins --from-artifacts' makes it"
            )
        return {v: parsed[v] for v in versions if v in parsed}

    def _note(self, event: str, group: str, path: str, name: str | None) -> None:
        """Record a file written or deleted in ``files``, and its collection as changed."""
        if group == "artifact":
            dl = urllib.parse.urlsplit(self.config.dl)
            group = "dl" if dl.scheme or dl.netloc else "index"
        bases = {"dl": _dl_dir(self.root, self.dl), "index": self.root, "private": self.private}
        self.files[event][group].append(os.path.relpath(path, bases[group]).replace(os.sep, "/"))
        # Only a collection whose own files changed, so a batch commits no more than it has to.
        if name is not None and name not in self.files["collections"]:
            self.files["collections"].append(name)

    def _write(self, group: str, path: str, data: bytes, name: str | None = None) -> None:
        if _write_changed(path, data):
            self._note("written", group, path, name)

    def _remove(self, group: str, path: str, name: str | None = None) -> None:
        if os.path.exists(path):
            _remove(path)
            self._note("deleted", group, path, name)

    def _commit(
        self,
        lines: dict[str, list[CollectionVersion]],
        listing: Collections,
        *,
        artifacts: _c.Sequence[tuple[CollectionVersion, bytes]] = (),
        extractions: dict[str, dict[str, dict[str, object]]] | None = None,
        plugin_index: bytes | None = None,
        removed: _c.Sequence[CollectionVersion] = (),
        public_listing: bool = True,
    ) -> None:
        """Write a change to the collections in ``lines``, given all their lines, and note each file in ``files``.

        Private files come first, as the source of truth, then public ones in the order that never leaves the index
        naming a file that isn't there. Artifacts, the state and extractions, index files, the private listing,
        ``plugins.jsonl`` and ``collections.json``, and last deleting what ``removed`` and collections left with no
        lines leave behind. A file whose bytes wouldn't change isn't written. Without ``plugin_index``,
        ``plugins.jsonl`` isn't written, and without ``public_listing``, neither is ``collections.json``.
        """
        write, remove = self._write, self._remove

        for line, data in artifacts:
            write("artifact", self.artifact_file(line), data, line.name)
        # Each collection's extractions are in its index file's order.
        for name, found in (extractions or {}).items():
            if lines.get(name) != []:
                write("private", self.extractions_file(name), plugins.extractions_jsonl(found.values()), name)
        for name, collection_lines in lines.items():
            if collection_lines:
                write("private", self.state_file(name), _jsonl(_state(line) for line in collection_lines), name)

        updated = now()
        entries = {name: e for name in listing if (e := listing.get(name)) is not None and lines.get(name) != []}
        for name, collection_lines in lines.items():
            old = listing.get(name)
            if collection_lines:
                # Lines added are checked before this, so only lines already there can have a problem, kept as they are.
                data = CollectionIndex(name, collection_lines).to_jsonl(allow_unreadable=True).encode()
                sha256 = hashlib.sha256(data).hexdigest()
                write("index", self.file(self.config.index_path(name=name, sha256=sha256)), data, name)
                if old is None or old.sha256 != sha256:
                    entries[name] = CollectionEntry(updated, sha256)
            # One found by sha256 stays, for clients with an older listing, until the caller deletes it.
            if self.config.hashed and old is not None and entries.get(name) != old:
                self.files["stale"].append(self.config.index_path(name=name, sha256=old.sha256))
        listed = Collections(entries).to_json().encode()
        write("private", self.listing_file, listed)

        if plugin_index is not None and extractions is not None and self.config.plugins is not None:
            changed = {name: plugins.plugin_lines(name, found) for name, found in extractions.items()}
            write("index", self.file(self.config.plugins), plugins.merge(plugin_index, changed))
        if public_listing and self.config.collections is not None:
            write("index", self.file(self.config.collections), listed)

        for line in removed:
            remove("artifact", self.artifact_file(line), line.name)
        for name, collection_lines in lines.items():
            if not collection_lines:
                remove("private", self.state_file(name), name)
                remove("private", self.extractions_file(name), name)
                if not self.config.hashed:
                    remove("index", self.file(self.config.index_path(name=name)), name)


def _read(path: str) -> bytes | None:
    try:
        with open(path, "rb") as f:
            return f.read()
    except FileNotFoundError:
        return None


def _remove(path: str) -> None:
    with contextlib.suppress(FileNotFoundError):
        os.unlink(path)


def _write_changed(path: str, data: bytes) -> bool:
    """Write a file unless it already has these bytes, returning whether it was written."""
    if _read(path) == data:
        return False
    _write(path, data)
    return True


def _write(path: str, data: bytes) -> None:
    """Write a file whole or not at all, so a reader never sees half of one."""
    parent = os.path.dirname(path)
    os.makedirs(parent, exist_ok=True)
    # Not mkstemp, whose files are 0600, which a web server serving the tree couldn't read.
    tmp = os.path.join(parent, f".tmp-{uuid.uuid4().hex}")
    try:
        with open(tmp, "xb") as f:
            f.write(data)
        os.replace(tmp, path)
    except BaseException:
        os.unlink(tmp)
        raise
