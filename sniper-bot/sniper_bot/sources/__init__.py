"""Ad boards the bot can scan. Each source turns a search into ``Listing`` objects."""

from __future__ import annotations

from typing import Protocol

from ..config import SearchConfig
from ..fetch import HttpClient
from ..models import Listing


class Source(Protocol):
    name: str

    def search(self, search: SearchConfig) -> list[Listing]:
        """Newest listings for a search."""

    def enrich(self, listing: Listing) -> None:
        """Fill in details the search page lacks (the full description)."""


def build_sources(http: HttpClient) -> dict[str, Source]:
    from .rss import RssSource
    from .yad2 import Yad2Source

    return {"yad2": Yad2Source(http), "rss": RssSource(http)}
