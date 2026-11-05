# Copyright: (c) 2026, Jordan Borean (@jborean93) <jborean93@gmail.com>
# MIT License (see LICENSE or https://opensource.org/licenses/MIT)

from __future__ import annotations

import gzip
import hashlib
import json
import lzma
from pathlib import Path

import pytest
from collection_data import build_collection, module, relist

from alai_index import _index as index
from alai_index import _plugins as plugins

PUBLISHED = "2026-11-01T00:00:00Z"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _lines(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_bytes().splitlines()]


def _cache(root: Path, name: str = "ns.coll") -> Path:
    ns, short = name.split(".")
    return root / "plugin-cache" / ns / f"{short}.jsonl"


@pytest.fixture
def root(tmp_path: Path) -> Path:
    root = tmp_path / "index"
    index.configure(root)
    return root


def test_add(tmp_path: Path, root: Path) -> None:
    tarball = build_collection(
        tmp_path / "a.tar.gz",
        dependencies={"ns.dep": ">=1.0"},
        requires_ansible=">=2.17",
        modules={"thing": module("Manage things")},
    )
    dep = build_collection(tmp_path / "dep.tar.gz", "ns.dep", "1.0.0")
    sha256 = _sha256(tarball)

    added = index.Index(root).add([tarball, dep], published=PUBLISHED)

    assert added == [
        index.Change("ns.coll", "1.0.0", "added", {"sha256": sha256}),
        index.Change("ns.dep", "1.0.0", "added", {"sha256": _sha256(dep)}),
    ]
    assert (root / "dl" / sha256[:2] / sha256).read_bytes() == tarball.read_bytes()
    assert _lines(root / "index" / "ns" / "coll.jsonl") == [
        {
            "v": 1,
            "name": "ns.coll",
            "vers": "1.0.0",
            "deps": [{"name": "ns.dep", "req": ">=1.0"}],
            "sha256": sha256,
            "size": tarball.stat().st_size,
            "requires_ansible": ">=2.17",
            "yanked": False,
            "published": PUBLISHED,
        }
    ]
    listing = json.loads((root / "collections.json").read_bytes())["namespaces"]["ns"]
    assert listing["coll"]["sha256"] == _sha256(root / "index" / "ns" / "coll.jsonl")
    assert set(listing) == {"coll", "dep"}
    assert _lines(root / "plugins.jsonl") == [
        {
            "v": 1,
            "type": "module",
            "name": "thing",
            "collection": "ns.coll",
            "since": "1.0.0",
            "short_description": "Manage things",
        }
    ]
    assert _lines(_cache(root)) == [
        {
            "v": 1,
            "version": "1.0.0",
            "plugins": [{"type": "module", "name": "thing", "short_description": "Manage things"}],
            "routing": {},
        }
    ]


def test_add_again_is_a_no_op(tmp_path: Path, root: Path) -> None:
    tarball = build_collection(tmp_path / "a.tar.gz")
    index.Index(root).add([tarball])
    before = (root / "collections.json").read_bytes()

    assert (
        index.Index(root).add([tarball, tarball])
        == [index.Change("ns.coll", "1.0.0", "unchanged", {"sha256": _sha256(tarball)})] * 2
    )
    assert (root / "collections.json").read_bytes() == before


def test_same_version_other_artifact(tmp_path: Path, root: Path) -> None:
    index.Index(root).add([build_collection(tmp_path / "a.tar.gz")])
    other = build_collection(tmp_path / "b.tar.gz", modules={"x": module("X")})

    with pytest.raises(index.Error, match=r"ns\.coll 1\.0\.0 is already in the index with another artifact"):
        index.Index(root).add([other])


def test_new_version_appends_and_ends_plugins(tmp_path: Path, root: Path) -> None:
    index.Index(root).add([build_collection(tmp_path / "a.tar.gz", modules={"a": module("A"), "b": module("B")})])
    index.Index(root).add([build_collection(tmp_path / "b.tar.gz", version="2.0.0", modules={"a": module("A2")})])

    assert [line["vers"] for line in _lines(root / "index" / "ns" / "coll.jsonl")] == ["1.0.0", "2.0.0"]
    assert [(p["name"], p.get("until"), p["short_description"]) for p in _lines(root / "plugins.jsonl")] == [
        ("a", None, "A2"),
        ("b", "2.0.0", "B"),
    ]


def test_missing_dependency(tmp_path: Path, root: Path) -> None:
    tarball = build_collection(tmp_path / "a.tar.gz", dependencies={"ns.dep": ">=2.0.0"})
    dep = build_collection(tmp_path / "dep.tar.gz", "ns.dep", "1.0.0")

    with pytest.raises(index.Error, match=r"needs ns\.dep >=2\.0\.0, which no version"):
        index.Index(root).add([tarball, dep])
    assert not (root / "dl").exists()
    assert not (root / "index").exists()


def test_yanked_dependency_doesnt_count(tmp_path: Path, root: Path) -> None:
    index.Index(root).add([build_collection(tmp_path / "dep.tar.gz", "ns.dep", "1.0.0")])
    index.Index(root).set(["ns.dep"], yanked=True)

    with pytest.raises(index.Error, match="no version in the index that isn't yanked"):
        index.Index(root).add([build_collection(tmp_path / "a.tar.gz", dependencies={"ns.dep": "*"})])


@pytest.mark.parametrize(
    ("kwargs", "match"),
    [
        ({"dependencies": {"ns.dep": "^1.0"}}, r"dependencies\.ns\.dep"),
        ({"requires_ansible": "not a specifier"}, "meta/runtime.yml"),
        ({"runtime": b"requires_ansible: [\n"}, "meta/runtime.yml"),
    ],
)
def test_invalid_metadata(tmp_path: Path, root: Path, kwargs: dict[str, object], match: str) -> None:
    tarball = build_collection(tmp_path / "a.tar.gz", **kwargs)  # pyright: ignore[reportArgumentType]

    with pytest.raises(index.Error, match=match):
        index.Index(root).add([tarball])


def test_invalid_tarball(tmp_path: Path, root: Path) -> None:
    bad = tmp_path / "bad.tar.gz"
    bad.write_bytes(b"not a tarball")

    with pytest.raises(index.Error, match=r"bad\.tar\.gz"):
        index.Index(root).add([build_collection(tmp_path / "a.tar.gz"), bad])
    assert not (root / "dl").exists()


@pytest.mark.parametrize("compression", ["none", "zstd"])
def test_other_compression(tmp_path: Path, root: Path, compression: str) -> None:
    tar = gzip.decompress(build_collection(tmp_path / "a.tar.gz").read_bytes())
    if compression == "zstd":
        zstd = pytest.importorskip("compression.zstd")
        tar = zstd.compress(tar)
    tarball = tmp_path / "a"
    tarball.write_bytes(tar)

    index.Index(root).add([tarball])

    assert (root / "dl" / _sha256(tarball)[:2] / _sha256(tarball)).read_bytes() == tar


def test_unsupported_compression(tmp_path: Path, root: Path) -> None:
    tarball = tmp_path / "a.tar.xz"
    tarball.write_bytes(lzma.compress(gzip.decompress(build_collection(tmp_path / "a.tar.gz").read_bytes())))

    with pytest.raises(index.Error, match="xz"):
        index.Index(root).add([tarball])
    assert not (root / "dl").exists()


def test_missing_tarball(tmp_path: Path, root: Path) -> None:
    with pytest.raises(index.Error, match="doesn't exist"):
        index.Index(root).add([tmp_path / "missing.tar.gz"])


def test_failed_extraction_still_adds(tmp_path: Path, root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(path: object) -> None:
        raise ValueError("boom")

    monkeypatch.setattr("alai_core.extract.extract", fail)
    tarball = build_collection(tmp_path / "a.tar.gz", modules={"a": module("A")})

    assert index.Index(root).add([tarball])[0].details == {
        "sha256": hashlib.sha256(tarball.read_bytes()).hexdigest(),
        "error": "ValueError: boom",
    }
    assert _lines(_cache(root)) == [{"v": 1, "version": "1.0.0", "error": "ValueError: boom"}]
    assert (root / "plugins.jsonl").read_bytes() == b""


def test_cache_lines_follow_the_index(tmp_path: Path, root: Path) -> None:
    index.Index(root).add([build_collection(tmp_path / "a.tar.gz")])
    # As a run that stopped after writing the plugin cache, but before the index file, leaves it.
    _cache(root).write_bytes(_cache(root).read_bytes() + b'{"v":1,"version":"9.0.0","plugins":[],"routing":{}}\n')
    index.Index(root).add([build_collection(tmp_path / "b.tar.gz", version="2.0.0")])

    assert [line["version"] for line in _lines(_cache(root))] == ["1.0.0", "2.0.0"]


@pytest.mark.parametrize("lost", [b"", b"not json\n", b'{"v":1,"version":"1.0.0"}\n', None])
def test_lost_extraction_is_made_again(tmp_path: Path, root: Path, lost: bytes | None) -> None:
    index.Index(root).add([build_collection(tmp_path / "a.tar.gz", modules={"a": module("A")})])
    if lost is None:
        _cache(root).unlink()
    else:
        _cache(root).write_bytes(lost)

    # The artifact is on disk, so it's extracted again as the collection is changed.
    tree = index.Index(root)
    changes = tree.add([build_collection(tmp_path / "b.tar.gz", version="2.0.0", modules={"b": module("B")})])
    assert [(c.version, c.result) for c in changes] == [("2.0.0", "added"), ("1.0.0", "extracted")]
    assert [line["version"] for line in _lines(_cache(root))] == ["1.0.0", "2.0.0"]
    assert [(p["name"], p["since"], p.get("until")) for p in _lines(root / "plugins.jsonl")] == [
        ("a", "1.0.0", "2.0.0"),
        ("b", "2.0.0", None),
    ]
    assert tree.files["outdated"] == []


def test_lost_extraction_without_its_artifact(tmp_path: Path, root: Path) -> None:
    first = build_collection(tmp_path / "a.tar.gz", modules={"a": module("A")})
    index.Index(root).add([first])
    before = (root / "plugins.jsonl").read_bytes()
    _cache(root).unlink()
    sha256 = _sha256(first)
    (root / "dl" / sha256[:2] / sha256).unlink()

    # Reported, and the collection keeps the plugin lines it has until its plugins can be extracted again.
    tree = index.Index(root)
    tree.add([build_collection(tmp_path / "b.tar.gz", version="2.0.0", modules={"b": module("B")})])
    assert tree.files["outdated"] == [{"name": "ns.coll", "version": "1.0.0", "index": f"dl/{sha256[:2]}/{sha256}"}]
    assert (root / "plugins.jsonl").read_bytes() == before
    assert [line["version"] for line in _lines(_cache(root))] == ["2.0.0"]


def test_older_extraction(tmp_path: Path, root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    first = build_collection(tmp_path / "a.tar.gz", modules={"a": module("A")})
    index.Index(root).add([first])
    monkeypatch.setattr(plugins, "CURRENT", 2)
    monkeypatch.setattr(plugins, "READABLE", frozenset({1, 2}))

    # Made again from the artifact on disk, and replaced in place.
    changes = index.Index(root).add([build_collection(tmp_path / "b.tar.gz", version="2.0.0")])
    assert [(c.version, c.result) for c in changes] == [("2.0.0", "added"), ("1.0.0", "extracted")]
    assert [line["v"] for line in _lines(_cache(root))] == [2, 2]

    # Without the artifact, the older one is used as it is until then.
    monkeypatch.setattr(plugins, "CURRENT", 3)
    monkeypatch.setattr(plugins, "READABLE", frozenset({2, 3}))
    sha256 = _sha256(first)
    (root / "dl" / sha256[:2] / sha256).unlink()
    tree = index.Index(root)
    tree.add([build_collection(tmp_path / "c.tar.gz", version="3.0.0")])
    assert [o["version"] for o in tree.files["outdated"]] == ["1.0.0"]
    assert [p["name"] for p in _lines(root / "plugins.jsonl")] == ["a"]


def test_cache_from_a_newer_alai_index(tmp_path: Path, root: Path) -> None:
    index.Index(root).add([build_collection(tmp_path / "a.tar.gz")])
    _cache(root).write_bytes(b'{"v":2,"version":"1.0.0"}\n')
    before = (root / "collections.json").read_bytes()

    with pytest.raises(index.Error, match="line 1 was written by a newer alai-index"):
        index.Index(root).add([build_collection(tmp_path / "b.tar.gz", version="2.0.0")])
    assert (root / "collections.json").read_bytes() == before


def test_plugin_lines_for_collections_the_index_doesnt_have_are_dropped(tmp_path: Path, root: Path) -> None:
    # As another tool left them, such as ansible.builtin's.
    stray = b'{"v":1,"type":"module","name":"ping","collection":"ansible.builtin"}\n'
    (root / "plugins.jsonl").write_bytes(stray)

    index.Index(root).add([build_collection(tmp_path / "a.tar.gz", modules={"a": module("A")})])
    assert [p["collection"] for p in _lines(root / "plugins.jsonl")] == ["ns.coll"]


def test_hashed_index_files(tmp_path: Path) -> None:
    root = tmp_path / "index"
    index.configure(root, index="index/{sha256-prefix}/{sha256-checksum}.jsonl")
    index.Index(root).add([build_collection(tmp_path / "a.tar.gz")])
    first = json.loads((root / "collections.json").read_bytes())["namespaces"]["ns"]["coll"]["sha256"]
    index.Index(root).add([build_collection(tmp_path / "b.tar.gz", version="2.0.0")])
    second = json.loads((root / "collections.json").read_bytes())["namespaces"]["ns"]["coll"]["sha256"]

    # The old file is kept for clients with an older collections.json, for the caller to delete later.
    for sha256, versions in ((first, ["1.0.0"]), (second, ["1.0.0", "2.0.0"])):
        path = root / "index" / sha256[:2] / f"{sha256}.jsonl"
        assert _sha256(path) == sha256
        assert [line["vers"] for line in _lines(path)] == versions


def test_no_listing_or_plugins(tmp_path: Path) -> None:
    root = tmp_path / "index"
    index.configure(root, listing=False, plugins=False)
    index.Index(root).add([build_collection(tmp_path / "a.tar.gz")])
    index.Index(root).add([build_collection(tmp_path / "b.tar.gz", version="2.0.0")])

    assert [line["vers"] for line in _lines(root / "index" / "ns" / "coll.jsonl")] == ["1.0.0", "2.0.0"]
    # Kept in the tree, as it's the record of each collection's index file. It's left out when publishing.
    assert json.loads((root / "config.json").read_bytes())["formats"]["1"].get("collections") is None
    assert list(json.loads((root / "collections.json").read_bytes())["namespaces"]["ns"]) == ["coll"]
    assert not (root / "plugins.jsonl").exists()
    assert not (root / "plugin-cache").exists()


@pytest.mark.parametrize("dl", [None, "artifacts"])
def test_dl_on_another_host(tmp_path: Path, dl: str | None) -> None:
    root = tmp_path / "index"
    dl_dir = tmp_path / dl if dl else None
    template = "https://dl.example/sha256/{sha256-prefix}/{sha256-checksum}.tar.gz"
    index.configure(root, dl=dl_dir, dl_template=template)
    tarball = build_collection(tmp_path / "a.tar.gz")
    sha256 = _sha256(tarball)
    index.Index(root, dl=dl_dir).add([tarball])

    # At the URL's path, below the directory standing for the host's root.
    host = dl_dir or root / "dl"
    assert (host / "sha256" / sha256[:2] / f"{sha256}.tar.gz").read_bytes() == tarball.read_bytes()


def test_dl_in_the_index(tmp_path: Path) -> None:
    root = tmp_path / "index"
    index.configure(root, dl=root / "artifacts")
    tarball = build_collection(tmp_path / "a.tar.gz")
    sha256 = _sha256(tarball)
    index.Index(root).add([tarball])

    assert (root / "artifacts" / sha256[:2] / sha256).read_bytes() == tarball.read_bytes()


def test_listed_index_file_missing(tmp_path: Path, root: Path) -> None:
    index.Index(root).add([build_collection(tmp_path / "a.tar.gz")])
    (root / "index" / "ns" / "coll.jsonl").unlink()

    with pytest.raises(index.Error, match=r"coll\.jsonl is missing, which .*collections\.json has for ns\.coll"):
        index.Index(root).add([build_collection(tmp_path / "b.tar.gz", version="2.0.0")])


def test_index_file_that_isnt_the_listed_one(tmp_path: Path, root: Path) -> None:
    index.Index(root).add([build_collection(tmp_path / "a.tar.gz")])
    path = root / "index" / "ns" / "coll.jsonl"
    path.write_bytes(path.read_bytes().replace(b'"yanked":false', b'"yanked":true'))

    with pytest.raises(index.Error, match=r"isn't the index file .* has for ns\.coll, its sha256 differs"):
        index.Index(root).add([build_collection(tmp_path / "b.tar.gz", version="2.0.0")])


def test_unreadable_index_line_isnt_dropped(tmp_path: Path, root: Path) -> None:
    index.Index(root).add([build_collection(tmp_path / "a.tar.gz")])
    path = root / "index" / "ns" / "coll.jsonl"
    path.write_bytes(path.read_bytes() + b'{"v":2,"name":"ns.coll"}\nnot json\n')
    relist(root, "ns.coll")

    with pytest.raises(index.Error, match="has lines this alai-index can't read, line 3"):
        index.Index(root).add([build_collection(tmp_path / "b.tar.gz", version="2.0.0")])


def test_no_config(tmp_path: Path) -> None:
    with pytest.raises(index.Error, match="'alai-index config' makes one"):
        index.Index(tmp_path)


def test_skip_dependencies(tmp_path: Path, root: Path) -> None:
    tree = index.Index(root)
    tree.add([build_collection(tmp_path / "a.tar.gz", dependencies={"ns.missing": ">=1.0"})], skip=["dependencies"])

    assert _lines(root / "index" / "ns" / "coll.jsonl")[0]["deps"] == [{"name": "ns.missing", "req": ">=1.0"}]
    assert tree.warnings == [
        "ns.coll 1.0.0 needs ns.missing >=1.0, which no version in the index that isn't yanked meets"
    ]


def test_skip_requirements(tmp_path: Path, root: Path) -> None:
    tree = index.Index(root)
    tarball = build_collection(
        tmp_path / "a.tar.gz", dependencies={"ns.missing": "^1.0"}, requires_ansible="not a specifier"
    )
    # A requirement that can't be read can't be met either, so the dependency check doesn't count it.
    tree.add([tarball], skip=["requirements"])

    line = _lines(root / "index" / "ns" / "coll.jsonl")[0]
    assert (line["deps"], line["requires_ansible"]) == ([{"name": "ns.missing", "req": "^1.0"}], "not a specifier")
    assert len(tree.warnings) == 2
    assert all(w.startswith("ns.coll 1.0.0 has a requirement ansible-core can't read. ") for w in tree.warnings)


def test_name_and_version_are_always_checked(tmp_path: Path, root: Path) -> None:
    tarball = build_collection(tmp_path / "a.tar.gz", version="1.0")

    with pytest.raises(index.Error, match="version"):
        index.Index(root).add([tarball], skip=index.SKIPPABLE_CHECKS)


def test_failed_extraction_warns(tmp_path: Path, root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(path: object) -> None:
        raise ValueError("boom")

    monkeypatch.setattr("alai_core.extract.extract", fail)
    tree = index.Index(root)
    tree.add([build_collection(tmp_path / "a.tar.gz")])

    assert tree.warnings == [
        "extracting the plugins of ns.coll 1.0.0 failed, so the plugin index has none of them. ValueError: boom"
    ]
