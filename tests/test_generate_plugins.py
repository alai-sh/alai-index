# Copyright: (c) 2026, Jordan Borean (@jborean93) <jborean93@gmail.com>
# MIT License (see LICENSE or https://opensource.org/licenses/MIT)

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from collection_data import build_collection, module

from alai_index import _index as index
from alai_index import _plugins as plugins


def _lines(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_bytes().splitlines()]


def _public(root: Path) -> dict[Path, bytes]:
    """Every public file but the artifacts."""
    return {
        p: p.read_bytes()
        for p in root.rglob("*")
        if p.is_file() and "plugin-cache" not in p.parts and "dl" not in p.parts
    }


@pytest.fixture
def root(tmp_path: Path) -> Path:
    """An index with ns.coll 1.0.0, yanked, and 2.0.0, and ns.other 1.0.0, which needs ns.coll."""
    root = tmp_path / "index"
    index.configure(root)
    index.Index(root).add(
        [
            build_collection(tmp_path / "a.tar.gz", modules={"old": module("Old")}, requires_ansible=">=2.17"),
            build_collection(tmp_path / "b.tar.gz", version="2.0.0", modules={"new": module("New")}),
            build_collection(
                tmp_path / "other.tar.gz", "ns.other", dependencies={"ns.coll": ">=1.0"}, modules={"x": module("X")}
            ),
        ]
    )
    index.Index(root).set(["ns.coll:1.0.0"], yanked=True)
    return root


def _generated(root: Path, *args: object, **kwargs: object) -> list[str]:
    """The public files generate-plugins wrote."""
    tree = index.Index(root)
    tree.generate_plugins(*args, **kwargs)  # pyright: ignore[reportArgumentType]
    return tree.files["written"]["index"]


def _extractions(root: Path, name: str = "coll") -> Path:
    return root / "plugin-cache" / "ns" / f"{name}.jsonl"


def test_nothing_changed(root: Path) -> None:
    assert index.Index(root).generate_plugins(from_plugin_cache=True) == []
    assert index.Index(root).generate_plugins(["ns.coll"], from_plugin_cache=True) == []

    # By default every targeted version is extracted again, with the same result.
    tree = index.Index(root)
    assert len(tree.generate_plugins()) == 3
    assert tree.files["written"]["index"] == []


def test_every_collection(root: Path) -> None:
    before = _public(root)
    (root / "plugins.jsonl").write_bytes(b"")

    assert _generated(root, from_plugin_cache=True) == ["plugins.jsonl"]
    assert _public(root) == before


def test_one_collection_is_merged_in(root: Path) -> None:
    before = (root / "plugins.jsonl").read_bytes()
    (root / "plugins.jsonl").write_bytes(
        b"\n".join(line for line in before.splitlines() if b"ns.other" in line) + b"\n"
    )

    assert _generated(root, ["ns.coll"], from_plugin_cache=True) == ["plugins.jsonl"]
    assert sorted((root / "plugins.jsonl").read_bytes().splitlines()) == sorted(before.splitlines())


def test_restore_from_the_index_and_artifacts(root: Path) -> None:
    # As the README's restore has it: the plugin cache and plugins.jsonl are made again from the artifacts.
    before = {**_public(root), _extractions(root): _extractions(root).read_bytes()}
    shutil.rmtree(root / "plugin-cache")
    (root / "plugins.jsonl").unlink()

    changes = index.Index(root).generate_plugins(from_plugin_cache=True)
    assert sorted((c.name, c.version, c.result) for c in changes) == [
        ("ns.coll", "1.0.0", "extracted"),
        ("ns.coll", "2.0.0", "extracted"),
        ("ns.other", "1.0.0", "extracted"),
    ]
    assert {**_public(root), _extractions(root): _extractions(root).read_bytes()} == before


def test_lost_plugin_cache_without_artifacts(root: Path) -> None:
    plugin_index = (root / "plugins.jsonl").read_bytes()
    shutil.rmtree(root / "plugin-cache")
    for artifact in (root / "dl").glob("*/*"):
        artifact.unlink()

    # Each version is reported, and every collection keeps the plugin lines it has.
    tree = index.Index(root)
    assert tree.generate_plugins(from_plugin_cache=True) == []
    assert sorted((o["name"], o["version"]) for o in tree.files["outdated"]) == [
        ("ns.coll", "1.0.0"),
        ("ns.coll", "2.0.0"),
        ("ns.other", "1.0.0"),
    ]
    assert (root / "plugins.jsonl").read_bytes() == plugin_index


def test_lost_extractions(tmp_path: Path, root: Path) -> None:
    before = _extractions(root).read_bytes()
    plugin_index = (root / "plugins.jsonl").read_bytes()
    _extractions(root).unlink()

    assert index.Index(root).generate_plugins(["ns.coll"], from_plugin_cache=True) == [
        index.Change("ns.coll", "1.0.0", "extracted"),
        index.Change("ns.coll", "2.0.0", "extracted"),
    ]
    assert _extractions(root).read_bytes() == before
    assert (root / "plugins.jsonl").read_bytes() == plugin_index
    # The collection takes new versions again.
    index.Index(root).add([build_collection(tmp_path / "c.tar.gz", version="3.0.0")])


def test_one_version(root: Path) -> None:
    # Extracted again whatever the cache says.
    assert index.Index(root).generate_plugins(["ns.coll:2.0.0"]) == [index.Change("ns.coll", "2.0.0", "extracted")]


def test_one_unpacked_artifact_at_a_time(tmp_path: Path, root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # A collection can have thousands of versions, each unpacked, so each is deleted again before the next.
    extraction = plugins.extraction
    seen: list[int] = []

    def counting(path: str, version: str) -> dict[str, object]:
        # The staged copies of add's tarballs stay, but each unpacked collection is gone before the next.
        seen.append(sum((d / "collection").exists() for d in Path(path).parents[1].iterdir()))
        return extraction(path, version)

    monkeypatch.setattr(plugins, "extraction", counting)
    index.Index(root).generate_plugins()
    shutil.rmtree(root / "plugin-cache")
    index.Index(root).generate_plugins(from_plugin_cache=True)
    index.Index(root).add(
        [build_collection(tmp_path / f"{v}.tar.gz", "ns.new", v) for v in ("1.0.0", "2.0.0", "3.0.0")]
    )
    assert seen == [1] * 9


def test_extraction_still_fails(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(path: object) -> None:
        raise ValueError("boom")

    monkeypatch.setattr("alai_core.extract.extract", fail)

    tree = index.Index(root)
    assert tree.generate_plugins(["ns.other"]) == [
        index.Change("ns.other", "1.0.0", "extracted", {"error": "ValueError: boom"})
    ]
    assert tree.files["written"]["index"] == ["plugins.jsonl"]
    assert tree.warnings == [
        "extracting the plugins of ns.other 1.0.0 failed, so the plugin index has none of them. ValueError: boom"
    ]
    assert [p["name"] for p in _lines(root / "plugins.jsonl")] == ["new", "old"]


def test_the_rest_are_extracted_again_too(root: Path) -> None:
    _extractions(root).unlink()

    assert index.Index(root).generate_plugins(["ns.coll:2.0.0"]) == [
        index.Change("ns.coll", "2.0.0", "extracted"),
        index.Change("ns.coll", "1.0.0", "extracted"),
    ]
    assert [line["version"] for line in _lines(_extractions(root))] == ["1.0.0", "2.0.0"]


def test_missing_artifact(root: Path) -> None:
    for artifact in (root / "dl").glob("*/*"):
        artifact.unlink()

    with pytest.raises(index.Error, match=r"which reading ns\.coll 1\.0\.0's artifact needs"):
        index.Index(root).generate_plugins()


def test_artifact_isnt_the_one_named(root: Path) -> None:
    for artifact in (root / "dl").glob("*/*"):
        artifact.write_bytes(b"other bytes")

    with pytest.raises(index.Error, match="its sha256 differs"):
        index.Index(root).generate_plugins(["ns.coll"])


def test_no_plugin_index(tmp_path: Path) -> None:
    root = tmp_path / "index"
    index.configure(root, plugins=False)
    index.Index(root).add([build_collection(tmp_path / "a.tar.gz", modules={"a": module("A")})])

    with pytest.raises(index.Error, match="'alai-index config --plugins' adds one"):
        index.Index(root).generate_plugins()

    # Added later, with the plugin index made from the artifacts.
    index.configure(root, plugins=True)
    assert [c.result for c in index.Index(root).generate_plugins(from_plugin_cache=True)] == ["extracted"]
    assert [p["name"] for p in _lines(root / "plugins.jsonl")] == ["a"]
