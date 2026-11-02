# Copyright: (c) 2026, Jordan Borean (@jborean93) <jborean93@gmail.com>
# MIT License (see LICENSE or https://opensource.org/licenses/MIT)

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from collection_data import build_collection

from alai_index._cli import SHELLS, main


def test_config_and_add(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = str(tmp_path / "index")
    tarball = str(build_collection(tmp_path / "a.tar.gz"))

    assert main(["config", "--root", root, "--no-plugins"]) == 0
    assert main(["add", "--root", root, tarball, "--published", "2026-11-01T00:00:00Z"]) == 0
    assert main(["add", tarball, "--root", root]) == 0

    assert capsys.readouterr().out == "added ns.coll 1.0.0\nns.coll 1.0.0 is already in the index\n"
    assert '"published":"2026-11-01T00:00:00Z"' in (tmp_path / "index" / "index" / "ns" / "coll.jsonl").read_text()


def test_error(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["add", "--root", str(tmp_path), "a.tar.gz"]) == 1
    assert capsys.readouterr().err.startswith("error: ")


def complete(tmp_path: Path, line: str, cwd: Path | None = None) -> list[str]:
    """The completions argcomplete gives for the command line typed so far, run in a subprocess as the shell does."""
    out = tmp_path / "completions"
    env = os.environ | {
        "_ARGCOMPLETE": "1",
        "_ARGCOMPLETE_STDOUT_FILENAME": str(out),
        "COMP_LINE": line,
        "COMP_POINT": str(len(line)),
    }

    subprocess.run([sys.executable, "-m", "alai_index"], check=True, capture_output=True, env=env, cwd=cwd)

    return out.read_text().split("\013")


def test_complete_command(tmp_path: Path) -> None:
    assert complete(tmp_path, "alai-index ad") == ["add "]


def test_complete_option(tmp_path: Path) -> None:
    assert complete(tmp_path, "alai-index config --no-p") == ["--no-plugins "]


def test_complete_root_directories(tmp_path: Path) -> None:
    work = tmp_path / "work"
    (work / "indexes").mkdir(parents=True)
    (work / "index.txt").touch()

    assert complete(tmp_path, "alai-index add --root ind", cwd=work) == ["indexes/"]


@pytest.mark.parametrize("shell", SHELLS)
def test_generate_shell_completion(capsys: pytest.CaptureFixture[str], shell: str) -> None:
    assert main(["generate-shell-completion", shell]) == 0
    assert "alai-index" in capsys.readouterr().out


def test_set_and_remove(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = str(tmp_path / "index")
    main(["config", "--root", root])
    main(["add", "--root", root, str(build_collection(tmp_path / "a.tar.gz"))])
    capsys.readouterr()

    assert main(["set", "--root", root, "ns.coll", "--yanked"]) == 0
    assert main(["set", "--root", root, "ns.coll:1.0.0", "--yanked"]) == 0
    assert main(["remove", "--root", root, "ns.coll"]) == 0

    assert capsys.readouterr().out == ("updated ns.coll 1.0.0\nns.coll 1.0.0 is unchanged\nremoved ns.coll 1.0.0\n")


def test_rebuild(tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
    root = str(tmp_path / "index")
    main(["config", "--root", root])
    main(["add", "--root", root, str(build_collection(tmp_path / "a.tar.gz"))])
    capsys.readouterr()

    assert main(["rebuild", "--root", root]) == 0
    assert capsys.readouterr().out == "nothing changed\n"

    def fail(path: object) -> None:
        raise ValueError("boom")

    monkeypatch.setattr("alai_core.extract.extract", fail)
    assert main(["rebuild", "--root", root, "--all", "--files", "plugins", "--from-artifacts"]) == 0
    captured = capsys.readouterr()
    assert captured.out == "rebuilt ns.coll 1.0.0's plugins\n"
    assert captured.err.startswith("warning: extracting ns.coll 1.0.0's plugins failed")


def test_private(tmp_path: Path) -> None:
    root, state = str(tmp_path / "index"), str(tmp_path / "state")

    assert main(["config", "--root", root, "--private", state]) == 0
    assert main(["add", "--root", root, "--private", state, str(build_collection(tmp_path / "a.tar.gz"))]) == 0
    assert (tmp_path / "state" / "collections.json").exists()
    assert not (tmp_path / "index" / "private").exists()
