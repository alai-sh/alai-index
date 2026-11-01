# Copyright: (c) 2026, Jordan Borean (@jborean93) <jborean93@gmail.com>
# MIT License (see LICENSE or https://opensource.org/licenses/MIT)

"""An index as a tree of files on disk, its ``config.json`` made and collection artifacts added to it.

The tree holds what the index serves, at the paths ``config.json`` gives. An artifact whose ``dl`` URL is on another
host is kept under ``dl/`` at that URL's path. ``private/`` holds alai-index's state, such as each collection's
extractions (``_plugins``), which isn't part of the index format and is never published. Publishing the rest of the tree
is the caller's, everything but ``plugins.jsonl`` and ``collections.json`` first, ``collections.json`` last.
"""

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
"""The tree's directory of alai-index's own state, which no template may lead into."""


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


def now() -> str:
    """The time as the index writes it, RFC 3339 in UTC to the second."""
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def configure(
    root: str | os.PathLike[str],
    *,
    index: str | None = None,
    dl: str | None = None,
    listing: bool | None = None,
    plugins: bool | None = None,
) -> Config:
    """Write ``config.json``, with an empty ``collections.json`` and ``plugins.jsonl`` if it names them.

    Each option left as None keeps what ``config.json`` already says, or else takes the default: files by name, the
    artifacts in the tree, a listing and a plugin index. Once the index has collections, ``config.json`` can't change,
    as their files would be left where the old one put them.

    Raises:
        Error: The config isn't valid, or the index has collections and it would change.
    """
    root = os.fspath(root)
    path = os.path.join(root, "config.json")
    tree = Index(root) if os.path.exists(path) else None
    current = tree.config if tree else None
    if listing is None:
        listing = current is None or current.collections is not None
    if plugins is None:
        plugins = current is None or current.plugins is not None
    try:
        config = Config(
            index=index or (current.index if current else DEFAULT_INDEX),
            dl=dl or (current.dl if current else DEFAULT_DL),
            collections=COLLECTIONS if listing else None,
            plugins=PLUGINS if plugins else None,
            moved_to=current.moved_to if current else None,
        )
    except ValueError as e:
        raise Error(str(e)) from None
    sample = config.index_path(name="ns.coll", sha256="0" * 64)
    if _private_ref(sample) or _private_ref(
        config.artifact_path(name="ns.coll", version=Version("1.0.0"), sha256="0" * 64)
    ):
        raise Error(f"index files and artifacts can't be kept under {PRIVATE}/, which holds alai-index's own state")
    if config == current:
        return config
    if tree is not None and _has_collections(tree):
        raise Error(f"{root} already has collections, which changing config.json would leave behind")

    _write(path, config.to_json().encode())
    tree = Index(root)
    if config.collections and not os.path.exists(tree.file(config.collections)):
        _write(tree.file(config.collections), Collections({}).to_json().encode())
    if config.plugins and not os.path.exists(tree.file(config.plugins)):
        _write(tree.file(config.plugins), b"")
    return config


def _private_ref(ref: str) -> bool:
    parts = urllib.parse.urlsplit(ref)
    return (
        not (parts.scheme or parts.netloc)
        and posixpath.normpath(urllib.parse.unquote(parts.path)).split("/")[0] == PRIVATE
    )


def _has_collections(tree: Index) -> bool:
    if tree.config.collections is not None:
        data = _read(tree.file(tree.config.collections))
        return data is not None and len(Collections.parse(data)) > 0
    return next(tree._unlisted(), None) is not None


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


def _with(line: CollectionVersion, *, yanked: bool | None, deprecated: bool | None) -> CollectionVersion:
    """The line with these fields changed, kept as read otherwise, even a requirement it can't read."""
    data = json.loads(line.to_json(allow_unreadable=True))
    if yanked is not None:
        data["yanked"] = yanked
    if deprecated is not None:
        data["deprecated"] = deprecated
    return CollectionIndex.parse(json.dumps(data).encode(), line.name).lines[0]


class _Staged(_t.NamedTuple):
    line: CollectionVersion
    data: bytes
    extraction: dict[str, object] | None
    """Its private line, or None for an index without a plugin index."""


class Index:
    """The index whose tree is at ``root``.

    Raises:
        Error: ``config.json`` is missing or isn't valid.
    """

    def __init__(self, root: str | os.PathLike[str]) -> None:
        self.root = os.fspath(root)
        path = os.path.join(self.root, "config.json")
        data = _read(path)
        if data is None:
            raise Error(f"{path} doesn't exist. 'alai-index config' makes one")
        try:
            self.config = Config.parse(data)
        except ValueError as e:
            raise Error(f"{path}: {e}") from None

    def file(self, ref: str) -> str:
        """Where a file ``config.json`` names by a reference relative to the index root is in the tree.

        Raises:
            Error: The reference leads outside the tree or isn't a plain path.
        """
        parts = urllib.parse.urlsplit(ref)
        if parts.scheme or parts.netloc or parts.query or parts.fragment:
            raise Error(f"{ref!r} isn't a path in the index")
        return self._tree_path(urllib.parse.unquote(parts.path), ref)

    def artifact_file(self, line: CollectionVersion) -> str:
        """Where a version's artifact is in the tree, from the ``dl`` template, under ``dl/`` if that's on another
        host."""
        url = self.config.artifact_path(name=line.name, version=line.version, sha256=line.sha256)
        parts = urllib.parse.urlsplit(url)
        if not (parts.scheme or parts.netloc):
            return self.file(url)
        return self._tree_path("dl/" + urllib.parse.unquote(parts.path).lstrip("/"), url)

    def private_file(self, name: str) -> str:
        """Where a collection's private file of extractions is in the tree."""
        namespace, short = name.split(".", 1)
        return os.path.join(self.root, PRIVATE, "plugins", namespace, f"{short}.jsonl")

    def _tree_path(self, rel: str, ref: str) -> str:
        norm = posixpath.normpath(rel)
        parts = norm.split("/")
        # A drive or a backslash would lead elsewhere on Windows.
        if rel.startswith("/") or ".." in parts or any(":" in p or "\\" in p for p in parts):
            raise Error(f"{ref!r} leads outside the index")
        return os.path.join(self.root, *parts)

    def add(self, tarballs: _c.Sequence[str | os.PathLike[str]], /, *, published: str | None = None) -> list[Added]:
        """Add collection artifacts, each version's line taken from its own ``MANIFEST.json`` and ``meta/runtime.yml``.

        Every tarball is checked before anything is written. A version the index has with the same artifact is
        skipped, and one it has with another is refused. Each dependency must be met by a version that isn't yanked,
        in the index or among ``tarballs``. Then the artifacts and private files are written, then the index files,
        ``plugins.jsonl`` and ``collections.json``.

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
        plugin_index, private = self._plugin_state(before, touched)
        if private is not None:
            for s in new:
                private[s.line.name][s.line.version.text] = _t.cast("dict[str, object]", s.extraction)
        self._commit(
            touched, listing, artifacts=[(s.line, s.data) for s in new], private=private, plugin_index=plugin_index
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
        lines: dict[str, list[CollectionVersion]] = {}
        results: list[Changed] = []
        touched: set[str] = set()
        for name, i in self._select(targets, listing, lines):
            old = lines[name][i]
            lines[name][i] = _with(old, yanked=yanked, deprecated=deprecated)
            changed = lines[name][i] != old
            results.append(Changed(name, old.version.text, changed))
            if changed:
                touched.add(name)
        if touched:
            # Plugin lines count yanked and deprecated versions too, so they stay as they are.
            self._commit({name: lines[name] for name in touched}, listing)
        return results

    def remove(self, targets: _c.Sequence[str], /) -> list[Changed]:
        """Take versions down: their lines, artifacts and private lines are deleted, and with a collection's last, its
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
        lines: dict[str, list[CollectionVersion]] = {}
        selected = self._select(targets, listing, lines)
        before = {name: list(have) for name, have in lines.items()}
        gone = [before[name][i] for name, i in selected]
        for name in lines:
            lines[name] = [line for line in lines[name] if line not in gone]

        unmet: list[str] = []
        for _, others in self._everything(listing, lines):
            for line in others:
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

        plugin_index, private = self._plugin_state(before, lines)
        if private is not None:
            for name, extractions in private.items():
                private[name] = {line.version.text: extractions[line.version.text] for line in lines[name]}
        self._commit(lines, listing, private=private, plugin_index=plugin_index, removed=gone)
        return [Changed(line.name, line.version.text, True) for line in gone]

    def _select(
        self, targets: _c.Sequence[str], listing: Collections | None, lines: dict[str, list[CollectionVersion]]
    ) -> list[tuple[str, int]]:
        """Each version the targets name, as its collection and position in ``lines``, which gets each one's lines."""
        selected: dict[tuple[str, int], None] = {}
        for text in targets:
            name, version = _target(text)
            if name not in lines:
                lines[name] = self._lines(name, listing)
            matches = [i for i, line in enumerate(lines[name]) if version in (None, line.version.text)]
            if not matches:
                raise Error(f"{name} {version} isn't in the index" if version else f"{name} isn't in the index")
            selected.update(dict.fromkeys((name, i) for i in matches))
        return list(selected)

    def _everything(
        self, listing: Collections | None, lines: dict[str, list[CollectionVersion]]
    ) -> _c.Iterator[tuple[str, list[CollectionVersion]]]:
        """Every collection in the index and its lines, taking those in ``lines`` from there."""
        names = list(listing) if listing is not None else [name for name, _ in self._unlisted()]
        for name in names:
            yield name, lines[name] if name in lines else self._lines(name, listing)

    def _unlisted(self) -> _c.Iterator[tuple[str, str]]:
        """Each collection with an index file, and that file, found without a listing, so by name, below the index
        template's fixed directory."""
        top = self.file(self.config.index.split("{", 1)[0] or ".")
        for d, dirs, files in os.walk(top):
            dirs[:] = [x for x in dirs if os.path.join(d, x) != os.path.join(self.root, PRIVATE)]
            for f in files:
                path = os.path.join(d, f)
                first = (_read(path) or b"").split(b"\n", 1)[0]
                try:
                    name = json.loads(first)["name"]
                    found = self.file(self.config.index_path(name=name))
                except (ValueError, KeyError, TypeError, Error):
                    continue
                if found == path:
                    yield name, path

    def _plugin_state(
        self, before: dict[str, list[CollectionVersion]], touched: _c.Iterable[str]
    ) -> tuple[bytes | None, dict[str, dict[str, dict[str, object]]] | None]:
        """``plugins.jsonl`` and the touched collections' private lines, or None for each without a plugin index."""
        if self.config.plugins is None:
            return None, None
        path = self.file(self.config.plugins)
        plugin_index = _read(path)
        if plugin_index is None:
            raise Error(f"{path} is missing, which config.json says the index has")
        return plugin_index, {name: self._private(name, before[name]) for name in touched}

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

    def _listing(self) -> Collections | None:
        if self.config.collections is None:
            return None
        path = self.file(self.config.collections)
        data = _read(path)
        if data is None:
            raise Error(f"{path} is missing, which config.json says the index has")
        try:
            return Collections.parse(data)
        except ValueError as e:
            raise Error(f"{path}: {e}") from None

    def _lines(self, name: str, listing: Collections | None) -> list[CollectionVersion]:
        """A collection's lines, or none if the index doesn't have it."""
        entry = listing.get(name) if listing is not None else None
        if self.config.hashed:
            if entry is None:
                return []
            if entry.sha256 is None:
                raise Error(f"{name} has no sha256 in collections.json, which finding its index file needs")
        ref = self.config.index_path(name=name, sha256=entry.sha256 if entry else None)
        path = self.file(ref)
        data = _read(path)
        if data is None:
            # Never taken as empty, which would publish the collection with only the versions added now.
            if entry is not None:
                raise Error(f"{path} is missing, which collections.json lists for {name}")
            return []
        parsed = CollectionIndex.parse(data, name)
        if parsed.invalid or parsed.newer:
            problems = [f"line {n}: {why}" for n, why in parsed.invalid]
            if parsed.newer:
                problems.append(f"{parsed.newer} lines need a newer alai-index")
            raise Error(f"{path} has lines that rewriting it would lose: {'; '.join(problems)}")
        return list(parsed.lines)

    def _private(self, name: str, have: list[CollectionVersion]) -> dict[str, dict[str, object]]:
        """A collection's private lines for the versions it has, by version, dropping any for versions it doesn't."""
        path = self.private_file(name)
        data = _read(path)
        if data is None:
            if have:
                raise Error(f"{path} is missing, which {name}'s plugin lines need")
            return {}
        try:
            parsed = plugins.read_private(data, path)
        except ValueError as e:
            raise Error(str(e)) from None
        if missing := [line.version.text for line in have if line.version.text not in parsed]:
            raise Error(f"{path} has no extraction for {name} {', '.join(missing)}")
        return {line.version.text: parsed[line.version.text] for line in have}

    def _commit(
        self,
        lines: dict[str, list[CollectionVersion]],
        listing: Collections | None,
        *,
        artifacts: _c.Sequence[tuple[CollectionVersion, bytes]] = (),
        private: dict[str, dict[str, dict[str, object]]] | None = None,
        plugin_index: bytes | None = None,
        removed: _c.Sequence[CollectionVersion] = (),
    ) -> None:
        """Write a change to the collections in ``lines``, given their new lines, in the order that never leaves the
        index naming a file that isn't there. Artifacts and private files, then index files, ``plugins.jsonl`` and
        ``collections.json``, and last deleting what ``removed`` and collections left with no lines leave behind."""
        for line, data in artifacts:
            _write(self.artifact_file(line), data)
        for name, extractions in (private or {}).items():
            if lines[name]:
                jsonl = plugins.private_jsonl(extractions[line.version.text] for line in lines[name])
                _write(self.private_file(name), jsonl)

        updated = now()
        entries: dict[str, CollectionEntry] = {}
        for name, collection_lines in lines.items():
            if collection_lines:
                # Lines added are checked before this, so only lines already there can have a problem, kept as they are.
                data = CollectionIndex(name, collection_lines).to_jsonl(allow_unreadable=True).encode()
                sha256 = hashlib.sha256(data).hexdigest()
                _write(self.file(self.config.index_path(name=name, sha256=sha256)), data)
                entries[name] = CollectionEntry(updated, sha256)

        if plugin_index is not None and private is not None and self.config.plugins is not None:
            changed = {name: plugins.plugin_lines(name, extractions) for name, extractions in private.items()}
            _write(self.file(self.config.plugins), plugins.merge(plugin_index, changed))

        if listing is not None and self.config.collections is not None:
            merged = {name: e for name in listing if (e := listing.get(name)) is not None and lines.get(name) != []}
            merged.update(entries)
            _write(self.file(self.config.collections), Collections(merged).to_json().encode())

        for line in removed:
            _remove(self.artifact_file(line))
        for name, collection_lines in lines.items():
            if not collection_lines:
                _remove(self.private_file(name))
                # One found by sha256 stays, for clients with an older listing, until the caller deletes it.
                if not self.config.hashed:
                    _remove(self.file(self.config.index_path(name=name)))


def _read(path: str) -> bytes | None:
    try:
        with open(path, "rb") as f:
            return f.read()
    except FileNotFoundError:
        return None


def _remove(path: str) -> None:
    with contextlib.suppress(FileNotFoundError):
        os.unlink(path)


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
