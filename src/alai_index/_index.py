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
import os
import posixpath
import tempfile
import typing as _t
import urllib.parse
import uuid

from alai_core import artifact, collection
from alai_core.index import CollectionEntry, CollectionIndex, Collections, CollectionVersion, Config
from alai_core.semver import Version, VersionReq

from . import _plugins as plugins

DEFAULT_INDEX = "index/{namespace}/{name}.jsonl"
DEFAULT_DL = "dl/{sha256-prefix}/{sha256-checksum}.tar.gz"
COLLECTIONS = "collections.json"
PLUGINS = "plugins.jsonl"
PRIVATE = "private"
"""The private directory's name in the tree, when it isn't given."""


class Error(Exception):
    """A failure to report to the user, without a traceback."""


class Added(_t.NamedTuple):
    """A version ``Index.add`` was given."""

    name: str
    version: str
    sha256: str
    added: bool
    """False if the index already had it, with the same artifact."""


class Changed(_t.NamedTuple):
    """A version ``Index.set`` or ``Index.remove`` was asked to change."""

    name: str
    version: str
    changed: bool
    """False if it already was as asked."""


class Rebuilt(_t.NamedTuple):
    """Something ``Index.rebuild`` made again."""

    what: str
    """A version's plugins, such as ``ns.coll 1.0.0's plugins``, or a public file's path in the tree."""
    error: str | None = None
    """Why extracting a version's plugins failed, or None."""


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

    def add(self, tarballs: _c.Sequence[str | os.PathLike[str]], /, *, published: str | None = None) -> list[Added]:
        """Add collection artifacts, each version's line taken from its own ``MANIFEST.json`` and ``meta/runtime.yml``.

        Every tarball is checked before anything is written. A version the index has with the same artifact is
        skipped, and one it has with another is refused. Each dependency must be met by a version that isn't yanked,
        in the index or among ``tarballs``.

        Args:
            tarballs: The artifacts.
            published: When they were first published, RFC 3339 in UTC, or None for now.

        Returns:
            Each tarball's version, in the order given.

        Raises:
            Error: A tarball can't be added. Nothing is written.
        """
        published = published or now()
        listing = self._listing()
        with tempfile.TemporaryDirectory(prefix="alai-index-", ignore_cleanup_errors=True) as tmp:
            staged = [self._stage(os.fspath(t), os.path.join(tmp, str(i)), published) for i, t in enumerate(tarballs)]

        lines: dict[str, list[CollectionVersion]] = {}
        before: dict[str, list[CollectionVersion]] = {}
        results: list[Added] = []
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
            results.append(Added(name, vers, s.line.sha256, existing is None))
            if existing is None:
                have.append(s.line)
                new.append(s)

        others: dict[str, list[CollectionVersion]] = {}
        for s in new:
            for dep, req in s.line.deps:
                if dep not in lines and dep not in others:
                    others[dep] = self._lines(dep, listing)
                if not _met(req, lines[dep] if dep in lines else others[dep]):
                    raise Error(
                        f"{s.line.name} {s.line.version.text} needs {dep} {req.text}, "
                        + "which no version in the index that isn't yanked meets"
                    )
        if not new:
            return results

        touched = {s.line.name: lines[s.line.name] for s in new}
        plugin_index, extractions = self._plugin_state(before, touched)
        if extractions is not None:
            for s in new:
                extractions[s.line.name][s.line.version.text] = _t.cast("dict[str, object]", s.extraction)
        self._commit(
            touched,
            listing,
            artifacts=[(s.line, s.data) for s in new],
            extractions=extractions,
            plugin_index=plugin_index,
        )
        return results

    def set(
        self, targets: _c.Sequence[str], /, *, yanked: bool | None = None, deprecated: bool | None = None
    ) -> list[Changed]:
        """Change whether versions are yanked or deprecated, the only fields of a line that may change.

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
        results: list[Changed] = []
        touched: set[str] = set()
        for name, version in selected:
            i = next(i for i, line in enumerate(lines[name]) if line.version.text == version)
            old = lines[name][i]
            lines[name][i] = _with(old, yanked=yanked, deprecated=deprecated)
            changed = lines[name][i] != old
            results.append(Changed(name, version, changed))
            if changed:
                touched.add(name)
        if touched:
            # Plugin lines count yanked and deprecated versions too, so they stay as they are.
            self._commit({name: lines[name] for name in touched}, listing)
        return results

    def remove(self, targets: _c.Sequence[str], /) -> list[Changed]:
        """Take versions down: their lines, artifacts and extractions are deleted, and with a collection's last, its
        index file and listing entry.

        It's refused if a version that stays, and isn't yanked, would be left without a version it needs, which a
        takedown mustn't do to an index. Yank those first, or remove them too.

        Args:
            targets: Each ``NS.NAME``, for every version of the collection, or ``NS.NAME:VERSION``.

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

        plugin_index, extractions = self._plugin_state(before, lines)
        if extractions is not None:
            for name, found in extractions.items():
                extractions[name] = {line.version.text: found[line.version.text] for line in lines[name]}
        self._commit(lines, listing, extractions=extractions, plugin_index=plugin_index, removed=gone)
        return [Changed(line.name, line.version.text, True) for line in gone]

    def rebuild(
        self,
        targets: _c.Sequence[str] = (),
        /,
        *,
        everything: bool = False,
        files: _c.Collection[str] | None = None,
        from_artifacts: bool = False,
        allow_changes: bool = False,
    ) -> list[Rebuilt]:
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
            Each version whose plugins were extracted again, and each public file that changed.

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
        results: list[Rebuilt] = []
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
                        results.append(
                            Rebuilt(f"{name} {version}'s plugins", _t.cast("str | None", found[version].get("error")))
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
        written = self._commit(
            lines,
            listing,
            extractions=extractions if do_plugins else None,
            plugin_index=plugin_index,
            public_listing=do_index,
        )
        return results + [Rebuilt(path) for path in written]

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
            raw: object = info.get("dependencies") or {}
            deps = _t.cast("dict[str, object]", raw) if isinstance(raw, dict) else None
            if deps is None or not all(isinstance(r, str) for r in deps.values()):
                raise Error(f"{where} has dependencies that aren't a mapping of names to requirements")
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
            "deps": [{"name": n, "req": r} for n, r in _t.cast("dict[str, str]", deps).items()],
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
        self, before: dict[str, list[CollectionVersion]], touched: _c.Iterable[str]
    ) -> tuple[bytes | None, dict[str, dict[str, dict[str, object]]] | None]:
        """``plugins.jsonl`` and the touched collections' extractions, or None for each without a plugin index."""
        if self.config.plugins is None:
            return None, None
        path = self.file(self.config.plugins)
        plugin_index = _read(path)
        if plugin_index is None:
            raise Error(f"{path} is missing, which config.json says the index has")
        return plugin_index, {
            name: self._extractions(name, [_state(line) for line in before[name]]) for name in touched
        }

    def _stage(self, path: str, tmp: str, published: str) -> _Staged:
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
            requires_ansible = None
            runtime_data = _read(os.path.join(dest, "meta", "runtime.yml"))
            if runtime_data is not None:
                runtime = collection.Runtime.parse(runtime_data, name=f"{path}: meta/runtime.yml", on_warning=None)
                if runtime.get_requires_ansible() is not None:
                    requires_ansible = str(runtime.requires_ansible)
            line = CollectionVersion(
                name=meta.get_fqcn(),
                version=meta.get_version(),
                sha256=hashlib.sha256(data).hexdigest(),
                size=len(data),
                published=published,
                deps=[(n, r.text) for n, r in meta.get_dependencies().items()],
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
    ) -> list[str]:
        """Write a change to the collections in ``lines``, given all their lines, and return each public file that
        changed, by its path in the tree.

        Private files come first, as the source of truth, then public ones in the order that never leaves the index
        naming a file that isn't there. Artifacts, the state and extractions, index files, the private listing,
        ``plugins.jsonl`` and ``collections.json``, and last deleting what ``removed`` and collections left with no
        lines leave behind. A file whose bytes wouldn't change isn't written.
        """
        written: list[str] = []

        def public(path: str, data: bytes) -> None:
            if _write_changed(path, data):
                written.append(os.path.relpath(path, self.root).replace(os.sep, "/"))

        for line, data in artifacts:
            _write(self.artifact_file(line), data)
        # Each collection's extractions are in its index file's order.
        for name, found in (extractions or {}).items():
            if lines.get(name) != []:
                _write_changed(self.extractions_file(name), plugins.extractions_jsonl(found.values()))
        for name, collection_lines in lines.items():
            if collection_lines:
                _write_changed(self.state_file(name), _jsonl(_state(line) for line in collection_lines))

        updated = now()
        entries = {name: e for name in listing if (e := listing.get(name)) is not None and lines.get(name) != []}
        for name, collection_lines in lines.items():
            if collection_lines:
                # Lines added are checked before this, so only lines already there can have a problem, kept as they are.
                data = CollectionIndex(name, collection_lines).to_jsonl(allow_unreadable=True).encode()
                sha256 = hashlib.sha256(data).hexdigest()
                public(self.file(self.config.index_path(name=name, sha256=sha256)), data)
                if (old := entries.get(name)) is None or old.sha256 != sha256:
                    entries[name] = CollectionEntry(updated, sha256)
        listed = Collections(entries).to_json().encode()
        _write_changed(self.listing_file, listed)

        if plugin_index is not None and extractions is not None and self.config.plugins is not None:
            changed = {name: plugins.plugin_lines(name, found) for name, found in extractions.items()}
            public(self.file(self.config.plugins), plugins.merge(plugin_index, changed))
        if public_listing and self.config.collections is not None:
            public(self.file(self.config.collections), listed)

        for line in removed:
            _remove(self.artifact_file(line))
        for name, collection_lines in lines.items():
            if not collection_lines:
                _remove(self.state_file(name))
                _remove(self.extractions_file(name))
                # One found by sha256 stays, for clients with an older listing, until the caller deletes it.
                if not self.config.hashed:
                    _remove(self.file(self.config.index_path(name=name)))
        return written


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
