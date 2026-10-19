"""Any board that publishes an RSS 2.0 or Atom feed.

Feeds rarely carry a structured price, so the price is read from the title or
description text ("400 ₪", "מחיר: 350"). Ads without a readable price are
skipped unless the search sets ``allow_no_price = true``.
"""

from __future__ import annotations

import hashlib
import re
import xml.etree.ElementTree as ET

from ..config import SearchConfig
from ..fetch import HttpClient, SourceError
from ..models import Listing
from ..text import parse_price, strip_html

_ATOM = "{http://www.w3.org/2005/Atom}"
_CONTENT = "{http://purl.org/rss/1.0/modules/content/}encoded"
_MEDIA = "{http://search.yahoo.com/mrss/}"
_IMG_SRC = re.compile(r'<img[^>]+src="([^"]+)"', re.I)


def _child_text(elem: ET.Element, tag: str) -> str:
    child = elem.find(tag)
    return (child.text or "").strip() if child is not None else ""


def _image(elem: ET.Element, raw_description: str) -> str:
    for tag in ("enclosure", f"{_MEDIA}content", f"{_MEDIA}thumbnail"):
        child = elem.find(tag)
        if child is not None and child.get("url"):
            return child.get("url", "")
    match = _IMG_SRC.search(raw_description)
    return match.group(1) if match else ""


def _listing(listing_id: str, title: str, link: str, raw_description: str, image: str) -> Listing:
    description = strip_html(raw_description)
    if not listing_id:
        listing_id = link or hashlib.sha1(f"{title}\n{description}".encode()).hexdigest()
    return Listing(
        source="rss",
        listing_id=listing_id,
        title=strip_html(title),
        url=link,
        price=parse_price(f"{title}\n{description}"),
        description=description,
        image_url=image,
    )


def parse_feed(content: bytes) -> list[Listing]:
    try:
        root = ET.fromstring(content)
    except ET.ParseError as exc:
        raise SourceError(f"feed is not valid XML: {exc}") from exc

    listings = []
    for item in root.iter("item"):
        raw = _child_text(item, _CONTENT) or _child_text(item, "description")
        listings.append(_listing(
            _child_text(item, "guid"), _child_text(item, "title"), _child_text(item, "link"),
            raw, _image(item, raw),
        ))
    for entry in root.iter(f"{_ATOM}entry"):
        links = entry.findall(f"{_ATOM}link")
        link = next((l.get("href", "") for l in links if l.get("rel", "alternate") == "alternate"), "")
        raw = _child_text(entry, f"{_ATOM}content") or _child_text(entry, f"{_ATOM}summary")
        listings.append(_listing(
            _child_text(entry, f"{_ATOM}id"), _child_text(entry, f"{_ATOM}title"), link,
            raw, _image(entry, raw),
        ))
    return listings


class RssSource:
    name = "rss"

    def __init__(self, http: HttpClient):
        self._http = http

    def search(self, search: SearchConfig) -> list[Listing]:
        return parse_feed(self._http.get_bytes(search.url))

    def enrich(self, listing: Listing) -> None:
        pass  # feeds already include the description
