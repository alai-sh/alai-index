# Copyright: (c) 2026, Jordan Borean (@jborean93) <jborean93@gmail.com>
# MIT License (see LICENSE or https://opensource.org/licenses/MIT)

from __future__ import annotations

import collections.abc as _c
import hashlib
import json
from pathlib import Path

import pytest
from collection_data import build_collection, module
from galaxy_data import MockGalaxy, requests_for

from alai_index import _index as index
from alai_index._cli import main
from alai_index._galaxy import GalaxyClient


def _lines(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_bytes().splitlines()]


def _results(changes: list[index.Change]) -> list[tuple[str, str | None, str]]:
    return [(c.name, c.version, c.result) for c in changes]


@pytest.fixture
def galaxy() -> _c.Iterator[MockGalaxy]:
    server = MockGalaxy()
    yield server
    server.close()


@pytest.fixture
def client(galaxy: MockGalaxy) -> GalaxyClient:
    return GalaxyClient(galaxy.url, user_agent="test", retries=1, backoff=0)


@pytest.fixture
def root(tmp_path: Path) -> Path:
    root = tmp_path / "index"
    index.configure(root)
    return root


def _publish(galaxy: MockGalaxy, tmp_path: Path, name: str, version: str, created: str, **kwargs: object) -> None:
    path = tmp_path / f"{name}-{version}.tar.gz"
    galaxy.add(name, version, build_collection(path, name, version, **kwargs), created)  # pyright: ignore[reportArgumentType]


def test_mirrors_new_versions(tmp_path: Path, root: Path, galaxy: MockGalaxy, client: GalaxyClient) -> None:
    # Listed newest first, as Galaxy lists them, but mirrored in publication order.
    _publish(galaxy, tmp_path, "ns.coll", "2.0.0", "2026-02-01T10:00:00.5Z", modules={"b": module("B")})
    _publish(galaxy, tmp_path, "ns.coll", "1.0.0", "2026-01-01T10:00:00.5Z", modules={"a": module("A")})
    _publish(galaxy, tmp_path, "ns.coll", "3.0.0", "2026-03-01T10:00:00.5Z")
    tree = index.Index(root)

    assert _results(tree.galaxy_mirror(client, ["ns.coll"])) == [
        ("ns.coll", "1.0.0", "added"),
        ("ns.coll", "2.0.0", "added"),
        ("ns.coll", "3.0.0", "added"),
    ]
    lines = _lines(root / "index" / "ns" / "coll.jsonl")
    assert [(line["vers"], line["published"]) for line in lines] == [
        ("1.0.0", "2026-01-01T10:00:00Z"),
        ("2.0.0", "2026-02-01T10:00:00Z"),
        ("3.0.0", "2026-03-01T10:00:00Z"),
    ]
    assert [(p["name"], p["since"], p.get("until")) for p in _lines(root / "plugins.jsonl")] == [
        ("a", "1.0.0", "2.0.0"),
        ("b", "2.0.0", "3.0.0"),
    ]


def test_failed_extraction_is_in_the_result(
    tmp_path: Path, root: Path, galaxy: MockGalaxy, client: GalaxyClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail(path: object) -> None:
        raise ValueError("boom")

    monkeypatch.setattr("alai_core.extract.extract", fail)
    _publish(galaxy, tmp_path, "ns.coll", "1.0.0", "2026-01-01T00:00:00Z")

    [change] = index.Index(root).galaxy_mirror(client, ["ns.coll"])
    assert (change.result, (change.details or {}).get("error")) == ("added", "ValueError: boom")


def test_nothing_to_do_asks_for_no_details(
    tmp_path: Path, root: Path, galaxy: MockGalaxy, client: GalaxyClient
) -> None:
    _publish(galaxy, tmp_path, "ns.coll", "1.0.0", "2026-01-01T00:00:00Z")
    index.Index(root).galaxy_mirror(client, ["ns.coll"])
    galaxy.requests.clear()

    assert index.Index(root).galaxy_mirror(client, ["ns.coll"]) == []
    assert list(requests_for(galaxy, "/versions/1.0.0/")) == []


def test_all(tmp_path: Path, root: Path, galaxy: MockGalaxy, client: GalaxyClient) -> None:
    for name in ("ns.a", "ns.b", "ns.c"):
        _publish(galaxy, tmp_path, name, "1.0.0", "2026-01-01T00:00:00Z")
    index.Index(root).galaxy_mirror(client, everything=True)
    del galaxy.collections["ns.b"]

    # One the index has and the server no longer lists is still asked about, and its 404 yanks it.
    assert index.Index(root).galaxy_mirror(client, everything=True) == [
        index.Change("ns.b", "1.0.0", "changed", {"yanked": True})
    ]


def test_yanked_only_on_its_own_404(tmp_path: Path, root: Path, galaxy: MockGalaxy, client: GalaxyClient) -> None:
    _publish(galaxy, tmp_path, "ns.coll", "1.0.0", "2026-01-01T00:00:00Z")
    _publish(galaxy, tmp_path, "ns.coll", "2.0.0", "2026-02-01T00:00:00Z")
    index.Index(root).galaxy_mirror(client, ["ns.coll"])
    entry = galaxy.collections["ns.coll"]["versions"].pop("1.0.0")

    # Left out of the listing, but its page still answers, as for a delete while the listing was read.
    galaxy.status["/api/v3/collections/ns/coll/versions/1.0.0/"] = 200
    assert index.Index(root).galaxy_mirror(client, ["ns.coll"]) == []
    del galaxy.status["/api/v3/collections/ns/coll/versions/1.0.0/"]
    galaxy.status["/api/v3/collections/ns/coll/versions/1.0.0/"] = 503
    assert _results(index.Index(root).galaxy_mirror(client, ["ns.coll"])) == [("ns.coll", "1.0.0", "failed")]
    del galaxy.status["/api/v3/collections/ns/coll/versions/1.0.0/"]

    assert index.Index(root).galaxy_mirror(client, ["ns.coll"]) == [
        index.Change("ns.coll", "1.0.0", "changed", {"yanked": True})
    ]
    assert [line["yanked"] for line in _lines(root / "index" / "ns" / "coll.jsonl")] == [True, False]

    # Back, with the same artifact: checked and unyanked.
    galaxy.collections["ns.coll"]["versions"]["1.0.0"] = entry
    sha256 = _lines(root / "index" / "ns" / "coll.jsonl")[0]["sha256"]
    assert index.Index(root).galaxy_mirror(client, ["ns.coll"]) == [
        index.Change("ns.coll", "1.0.0", "unchanged", {"sha256": sha256, "published": "2026-01-01T00:00:00Z"}),
        index.Change("ns.coll", "1.0.0", "changed", {"yanked": False}),
    ]


def test_collection_gone(tmp_path: Path, root: Path, galaxy: MockGalaxy, client: GalaxyClient) -> None:
    _publish(galaxy, tmp_path, "ns.coll", "1.0.0", "2026-01-01T00:00:00Z")
    _publish(galaxy, tmp_path, "ns.coll", "2.0.0", "2026-02-01T00:00:00Z")
    index.Index(root).galaxy_mirror(client, ["ns.coll"])
    del galaxy.collections["ns.coll"]

    assert index.Index(root).galaxy_mirror(client, ["ns.coll"]) == [
        index.Change("ns.coll", "1.0.0", "changed", {"yanked": True}),
        index.Change("ns.coll", "2.0.0", "changed", {"yanked": True}),
    ]


def test_conflict_is_never_written(tmp_path: Path, root: Path, galaxy: MockGalaxy, client: GalaxyClient) -> None:
    _publish(galaxy, tmp_path, "ns.coll", "1.0.0", "2026-01-01T00:00:00Z")
    index.Index(root).galaxy_mirror(client, ["ns.coll"])
    before = (root / "index" / "ns" / "coll.jsonl").read_bytes()
    galaxy.collections["ns.coll"]["versions"]["1.0.0"]["sha256"] = "ab" * 32

    # Not checked until something suggests it changed.
    assert index.Index(root).galaxy_mirror(client, ["ns.coll"]) == []
    changes = index.Index(root).galaxy_mirror(client, ["ns.coll"], revalidate=True)
    details: dict[str, object] = {
        "sha256": "ab" * 32,
        "url": "/download/ns.coll--1.0.0.tar.gz",
        "published": "2026-01-01T00:00:00Z",
    }
    assert changes == [index.Change("ns.coll", "1.0.0", "conflict", details)]
    assert (root / "index" / "ns" / "coll.jsonl").read_bytes() == before

    # A re-upload, its created_at moved, is checked without being asked.
    galaxy.collections["ns.coll"]["versions"]["1.0.0"]["created_at"] = "2026-05-01T00:00:00Z"
    [change] = index.Index(root).galaxy_mirror(client, ["ns.coll"])
    assert (change.result, (change.details or {})["published"]) == ("conflict", "2026-05-01T00:00:00Z")


def test_conflict_keeps_a_version_yanked(tmp_path: Path, root: Path, galaxy: MockGalaxy, client: GalaxyClient) -> None:
    _publish(galaxy, tmp_path, "ns.coll", "1.0.0", "2026-01-01T00:00:00Z")
    index.Index(root).galaxy_mirror(client, ["ns.coll"])
    index.Index(root).set(["ns.coll"], yanked=True)
    galaxy.collections["ns.coll"]["versions"]["1.0.0"]["sha256"] = "ab" * 32

    # Listed again, but with another artifact, so the index's isn't put back on offer.
    assert _results(index.Index(root).galaxy_mirror(client, ["ns.coll"])) == [("ns.coll", "1.0.0", "conflict")]
    assert [line["yanked"] for line in _lines(root / "index" / "ns" / "coll.jsonl")] == [True]


def test_collection_nowhere_warns(root: Path, galaxy: MockGalaxy, client: GalaxyClient) -> None:
    tree = index.Index(root)

    assert tree.galaxy_mirror(client, ["ns.typo"]) == []
    assert tree.warnings == ["ns.typo isn't on the server or in the index"]


def test_only_http_urls_are_fetched(
    tmp_path: Path, root: Path, galaxy: MockGalaxy, client: GalaxyClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _publish(galaxy, tmp_path, "ns.coll", "1.0.0", "2026-01-01T00:00:00Z")
    answer = galaxy._answer

    def file_url(path: str, query: dict[str, list[str]]) -> tuple[int, bytes]:
        code, body = answer(path, query)
        if path.endswith("/versions/1.0.0/"):
            body = json.dumps(json.loads(body) | {"download_url": "file:///etc/hostname"}).encode()
        return code, body

    monkeypatch.setattr(galaxy, "_answer", file_url)
    [change] = index.Index(root).galaxy_mirror(client, ["ns.coll"])
    assert (change.result, str((change.details or {})["error"])) == (
        "failed",
        "file:///etc/hostname: isn't an http or https URL",
    )


def test_checked_isnt_checked_again(tmp_path: Path, root: Path, galaxy: MockGalaxy, client: GalaxyClient) -> None:
    _publish(galaxy, tmp_path, "ns.coll", "1.0.0", "2026-01-01T00:00:00Z")
    index.Index(root).galaxy_mirror(client, ["ns.coll"])
    version = galaxy.collections["ns.coll"]["versions"]["1.0.0"]
    version["sha256"], version["created_at"] = "ab" * 32, "2026-05-01T00:00:00Z"
    checked = tmp_path / "checked.json"

    # Reported once, then left while the caller says it already checked that created_at.
    [change] = index.Index(root).galaxy_mirror(client, ["ns.coll"])
    assert change.result == "conflict"
    checked.write_text(json.dumps({"ns.coll": {"1.0.0": "2026-05-01T00:00:00Z"}}))
    galaxy.requests.clear()
    assert index.Index(root).galaxy_mirror(client, ["ns.coll"], checked=checked) == []
    assert not [r for r in galaxy.requests if r.endswith("/versions/1.0.0/")]

    # Served as ours again, with its old created_at, which differs from the one checked.
    sha256 = str(_lines(root / "index" / "ns" / "coll.jsonl")[0]["sha256"])
    version["sha256"], version["created_at"] = sha256, "2026-01-01T00:00:00Z"
    assert index.Index(root).galaxy_mirror(client, ["ns.coll"], checked=checked) == [
        index.Change("ns.coll", "1.0.0", "unchanged", {"sha256": sha256, "published": "2026-01-01T00:00:00Z"})
    ]


@pytest.mark.parametrize("content", [None, "not json", "[]", '{"ns.coll": {"1.0.0": 1}}'])
def test_checked_file_has_to_be_right(tmp_path: Path, root: Path, client: GalaxyClient, content: str | None) -> None:
    checked = tmp_path / "checked.json"
    if content is not None:
        checked.write_text(content)
    with pytest.raises(index.Error, match=r"checked\.json"):
        index.Index(root).galaxy_mirror(client, ["ns.coll"], checked=checked)


def test_lost_plugin_cache_is_made_again_from_artifacts_on_disk(
    tmp_path: Path, root: Path, galaxy: MockGalaxy, client: GalaxyClient
) -> None:
    _publish(galaxy, tmp_path, "ns.coll", "1.0.0", "2026-01-01T00:00:00Z", modules={"a": module("A")})
    index.Index(root).galaxy_mirror(client, ["ns.coll"])
    (root / "plugin-cache" / "ns" / "coll.jsonl").unlink()

    # Nothing changed on the server, but the collection was looked at, and its artifact is on disk.
    assert _results(index.Index(root).galaxy_mirror(client, ["ns.coll"])) == [("ns.coll", "1.0.0", "extracted")]
    assert [p["name"] for p in _lines(root / "plugins.jsonl")] == ["a"]

    # Without its artifact, it's reported for the caller to fetch.
    (root / "plugin-cache" / "ns" / "coll.jsonl").unlink()
    for artifact in (root / "dl").glob("*/*"):
        artifact.unlink()
    tree = index.Index(root)
    assert tree.galaxy_mirror(client, ["ns.coll"]) == []
    assert [(o["name"], o["version"], "index" in o) for o in tree.files["outdated"]] == [("ns.coll", "1.0.0", True)]


def test_deprecation(tmp_path: Path, root: Path, galaxy: MockGalaxy, client: GalaxyClient) -> None:
    _publish(galaxy, tmp_path, "ns.coll", "1.0.0", "2026-01-01T00:00:00Z")
    _publish(galaxy, tmp_path, "ns.coll", "2.0.0", "2026-02-01T00:00:00Z")
    index.Index(root).galaxy_mirror(client, ["ns.coll"])

    galaxy.collections["ns.coll"]["deprecated"] = True
    assert _results(index.Index(root).galaxy_mirror(client, ["ns.coll"])) == [
        ("ns.coll", "1.0.0", "changed"),
        ("ns.coll", "2.0.0", "changed"),
    ]
    assert [line.get("deprecated") for line in _lines(root / "index" / "ns" / "coll.jsonl")] == [True, True]
    galaxy.collections["ns.coll"]["deprecated"] = False
    index.Index(root).galaxy_mirror(client, ["ns.coll"])
    assert [line.get("deprecated") for line in _lines(root / "index" / "ns" / "coll.jsonl")] == [None, None]


def test_limit(tmp_path: Path, root: Path, galaxy: MockGalaxy, client: GalaxyClient) -> None:
    for i in range(3):
        _publish(galaxy, tmp_path, "ns.a", f"{i}.0.0", f"2026-01-0{i + 1}T00:00:00Z")
    _publish(galaxy, tmp_path, "ns.b", "1.0.0", "2026-01-01T00:00:00Z")

    assert _results(index.Index(root).galaxy_mirror(client, ["ns.a", "ns.b"], limit=2)) == [
        ("ns.a", "0.0.0", "added"),
        ("ns.a", "1.0.0", "added"),
        ("ns.a", "2.0.0", "deferred"),
        ("ns.b", None, "deferred"),
    ]
    # What was done is written, so the next run carries on from it.
    assert [line["vers"] for line in _lines(root / "index" / "ns" / "a.jsonl")] == ["0.0.0", "1.0.0"]
    assert _results(index.Index(root).galaxy_mirror(client, ["ns.a", "ns.b"], limit=2)) == [
        ("ns.a", "2.0.0", "added"),
        ("ns.b", "1.0.0", "added"),
    ]


def test_failures_dont_stop_the_rest(tmp_path: Path, root: Path, galaxy: MockGalaxy, client: GalaxyClient) -> None:
    for i, version in enumerate(("1.0.0", "2.0.0", "3.0.0", "4.0.0", "5.0.0")):
        _publish(galaxy, tmp_path, "ns.coll", version, f"2026-01-0{i + 1}T00:00:00Z")
    versions = galaxy.collections["ns.coll"]["versions"]
    galaxy.status["/download/ns.coll--1.0.0"] = 503
    versions["2.0.0"]["sha256"] = "ab" * 32
    versions["3.0.0"]["data"] = (tmp_path / "ns.coll-4.0.0.tar.gz").read_bytes()  # MANIFEST.json says 4.0.0.
    versions["3.0.0"]["sha256"] = versions["4.0.0"]["sha256"]
    versions["4.0.0"]["data"] = b"not a tarball"
    versions["4.0.0"]["sha256"] = hashlib.sha256(b"not a tarball").hexdigest()
    tree = index.Index(root)

    changes = tree.galaxy_mirror(client, ["ns.coll"])
    assert [(c.version, c.result, (c.details or {}).get("kind")) for c in changes] == [
        ("1.0.0", "failed", "galaxy_unavailable"),
        ("2.0.0", "failed", "sha256_mismatch"),
        ("3.0.0", "failed", "manifest_mismatch"),
        ("4.0.0", "failed", "bad_artifact"),
        ("5.0.0", "added", None),
    ]
    assert [line["vers"] for line in _lines(root / "index" / "ns" / "coll.jsonl")] == ["5.0.0"]
    assert len(tree.warnings) == 4


def test_a_body_cut_short_is_tried_again(tmp_path: Path, root: Path, galaxy: MockGalaxy) -> None:
    _publish(galaxy, tmp_path, "ns.coll", "1.0.0", "2026-01-01T00:00:00Z")
    galaxy.cut["/download/"] = 1
    client = GalaxyClient(galaxy.url, user_agent="test", retries=2, backoff=0)

    assert _results(index.Index(root).galaxy_mirror(client, ["ns.coll"])) == [("ns.coll", "1.0.0", "added")]
    assert len(list(requests_for(galaxy, "/download/"))) == 2


def test_a_detail_without_an_artifact_fails_only_its_version(
    tmp_path: Path, root: Path, galaxy: MockGalaxy, client: GalaxyClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _publish(galaxy, tmp_path, "ns.coll", "1.0.0", "2026-01-01T00:00:00Z")
    _publish(galaxy, tmp_path, "ns.coll", "2.0.0", "2026-01-02T00:00:00Z")
    answer = galaxy._answer

    def null_artifact(path: str, query: dict[str, list[str]]) -> tuple[int, bytes]:
        code, body = answer(path, query)
        if path.endswith("/versions/1.0.0/"):
            body = json.dumps(json.loads(body) | {"artifact": None}).encode()
        return code, body

    monkeypatch.setattr(galaxy, "_answer", null_artifact)
    changes = index.Index(root).galaxy_mirror(client, ["ns.coll"])
    assert [(c.version, c.result, (c.details or {}).get("kind")) for c in changes] == [
        ("1.0.0", "failed", "bad_detail"),
        ("2.0.0", "added", None),
    ]


def test_a_bad_index_is_refused_before_any_download(
    tmp_path: Path, root: Path, galaxy: MockGalaxy, client: GalaxyClient
) -> None:
    _publish(galaxy, tmp_path, "ns.a", "1.0.0", "2026-01-01T00:00:00Z")
    _publish(galaxy, tmp_path, "ns.b", "1.0.0", "2026-01-01T00:00:00Z")
    index.Index(root).galaxy_mirror(client, ["ns.b"])
    (root / "index" / "ns" / "b.jsonl").write_bytes(b"")
    galaxy.requests.clear()

    with pytest.raises(index.Error, match=r"for ns\.b, its sha256 differs"):
        index.Index(root).galaxy_mirror(client, ["ns.a", "ns.b"])
    assert galaxy.requests == []
    assert not (root / "index" / "ns" / "a.jsonl").exists()


def test_copied_as_published(tmp_path: Path, root: Path, galaxy: MockGalaxy, client: GalaxyClient) -> None:
    _publish(galaxy, tmp_path, "ns.coll", "1.0.0", "2026-01-01T00:00:00Z", dependencies={"ns.missing": "^1.0"})
    tree = index.Index(root)

    assert _results(tree.galaxy_mirror(client, ["ns.coll"])) == [("ns.coll", "1.0.0", "added")]
    assert _lines(root / "index" / "ns" / "coll.jsonl")[0]["deps"] == [{"name": "ns.missing", "req": "^1.0"}]
    assert tree.warnings


def test_api_under_a_prefix(tmp_path: Path, root: Path) -> None:
    # As Artifactory serves a repository, with available_versions at the URL ansible-galaxy is given.
    server = MockGalaxy(prefix="/artifactory/api/ansible/repo/")
    try:
        _publish(server, tmp_path, "ns.coll", "1.0.0", "2026-01-01T00:00:00Z")
        client = GalaxyClient(server.url, user_agent="test", retries=1, backoff=0)
        assert _results(index.Index(root).galaxy_mirror(client, ["ns.coll"])) == [("ns.coll", "1.0.0", "added")]
    finally:
        server.close()


def test_not_a_galaxy_server(tmp_path: Path, root: Path, galaxy: MockGalaxy) -> None:
    client = GalaxyClient(f"{galaxy.url}nothing/", user_agent="test", retries=1, backoff=0)
    _publish(galaxy, tmp_path, "ns.coll", "1.0.0", "2026-01-01T00:00:00Z")

    changes = index.Index(root).galaxy_mirror(client, ["ns.coll"])
    assert [(c.version, c.result) for c in changes] == [(None, "failed")]
    assert "isn't a Galaxy server with a v3 API" in str((changes[0].details or {})["error"])


@pytest.mark.parametrize(
    ("targets", "everything", "match"),
    [
        (["ns.coll"], True, "not both"),
        ([], False, "name collections, or --all"),
        (["ns.coll:1.0.0"], False, "takes collections, not versions"),
    ],
)
def test_bad_options(root: Path, client: GalaxyClient, targets: list[str], everything: bool, match: str) -> None:
    with pytest.raises(index.Error, match=match):
        index.Index(root).galaxy_mirror(client, targets, everything=everything)


def test_cli(tmp_path: Path, root: Path, galaxy: MockGalaxy, capsys: pytest.CaptureFixture[str]) -> None:
    _publish(galaxy, tmp_path, "ns.coll", "1.0.0", "2026-01-01T00:00:00Z")
    _publish(galaxy, tmp_path, "ns.coll", "2.0.0", "2026-02-01T00:00:00Z")
    galaxy.status["/download/ns.coll--2.0.0"] = 404
    argv = ["galaxy-mirror", "--root", str(root), "--output-format", "json", galaxy.url, "ns.coll"]
    capsys.readouterr()

    assert main(argv) == 0
    out = json.loads(capsys.readouterr().out)
    assert [(v["version"], v["result"]) for v in out["versions"]] == [("1.0.0", "added"), ("2.0.0", "failed")]
    assert out["versions"][1]["details"]["kind"] == "galaxy_http_404"
    assert main([*argv, "--strict"]) == 1


def _three_versions(galaxy: MockGalaxy, tmp_path: Path) -> None:
    for i in range(3):
        _publish(galaxy, tmp_path, "ns.coll", f"{i}.0.0", f"2026-01-0{i + 1}T00:00:00Z")


def _listed(galaxy: MockGalaxy) -> list[str]:
    """The requests for the collection's page or its versions."""
    return [p for p in galaxy.requests if p.endswith(("/ns/coll/", "/ns/coll/versions/"))]


def test_session_carries_on(tmp_path: Path, root: Path, galaxy: MockGalaxy, client: GalaxyClient) -> None:
    _three_versions(galaxy, tmp_path)
    session = tmp_path / "session.json"
    tree = index.Index(root)
    tree.galaxy_mirror(client, ["ns.coll"], limit=1, session=session)
    assert not [p for p in tree.files["written"]["plugin-cache"] if "session" in p]  # Not part of the tree.
    galaxy.requests.clear()

    # Only downloads: neither the collection's page nor its versions are asked for again.
    assert _results(index.Index(root).galaxy_mirror(client, ["ns.coll"], limit=1, session=session)) == [
        ("ns.coll", "1.0.0", "added"),
        ("ns.coll", "2.0.0", "deferred"),
    ]
    assert _listed(galaxy) == []

    # Done, so the session has nothing left for it, and the next run asks the server again.
    index.Index(root).galaxy_mirror(client, ["ns.coll"], limit=1, session=session)
    galaxy.requests.clear()
    index.Index(root).galaxy_mirror(client, ["ns.coll"], session=session)
    assert _listed(galaxy)


def test_revalidate_lists_again(tmp_path: Path, root: Path, galaxy: MockGalaxy, client: GalaxyClient) -> None:
    _three_versions(galaxy, tmp_path)
    session = tmp_path / "session.json"
    index.Index(root).galaxy_mirror(client, ["ns.coll"], limit=1, session=session)
    galaxy.requests.clear()

    index.Index(root).galaxy_mirror(client, ["ns.coll"], limit=1, revalidate=True, session=session)
    assert _listed(galaxy)


def test_not_a_session_file(tmp_path: Path, root: Path, client: GalaxyClient) -> None:
    session = tmp_path / "session.json"
    session.write_text("{}")

    with pytest.raises(index.Error, match="isn't a galaxy-mirror session file"):
        index.Index(root).galaxy_mirror(client, ["ns.coll"], session=session)


def test_carrying_on_leaves_checks_to_the_next_listing(
    tmp_path: Path, root: Path, galaxy: MockGalaxy, client: GalaxyClient
) -> None:
    _three_versions(galaxy, tmp_path)
    session = tmp_path / "session.json"
    index.Index(root).galaxy_mirror(client, ["ns.coll"], limit=1, session=session)
    galaxy.collections["ns.coll"]["deprecated"] = True

    # Not seen until the collection is done and the server is asked again.
    index.Index(root).galaxy_mirror(client, ["ns.coll"], limit=1, session=session)
    assert not any(line.get("deprecated") for line in _lines(root / "index" / "ns" / "coll.jsonl"))
    index.Index(root).galaxy_mirror(client, ["ns.coll"], limit=1, session=session)
    index.Index(root).galaxy_mirror(client, ["ns.coll"], session=session)
    assert all(line.get("deprecated") for line in _lines(root / "index" / "ns" / "coll.jsonl"))


def test_progress(tmp_path: Path, root: Path, galaxy: MockGalaxy, capsys: pytest.CaptureFixture[str]) -> None:
    _publish(galaxy, tmp_path, "ns.coll", "1.0.0", "2026-01-01T00:00:00Z")
    capsys.readouterr()

    assert main(["galaxy-mirror", "--root", str(root), galaxy.url, "ns.coll"]) == 0
    assert capsys.readouterr().err.splitlines() == [
        "ns.coll, asking the server for its versions",
        "ns.coll has 1 version on the server, 1 not in the index yet",
        "downloading ns.coll 1.0.0, 1 of 1",
        "writing the changes to 1 collection",
    ]


def test_quiet_and_verbose(tmp_path: Path, root: Path, galaxy: MockGalaxy, capsys: pytest.CaptureFixture[str]) -> None:
    _publish(galaxy, tmp_path, "ns.coll", "1.0.0", "2026-01-01T00:00:00Z", dependencies={"ns.missing": "*"})
    argv = ["galaxy-mirror", "--root", str(root), galaxy.url, "ns.coll"]
    capsys.readouterr()

    assert main([*argv, "-q"]) == 0
    assert capsys.readouterr().err == ""  # Neither progress nor the warning.

    assert main([*argv, "-v"]) == 0
    err = capsys.readouterr().err.splitlines()
    assert any(line.startswith("debug: GET http://") and "/ns/coll/versions/" in line for line in err)

    with pytest.raises(SystemExit):
        main([*argv, "-q", "-v"])
    assert "can't be used together" in capsys.readouterr().err


def test_retry_waits_are_shown(
    tmp_path: Path, root: Path, galaxy: MockGalaxy, caplog: pytest.LogCaptureFixture
) -> None:
    _publish(galaxy, tmp_path, "ns.coll", "1.0.0", "2026-01-01T00:00:00Z")
    galaxy.status["/download/"] = 503
    client = GalaxyClient(galaxy.url, user_agent="test", retries=2, backoff=0)

    with caplog.at_level("INFO", logger="alai_index"):
        index.Index(root).galaxy_mirror(client, ["ns.coll"])
    assert any("failed" in m and "so trying again in 0s" in m for m in caplog.messages)
