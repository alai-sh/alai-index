# Copyright: (c) 2026, Jordan Borean (@jborean93) <jborean93@gmail.com>
# MIT License (see LICENSE or https://opensource.org/licenses/MIT)

from __future__ import annotations

import hashlib
import json
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
        "versions": [{"name": "ns.coll", "version": "1.0.0", "result": "added", "details": {"sha256": sha256}}],
        "written": {
            "dl": [],
            # The artifacts are a path in the index, so they're published with it, and first.
            "index": [f"dl/{sha256[:2]}/{sha256}.tar.gz", "index/ns/coll.jsonl", "plugins.jsonl", "collections.json"],
            "plugin-cache": ["ns/coll.jsonl"],
        },
        "deleted": {"dl": [], "index": [], "plugin-cache": []},
        "stale": [],
        "outdated": [],
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
    assert out["versions"] == [
        {"name": "ns.coll", "version": "1.0.0", "result": "removed", "details": {"sha256": sha256}}
    ]
    assert out["deleted"] == {
        "dl": [],
        "index": [f"dl/{sha256[:2]}/{sha256}.tar.gz", "index/ns/coll.jsonl"],
        "plugin-cache": ["ns/coll.jsonl"],
    }


def test_stale_index_files(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = str(tmp_path / "index")
    main(["config", "--root", root, "--index", "index/{sha256-prefix}/{sha256-checksum}.jsonl"])
    main(["add", "--root", root, str(build_collection(tmp_path / "a.tar.gz"))])
    listed = json.loads(Path(root, "collections.json").read_bytes())["namespaces"]["ns"]["coll"]["sha256"]

    out = _json(capsys, ["set", "--root", root, "--output-format", "json", "ns.coll", "--yanked"])
    assert out["stale"] == [f"index/{listed[:2]}/{listed}.jsonl"]
    assert Path(root, "index", listed[:2], f"{listed}.jsonl").exists()

    # Unyanked, the first file is named again, so it's published again though it's still in the tree.
    out = _json(capsys, ["set", "--root", root, "--output-format", "json", "ns.coll", "--no-yanked"])
    assert out["written"]["index"] == [f"index/{listed[:2]}/{listed}.jsonl", "collections.json"]  # pyright: ignore[reportIndexIssue]


def test_arguments_from_a_file(tmp_path: Path, root: str, capsys: pytest.CaptureFixture[str]) -> None:
    tarballs = [
        str(build_collection(tmp_path / "a.tar.gz", modules={"a": module("A")})),
        str(build_collection(tmp_path / "b.tar.gz", "ns.b", modules={"b": module("B")})),
    ]
    main(["add", "--root", root, *tarballs])
    Path(root, "plugins.jsonl").write_bytes(b"")

    # A long list of collections, read from a file a line each.
    names = tmp_path / "changed.txt"
    names.write_text("ns.coll\nns.b\n")
    out = _json(
        capsys, ["generate-plugins", "--root", root, "--from-plugin-cache", "--output-format", "json", f"@{names}"]
    )
    assert out["written"]["index"] == ["plugins.jsonl"]  # pyright: ignore[reportIndexIssue]


def test_warnings_go_to_stderr(tmp_path: Path, root: str, capsys: pytest.CaptureFixture[str]) -> None:
    tarball = build_collection(tmp_path / "a.tar.gz", dependencies={"ns.missing": "*"})
    capsys.readouterr()

    argv = ["add", "--root", root, "--output-format", "json", "--skip-checks", "dependencies", str(tarball)]
    assert main(argv) == 0
    captured = capsys.readouterr()
    assert json.loads(captured.out)["versions"][0]["result"] == "added"
    assert captured.err.splitlines() == [
        f"reading {tarball}",
        "warning: ns.coll 1.0.0 needs ns.missing *, which no version in the index that isn't yanked meets",
    ]


def test_outdated(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root, dl = str(tmp_path / "index"), str(tmp_path / "artifacts")
    main(["config", "--root", root, "--dl", dl, "--dl-template", "https://dl.example/sha256/{sha256-checksum}.tar.gz"])
    tarball = build_collection(tmp_path / "a.tar.gz")
    sha256 = hashlib.sha256(tarball.read_bytes()).hexdigest()
    main(["add", "--root", root, "--dl", dl, str(tarball)])
    Path(root, "plugin-cache", "ns", "coll.jsonl").unlink()
    Path(dl, "sha256", f"{sha256}.tar.gz").unlink()

    out = _json(
        capsys,
        ["generate-plugins", "--root", root, "--dl", dl, "ns.coll", "--from-plugin-cache", "--output-format", "json"],
    )
    assert out["outdated"] == [{"name": "ns.coll", "version": "1.0.0", "dl": f"sha256/{sha256}.tar.gz"}]
    capsys.readouterr()
    assert main(["generate-plugins", "--root", root, "--dl", dl, "ns.coll", "--from-plugin-cache"]) == 0
    assert capsys.readouterr().out.splitlines() == [
        "ns.coll 1.0.0's plugins are out of date, as its artifact isn't on disk to extract them again",
        "nothing changed",
    ]


def test_skippable_matches_the_index() -> None:
    assert _cli.SKIPPABLE_CHECKS == _index.SKIPPABLE_CHECKS


def test_nothing_changed(tmp_path: Path, root: str, capsys: pytest.CaptureFixture[str]) -> None:
    tarball = str(build_collection(tmp_path / "a.tar.gz"))
    main(["add", "--root", root, tarball])
    empty: dict[str, list[str]] = {"dl": [], "index": [], "plugin-cache": []}
    nothing: dict[str, object] = {"written": empty, "deleted": empty, "stale": [], "outdated": []}

    assert _json(capsys, ["generate-plugins", "--root", root, "--from-plugin-cache", "--output-format", "json"]) == {
        "versions": [],
        **nothing,
    }
    out = _json(capsys, ["add", "--root", root, "--output-format", "json", tarball])
    assert out["written"] == empty
    out = _json(capsys, ["set", "--root", root, "--output-format", "json", "ns.coll", "--no-yanked"])
    unchanged: dict[str, object] = {"name": "ns.coll", "version": "1.0.0", "result": "unchanged", "details": {}}
    assert out == {"versions": [unchanged], **nothing}
