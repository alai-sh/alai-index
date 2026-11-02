# Copyright: (c) 2026, Jordan Borean (@jborean93) <jborean93@gmail.com>
# MIT License (see LICENSE or https://opensource.org/licenses/MIT)

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from collection_data import build_collection, module

from alai_index import _index as index


def _lines(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_bytes().splitlines()]


def _listing(root: Path) -> dict[str, dict[str, dict[str, str]]]:
    return json.loads((root / "collections.json").read_bytes())["namespaces"]


@pytest.fixture
def root(tmp_path: Path) -> Path:
    """An index with ns.coll 1.0.0 and 2.0.0, and ns.dep 1.0.0, which needs ns.coll >=2.0.0."""
    root = tmp_path / "index"
    index.configure(root)
    index.Index(root).add(
        [
            build_collection(tmp_path / "a.tar.gz", modules={"old": module("Old")}),
            build_collection(tmp_path / "b.tar.gz", version="2.0.0", modules={"new": module("New")}),
            build_collection(tmp_path / "dep.tar.gz", "ns.dep", dependencies={"ns.coll": ">=2.0.0"}),
        ]
    )
    return root


def test_set_one_version(root: Path) -> None:
    assert index.Index(root).set(["ns.coll:1.0.0"], yanked=True) == [index.Changed("ns.coll", "1.0.0", True)]

    path = root / "index" / "ns" / "coll.jsonl"
    assert [(line["vers"], line["yanked"]) for line in _lines(path)] == [("1.0.0", True), ("2.0.0", False)]
    assert _listing(root)["ns"]["coll"]["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()


def test_set_every_version(root: Path) -> None:
    index.Index(root).set(["ns.coll"], deprecated=True)

    assert [line.get("deprecated") for line in _lines(root / "index" / "ns" / "coll.jsonl")] == [True, True]


def test_set_unchanged_writes_nothing(root: Path) -> None:
    before = (root / "collections.json").read_bytes()

    assert index.Index(root).set(["ns.coll:2.0.0"], yanked=False) == [index.Changed("ns.coll", "2.0.0", False)]
    assert (root / "collections.json").read_bytes() == before


def test_set_back(root: Path) -> None:
    index.Index(root).set(["ns.coll"], yanked=True, deprecated=True)
    index.Index(root).set(["ns.coll"], yanked=False, deprecated=False)

    assert [(line["yanked"], "deprecated" in line) for line in _lines(root / "index" / "ns" / "coll.jsonl")] == [
        (False, False),
        (False, False),
    ]


def test_set_keeps_plugins(root: Path) -> None:
    before = (root / "plugins.jsonl").read_bytes()
    index.Index(root).set(["ns.coll:2.0.0"], yanked=True)

    assert (root / "plugins.jsonl").read_bytes() == before


def test_set_needs_a_change(root: Path) -> None:
    with pytest.raises(index.Error, match="nothing to change"):
        index.Index(root).set(["ns.coll"])


@pytest.mark.parametrize(
    ("target", "match"),
    [
        ("ns.missing", r"ns\.missing isn't in the index"),
        ("ns.coll:3.0.0", r"ns\.coll 3\.0\.0 isn't in the index"),
        ("NS.coll", "lowercase"),
        ("ns.coll:not-a-version", "'ns.coll:not-a-version'"),
    ],
)
def test_bad_target(root: Path, target: str, match: str) -> None:
    with pytest.raises(index.Error, match=match):
        index.Index(root).set(["ns.coll:1.0.0", target], yanked=True)
    assert not any(line["yanked"] for line in _lines(root / "index" / "ns" / "coll.jsonl"))


def test_set_keeps_unreadable_requirements(root: Path) -> None:
    # As a mirror publishes them, verbatim.
    path = root / "index" / "ns" / "dep.jsonl"
    path.write_bytes(path.read_bytes().replace(b'">=2.0.0"', b'"^2.0.0"'))
    index.Index(root).set(["ns.dep"], deprecated=True)

    assert _lines(path)[0]["deps"] == [{"name": "ns.coll", "req": "^2.0.0"}]


def test_remove_version(root: Path) -> None:
    artifact = _lines(root / "index" / "ns" / "coll.jsonl")[0]["sha256"]
    assert isinstance(artifact, str)

    assert index.Index(root).remove(["ns.coll:1.0.0"]) == [index.Changed("ns.coll", "1.0.0", True)]

    assert [line["vers"] for line in _lines(root / "index" / "ns" / "coll.jsonl")] == ["2.0.0"]
    assert not (root / "dl" / artifact[:2] / f"{artifact}.tar.gz").exists()
    assert [line["version"] for line in _lines(root / "private" / "cache" / "plugins" / "ns" / "coll.jsonl")] == [
        "2.0.0"
    ]
    assert [(p["name"], p["since"]) for p in _lines(root / "plugins.jsonl")] == [("new", "2.0.0")]


def test_remove_collection(root: Path) -> None:
    index.Index(root).remove(["ns.dep"])

    assert set(_listing(root)["ns"]) == {"coll"}
    assert not (root / "index" / "ns" / "dep.jsonl").exists()
    assert not (root / "private" / "cache" / "plugins" / "ns" / "dep.jsonl").exists()
    assert len(list((root / "dl").rglob("*.tar.gz"))) == 2


def test_remove_refused_for_dependents(root: Path) -> None:
    before = (root / "collections.json").read_bytes()

    with pytest.raises(
        index.Error, match=r"Yank these first, or remove them too\. ns\.dep 1\.0\.0 needs ns\.coll >=2\.0\.0"
    ):
        index.Index(root).remove(["ns.coll:2.0.0"])
    assert (root / "collections.json").read_bytes() == before
    assert len(list((root / "dl").rglob("*.tar.gz"))) == 3


def test_remove_after_yanking_dependents(root: Path) -> None:
    index.Index(root).set(["ns.dep"], yanked=True)

    assert index.Index(root).remove(["ns.coll:2.0.0"]) == [index.Changed("ns.coll", "2.0.0", True)]


def test_remove_with_dependents(root: Path) -> None:
    index.Index(root).remove(["ns.coll", "ns.dep"])

    assert _listing(root) == {}


def test_remove_without_listing(tmp_path: Path) -> None:
    root = tmp_path / "index"
    index.configure(root, listing=False)
    index.Index(root).add(
        [
            build_collection(tmp_path / "a.tar.gz"),
            build_collection(tmp_path / "dep.tar.gz", "ns.dep", dependencies={"ns.coll": "*"}),
        ]
    )

    with pytest.raises(index.Error, match=r"ns\.dep 1\.0\.0 needs ns\.coll \*"):
        index.Index(root).remove(["ns.coll"])
    index.Index(root).remove(["ns.dep"])
    index.Index(root).remove(["ns.coll"])
    assert not list((root / "index").rglob("*.jsonl"))


def test_remove_hashed_keeps_the_old_file(tmp_path: Path) -> None:
    root = tmp_path / "index"
    index.configure(root, index="index/{sha256-prefix}/{sha256-checksum}.jsonl")
    index.Index(root).add([build_collection(tmp_path / "a.tar.gz")])
    index.Index(root).remove(["ns.coll"])

    assert _listing(root) == {}
    assert len(list((root / "index").rglob("*.jsonl"))) == 1


def test_remove_ignores_what_was_already_unmet(root: Path) -> None:
    path = root / "index" / "ns" / "dep.jsonl"
    path.write_bytes(path.read_bytes().replace(b'">=2.0.0"', b'">=9.0.0"'))

    assert index.Index(root).remove(["ns.coll:2.0.0"]) == [index.Changed("ns.coll", "2.0.0", True)]
