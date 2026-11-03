# Copyright: (c) 2026, Jordan Borean (@jborean93) <jborean93@gmail.com>
# MIT License (see LICENSE or https://opensource.org/licenses/MIT)

"""An index as a tree of files on disk, and the operations that change it. The index files, ``collections.json`` and the
artifacts are the source of truth. ``plugins.jsonl`` is derived from the plugin cache, which can always be made again
from the artifacts, as README.md's "The tree" describes."""

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

PLUGIN_CACHE = "plugin-cache"
"""The plugin cache's name in the tree, when it isn't given."""


class Error(Exception):
    """A failure to report to the user, without a traceback."""


class Change(_t.NamedTuple):
    """A version a command was given, and what happened to it, as README.md's "Results" lists them."""

    name: str
    version: str | None
    """None for the whole collection."""
    result: str
    """``added``, ``changed``, ``unchanged``, ``removed``, ``extracted``, ``conflict``, ``failed`` or ``deferred``."""
    details: dict[str, object] | None = None
    """What there is to say about the result, by result."""


def _changed(old: CollectionVersion, new: CollectionVersion) -> dict[str, object]:
    """Each field of a line that differs, as ``new`` has it. ``yanked`` and ``deprecated`` are given as booleans, and a
    field ``new`` doesn't have as None."""
    was, now_ = _fields(old), _fields(new)
    changed = {k: now_.get(k) for k in sorted(was.keys() | now_.keys()) if was.get(k) != now_.get(k)}
    return changed | {k: getattr(new, k) for k in ("yanked", "deprecated") if getattr(new, k) != getattr(old, k)}


def _added(name: str, version: str, sha256: str, extraction: _c.Mapping[str, object] | None) -> Change:
    """A version added, with why extracting its plugins failed if it did."""
    error = extraction.get("error") if extraction is not None else None
    return Change(name, version, "added", {"sha256": sha256, **({"error": error} if error else {})})


def _failed(name: str, version: str | None, kind: str, error: str) -> Change:
    return Change(name, version, "failed", {"kind": kind, "error": error})


SKIPPABLE_CHECKS = ("dependencies", "requirements")
"""The checks ``Index.add`` can skip, to copy versions as their origin published them. ``dependencies`` is a dependency
no version in the index meets, and ``requirements`` one ``deps`` or ``requires_ansible`` ansible-core can't read."""


def _files() -> dict[str, _t.Any]:
    """What a command changed, by where each file is, as ``--output-format json`` gives it."""
    groups = ("dl", "index", "plugin-cache")
    return {
        "written": {g: [] for g in groups},
        "deleted": {g: [] for g in groups},
        "stale": [],
        "outdated": [],
    }


def now() -> str:
    """The time as the index writes it, RFC 3339 in UTC to the second."""
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def configure(
    root: str | os.PathLike[str],
    *,
    plugin_cache: str | os.PathLike[str] | None = None,
    dl: str | os.PathLike[str] | None = None,
    index: str | None = None,
    dl_template: str | None = None,
    listing: bool | None = None,
    plugins: bool | None = None,
) -> Config:
    """Write ``config.json``, and the empty ``collections.json`` and ``plugins.jsonl`` it needs.

    Each option left as None keeps what ``config.json`` already says, or else takes the default: files by name, a
    listing and a plugin index. The ``dl`` template defaults to the artifacts' directory, ``dl``, as a path in the
    index, which it has to be in. Once the index has collections, the index and ``dl`` templates can't change, as their
    files would be left where the old ones put them. Whether it has a listing or a plugin index can, as nothing moves.

    Raises:
        Error: The config isn't valid, or the index has collections and a template would change.
    """
    root = os.fspath(root)
    path = os.path.join(root, "config.json")
    tree = Index(root, plugin_cache=plugin_cache, dl=dl) if os.path.exists(path) else None
    current = tree.config if tree else None
    # An index's listing is never made empty, which would make every collection look new to the next add.
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
    _check_layout(root, _cache_dir(root, plugin_cache), dl, config)
    if config != current:
        if has_collections and current is not None and (config.index, config.dl) != (current.index, current.dl):
            raise Error(f"{root} already has collections, which changing where files are would leave behind")
        _write(path, config.to_json().encode())

    tree = Index(root, plugin_cache=plugin_cache, dl=dl)
    if not os.path.exists(tree.listing_file):
        _write(tree.listing_file, Collections({}).to_json().encode())
    if config.plugins and not os.path.exists(tree.file(config.plugins)):
        _write(tree.file(config.plugins), b"")
    return config


def _cache_dir(root: str, plugin_cache: str | os.PathLike[str] | None) -> str:
    return os.fspath(plugin_cache) if plugin_cache is not None else os.path.join(root, PLUGIN_CACHE)


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


def _check_layout(root: str, cache: str, dl: str | os.PathLike[str] | None, config: Config) -> None:
    """Refuse a ``dl`` template that isn't by sha256, a layout that puts index files or artifacts in the plugin cache,
    which ``add`` would overwrite, and artifacts the ``dl`` template puts outside the artifacts' directory given."""
    # The spec lets a reader take artifacts by name and version. A writer that did would reuse a URL for other bytes
    # when a version taken down is published again, which a cache could go on serving.
    if "{sha256-checksum}" not in config.dl:
        raise Error(f"the dl template {config.dl!r} needs {{sha256-checksum}}, so an artifact's URL never changes")
    artifact = _artifact_path(root, dl, config, "ns.coll", Version("1.0.0"), "0" * 64)
    places = [artifact]
    index_ref = urllib.parse.urlsplit(config.index_path(name="ns.coll", sha256="0" * 64))
    if not (index_ref.scheme or index_ref.netloc):
        places.append(_tree_path(root, urllib.parse.unquote(index_ref.path), index_ref.geturl()))
    if any(_within(p, cache) for p in places) or _within(cache, _dl_dir(root, dl)):
        raise Error(f"index files and artifacts can't be kept in {cache}, which is the plugin cache")
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


def _changes(name: str, before: list[CollectionVersion], after: list[CollectionVersion]) -> list[Change]:
    """A ``changed`` for each version in ``before`` whose fields that may change differ in ``after``."""
    old = {line.version.text: line for line in before}
    return [
        Change(name, line.version.text, "changed", changed)
        for line in after
        if line.version.text in old and (changed := _changed(old[line.version.text], line))
    ]


class _Cached(_t.NamedTuple):
    entries: dict[str, dict[str, object]]
    """A collection's extractions, by version, in its index file's order."""
    complete: bool
    """Whether every version has one, so its plugin lines can be worked out."""


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


def _read_checked(path: str | os.PathLike[str]) -> dict[str, dict[str, str]]:
    """A ``galaxy_mirror`` checked file, each collection's versions and the server's ``created_at`` checked for each."""
    data = _read(os.fspath(path))
    if data is None:
        raise Error(f"{os.fspath(path)} doesn't exist")
    try:
        checked: object = json.loads(data)
    except ValueError as e:
        raise Error(f"{os.fspath(path)}: {e}") from None
    if not isinstance(checked, dict) or not all(
        isinstance(versions, dict) and all(isinstance(t, str) for t in _t.cast("dict[str, object]", versions).values())
        for versions in _t.cast("dict[str, object]", checked).values()
    ):
        raise Error(f"{os.fspath(path)} isn't a JSON object of collections, each an object of versions and times")
    return _t.cast("dict[str, dict[str, str]]", checked)


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
    """The index whose tree is at ``root``, with its plugin cache in ``plugin_cache``, ``<root>/plugin-cache`` by default,
    and its artifacts in ``dl``, ``<root>/dl`` by default, for a ``dl`` template on another host.

    Raises:
        Error: ``config.json`` is missing or isn't valid, or a template leads into the plugin cache.
    """

    def __init__(
        self,
        root: str | os.PathLike[str],
        *,
        plugin_cache: str | os.PathLike[str] | None = None,
        dl: str | os.PathLike[str] | None = None,
    ) -> None:
        self.root = os.fspath(root)
        self.plugin_cache = _cache_dir(self.root, plugin_cache)
        self.dl = dl
        self.files = _files()
        """Every file the commands run on this index wrote, deleted or left stale, by group: ``dl``, artifacts on
        another host, relative to the artifacts' directory, ``index``, what's published with the index, relative to the
        root, in the order to publish it, and ``plugin-cache``, relative to the plugin cache. ``stale`` is each index
        file found by sha256 that no listing names any more, to delete once copies of the old listing have expired.
        ``outdated`` is each version whose extraction isn't current, as its name, version and artifact, whose artifact
        wasn't on disk to extract it again."""
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
        _check_layout(self.root, self.plugin_cache, self.dl, self.config)
        self._caches: dict[str, dict[str, dict[str, object]]] = {}

    @property
    def listing_file(self) -> str:
        """``collections.json``, every collection the index has. It's in the tree even when ``config.json`` says the
        index has no listing, as the index needs it. Whether it's published is up to whoever hosts the index."""
        return self.file(self.config.collections or COLLECTIONS)

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

    def cache_file(self, name: str) -> str:
        """Where a collection's plugin extractions are in the plugin cache."""
        namespace, short = name.split(".", 1)
        return os.path.join(self.plugin_cache, namespace, f"{short}.jsonl")

    def add(
        self,
        tarballs: _c.Sequence[str | os.PathLike[str]],
        /,
        *,
        published: str | None = None,
        skip: _c.Collection[str] = (),
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

        Returns:
            Each tarball's version, in the order given, then each version whose plugins were extracted again.

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
            results: list[Change] = []
            new: list[_Staged] = []
            for s in staged:
                name, vers = s.line.name, s.line.version.text
                if name not in lines:
                    lines[name] = self._lines(name, listing)
                have = lines[name]
                existing = next((line for line in have if line.version == s.line.version), None)
                if existing is not None and existing.sha256 != s.line.sha256:
                    raise Error(
                        f"{name} {vers} is already in the index with another artifact, sha256 {existing.sha256}"
                    )
                results.append(
                    _added(name, vers, s.line.sha256, s.extraction)
                    if existing is None
                    else Change(name, vers, "unchanged", {"sha256": s.line.sha256})
                )
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
                        f"extracting the plugins of {s.line.name} {s.line.version.text} failed, so the plugin index "
                        + f"has none of them. {error}"
                    )

            touched = {s.line.name: lines[s.line.name] for s in new}
            added: dict[str, dict[str, dict[str, object]]] = {}
            for s in new:
                if s.extraction is not None:
                    added.setdefault(s.line.name, {})[s.line.version.text] = s.extraction
            plugin_index = self._plugin_index()
            cache = self._plugin_cache(touched, tmp, results, added=added)
            self._commit(
                touched,
                listing,
                artifacts=[(s.line, s.data) for s in new],
                cache=cache,
                plugin_index=plugin_index,
            )
        return results

    def set(
        self,
        targets: _c.Sequence[str],
        /,
        *,
        yanked: bool | None = None,
        deprecated: bool | None = None,
    ) -> list[Change]:
        """Change whether versions are yanked or deprecated, the only fields of a line the index sets after it's added.

        Plugin lines count yanked and deprecated versions too, so ``plugins.jsonl`` stays as it is.

        Args:
            targets: Each ``NS.NAME``, for every version of the collection, or ``NS.NAME:VERSION``.
            yanked: Yank or unyank them, or None to leave it.
            deprecated: Deprecate them or not, or None to leave it.

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
            self._commit({name: lines[name] for name in touched}, listing)
        return results

    def remove(self, targets: _c.Sequence[str], /) -> list[Change]:
        """Take versions down: their lines, artifacts and extractions are deleted, and with a collection's last, its
        index file and listing entry.

        It's refused if a version that stays, and isn't yanked, would be left without a version it needs, which a
        takedown mustn't do to an index. Yank those first, or remove them too.

        Args:
            targets: Each ``NS.NAME``, for every version of the collection, or ``NS.NAME:VERSION``.

        Returns:
            Each version removed, in order, then each version whose plugins were extracted again.

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

        results = [Change(line.name, line.version.text, "removed", {"sha256": line.sha256}) for line in gone]
        plugin_index = self._plugin_index()
        with tempfile.TemporaryDirectory(prefix="alai-index-", ignore_cleanup_errors=True) as tmp:
            cache = self._plugin_cache({name: have for name, have in lines.items() if have}, tmp, results)
            self._commit(lines, listing, cache=cache, plugin_index=plugin_index, removed=gone)
        return results

    def generate_indexes(self, targets: _c.Sequence[str] = (), /) -> list[Change]:
        """Make index lines again from the artifacts, as ``add`` makes them, keeping what the index set.

        Each targeted version's line is read again from its artifact's ``MANIFEST.json`` and ``meta/runtime.yml``,
        with its ``published``, ``yanked`` and ``deprecated`` kept from the line it has. It's for when alai-index reads
        artifacts differently than it did when a version was added, such as a field added or a fix. Requirements are
        kept as the artifact gives them, as when mirroring.

        Args:
            targets: Each ``NS.NAME``, for every version of the collection, or ``NS.NAME:VERSION``. None for every
                collection.

        Returns:
            Each version whose line changed, with the fields that changed.

        Raises:
            Error: A target isn't in the index, an index file can't be read, or an artifact isn't on disk, isn't the
                one its line names or can't be read. Nothing is written.
        """
        listing = self._listing()
        if targets:
            selected = self._select(targets, listing)
        else:
            selected = [(name, line.version.text) for name in listing for line in self._lines(name, listing)]
        wanted = set(selected)
        lines = {name: self._lines(name, listing) for name in dict.fromkeys(name for name, _ in selected)}
        results: list[Change] = []
        with tempfile.TemporaryDirectory(prefix="alai-index-", ignore_cleanup_errors=True) as tmp:
            for i, (name, have) in enumerate(lines.items()):
                # As written, so a field this alai-index doesn't read, and so drops, is a change too.
                written = self._written_lines(name, listing)
                for n, old in enumerate(have):
                    if (name, old.version.text) not in wanted:
                        continue
                    have[n] = self._read_again(old, os.path.join(tmp, f"{i}-{n}"))
                    was, now_ = written.get(old.version.text, {}), _fields(have[n])
                    if changed := {
                        k: now_.get(k) for k in sorted(was.keys() | now_.keys()) if was.get(k) != now_.get(k)
                    }:
                        results.append(Change(name, old.version.text, "changed", changed))
        self._commit(lines, listing)
        return results

    def _written_lines(self, name: str, listing: Collections) -> dict[str, dict[str, object]]:
        """A collection's index file lines as written, each as plain JSON by version. Only called after ``_lines``,
        which checks the file."""
        entry = _t.cast(CollectionEntry, listing.get(name))
        data = _read(self.file(self.config.index_path(name=name, sha256=entry.sha256))) or b""
        lines = [_t.cast("dict[str, object]", json.loads(raw)) for raw in data.splitlines() if raw.strip()]
        return {str(line.get("vers")): line for line in lines}

    def _read_again(self, old: CollectionVersion, tmp: str) -> CollectionVersion:
        """A version's line read again from its artifact, with what the index set kept from ``old``."""
        path = self.artifact_file(old)
        where = f"{old.name} {old.version.text}"
        if not os.path.exists(path):
            raise Error(f"{path} is missing, which reading {where}'s artifact needs")
        log.info("reading the artifact of %s", where)
        staged = self._stage(path, tmp, old.published, SKIPPABLE_CHECKS, extract=False)
        if staged.line.sha256 != old.sha256:
            raise Error(f"{path} isn't the artifact {where} has, its sha256 differs")
        if (staged.line.name, staged.line.version.text) != (old.name, old.version.text):
            raise Error(f"{path} is for {staged.line.name} {staged.line.version.text}, not {where}")
        return _with(staged.line, yanked=old.yanked, deprecated=old.deprecated)

    def generate_plugins(self, targets: _c.Sequence[str] = (), /, *, from_plugin_cache: bool = False) -> list[Change]:
        """Extract the targeted versions' plugins again from their artifacts, and work ``plugins.jsonl`` out again.

        With ``from_plugin_cache``, ``plugins.jsonl`` is worked out from the plugin cache instead, so artifacts are
        only needed for an extraction that's missing or out of date. Each of those is made again when its artifact is
        on disk, and listed in ``files["outdated"]`` otherwise. Without targets, it's every collection, and a lost
        ``plugins.jsonl`` is made again. Writing ``plugins.jsonl`` always drops lines for a collection the index
        doesn't have.

        Args:
            targets: Each ``NS.NAME``, for every version of the collection, or ``NS.NAME:VERSION``. None for every
                collection.
            from_plugin_cache: Use the plugin cache rather than extract every targeted version again.

        Returns:
            Each version whose plugins were extracted again.

        Raises:
            Error: The index has no plugin index, a target isn't in the index, or an artifact that's needed isn't on
                disk. Nothing is written.
        """
        if self.config.plugins is None:
            raise Error("the index has no plugin index. 'alai-index config --plugins' adds one")
        listing = self._listing()
        if targets:
            selected = self._select(targets, listing)
            names: _c.Iterable[str] = dict.fromkeys(name for name, _ in selected)
        else:
            names = list(listing)
            selected = []
        haves = {name: self._lines(name, listing) for name in names}
        wanted = (
            set(selected) if targets else {(name, line.version.text) for name, have in haves.items() for line in have}
        )

        results: list[Change] = []
        added: dict[str, dict[str, dict[str, object]]] = {}
        with tempfile.TemporaryDirectory(prefix="alai-index-", ignore_cleanup_errors=True) as tmp:
            if not from_plugin_cache:
                for i, (name, have) in enumerate(haves.items()):
                    for n, line in enumerate(have):
                        version = line.version.text
                        if (name, version) in wanted:
                            log.info("extracting the plugins of %s %s", name, version)
                            dest = self._unpack(line, os.path.join(tmp, f"{i}-{n}"))
                            added.setdefault(name, {})[version] = self._extracted(name, version, dest, results)
            cache = self._plugin_cache(haves, tmp, results, added=added)
            # Every collection's lines can be worked out again from nothing, so a lost plugins.jsonl is no error.
            plugin_index = self._plugin_index() if targets else _read(self.file(self.config.plugins)) or b""
            self._commit({}, listing, cache=cache, plugin_index=plugin_index)
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
        session: str | os.PathLike[str] | None = None,
        checked: str | os.PathLike[str] | None = None,
    ) -> list[Change]:
        """Bring collections in line with a Galaxy server, through the v3 endpoints ansible-galaxy uses.

        For each collection, versions the server lists and the index doesn't have are added, oldest upload first, with
        ``published`` from the server's ``created_at``, and their artifacts checked against its sha256. They're copied as
        published, so the ``dependencies`` and ``requirements`` checks warn rather than refuse. A version is yanked only
        when its own page, or the collection's, is a 404, so a listing that's cut short can never yank anything, and a
        yanked one the server lists again is checked and unyanked. A version the index has is checked against the
        server's sha256 when it's yanked, its ``created_at`` differs from the one ``checked`` gives or else from
        ``published``, or with ``revalidate``. The same artifact is ``unchanged``, and another is a ``conflict``, never
        written. The collection's ``deprecated`` goes on every line.

        A failure is reported for its version, and the rest go on.

        Args:
            client: The server.
            targets: Each ``NS.NAME``.
            everything: Every collection the server lists or the index has, in place of targets.
            limit: The most versions to download. Those left, and the collections after, are ``deferred``.
            revalidate: Check every version the index has against the server's sha256.
            session: A file to carry on from, if it's there, and to write what's needed to carry on to. A collection
                ``limit`` leaves part done is kept there with what the server said of it, so the next run only
                downloads. It's for runs close together, against the same server, and its contents are internal to
                alai-index.
            checked: A JSON file of the server's ``created_at`` already checked for versions whose ``created_at``
                differs from ``published``, as ``{"ns.name": {"version": "created_at"}}``, which the caller keeps.

        Returns:
            What happened to each version.

        Raises:
            Error: The options conflict, a target isn't a collection name, the server can't be listed, or ``checked``
                can't be read.
        """
        if targets and everything:
            raise Error("give collections or --all, not both")
        if not targets and not everything:
            raise Error("name collections, or --all for every one")
        known = _read_checked(checked) if checked is not None else {}
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
        looked: dict[str, list[CollectionVersion]] = {}
        downloads = 0
        listings = _read_session(session) if session is not None else {}
        # Checked before any download, as artifacts are written as they come.
        haves = {name: self._lines(name, listing) for name in names}
        if self.config.plugins is not None:
            for name in names:
                self._read_cache(name)
        plugin_index = self._plugin_index()
        with tempfile.TemporaryDirectory(prefix="alai-index-", ignore_cleanup_errors=True) as tmp:
            for i, (name, have) in enumerate(haves.items()):
                if limit is not None and downloads >= limit:
                    results.append(Change(name, None, "deferred"))
                    continue
                mirrored = self._mirror_collection(
                    client, name, have, revalidate, limit, downloads, tmp, i, results, listings, known.get(name, {})
                )
                downloads += mirrored.downloads
                looked[name] = mirrored.lines
                if mirrored.lines != have:
                    lines[name] = mirrored.lines
                if mirrored.extractions:
                    new[name] = mirrored.extractions
            if session is not None:
                data = {"v": 1, "collections": listings}
                _write(os.fspath(session), json.dumps(data, separators=(",", ":")).encode())

            cache = self._plugin_cache(looked, tmp, results, added=new)
            extracted = any(c.result == "extracted" for c in results)
            if not lines and not extracted:
                log.info("nothing to write")
                return results
            log.info("writing the changes to %s", _count(len(lines), "collection"))
            self._commit(lines, listing, cache=cache, plugin_index=plugin_index)
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
        checked: _c.Mapping[str, str],
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
            results.append(_added(name, version, line.sha256, extraction))

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
                # A created_at the caller already checked, for an artifact the server changed, isn't checked again.
                if line.yanked or listed[version] != checked.get(version, line.published) or revalidate:
                    lines[i] = self._mirror_recheck(client, name, line, listed[version], results)
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
        self._write("artifact", self.artifact_file(staged.line), data)
        return staged.line, staged.extraction

    def _mirror_recheck(
        self,
        client: galaxy.GalaxyClient,
        name: str,
        line: CollectionVersion,
        created: str | None,
        results: list[Change],
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
        published = _rfc3339(detail.get("created_at")) or created
        if sha256 != line.sha256:
            # Never written over: the caller decides what another artifact for a published version means.
            details: dict[str, object] = {"sha256": sha256, "url": detail.get("download_url"), "published": published}
            results.append(Change(name, version, "conflict", details))
        else:
            results.append(Change(name, version, "unchanged", {"sha256": sha256, "published": published}))
        return _with(line, yanked=False) if line.yanked else line

    def _unpack(self, line: CollectionVersion, tmp: str) -> str | artifact.ExtractError:
        """Unpack a version's artifact, checked against its line, into ``tmp``. If alai-core refuses one it once took,
        why, which costs the version its plugins rather than stopping the command."""
        path = self.artifact_file(line)
        data = _read(path)
        if data is None:
            raise Error(f"{path} is missing, which reading {line.name} {line.version.text}'s artifact needs")
        if hashlib.sha256(data).hexdigest() != line.sha256:
            raise Error(f"{path} isn't the artifact {line.name} {line.version.text} has, its sha256 differs")
        os.makedirs(tmp)
        copy = os.path.join(tmp, "artifact.tar.gz")
        _write(copy, data)
        dest = os.path.join(tmp, "collection")
        try:
            artifact.extract(copy, dest)
        except artifact.ExtractError as e:
            return e
        return dest

    def _extracted(
        self, name: str, version: str, dest: str | artifact.ExtractError, results: list[Change]
    ) -> dict[str, object]:
        """A version's extraction from its unpacked artifact, reported as ``extracted``."""
        found = plugins.extraction(dest, version) if isinstance(dest, str) else plugins.failure(version, dest)
        error = _t.cast("str | None", found.get("error"))
        results.append(Change(name, version, "extracted", {"error": error} if error else None))
        if error:
            self.warnings.append(
                f"extracting the plugins of {name} {version} failed, so the plugin index has none of them. {error}"
            )
        return found

    def _select(self, targets: _c.Sequence[str], listing: Collections) -> list[tuple[str, str]]:
        """Each version the targets name, as its collection and version."""
        selected: dict[tuple[str, str], None] = {}
        for text in targets:
            name, version = _target(text)
            versions = [line.version.text for line in self._lines(name, listing)]
            matches = [v for v in versions if version in (None, v)]
            if not matches:
                raise Error(f"{name} {version} isn't in the index" if version else f"{name} isn't in the index")
            selected.update(dict.fromkeys((name, v) for v in matches))
        return list(selected)

    def _plugin_index(self) -> bytes | None:
        """``plugins.jsonl``, to merge a change into, or None for an index without a plugin index."""
        if self.config.plugins is None:
            return None
        path = self.file(self.config.plugins)
        data = _read(path)
        if data is None:
            raise Error(f"{path} is missing, which config.json says the index has")
        return data

    def _read_cache(self, name: str) -> dict[str, dict[str, object]]:
        """A collection's extractions this alai-index can read, by version, from the plugin cache. Read once, so a
        command refuses a cache from a newer alai-index before it writes anything."""
        if name not in self._caches:
            path = self.cache_file(name)
            data = _read(path)
            try:
                self._caches[name] = plugins.read_extractions(data, path) if data is not None else {}
            except plugins.NewerCache as e:
                raise Error(f"{e}. Use that alai-index, or delete {path} to extract the plugins again") from None
        return dict(self._caches[name])

    def _plugin_cache(
        self,
        collections: _c.Mapping[str, list[CollectionVersion]],
        tmp: str,
        results: list[Change],
        *,
        added: _c.Mapping[str, _c.Mapping[str, dict[str, object]]] | None = None,
    ) -> dict[str, _Cached] | None:
        """Each collection's extractions for the versions it has, or None for an index without a plugin index.

        ``added`` are new extractions, made as versions were added or read again. An extraction that isn't current is
        made again when its artifact is on disk, and reported as ``extracted``. Otherwise it's listed in
        ``files["outdated"]``, and used as it is until then, if it can be read. A collection without an extraction for
        every version isn't complete, so it keeps the plugin lines it has.
        """
        if self.config.plugins is None:
            return None
        cached: dict[str, _Cached] = {}
        for i, (name, lines) in enumerate(collections.items()):
            found = self._read_cache(name)
            found.update((added or {}).get(name, {}))
            complete = True
            for n, line in enumerate(lines):
                version = line.version.text
                entry = found.get(version)
                if entry is not None and plugins.current(entry):
                    continue
                path = self.artifact_file(line)
                if os.path.exists(path):
                    log.info("extracting the plugins of %s %s again", name, version)
                    dest = self._unpack(line, os.path.join(tmp, f"cache-{i}-{n}"))
                    found[version] = self._extracted(name, version, dest, results)
                    continue
                group, rel = self._relative("artifact", path)
                self.files["outdated"].append({"name": name, "version": version, group: rel})
                if entry is None:
                    complete = False
            entries = {line.version.text: found[line.version.text] for line in lines if line.version.text in found}
            cached[name] = _Cached(entries, complete)
        return cached

    def _stage(self, path: str, tmp: str, published: str, skip: _c.Collection[str], *, extract: bool = True) -> _Staged:
        """Read and check an artifact, and make its line, and its extraction unless ``extract`` is False."""
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
        extraction = (
            plugins.extraction(dest, line.version.text) if extract and self.config.plugins is not None else None
        )
        return _Staged(line, data, extraction)

    def _listing(self) -> Collections:
        """``collections.json``, every collection the index has and its current index file."""
        data = _read(self.listing_file)
        if data is None:
            raise Error(f"{self.listing_file} is missing, which every index has. Is --root right?")
        try:
            return Collections.parse(data)
        except ValueError as e:
            raise Error(f"{self.listing_file}: {e}") from None

    def _lines(self, name: str, listing: Collections) -> list[CollectionVersion]:
        """A collection's lines, in publication order, from its current index file, or none if the index doesn't have
        it.

        Raises:
            Error: The index file is missing, isn't the one ``collections.json`` names, or has lines this alai-index
                can't read.
        """
        entry = listing.get(name)
        if entry is None:
            return []
        path = self.file(self.config.index_path(name=name, sha256=entry.sha256))
        data = _read(path)
        if data is None:
            # Never taken as empty, which would publish the collection with only the versions added now.
            raise Error(f"{path} is missing, which {self.listing_file} has for {name}")
        if hashlib.sha256(data).hexdigest() != entry.sha256:
            raise Error(f"{path} isn't the index file {self.listing_file} has for {name}, its sha256 differs")
        parsed = CollectionIndex.parse(data, name)
        if parsed.invalid or parsed.newer:
            problems = [f"line {n}: {why}" for n, why in parsed.invalid]
            if parsed.newer:
                problems.append(f"{parsed.newer} lines need a newer alai-index")
            raise Error(f"{path} has lines this alai-index can't read, {'; '.join(problems)}")
        return list(parsed.lines)

    def _relative(self, group: str, path: str) -> tuple[str, str]:
        """A file's group in ``files`` and its path relative to the group's directory. An artifact's group is ``dl`` for
        a ``dl`` template on another host, and ``index`` for one that's a path in the index."""
        if group == "artifact":
            dl = urllib.parse.urlsplit(self.config.dl)
            group = "dl" if dl.scheme or dl.netloc else "index"
        bases = {"dl": _dl_dir(self.root, self.dl), "index": self.root, "plugin-cache": self.plugin_cache}
        return group, os.path.relpath(path, bases[group]).replace(os.sep, "/")

    def _note(self, event: str, group: str, path: str) -> None:
        """Record a file written or deleted in ``files``."""
        group, rel = self._relative(group, path)
        self.files[event][group].append(rel)

    def _write(self, group: str, path: str, data: bytes) -> None:
        if _write_changed(path, data):
            self._note("written", group, path)

    def _remove(self, group: str, path: str) -> None:
        if os.path.exists(path):
            _remove(path)
            self._note("deleted", group, path)

    def _commit(
        self,
        lines: dict[str, list[CollectionVersion]],
        listing: Collections,
        *,
        artifacts: _c.Sequence[tuple[CollectionVersion, bytes]] = (),
        cache: dict[str, _Cached] | None = None,
        plugin_index: bytes | None = None,
        removed: _c.Sequence[CollectionVersion] = (),
    ) -> None:
        """Write a change to the collections in ``lines``, given all their lines, and note each file in ``files``.

        Files are written in the order that never leaves the index naming a file that isn't there. That's artifacts,
        the plugin cache, index files, ``plugins.jsonl`` and ``collections.json``, and last deleting what ``removed``
        and collections left with no lines leave behind. A file whose bytes wouldn't change isn't written. Without
        ``plugin_index``, ``plugins.jsonl`` isn't written. ``collections.json`` always is, as it's the record of which
        index file is each collection's.
        """
        write, remove = self._write, self._remove

        for line, data in artifacts:
            write("artifact", self.artifact_file(line), data)
        # Each collection's extractions are in its index file's order.
        for name, found in (cache or {}).items():
            if lines.get(name) != []:
                write("plugin-cache", self.cache_file(name), plugins.extractions_jsonl(found.entries.values()))

        updated = now()
        entries = {name: e for name in listing if (e := listing.get(name)) is not None and lines.get(name) != []}
        for name, collection_lines in lines.items():
            old = listing.get(name)
            if collection_lines:
                # Lines added are checked before this, so only lines already there can have a problem, kept as they are.
                data = CollectionIndex(name, collection_lines).to_jsonl(allow_unreadable=True).encode()
                sha256 = hashlib.sha256(data).hexdigest()
                path = self.file(self.config.index_path(name=name, sha256=sha256))
                renamed = old is None or old.sha256 != sha256
                # One found by sha256 that's named again, as when a yank is undone, is published again too, in case
                # the host deleted it as stale.
                if _write_changed(path, data) or (renamed and self.config.hashed):
                    self._note("written", "index", path)
                if renamed:
                    entries[name] = CollectionEntry(updated, sha256)
            # One found by sha256 stays, for clients with an older listing, until the caller deletes it.
            if self.config.hashed and old is not None and entries.get(name) != old:
                self.files["stale"].append(self.config.index_path(name=name, sha256=old.sha256))

        if plugin_index is not None and cache is not None and self.config.plugins is not None:
            changed = {name: plugins.plugin_lines(name, c.entries) for name, c in cache.items() if c.complete}
            changed |= {name: [] for name, collection_lines in lines.items() if not collection_lines}
            write("index", self.file(self.config.plugins), plugins.merge(plugin_index, changed, entries))
        write("index", self.listing_file, Collections(entries).to_json().encode())

        for line in removed:
            remove("artifact", self.artifact_file(line))
        for name, collection_lines in lines.items():
            if not collection_lines:
                remove("plugin-cache", self.cache_file(name))
                if not self.config.hashed:
                    remove("index", self.file(self.config.index_path(name=name)))


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
