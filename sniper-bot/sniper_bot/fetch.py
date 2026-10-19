"""A polite HTTP client: browser-like headers, spacing between requests, retries."""

from __future__ import annotations

import logging
import time
from typing import Any, Callable

import requests

log = logging.getLogger(__name__)

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "he-IL,he;q=0.9,en-US;q=0.8,en;q=0.7",
}


class SourceError(RuntimeError):
    """A board could not be read this time."""


class BlockedError(SourceError):
    """The board answered with a captcha / bot check / rate limit."""


class HttpClient:
    def __init__(
        self,
        delay_seconds: float = 3.0,
        timeout: float = 20.0,
        retries: int = 2,
        session: requests.Session | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.delay_seconds = delay_seconds
        self.timeout = timeout
        self.retries = retries
        self._session = session or requests.Session()
        self._session.headers.update(DEFAULT_HEADERS)
        self._sleep = sleep
        self._clock = clock
        self._last_request: float | None = None

    def _wait_turn(self) -> None:
        if self._last_request is not None:
            remaining = self.delay_seconds - (self._clock() - self._last_request)
            if remaining > 0:
                self._sleep(remaining)
        self._last_request = self._clock()

    def get(self, url: str, params: dict[str, Any] | None = None) -> requests.Response:
        for attempt in range(self.retries + 1):
            last_try = attempt == self.retries
            self._wait_turn()
            try:
                resp = self._session.get(url, params=params, timeout=self.timeout)
            except requests.RequestException as exc:
                if last_try:
                    raise SourceError(f"GET {url}: {exc}") from exc
                log.debug("GET %s failed (%s), retrying", url, exc)
                self._sleep(5 * (attempt + 1))
                continue

            if resp.status_code == 403:
                raise BlockedError(f"GET {url}: HTTP 403 (blocked)")
            if resp.status_code == 429 or resp.status_code >= 500:
                if last_try:
                    error = BlockedError if resp.status_code == 429 else SourceError
                    raise error(f"GET {url}: HTTP {resp.status_code}")
                self._sleep(10 * (attempt + 1))
                continue
            if resp.status_code >= 400:
                raise SourceError(f"GET {url}: HTTP {resp.status_code}")
            return resp
        raise AssertionError("unreachable")

    def get_text(self, url: str, params: dict[str, Any] | None = None) -> str:
        resp = self.get(url, params)
        if "charset" not in resp.headers.get("Content-Type", "").lower():
            resp.encoding = "utf-8"  # requests would otherwise guess latin-1 and garble Hebrew
        return resp.text

    def get_bytes(self, url: str, params: dict[str, Any] | None = None) -> bytes:
        return self.get(url, params).content
