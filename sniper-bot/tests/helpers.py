from __future__ import annotations

import json
from pathlib import Path

import requests

from sniper_bot.config import Config, SearchConfig, Settings
from sniper_bot.fetch import SourceError
from sniper_bot.models import Listing
from sniper_bot.notifiers import Message, NotifyError

FIXTURES = Path(__file__).parent / "fixtures"


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def make_response(status: int = 200, body: bytes | str = b"", content_type: str = "text/html") -> requests.Response:
    resp = requests.Response()
    resp.status_code = status
    resp._content = body.encode("utf-8") if isinstance(body, str) else body
    resp.headers["Content-Type"] = content_type
    return resp


def make_search(**overrides) -> SearchConfig:
    values = dict(name="laptops", source="fake", query="מחשב נייד", max_price=800)
    values.update(overrides)
    return SearchConfig(**values)


def make_config(*searches: SearchConfig, **settings) -> Config:
    settings.setdefault("interval_seconds", 100)
    settings.setdefault("jitter_seconds", 0)
    return Config(settings=Settings(**settings), searches=list(searches) or [make_search()])


def make_listing(listing_id: str, price: int | None = 500, title: str = "מחשב נייד", **kw) -> Listing:
    return Listing(source="fake", listing_id=listing_id, title=title,
                   url=f"https://board.example/{listing_id}", price=price, **kw)


class FakeSource:
    name = "fake"

    def __init__(self, listings=None, descriptions=None):
        self.listings: list[Listing] = list(listings or [])
        self.descriptions: dict[str, str] = dict(descriptions or {})
        self.enriched: list[str] = []
        self.error: Exception | None = None

    def search(self, search):
        if self.error:
            raise self.error
        # hand out copies, like a fresh page download would
        return [Listing(**vars(listing)) for listing in self.listings]

    def enrich(self, listing):
        self.enriched.append(listing.listing_id)
        if listing.listing_id in self.descriptions:
            listing.description = self.descriptions[listing.listing_id]


class BrokenSource(FakeSource):
    def __init__(self):
        super().__init__()
        self.error = SourceError("boom")


class FakeNotifier:
    name = "fake"

    def __init__(self):
        self.messages: list[Message] = []
        self.fail = False

    def send(self, message: Message) -> None:
        if self.fail:
            raise NotifyError("offline")
        self.messages.append(message)


class FakeSession:
    """Records POSTs and replies with queued (status, json) pairs."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls: list[dict] = []
        self.headers: dict = {}

    def post(self, url, **kwargs):
        self.calls.append({"url": url, **kwargs})
        reply = self.replies.pop(0) if self.replies else (200, {"ok": True})
        if isinstance(reply, Exception):
            raise reply
        status, payload = reply
        return make_response(status, json.dumps(payload), "application/json")
