# Copyright: (c) 2026, Jordan Borean (@jborean93) <jborean93@gmail.com>
# MIT License (see LICENSE or https://opensource.org/licenses/MIT)

from __future__ import annotations

import json
from pathlib import Path

import pytest
from collection_data import build_collection

from alai_index import _index as index


def test_defaults(tmp_path: Path) -> None:
    index.configure(tmp_path)

    assert json.loads((tmp_path / "config.json").read_bytes()) == {
        "formats": {"1": {"index": index.DEFAULT_INDEX, "collections": "collections.json", "plugins": "plugins.jsonl"}},
        "dl": index.DEFAULT_DL,
        "moved-to": None,
    }
    assert json.loads((tmp_path / "collections.json").read_bytes()) == {"v": 1, "namespaces": {}}
    assert (tmp_path / "plugins.jsonl").read_bytes() == b""


def test_without_listing_or_plugins(tmp_path: Path) -> None:
    index.configure(tmp_path, listing=False, plugins=False)

    assert json.loads((tmp_path / "config.json").read_bytes())["formats"] == {"1": {"index": index.DEFAULT_INDEX}}
    # collections.json is kept in the tree whatever config.json says, as it's the record of each collection's index file.
    assert sorted(p.name for p in tmp_path.iterdir()) == ["collections.json", "config.json"]
    assert json.loads((tmp_path / "collections.json").read_bytes()) == {"v": 1, "namespaces": {}}


def test_keeps_what_is_left_out(tmp_path: Path) -> None:
    index.configure(tmp_path, dl_template="https://dl.example/{sha256-checksum}.tar.gz", plugins=False)
    config = index.configure(tmp_path, index="i/{namespace}/{name}.jsonl")

    assert (config.index, config.dl, config.plugins) == ("i/{namespace}/{name}.jsonl", config.dl, None)
    assert config.dl == "https://dl.example/{sha256-checksum}.tar.gz"


def test_hashed_needs_listing(tmp_path: Path) -> None:
    with pytest.raises(index.Error, match="needs a 'collections' listing"):
        index.configure(tmp_path, index="index/{sha256-prefix}/{sha256-checksum}.jsonl", listing=False)
    assert not (tmp_path / "config.json").exists()


@pytest.mark.parametrize("listing", [True, False])
def test_cant_change_with_collections(tmp_path: Path, listing: bool) -> None:
    index.configure(tmp_path, listing=listing)
    index.Index(tmp_path).add([build_collection(tmp_path / "a.tar.gz")])

    index.configure(tmp_path)  # The same config is fine.
    with pytest.raises(index.Error, match="already has collections"):
        index.configure(tmp_path, dl_template="https://dl.example/{sha256-checksum}")


@pytest.mark.parametrize(
    "kwargs",
    [
        {"index": "plugin-cache/{namespace}/{name}.jsonl"},
        {"index": "./plugin-cache/x/{sha256-checksum}.jsonl"},
        {"dl_template": "plugin-cache/{sha256-checksum}.tar.gz"},
        {"plugin_cache": "dl"},
    ],
)
def test_templates_cant_lead_into_the_plugin_cache(tmp_path: Path, kwargs: dict[str, str]) -> None:
    with pytest.raises(index.Error, match=r"can't be kept in .*, which is the plugin cache"):
        if "plugin_cache" in kwargs:
            kwargs["plugin_cache"] = str(tmp_path / kwargs["plugin_cache"])
        index.configure(tmp_path, **kwargs)  # pyright: ignore[reportArgumentType]


@pytest.mark.parametrize(
    "dl_template",
    ["dl/{namespace}.tar.gz", "dl/{namespace}/{name}/{version}.tar.gz", "dl/{sha256-prefix}.tar.gz"],
)
def test_dl_template_needs_the_checksum(tmp_path: Path, dl_template: str) -> None:
    with pytest.raises(index.Error, match=r"needs \{sha256-checksum\}, so an artifact's URL never changes"):
        index.configure(tmp_path, dl_template=dl_template)


def test_dl_template_from_the_dl_directory(tmp_path: Path) -> None:
    config = index.configure(tmp_path, dl=tmp_path / "artifacts" / "v1")

    assert config.dl == "artifacts/v1/{sha256-prefix}/{sha256-checksum}"


def test_dl_directory_outside_the_index_needs_a_template(tmp_path: Path) -> None:
    with pytest.raises(index.Error, match="--dl-template gives the URL they're published at"):
        index.configure(tmp_path / "index", dl=tmp_path / "artifacts")


def test_dl_directory_the_template_doesnt_use(tmp_path: Path) -> None:
    index.configure(tmp_path)

    with pytest.raises(index.Error, match="puts artifacts in the index, not in"):
        index.Index(tmp_path, dl=tmp_path / "elsewhere")


@pytest.mark.parametrize(
    "dl",
    [
        "../{sha256-checksum}.tar.gz",
        "/abs/{sha256-checksum}.tar.gz",
        "dl/c:{sha256-checksum}.tar.gz",
        "dl\\..\\..\\{sha256-checksum}.tar.gz",
    ],
)
def test_dl_outside_the_tree(tmp_path: Path, dl: str) -> None:
    with pytest.raises(index.Error, match="leads outside the index"):
        index.configure(tmp_path / "index", dl_template=dl)


def test_plugin_cache_elsewhere(tmp_path: Path) -> None:
    root, cache = tmp_path / "index", tmp_path / "cache"
    index.configure(root, plugin_cache=cache)
    index.Index(root, plugin_cache=cache).add([build_collection(tmp_path / "a.tar.gz")])

    assert not (root / "plugin-cache").exists()
    assert (cache / "ns" / "coll.jsonl").exists()
    # Given another, it's made again from the artifacts, as a cache can always be.
    changes = index.Index(root).add([build_collection(tmp_path / "b.tar.gz", version="2.0.0")])
    assert [(c.version, c.result) for c in changes] == [("2.0.0", "added"), ("1.0.0", "extracted")]
    assert (root / "plugin-cache" / "ns" / "coll.jsonl").exists()


def test_listing_is_never_made_empty(tmp_path: Path) -> None:
    root = tmp_path / "index"
    index.configure(root)
    index.Index(root).add([build_collection(tmp_path / "a.tar.gz")])
    (root / "collections.json").unlink()

    # An index with collections is never given an empty listing, which the next add would take as all new.
    with pytest.raises(index.Error, match="is missing, which every index has"):
        index.configure(root)
    with pytest.raises(index.Error, match="is missing, which every index has"):
        index.Index(root).add([build_collection(tmp_path / "b.tar.gz", version="2.0.0")])
