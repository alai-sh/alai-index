# Copyright: (c) 2026, Jordan Borean (@jborean93) <jborean93@gmail.com>
# MIT License (see LICENSE or https://opensource.org/licenses/MIT)

from __future__ import annotations

import hashlib
import json
import typing as _t
from pathlib import Path

import pytest
from collection_data import build_collection, module

from alai_index import _cli, _index
from alai_index._cli import main


def _json(capsys: pytest.CaptureFixture[str], argv: list[str]) -> dict[str, object]:
    capsys.readouterr()
    assert main(argv) == 0
    return json.loads(capsys.readouterr().out)


@pytest.fixture
def root(tmp_path: Path) -> str:
    root = str(tmp_path / "index")
    main(["config", "--root", root])
    return root


def test_add(tmp_path: Path, root: str, capsys: pytest.CaptureFixture[str]) -> None:
    tarball = build_collection(tmp_path / "a.tar.gz", modules={"thing": module("Thing")})
    sha256 = hashlib.sha256(tarball.read_bytes()).hexdigest()

    assert _json(capsys, ["add", "--root", root, "--output-format", "json", str(tarball)]) == {
        "versions": [{"name": "ns.coll", "version": "1.0.0", "sha256": sha256, "result": "added"}],
        "collections": ["ns.coll"],
        "written": {
            "dl": [],
            # The artifacts are a path in the index, so they're published with it, and first.
            "index": [f"dl/{sha256[:2]}/{sha256}.tar.gz", "index/ns/coll.jsonl", "plugins.jsonl", "collections.json"],
            "private": ["cache/plugins/ns/coll.jsonl", "index/ns/coll.jsonl", "collections.json"],
        },
        "deleted": {"dl": [], "index": [], "private": []},
        "stale": [],
    }


def test_dl_on_another_host(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root, dl = str(tmp_path / "index"), str(tmp_path / "artifacts")
    main(["config", "--root", root, "--dl", dl, "--dl-template", "https://dl.example/sha256/{sha256-checksum}.tar.gz"])
    tarball = build_collection(tmp_path / "a.tar.gz")
    sha256 = hashlib.sha256(tarball.read_bytes()).hexdigest()

    out = _json(capsys, ["add", "--root", root, "--dl", dl, "--output-format", "json", str(tarball)])
    assert out["written"]["dl"] == [f"sha256/{sha256}.tar.gz"]  # pyright: ignore[reportIndexIssue]


def test_remove(tmp_path: Path, root: str, capsys: pytest.CaptureFixture[str]) -> None:
    tarball = build_collection(tmp_path / "a.tar.gz")
    sha256 = hashlib.sha256(tarball.read_bytes()).hexdigest()
    main(["add", "--root", root, str(tarball)])

    out = _json(capsys, ["remove", "--root", root, "--output-format", "json", "ns.coll"])
    assert out["versions"] == [{"name": "ns.coll", "version": "1.0.0", "result": "removed"}]
    assert out["deleted"] == {
        "dl": [],
        "index": [f"dl/{sha256[:2]}/{sha256}.tar.gz", "index/ns/coll.jsonl"],
        "private": ["index/ns/coll.jsonl", "cache/plugins/ns/coll.jsonl"],
    }


def test_stale_index_files(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = str(tmp_path / "index")
    main(["config", "--root", root, "--index", "index/{sha256-prefix}/{sha256-checksum}.jsonl"])
    main(["add", "--root", root, str(build_collection(tmp_path / "a.tar.gz"))])
    listed = json.loads(Path(root, "collections.json").read_bytes())["namespaces"]["ns"]["coll"]["sha256"]

    out = _json(capsys, ["set", "--root", root, "--output-format", "json", "ns.coll", "--yanked"])
    assert out["stale"] == [f"index/{listed[:2]}/{listed}.jsonl"]
    assert Path(root, "index", listed[:2], f"{listed}.jsonl").exists()


def test_no_commit_then_rebuild(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    committed, batched = str(tmp_path / "committed"), str(tmp_path / "batched")
    tarballs = [str(build_collection(tmp_path / "a.tar.gz")), str(build_collection(tmp_path / "b.tar.gz", "ns.b"))]
    for root in (committed, batched):
        main(["config", "--root", root])
    # The same publish time for both, or index files written a second apart would differ.
    published = ["--published", "2026-11-01T00:00:00Z"]
    main(["add", "--root", committed, *published, *tarballs])
    listing, plugin_index = Path(batched, "collections.json").read_bytes(), Path(batched, "plugins.jsonl").read_bytes()

    out = _json(capsys, ["add", "--root", batched, *published, "--no-commit", "--output-format", "json", *tarballs])
    assert "collections.json" not in out["written"]["index"]  # pyright: ignore[reportIndexIssue]
    assert "plugins.jsonl" not in out["written"]["index"]  # pyright: ignore[reportIndexIssue]
    assert Path(batched, "collections.json").read_bytes() == listing
    assert Path(batched, "plugins.jsonl").read_bytes() == plugin_index

    # Committed later, with the collections the output named, read from a file as a long list would be.
    names = tmp_path / "changed.txt"
    names.write_text("\n".join(out["collections"]) + "\n")  # pyright: ignore[reportCallIssue, reportArgumentType]
    assert main(["rebuild", "--root", batched, f"@{names}"]) == 0
    for name in ("collections.json", "plugins.jsonl"):
        assert _content(Path(committed, name)) == _content(Path(batched, name))


def _content(path: Path) -> object:
    """A file's JSON, or each line's, without the times it was written at, which differ between the two."""
    data = path.read_bytes()
    return _without_times(
        [json.loads(line) for line in data.splitlines()] if path.suffix == ".jsonl" else json.loads(data)
    )


def _without_times(data: object) -> object:
    if isinstance(data, dict):
        return {k: _without_times(v) for k, v in _t.cast("dict[str, object]", data).items() if k != "updated"}
    if isinstance(data, list):
        return [_without_times(v) for v in _t.cast("list[object]", data)]
    return data


def test_warnings_go_to_stderr(tmp_path: Path, root: str, capsys: pytest.CaptureFixture[str]) -> None:
    tarball = build_collection(tmp_path / "a.tar.gz", dependencies={"ns.missing": "*"})
    capsys.readouterr()

    argv = ["add", "--root", root, "--output-format", "json", "--skip-checks", "dependencies", str(tarball)]
    assert main(argv) == 0
    captured = capsys.readouterr()
    assert json.loads(captured.out)["versions"][0]["result"] == "added"
    assert captured.err == (
        "warning: ns.coll 1.0.0 needs ns.missing *, which no version in the index that isn't yanked meets\n"
    )


def test_skippable_matches_the_index() -> None:
    assert _cli.SKIPPABLE_CHECKS == _index.SKIPPABLE_CHECKS


def test_nothing_changed(tmp_path: Path, root: str, capsys: pytest.CaptureFixture[str]) -> None:
    tarball = str(build_collection(tmp_path / "a.tar.gz"))
    main(["add", "--root", root, tarball])
    empty: dict[str, list[str]] = {"dl": [], "index": [], "private": []}
    nothing: dict[str, object] = {"collections": [], "written": empty, "deleted": empty, "stale": []}

    assert _json(capsys, ["rebuild", "--root", root, "--all", "--output-format", "json"]) == {"versions": [], **nothing}
    assert _json(capsys, ["add", "--root", root, "--output-format", "json", tarball])["collections"] == []
    out = _json(capsys, ["set", "--root", root, "--output-format", "json", "ns.coll", "--no-yanked"])
    assert out == {"versions": [{"name": "ns.coll", "version": "1.0.0", "result": "unchanged"}], **nothing}
