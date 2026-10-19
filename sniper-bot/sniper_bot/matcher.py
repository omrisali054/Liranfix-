"""Decides whether a listing is a deal worth a notification."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Iterable

from .config import SearchConfig
from .keywords import EXCLUDE_KEYWORDS, FAULT_KEYWORDS, NEGATION_FILLERS, NEGATION_WORDS
from .models import Listing
from .text import normalize

# Hebrew words take one-letter prefixes (ו, ה, ב, ל, מ, ש, כ), up to three of them.
_HEBREW_PREFIXES = "[והבלמשכ]{0,3}"


@lru_cache(maxsize=4096)
def _pattern(phrase: str) -> re.Pattern[str] | None:
    norm = normalize(phrase)
    if not norm:
        return None
    if norm[0].isascii():
        start = r"(?<![a-z0-9])"
    else:
        start = r"(?<!\S)" + _HEBREW_PREFIXES
    # Latin words need a clean right edge ("as is" must not hit "has issues");
    # Hebrew phrases may carry suffixes ("שבור" -> "שבורה").
    end = r"(?![a-z0-9])" if norm[-1].isascii() else ""
    return re.compile(start + re.escape(norm) + end)


def _find(norm_text: str, phrase: str) -> Iterable[re.Match[str]]:
    pattern = _pattern(phrase)
    return pattern.finditer(norm_text) if pattern else ()


@dataclass(frozen=True)
class FaultHit:
    category: str
    phrase: str


@dataclass
class Verdict:
    ok: bool
    reason: str = ""
    faults: list[FaultHit] = field(default_factory=list)


class KeywordMatcher:
    def __init__(
        self,
        faults: dict[str, list[str]] | None = None,
        exclude: Iterable[str] | None = None,
        extra_faults: Iterable[str] = (),
        extra_exclude: Iterable[str] = (),
    ):
        self._faults = {cat: list(phrases) for cat, phrases in (faults or FAULT_KEYWORDS).items()}
        extra_faults = list(extra_faults)
        if extra_faults:
            self._faults.setdefault("מותאם אישית", []).extend(extra_faults)
        self._exclude = list(EXCLUDE_KEYWORDS if exclude is None else exclude) + list(extra_exclude)
        self._negations = {normalize(word) for word in NEGATION_WORDS}
        self._fillers = {normalize(word) for word in NEGATION_FILLERS}

    def _negated(self, norm_text: str, start: int) -> bool:
        before = norm_text[:start].split()
        while before and before[-1] in self._fillers:
            before.pop()
        return bool(before) and before[-1] in self._negations

    def faults(self, text: str) -> list[FaultHit]:
        norm_text = normalize(text)
        hits = []
        for category, phrases in self._faults.items():
            for phrase in phrases:
                if any(not self._negated(norm_text, m.start()) for m in _find(norm_text, phrase)):
                    hits.append(FaultHit(category, phrase))
        return hits

    def excluded_by(self, text: str, extra: Iterable[str] = ()) -> str | None:
        norm_text = normalize(text)
        for phrase in [*self._exclude, *extra]:
            if any(True for _ in _find(norm_text, phrase)):
                return phrase
        return None

    def contains_any(self, text: str, phrases: Iterable[str]) -> bool:
        norm_text = normalize(text)
        return any(any(True for _ in _find(norm_text, phrase)) for phrase in phrases)


def evaluate(
    listing: Listing,
    search: SearchConfig,
    matcher: KeywordMatcher,
    *,
    enforce_fault: bool = True,
) -> Verdict:
    """Apply a search's rules to a listing.

    With ``enforce_fault=False`` the "must mention a fault" rule is skipped; the
    bot uses that as a cheap pre-check before fetching the full description.
    """
    if listing.price is None:
        if not search.allow_no_price:
            return Verdict(False, "no price")
    elif listing.price > search.max_price:
        return Verdict(False, f"price {listing.price} > {search.max_price}")
    elif listing.price < search.min_price:
        return Verdict(False, f"price {listing.price} < {search.min_price}")

    if listing.is_business and not search.include_business:
        return Verdict(False, "business seller")
    if search.conditions and listing.condition_id not in search.conditions:
        return Verdict(False, f"condition {listing.condition or listing.condition_id!r}")
    if search.product_types and listing.product_type_id not in search.product_types:
        return Verdict(False, f"product type {listing.product_type or listing.product_type_id!r}")

    text = listing.text()
    if search.keywords and not matcher.contains_any(text, search.keywords):
        return Verdict(False, "no search keyword")
    excluded = matcher.excluded_by(text, search.exclude)
    if excluded:
        return Verdict(False, f"excluded by {excluded!r}")

    faults = matcher.faults(text)
    if enforce_fault and search.require_fault and not faults:
        return Verdict(False, "no fault keyword")
    return Verdict(True, faults=faults)
