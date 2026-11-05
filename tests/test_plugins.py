# Copyright: (c) 2026, Jordan Borean (@jborean93) <jborean93@gmail.com>
# MIT License (see LICENSE or https://opensource.org/licenses/MIT)

from __future__ import annotations

import collections.abc as _c
import json

from alai_index import _plugins as plugins


def _extraction(*names: str, routing: dict[str, dict[str, object]] | None = None) -> dict[str, object]:
    return {
        "v": plugins.CURRENT,
        "plugins": [{"type": "module", "name": n, "short_description": f"{n} desc"} for n in names],
        "routing": routing or {},
    }


def _lines(extractions: _c.Mapping[str, _c.Mapping[str, object]]) -> list[dict[str, object]]:
    return [json.loads(p.to_json()) for p in plugins.plugin_lines("ns.coll", extractions)]


def test_since_and_until() -> None:
    lines = _lines({"1.0.0": _extraction("a", "b"), "2.0.0": _extraction("a"), "1.5.0": _extraction("a", "b")})

    assert lines == [
        {
            "v": 1,
            "type": "module",
            "name": "a",
            "collection": "ns.coll",
            "since": "1.0.0",
            "short_description": "a desc",
        },
        {
            "v": 1,
            "type": "module",
            "name": "b",
            "collection": "ns.coll",
            "since": "1.0.0",
            "until": "2.0.0",
            "short_description": "b desc",
        },
    ]


def test_prereleases_ignored_unless_only_they_have_it() -> None:
    lines = _lines({"1.0.0-rc.1": _extraction("a", "b"), "1.0.0": _extraction("a"), "2.0.0-rc.1": _extraction("a")})

    assert [(p["name"], p["since"], p.get("until")) for p in lines] == [
        ("a", "1.0.0", None),
        ("b", "1.0.0-rc.1", "1.0.0"),
    ]


def test_routing_from_latest_and_without_a_plugin() -> None:
    lines = _lines(
        {
            "1.0.0": _extraction("a", routing={"module:a": {"redirect": "x.y.z"}}),
            "2.0.0": _extraction("a", routing={"module:gone": {"tombstone": {"removal_version": "2.0.0"}}}),
        }
    )

    assert [(p["name"], p.get("routing")) for p in lines] == [
        ("a", None),
        ("gone", {"tombstone": {"removal_version": "2.0.0"}}),
    ]


def test_extraction_without_routing() -> None:
    assert [
        p["name"] for p in _lines({"1.0.0": {"v": plugins.CURRENT, "plugins": [{"type": "module", "name": "a"}]}})
    ] == ["a"]


def test_failed_extraction_has_no_plugins() -> None:
    failed = {"v": plugins.CURRENT, "error": "ValueError: bad"}

    assert _lines({"1.0.0": failed}) == []
    # Its plugins aren't known, so it doesn't end any either.
    assert [p.get("until") for p in _lines({"1.0.0": _extraction("a"), "2.0.0": failed})] == [None]


def test_merge_replaces_only_the_collections_given() -> None:
    existing = (
        b'{"v":1,"type":"module","name":"z","collection":"other.coll","unknown":1}\n'
        b'{"v":1,"type":"module","name":"old","collection":"ns.coll"}\n'
    )
    new = plugins.plugin_lines("ns.coll", {"1.0.0": _extraction("a")})

    assert plugins.merge(existing, {"ns.coll": new}, {"ns.coll", "other.coll"}).splitlines() == [
        b'{"v":1,"type":"module","name":"a","collection":"ns.coll","since":"1.0.0","short_description":"a desc"}',
        b'{"v":1,"type":"module","name":"z","collection":"other.coll","unknown":1}',
    ]
    # A collection the index doesn't have loses its lines, and so does a line that isn't a JSON object.
    assert plugins.merge(existing + b"not json\n[]\n", {"ns.coll": new}, {"ns.coll"}).splitlines() == [
        b'{"v":1,"type":"module","name":"a","collection":"ns.coll","since":"1.0.0","short_description":"a desc"}',
    ]
