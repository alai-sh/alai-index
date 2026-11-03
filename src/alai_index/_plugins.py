# Copyright: (c) 2026, Jordan Borean (@jborean93) <jborean93@gmail.com>
# MIT License (see LICENSE or https://opensource.org/licenses/MIT)

"""The plugin index: each version's plugin extraction, as kept in the plugin cache, and a collection's plugin lines worked
out from every version's."""

from __future__ import annotations

import collections.abc as _c
import json
import os
import typing as _t

from alai_core import extract
from alai_core.plugins import PluginEntry
from alai_core.semver import Version

CURRENT = 1
"""The number each extraction in the plugin cache is written with. Bump it when the same artifact would extract
differently, or an extraction is written another way, so older ones are redone."""

READABLE = frozenset({1})
"""The numbers this alai-index can still read. One that's older but readable is used until it's redone, and one that
isn't is taken as missing."""


class NewerCache(ValueError):
    """The plugin cache has an extraction written by a newer alai-index."""


def extraction(path: str | os.PathLike[str], version: str, /) -> dict[str, object]:
    """The extraction of ``version`` of the collection extracted at ``path``. Plugin names, short descriptions and
    routing, or the error if extraction failed, which costs the plugin index that version's plugins but never the
    version itself."""
    try:
        result = extract.extract(path)
    except Exception as e:  # Untrusted input, so any failure is data, not a crash.
        return failure(version, e)
    plugins: list[dict[str, str]] = []
    for p in result.plugins:
        entry = {"type": p.type, "name": p.name}
        if p.short_description:
            entry["short_description"] = p.short_description
        plugins.append(entry)
    return {
        "v": CURRENT,
        "version": version,
        "plugins": plugins,
        "routing": {f"{r.type}:{r.name}": r.entry for r in result.routing},
    }


def failure(version: str, error: Exception, /) -> dict[str, object]:
    """The extraction of a version that failed."""
    return {"v": CURRENT, "version": version, "error": f"{type(error).__name__}: {error}"}


def current(line: _c.Mapping[str, object], /) -> bool:
    """Whether an extraction was written by this alai-index's extractor, so needn't be redone. A failed one counts, as
    the same extractor would only fail again."""
    return line.get("v") == CURRENT


def read_extractions(data: bytes, path: str, /) -> dict[str, dict[str, object]]:
    """A collection's file in the plugin cache, each extraction this alai-index can read by version. A line it can't read
    is left out, so the version's extraction is taken as missing, as the cache can always be made again.

    Raises:
        NewerCache: A line was written by a newer alai-index.
    """
    lines: dict[str, dict[str, object]] = {}
    for n, raw in enumerate(data.splitlines(), 1):
        try:
            line: object = json.loads(raw) if raw.strip() else None
        except ValueError:
            continue
        if not isinstance(line, dict):
            continue
        line = _t.cast("dict[str, object]", line)
        number, version = line.get("v"), line.get("version")
        if isinstance(number, int) and number > CURRENT:
            raise NewerCache(f"{path}: line {n} was written by a newer alai-index, which this one can't use")
        if number in READABLE and isinstance(version, str) and ("error" in line or "plugins" in line):
            lines[version] = line
    return lines


def extractions_jsonl(lines: _c.Iterable[_c.Mapping[str, object]], /) -> bytes:
    """A collection's extractions file, from its extractions in index file order."""
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


def merge(
    existing: bytes | None, lines: _c.Mapping[str, _c.Sequence[PluginEntry]], listed: _c.Collection[str], /
) -> bytes:
    """``plugins.jsonl`` with each collection in ``lines`` given those lines in place of the ones it had, sorted by type,
    name and collection. Every other line of a collection in ``listed`` is kept as written, unknown fields included.
    A line for a collection the index doesn't have is dropped, so the plugin index only describes the index."""
    kept: list[tuple[tuple[str, str, str], str]] = []
    for raw in (existing or b"").splitlines():
        if not raw.strip():
            continue
        data = json.loads(raw)
        if data.get("collection") in listed and data.get("collection") not in lines:
            kept.append(((data.get("type", ""), data.get("name", ""), data.get("collection", "")), raw.decode()))
    for entries in lines.values():
        kept.extend(((p.type, p.name, p.collection), p.to_json()) for p in entries)
    kept.sort(key=lambda k: k[0])
    return "".join(f"{text}\n" for _, text in kept).encode()
