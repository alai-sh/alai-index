# Copyright: (c) 2026, Jordan Borean (@jborean93) <jborean93@gmail.com>
# MIT License (see LICENSE or https://opensource.org/licenses/MIT)

"""A client for a Galaxy server's v3 API, the endpoints ansible-galaxy itself uses, as galaxy.ansible.com, Private
Automation Hub and Artifactory serve them."""

from __future__ import annotations

import collections.abc as _c
import email.utils
import http.client
import json
import logging
import time
import typing as _t
import urllib.error
import urllib.parse
import urllib.request

RETRY_STATUS = frozenset({429, 500, 502, 503, 504})

log = logging.getLogger("alai_index")


def _retry_after(value: str | None) -> float | None:
    """Seconds from a Retry-After header, given as seconds or an HTTP date."""
    if not value:
        return None
    try:
        return max(float(value), 0)
    except ValueError:
        pass
    try:
        when = email.utils.parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    return max(when.timestamp() - time.time(), 0)


class GalaxyError(Exception):
    """A request failed. ``kind`` names it as a caller's failure ledger would."""

    def __init__(self, url: str, status: int | None, message: str) -> None:
        super().__init__(f"{url}: {status or ''} {message}".strip())
        self.url = url
        self.status = status

    @property
    def kind(self) -> str:
        """``galaxy_unavailable`` if trying again may help, else ``galaxy_http_<code>``."""
        if self.status is None or self.status in RETRY_STATUS:
            return "galaxy_unavailable"
        return f"galaxy_http_{self.status}"


class NotFound(GalaxyError):
    """A 404, which for a collection or version means the server no longer has it."""


class GalaxyClient:
    """One request at a time, retrying timeouts, 429s and 5xx with backoff and honouring Retry-After.

    ``server`` is the URL ansible-galaxy would be given. Its v3 API is found as ansible-galaxy finds it, from the
    ``available_versions`` the server, or its ``api/``, answers with.
    """

    def __init__(
        self,
        server: str,
        *,
        user_agent: str,
        timeout: float = 120,
        retries: int = 5,
        backoff: float = 5,
        max_wait: float = 300,
    ) -> None:
        self.server = server if server.endswith("/") else f"{server}/"
        self.user_agent = user_agent
        self.timeout = timeout
        self.retries = retries
        self.backoff = backoff
        """Seconds to wait before trying again, times the attempt."""
        self.max_wait = max_wait
        """The longest Retry-After to honour."""
        self._v3: str | None = None

    def _url(self, url: str) -> str:
        # Pulp's next links come back as http://, so a link to the server's own host keeps the server's scheme.
        parts, server = urllib.parse.urlsplit(url), urllib.parse.urlsplit(self.server)
        if parts.netloc == server.netloc and parts.scheme != server.scheme:
            return urllib.parse.urlunsplit((server.scheme, *parts[1:]))
        return url

    def _get(self, url: str) -> bytes:
        url = self._url(url)
        last: Exception | None = None
        for attempt in range(self.retries):
            # Only ever an http or https URL, from the server given or its answers.
            req = urllib.request.Request(url, headers={"User-Agent": self.user_agent})  # noqa: S310
            start = time.monotonic()
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:  # noqa: S310
                    if urllib.parse.urlsplit(url).scheme == "https" and not resp.geturl().startswith("https:"):
                        raise GalaxyError(url, None, f"redirected from https to {resp.geturl()}")
                    data = resp.read()
                    log.debug("GET %s returned %d bytes in %.1fs", url, len(data), time.monotonic() - start)
                    return data
            except urllib.error.HTTPError as e:
                # The error holds the response open, and only its status and headers are used.
                e.close()
                log.debug("GET %s returned %d in %.1fs", url, e.code, time.monotonic() - start)
                if e.code == 404:
                    raise NotFound(url, 404, "not found") from None
                if e.code not in RETRY_STATUS:
                    raise GalaxyError(url, e.code, e.reason) from None
                last = e
                wait = _retry_after(e.headers.get("Retry-After"))
                wait = self.backoff * (attempt + 1) if wait is None else min(wait, self.max_wait)
            # A body cut short is an HTTPException, and a TLS error is an OSError.
            except (OSError, http.client.HTTPException) as e:
                last = e
                wait = self.backoff * (attempt + 1)
            if attempt + 1 < self.retries:
                # Waits are what a run spends longest on when a server struggles, so they're always shown.
                log.info("GET %s failed, %s, so trying again in %.0fs", url, last, wait)
                time.sleep(wait)
        raise GalaxyError(url, None, f"gave up after {self.retries} attempts: {last}")

    def _json(self, url: str) -> dict[str, _t.Any]:
        try:
            data = json.loads(self._get(url))
        except ValueError as e:
            raise GalaxyError(url, None, f"not JSON: {e}") from None
        if not isinstance(data, dict):
            raise GalaxyError(url, None, "not a JSON object")
        return _t.cast("dict[str, _t.Any]", data)

    def _paged(self, url: str) -> _c.Iterator[dict[str, _t.Any]]:
        """Every item of a paged listing, following ``links.next``."""
        next_url: str | None = url
        while next_url:
            page = self._json(next_url)
            # Anything else is no item ansible-galaxy would read, so it's left out.
            yield from (item for item in page.get("data", page.get("results", [])) if isinstance(item, dict))
            links: dict[str, _t.Any] = page.get("links") or {}
            next_url = urllib.parse.urljoin(next_url, links["next"]) if links.get("next") else None

    @property
    def v3(self) -> str:
        """The v3 API's URL, found on first use."""
        if self._v3 is None:
            for url in (self.server, urllib.parse.urljoin(self.server, "api/")):
                try:
                    versions = self._json(url).get("available_versions")
                except GalaxyError:
                    continue
                v3 = _t.cast("dict[str, object]", versions).get("v3") if isinstance(versions, dict) else None
                if isinstance(v3, str):
                    self._v3 = urllib.parse.urljoin(url, v3)
                    break
            else:
                raise GalaxyError(self.server, None, "isn't a Galaxy server with a v3 API")
        return self._v3

    def collections(self) -> _c.Iterator[dict[str, _t.Any]]:
        """Every collection the server lists."""
        return self._paged(urllib.parse.urljoin(self.v3, "collections/?limit=100"))

    def collection(self, namespace: str, name: str) -> dict[str, _t.Any] | None:
        """A collection, or None if the server doesn't have it."""
        try:
            return self._json(urllib.parse.urljoin(self.v3, f"collections/{namespace}/{name}/"))
        except NotFound:
            return None

    def versions(self, namespace: str, name: str) -> list[dict[str, _t.Any]]:
        """Every version of a collection the server lists."""
        try:
            return list(
                self._paged(urllib.parse.urljoin(self.v3, f"collections/{namespace}/{name}/versions/?limit=100"))
            )
        except NotFound:
            return []

    def version(self, namespace: str, name: str, version: str) -> dict[str, _t.Any]:
        """A version's detail, with its ``download_url`` and ``artifact.sha256``.

        Raises:
            NotFound: The server doesn't have it.
        """
        quoted = urllib.parse.quote(version, safe="")
        return self._json(urllib.parse.urljoin(self.v3, f"collections/{namespace}/{name}/versions/{quoted}/"))

    def download(self, url: str) -> bytes:
        """An artifact, through any redirect, as to the signed URL galaxy.ansible.com gives."""
        return self._get(urllib.parse.urljoin(self.v3, url))
