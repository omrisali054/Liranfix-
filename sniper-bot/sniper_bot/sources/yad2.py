"""Yad2 Market (www.yad2.co.il/market) — Israel's biggest second-hand board.

The search page is a Next.js app that embeds its results as JSON in
``<script id="__NEXT_DATA__">``, so no browser is needed. Server-side the search
honours ``q``, ``maxPrice`` and ``sortOption=newest`` (a search without ``q``
fails with HTTP 500). Conditions and product types are filtered locally from
the ids each item carries.

Useful ids (``condition.id`` / ``productType.id`` in the feed):
    conditions:     2 = כמו חדש, 3 = משומש, 4 = דרוש תיקון/שיפוץ
    product types:  328 = מחשבים ניידים, 267 = טלפונים ניידים, 4745 = טאבלטים,
                    1294 = קונסולות משחק
"""

from __future__ import annotations

import html
import json
import logging
import re
from typing import Any

from ..config import SearchConfig
from ..fetch import BlockedError, HttpClient, SourceError
from ..models import Listing

log = logging.getLogger(__name__)

SEARCH_URL = "https://www.yad2.co.il/market/search"
ITEM_URL = "https://www.yad2.co.il/market/item/{id}"

_NEXT_DATA = re.compile(r'<script id="__NEXT_DATA__" type="application/json"[^>]*>(.*?)</script>', re.S)
_META_DESCRIPTION = re.compile(r'<meta name="description" content="([^"]*)"', re.I)


def extract_next_data(page: str) -> dict[str, Any]:
    match = _NEXT_DATA.search(page)
    if not match:
        raise BlockedError("Yad2 returned a page without listing data (captcha / bot check?)")
    try:
        return json.loads(match.group(1))
    except json.JSONDecodeError as exc:
        raise SourceError(f"Yad2 listing data is not valid JSON: {exc}") from exc


def search_params(search: SearchConfig, page: int = 1) -> dict[str, Any]:
    params: dict[str, Any] = {"q": search.query, "sortOption": "newest", "maxPrice": search.max_price}
    if page > 1:
        params["page"] = page
    return params


def _text(obj: Any, key: str = "textHeb") -> str:
    return str(obj.get(key) or "") if isinstance(obj, dict) else ""


def _price(value: Any) -> int | None:
    try:
        price = int(value)
    except (TypeError, ValueError):
        return None
    return price or None  # Yad2 uses 0 for "no price stated"


def _to_listing(item: dict[str, Any]) -> Listing | None:
    item_id = item.get("id")
    if item_id is None:
        return None
    condition = item.get("condition") or {}
    product_type = item.get("productType") or {}
    images = item.get("images") or []
    return Listing(
        source="yad2",
        listing_id=str(item_id),
        title=str(item.get("title") or "").strip(),
        url=ITEM_URL.format(id=item_id),
        price=_price(item.get("price")),
        previous_price=_price(item.get("previousPrice")),
        description=str(item.get("description") or ""),
        city=_text((item.get("address") or {}).get("city")),
        condition=_text(condition),
        condition_id=str(condition.get("id") or ""),
        product_type=_text(product_type),
        product_type_id=str(product_type.get("id") or ""),
        image_url=images[0] if images and isinstance(images[0], str) else "",
        is_business=bool(item.get("isSMB")) or bool(item.get("agencyName")),
    )


def parse_search_page(page: str) -> list[Listing]:
    props = extract_next_data(page).get("props", {}).get("pageProps", {})
    items = (props.get("initialFeed") or {}).get("items")
    if items is None:  # fall back to the react-query cache
        for query in (props.get("dehydratedState") or {}).get("queries", []):
            pages = ((query.get("state") or {}).get("data") or {}).get("pages") or []
            if pages and "items" in pages[0]:
                items = [it for p in pages for it in p.get("items", [])]
                break
    if items is None:
        raise SourceError("Yad2 search page layout changed: no items found")
    return [listing for listing in map(_to_listing, items) if listing]


def parse_item_description(page: str) -> str:
    try:
        item = extract_next_data(page).get("props", {}).get("pageProps", {}).get("item") or {}
        if item.get("description"):
            return str(item["description"])
    except SourceError:
        pass
    match = _META_DESCRIPTION.search(page)
    return html.unescape(match.group(1)) if match else ""


class Yad2Source:
    name = "yad2"

    def __init__(self, http: HttpClient):
        self._http = http

    def search(self, search: SearchConfig) -> list[Listing]:
        listings: list[Listing] = []
        seen: set[str] = set()
        for page in range(1, search.pages + 1):
            try:
                found = parse_search_page(self._http.get_text(SEARCH_URL, search_params(search, page)))
            except SourceError:
                if page == 1:
                    raise
                log.warning("yad2: page %d of %r failed, using the first %d", page, search.name, page - 1)
                break
            fresh = [listing for listing in found if listing.listing_id not in seen]
            if not fresh:
                break
            seen.update(listing.listing_id for listing in fresh)
            listings.extend(fresh)
        return listings

    def enrich(self, listing: Listing) -> None:
        page = self._http.get_text(ITEM_URL.format(id=listing.listing_id))
        listing.description = parse_item_description(page) or listing.description
