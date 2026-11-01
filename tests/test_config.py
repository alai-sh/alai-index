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
    assert sorted(p.name for p in tmp_path.iterdir()) == ["config.json"]


def test_keeps_what_is_left_out(tmp_path: Path) -> None:
    index.configure(tmp_path, dl="https://dl.example/{sha256-checksum}.tar.gz", plugins=False)
    config = index.configure(tmp_path, index="i/{namespace}/{name}.jsonl")

    assert (config.index, config.dl, config.plugins) == ("i/{namespace}/{name}.jsonl", config.dl, None)
    assert config.dl == "https://dl.example/{sha256-checksum}.tar.gz"


def test_hashed_needs_listing(tmp_path: Path) -> None:
    with pytest.raises(index.Error, match="only a 'collections' listing gives"):
        index.configure(tmp_path, index="index/{sha256-prefix}/{sha256-checksum}.jsonl", listing=False)
    assert not (tmp_path / "config.json").exists()


@pytest.mark.parametrize("listing", [True, False])
def test_cant_change_with_collections(tmp_path: Path, listing: bool) -> None:
    index.configure(tmp_path, listing=listing)
    index.Index(tmp_path).add([build_collection(tmp_path / "a.tar.gz")])

    index.configure(tmp_path)  # The same config is fine.
    with pytest.raises(index.Error, match="already has collections"):
        index.configure(tmp_path, dl="https://dl.example/{sha256-checksum}")


@pytest.mark.parametrize(
    "kwargs",
    [
        {"index": "private/{namespace}/{name}.jsonl"},
        {"index": "./private/x/{sha256-checksum}.jsonl"},
        {"dl": "private/{sha256-checksum}.tar.gz"},
    ],
)
def test_templates_cant_lead_into_private(tmp_path: Path, kwargs: dict[str, str]) -> None:
    with pytest.raises(index.Error, match="can't be kept under private/"):
        index.configure(tmp_path, **kwargs)  # pyright: ignore[reportArgumentType]
