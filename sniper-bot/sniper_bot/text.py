"""Text helpers: Hebrew-aware normalisation and price extraction."""

from __future__ import annotations

import html
import re

_FINAL_LETTERS = str.maketrans("ךםןףץ", "כמנפצ")
_NIQQUD = re.compile(r"[֑-ׇ]")
_PUNCTUATION = re.compile(r"[^\w\s]+")
_TAGS = re.compile(r"<[^>]+>")

_NUMBER = r"(\d{1,3}(?:[,.]\d{3})+|\d+)"
_PRICE_PATTERNS = [
    re.compile(r"(?:₪|\bnis\b|\bils\b)\s*" + _NUMBER, re.IGNORECASE),
    re.compile(_NUMBER + r"\s*(?:₪|ש\"ח|ש״ח|ש''ח|שח\b|שקל|\bnis\b|\bils\b)", re.IGNORECASE),
    re.compile(r"(?:מחיר|price)\s*[:\-]?\s*" + _NUMBER, re.IGNORECASE),
]


def normalize(text: str | None) -> str:
    """Lower-case, drop niqqud and punctuation, unify final letters, squash spaces."""
    text = _NIQQUD.sub("", text or "").lower().translate(_FINAL_LETTERS)
    return " ".join(_PUNCTUATION.sub(" ", text).split())


def strip_html(markup: str | None) -> str:
    """Turn an HTML snippet (RSS descriptions) into plain text."""
    return " ".join(html.unescape(_TAGS.sub(" ", markup or "")).split())


def parse_price(text: str | None) -> int | None:
    """Find the first price in free text ("400 ₪", "₪1,200", "מחיר: 350")."""
    for pattern in _PRICE_PATTERNS:
        match = pattern.search(text or "")
        if match:
            return int(re.sub(r"[,.]", "", match.group(1)))
    return None
