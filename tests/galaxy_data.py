# Copyright: (c) 2026, Jordan Borean (@jborean93) <jborean93@gmail.com>
# MIT License (see LICENSE or https://opensource.org/licenses/MIT)

"""A local server answering as a Galaxy server's v3 API does, so tests never reach the network."""

from __future__ import annotations

import collections.abc as _c
import hashlib
import json
import re
import threading
import typing as _t
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


class Version(_t.TypedDict):
    data: bytes
    created_at: str
    sha256: str
    """What the version's page says its artifact's sha256 is."""
    gone: bool
    """Listed, but its own page is a 404, as a version deleted while a listing is read."""


class Collection(_t.TypedDict):
    deprecated: bool
    updated_at: str
    versions: dict[str, Version]


class MockGalaxy:
    """Galaxy's v3 API under ``prefix``, two items to a page, so paging is always followed.

    ``status`` answers a path, or the start of one, with that HTTP status instead. ``cut`` cuts the body of that many
    answers to a path, or the start of one, short, as a dropped connection does.
    """

    def __init__(self, prefix: str = "/api/") -> None:
        self.prefix = prefix
        self.collections: dict[str, Collection] = {}
        self.status: dict[str, int] = {}
        self.cut: dict[str, int] = {}
        self.requests: list[str] = []
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
        self._thread = threading.Thread(target=self._server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
        self._thread.start()

    @property
    def url(self) -> str:
        """The server, as ansible-galaxy would be given it."""
        return f"http://127.0.0.1:{self._server.server_address[1]}{self.prefix.removesuffix('api/')}"

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()

    def add(self, name: str, version: str, tarball: Path, created_at: str = "2026-01-01T00:00:00.123456Z") -> Version:
        data = tarball.read_bytes()
        entry: Version = {
            "data": data,
            "created_at": created_at,
            "sha256": hashlib.sha256(data).hexdigest(),
            "gone": False,
        }
        found = self.collections.setdefault(name, {"deprecated": False, "updated_at": created_at, "versions": {}})
        found["versions"][version] = entry
        return entry

    def _page(self, query: dict[str, list[str]], items: list[dict[str, object]], path: str) -> dict[str, object]:
        offset = int(query.get("offset", ["0"])[0])
        size = min(int(query.get("limit", ["100"])[0]), 2)
        more = offset + size < len(items)
        return {
            "meta": {"count": len(items)},
            "links": {"next": f"{path}?limit={size}&offset={offset + size}" if more else None},
            "data": items[offset : offset + size],
        }

    def _answer(self, path: str, query: dict[str, list[str]]) -> tuple[int, bytes]:
        for start, code in self.status.items():
            if path.startswith(start):
                return code, b"{}"
        if path == "/":
            return 200, b"<html>Not the API</html>"
        if path == self.prefix:
            return 200, json.dumps({"available_versions": {"v3": "v3/"}}).encode()
        if match := re.fullmatch(r"/download/(.+)\.tar\.gz", path):
            name, version = match.group(1).split("--")
            return 200, self.collections[name]["versions"][version]["data"]
        v3 = f"{self.prefix}v3/collections/"
        if not path.startswith(v3):
            return 404, b"{}"
        parts = [urllib.parse.unquote(p) for p in path[len(v3) :].split("/") if p]
        if not parts:
            items: list[dict[str, object]] = [
                {"namespace": n.split(".")[0], "name": n.split(".")[1], "deprecated": c["deprecated"]}
                for n, c in sorted(self.collections.items())
            ]
            return 200, json.dumps(self._page(query, items, path)).encode()
        found = self.collections.get(f"{parts[0]}.{parts[1]}")
        if len(parts) == 2:
            if found is None:
                return 404, b"{}"
            return 200, json.dumps({"deprecated": found["deprecated"], "updated_at": found["updated_at"]}).encode()
        versions = found["versions"] if found else {}
        if len(parts) == 3:
            items = [{"version": v, "created_at": e["created_at"]} for v, e in versions.items()]
            return 200, json.dumps(self._page(query, items, path)).encode()
        entry = versions.get(parts[3])
        if entry is None or entry["gone"]:
            return 404, b"{}"
        detail = {
            "version": parts[3],
            "created_at": entry["created_at"],
            "download_url": f"/download/{parts[0]}.{parts[1]}--{parts[3]}.tar.gz",
            "artifact": {"sha256": entry["sha256"]},
        }
        return 200, json.dumps(detail).encode()

    def _handler(self) -> type[BaseHTTPRequestHandler]:
        galaxy = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                url = urllib.parse.urlsplit(self.path)
                galaxy.requests.append(url.path)
                code, body = galaxy._answer(url.path, urllib.parse.parse_qs(url.query))
                cut = next((start for start, left in galaxy.cut.items() if left and url.path.startswith(start)), None)
                if cut is not None:
                    galaxy.cut[cut] -= 1
                self.send_response(code)
                self.send_header("Content-Length", str(len(body) + (1 if cut is not None else 0)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, format: str, *args: object) -> None:  # noqa: A002
                pass

        return Handler


def requests_for(galaxy: MockGalaxy, part: str) -> _c.Iterator[str]:
    """The paths requested that have ``part`` in them."""
    return (p for p in galaxy.requests if part in p)
