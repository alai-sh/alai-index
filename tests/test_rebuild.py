# Copyright: (c) 2026, Jordan Borean (@jborean93) <jborean93@gmail.com>
# MIT License (see LICENSE or https://opensource.org/licenses/MIT)

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from collection_data import build_collection, module

from alai_index import _index as index


def _lines(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_bytes().splitlines()]


def _public(root: Path) -> dict[Path, bytes]:
    """Every public file but the artifacts."""
    return {
        p: p.read_bytes() for p in root.rglob("*") if p.is_file() and "private" not in p.parts and "dl" not in p.parts
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


def _rebuilt(root: Path, *args: object, **kwargs: object) -> list[str]:
    """The public files a rebuild wrote."""
    tree = index.Index(root)
    tree.rebuild(*args, **kwargs)  # pyright: ignore[reportArgumentType]
    return tree.files["written"]["index"]


def _extractions(root: Path, name: str = "coll") -> Path:
    return root / "private" / "cache" / "plugins" / "ns" / f"{name}.jsonl"


def test_nothing_changed(root: Path) -> None:
    assert index.Index(root).rebuild() == []
    assert index.Index(root).rebuild(everything=True) == []


def test_without_targets_only_the_listing_and_plugin_index(root: Path) -> None:
    before = _public(root)
    (root / "collections.json").write_bytes(b"{}")
    (root / "plugins.jsonl").write_bytes(b"")
    coll = root / "index" / "ns" / "coll.jsonl"
    coll.write_bytes(coll.read_bytes().replace(b'"yanked":true', b'"yanked":false'))

    assert sorted(_rebuilt(root)) == ["collections.json", "plugins.jsonl"]
    assert b'"yanked":false' in coll.read_bytes()
    assert {p: b for p, b in _public(root).items() if p != coll} == {p: b for p, b in before.items() if p != coll}


def test_all_from_what_the_index_knows(root: Path) -> None:
    before = _public(root)
    coll = root / "index" / "ns" / "coll.jsonl"
    coll.write_bytes(coll.read_bytes().replace(b'"yanked":true', b'"yanked":false'))
    (root / "plugins.jsonl").write_bytes(b"")

    assert sorted(_rebuilt(root, everything=True)) == [
        "index/ns/coll.jsonl",
        "plugins.jsonl",
    ]
    assert _public(root) == before


def test_files_narrows_it(root: Path) -> None:
    (root / "collections.json").write_bytes(b"{}")
    (root / "plugins.jsonl").write_bytes(b"")

    assert _rebuilt(root, files=["plugins"]) == ["plugins.jsonl"]
    assert (root / "collections.json").read_bytes() == b"{}"
    assert _rebuilt(root, files=["index"]) == ["collections.json"]


def test_restore_from_config_state_and_artifacts(root: Path) -> None:
    # As the README's restore has it, with the cache left out of the backup too.
    before = {**_public(root), _extractions(root): _extractions(root).read_bytes()}
    shutil.rmtree(root / "index")
    shutil.rmtree(root / "private" / "cache")
    for name in ("collections.json", "plugins.jsonl"):
        (root / name).unlink()

    index.Index(root).rebuild(everything=True, from_artifacts=True)
    assert {**_public(root), _extractions(root): _extractions(root).read_bytes()} == before


def test_lost_index_file_needs_the_artifacts(root: Path) -> None:
    (root / "index" / "ns" / "coll.jsonl").unlink()

    with pytest.raises(index.Error, match=r"'alai-index rebuild ns\.coll --files index --from-artifacts' makes it"):
        index.Index(root).rebuild(["ns.coll"])
    index.Index(root).rebuild(["ns.coll"], files=["index"], from_artifacts=True)
    assert [line["vers"] for line in _lines(root / "index" / "ns" / "coll.jsonl")] == ["1.0.0", "2.0.0"]


def test_from_artifacts_refuses_changes(root: Path) -> None:
    path = root / "index" / "ns" / "other.jsonl"
    path.write_bytes(path.read_bytes().replace(b'">=1.0"', b'">=0.1"'))
    before = path.read_bytes()

    # From what the index knows, the index file is where deps are kept, so they stay.
    index.Index(root).rebuild(["ns.other"], files=["index"])
    assert path.read_bytes() == before

    with pytest.raises(index.Error, match=r"--allow-changes writes them anyway\. ns\.other 1\.0\.0: deps"):
        index.Index(root).rebuild(everything=True, files=["index"], from_artifacts=True)
    assert path.read_bytes() == before

    written = _rebuilt(root, ["ns.other"], files=["index"], from_artifacts=True, allow_changes=True)
    assert written == ["index/ns/other.jsonl", "collections.json"]
    assert _lines(path)[0]["deps"] == [{"name": "ns.coll", "req": ">=1.0"}]


def test_lost_extractions(tmp_path: Path, root: Path) -> None:
    before = _extractions(root).read_bytes()
    plugin_index = (root / "plugins.jsonl").read_bytes()
    index_files = {p: b for p, b in _public(root).items() if p.name != "plugins.jsonl"}
    _extractions(root).unlink()

    with pytest.raises(index.Error, match=r"'alai-index rebuild ns\.coll --files plugins --from-artifacts' makes it"):
        index.Index(root).rebuild()
    assert index.Index(root).rebuild(["ns.coll"], files=["plugins"], from_artifacts=True) == [
        index.Change("ns.coll", "1.0.0", "extracted"),
        index.Change("ns.coll", "2.0.0", "extracted"),
    ]
    assert _extractions(root).read_bytes() == before
    assert (root / "plugins.jsonl").read_bytes() == plugin_index
    assert {p: p.read_bytes() for p in index_files} == index_files
    # The collection takes new versions again.
    index.Index(root).add([build_collection(tmp_path / "c.tar.gz", version="3.0.0")])


def test_extraction_still_fails(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(path: object) -> None:
        raise ValueError("boom")

    monkeypatch.setattr("alai_core.extract.extract", fail)

    tree = index.Index(root)
    assert tree.rebuild(["ns.other"], files=["plugins"], from_artifacts=True) == [
        index.Change("ns.other", "1.0.0", "extracted", {"error": "ValueError: boom"})
    ]
    assert tree.files["written"]["index"] == ["plugins.jsonl"]
    assert tree.warnings == [
        "extracting the plugins of ns.other 1.0.0 failed, so the plugin index has none of them. ValueError: boom"
    ]
    assert [p["name"] for p in _lines(root / "plugins.jsonl")] == ["new", "old"]


def test_one_version_needs_the_rest(root: Path) -> None:
    _extractions(root).unlink()

    with pytest.raises(index.Error, match=r"ns\.coll has no plugin extraction for 1\.0\.0"):
        index.Index(root).rebuild(["ns.coll:2.0.0"], files=["plugins"], from_artifacts=True)
    assert not _extractions(root).exists()


def test_missing_artifact(root: Path) -> None:
    for artifact in (root / "dl").rglob("*.tar.gz"):
        artifact.unlink()

    with pytest.raises(index.Error, match=r"which rebuilding ns\.coll 1\.0\.0 needs"):
        index.Index(root).rebuild(everything=True, from_artifacts=True)


def test_artifact_isnt_the_one_named(root: Path) -> None:
    for artifact in (root / "dl").rglob("*.tar.gz"):
        artifact.write_bytes(b"other bytes")

    with pytest.raises(index.Error, match="its sha256 differs"):
        index.Index(root).rebuild(["ns.coll"], from_artifacts=True)


@pytest.mark.parametrize(
    ("targets", "kwargs", "match"),
    [
        (["ns.coll"], {"everything": True}, "give collections or --all, not both"),
        ([], {"from_artifacts": True}, "name collections, or --all"),
        ([], {"files": ["docs"]}, "there are no docs files"),
    ],
)
def test_conflicting_options(root: Path, targets: list[str], kwargs: dict[str, object], match: str) -> None:
    with pytest.raises(index.Error, match=match):
        index.Index(root).rebuild(targets, **kwargs)  # pyright: ignore[reportArgumentType]


def test_no_plugin_index(tmp_path: Path) -> None:
    root = tmp_path / "index"
    index.configure(root, plugins=False)

    assert index.Index(root).rebuild() == []
    with pytest.raises(index.Error, match="no plugin index"):
        index.Index(root).rebuild(files=["plugins"])


def test_unreadable_index_file_from_artifacts(root: Path) -> None:
    path = root / "index" / "ns" / "coll.jsonl"
    before = path.read_bytes()
    path.write_bytes(b"not json\n")

    with pytest.raises(index.Error, match="has lines this alai-index can't read"):
        index.Index(root).rebuild(["ns.coll"], files=["index"])
    index.Index(root).rebuild(["ns.coll"], files=["index"], from_artifacts=True)
    assert path.read_bytes() == before
