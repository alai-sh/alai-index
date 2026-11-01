# Copyright: (c) 2026, Jordan Borean (@jborean93) <jborean93@gmail.com>
# MIT License (see LICENSE or https://opensource.org/licenses/MIT)

"""The plugin index: each version's extraction, kept in a private file per collection, and how a collection's plugin
lines are worked out from every version's.

Each collection's private file, ``private/plugins/<namespace>/<name>.jsonl``, records what each version's artifact holds,
so the plugin index can be worked out again without the artifacts. Each line is one version's::

    {"v": 1, "version": "1.0.0", "revision": 1, "plugins": [{"type": "module", "name": "x"}], "routing": {}}

or ``"error"`` in place of ``plugins`` and ``routing`` when extraction failed. There's a line for every version in the
collection's index file, in the same order, and for no other.
"""

from __future__ import annotations

import collections.abc as _c
import json
import os
import typing as _t

from alai_core import extract
from alai_core.plugins import PluginEntry
from alai_core.semver import Version

REVISION = 1
"""The extraction's revision, kept on each private line. Bump it when the same artifact would extract differently, so
older lines can be told apart and redone."""


def extraction(path: str | os.PathLike[str], version: str, /) -> dict[str, object]:
    """The private line for ``version`` of the collection extracted at ``path``. Plugin names, short descriptions and
    routing, or the error if extraction failed, which costs the plugin index that version's plugins but never the
    version itself."""
    line: dict[str, object] = {"v": 1, "version": version, "revision": REVISION}
    try:
        result = extract.extract(path)
    except Exception as e:  # Untrusted input, so any failure is data, not a crash.
        return {**line, "error": f"{type(e).__name__}: {e}"}
    plugins: list[dict[str, str]] = []
    for p in result.plugins:
        entry = {"type": p.type, "name": p.name}
        if p.short_description:
            entry["short_description"] = p.short_description
        plugins.append(entry)
    return {**line, "plugins": plugins, "routing": {f"{r.type}:{r.name}": r.entry for r in result.routing}}


def read_private(data: bytes, path: str, /) -> dict[str, dict[str, object]]:
    """A collection's private file, each line by version.

    Raises:
        ValueError: A line can't be read, or needs a newer alai-index.
    """
    lines: dict[str, dict[str, object]] = {}
    for n, raw in enumerate(data.splitlines(), 1):
        if not raw.strip():
            continue
        try:
            line: object = json.loads(raw)
        except ValueError as e:
            raise ValueError(f"{path}: line {n}: {e}") from None
        line = _t.cast("dict[str, object]", line) if isinstance(line, dict) else {}
        if line.get("v") != 1 or not isinstance(version := line.get("version"), str):
            raise ValueError(f"{path}: line {n} isn't a version's extraction this alai-index reads")
        lines[version] = line
    return lines


def private_jsonl(lines: _c.Iterable[_c.Mapping[str, object]], /) -> bytes:
    """A collection's private file, from its lines in index file order."""
    return "".join(json.dumps(line, separators=(",", ":")) + "\n" for line in lines).encode()


def plugin_lines(collection: str, extractions: _c.Mapping[str, _c.Mapping[str, object]], /) -> list[PluginEntry]:
    """One collection's plugin lines, from each version's extraction by version. A failed extraction counts as a
    version without plugins.

    Pre-releases are ignored unless only pre-releases have the plugin. ``since`` is the lowest version that has it,
    ``until`` the lowest version above every one that has it, and routing comes from the latest version.
    """
    good = {Version(v): e for v, e in extractions.items() if "error" not in e}
    if not good:
        return []
    versions = sorted(good)
    stable = [v for v in versions if not v.is_prerelease]
    latest = stable[-1] if stable else versions[-1]

    present: dict[tuple[str, str], list[Version]] = {}
    descs: dict[tuple[str, str], str] = {}
    for v in versions:  # Ascending, so later versions' descriptions win.
        for p in _t.cast("list[dict[str, str]]", good[v]["plugins"]):
            key = (p["type"], p["name"])
            present.setdefault(key, []).append(v)
            if desc := p.get("short_description"):
                descs[key] = desc
    routing = _t.cast("dict[str, dict[str, object]]", good[latest]["routing"])

    pool = stable or versions
    result: list[PluginEntry] = []
    for key, containing in present.items():
        since = min([v for v in containing if not v.is_prerelease] or containing)
        highest = max(containing)
        later = [v for v in pool if v > highest]
        result.append(
            PluginEntry(
                key[0],
                key[1],
                collection,
                since=since.text,
                until=min(later).text if later else None,
                short_description=descs.get(key),
                routing=routing.get(f"{key[0]}:{key[1]}"),
            )
        )
    for rkey, r in routing.items():
        ptype, _, name = rkey.partition(":")
        if (ptype, name) not in present:
            result.append(PluginEntry(ptype, name, collection, routing=r))
    return result


def merge(existing: bytes | None, lines: _c.Mapping[str, _c.Sequence[PluginEntry]], /) -> bytes:
    """``plugins.jsonl`` with each collection in ``lines`` given those lines in place of the ones it had, sorted by type,
    name and collection. Every other line is kept as written, unknown fields included."""
    kept: list[tuple[tuple[str, str, str], str]] = []
    for raw in (existing or b"").splitlines():
        if not raw.strip():
            continue
        data = json.loads(raw)
        if data.get("collection") not in lines:
            kept.append(((data.get("type", ""), data.get("name", ""), data.get("collection", "")), raw.decode()))
    for entries in lines.values():
        kept.extend(((p.type, p.name, p.collection), p.to_json()) for p in entries)
    kept.sort(key=lambda k: k[0])
    return "".join(f"{text}\n" for _, text in kept).encode()
