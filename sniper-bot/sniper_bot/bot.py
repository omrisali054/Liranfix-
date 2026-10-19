"""The scan loop: fetch → filter → notify → remember."""

from __future__ import annotations

import logging
import random
import time
from dataclasses import dataclass, field
from typing import Callable, Mapping, Sequence

from .config import Config, SearchConfig
from .fetch import BlockedError, SourceError
from .matcher import FaultHit, KeywordMatcher, evaluate
from .models import Listing
from .notifiers import Message, Notifier, NotifyError
from .sources import Source
from .storage import Store

log = logging.getLogger(__name__)

MAX_BACKOFF_SECONDS = 30 * 60


@dataclass
class Alert:
    search: SearchConfig
    listing: Listing
    faults: list[FaultHit]


@dataclass
class ScanResult:
    alerts: list[Alert] = field(default_factory=list)
    failed_searches: list[str] = field(default_factory=list)


def _shekels(amount: int) -> str:
    return f"{amount:,} ₪"


def format_alert(alert: Alert) -> Message:
    listing, search = alert.listing, alert.search
    price = _shekels(listing.price) if listing.price is not None else "מחיר לא צוין"
    price_line = f"💰 {price} (תקרה: {_shekels(search.max_price)})"
    if listing.previous_price and listing.price and listing.previous_price > listing.price:
        price_line += f" · ירד מ-{_shekels(listing.previous_price)}"
    lines = [price_line]

    if alert.faults:
        by_category: dict[str, list[str]] = {}
        for hit in alert.faults:
            by_category.setdefault(hit.category, []).append(hit.phrase)
        lines.append("🔧 " + " | ".join(f"{cat}: {', '.join(phrases)}" for cat, phrases in by_category.items()))

    details = " · ".join(part for part in (listing.city, listing.condition, listing.product_type) if part)
    if details:
        lines.append(f"📍 {details}")
    lines.append(f"🔎 {search.name} ({listing.source})")
    return Message(
        title=f"🎯 {listing.title} – {price}",
        body="\n".join(lines),
        url=listing.url,
        image_url=listing.image_url,
        urgent=bool(alert.faults),
    )


class SniperBot:
    def __init__(
        self,
        config: Config,
        store: Store,
        sources: Mapping[str, Source],
        notifiers: Sequence[Notifier],
        matcher: KeywordMatcher,
        *,
        seed: bool | None = None,
        sleep: Callable[[float], None] = time.sleep,
        rand: Callable[[], float] = random.random,
    ):
        self.config = config
        self.store = store
        self.sources = sources
        self.notifiers = list(notifiers)
        self.matcher = matcher
        self.seed = config.settings.seed_on_first_run if seed is None else seed
        self._sleep = sleep
        self._rand = rand
        self._failed_scans = 0
        self._failure_reported = False

    # -- one scan ---------------------------------------------------------

    def scan_once(self) -> ScanResult:
        result = ScanResult()
        for search in self.config.searches:
            try:
                result.alerts.extend(self._scan_search(search))
            except BlockedError as exc:
                log.warning("%s: blocked – %s", search.name, exc)
                result.failed_searches.append(search.name)
            except SourceError as exc:
                log.warning("%s: %s", search.name, exc)
                result.failed_searches.append(search.name)
        return result

    def _scan_search(self, search: SearchConfig) -> list[Alert]:
        source = self.sources[search.source]
        listings = source.search(search)
        seeding = self.seed and not self.store.is_seeded(search.name)
        alerts = []
        for listing in listings:
            alert = self._process(search, source, listing, seeding)
            if alert:
                alerts.append(alert)
        if seeding:
            self.store.mark_seeded(search.name)
            log.info("%s: first scan, remembered %d existing listings (no alerts)", search.name, len(listings))
        else:
            log.info("%s: %d listings checked, %d alerts", search.name, len(listings), len(alerts))
        return alerts

    def _process(self, search: SearchConfig, source: Source, listing: Listing, seeding: bool) -> Alert | None:
        if self.store.was_alerted(listing.key) or self.store.is_unchanged(search.name, listing.key, listing.price):
            return None
        # New listing, or a known one whose price changed (a drop can make it a deal).
        self.store.mark_seen(search.name, listing.key, listing.price)
        if seeding:
            return None

        verdict = evaluate(listing, search, self.matcher, enforce_fault=False)
        if not verdict.ok:
            log.debug("skip %s %r: %s", listing.key, listing.title, verdict.reason)
            return None

        if self.config.settings.fetch_details and not listing.description:
            try:
                source.enrich(listing)
            except SourceError as exc:
                log.warning("could not fetch details for %s (%s), judging by title only", listing.key, exc)
        verdict = evaluate(listing, search, self.matcher)
        if not verdict.ok:
            log.debug("skip %s %r: %s", listing.key, listing.title, verdict.reason)
            return None

        alert = Alert(search, listing, verdict.faults)
        if not self._notify(format_alert(alert)):
            self.store.forget_seen(search.name, listing.key)  # retry on the next scan
            return None
        self.store.mark_alerted(search.name, listing)
        log.info("ALERT %s: %s (%s)", search.name, listing.title, listing.url)
        return alert

    def _notify(self, message: Message) -> bool:
        delivered = False
        for notifier in self.notifiers:
            try:
                notifier.send(message)
                delivered = True
            except NotifyError as exc:
                log.error("%s notification failed: %s", notifier.name, exc)
        return delivered

    # -- the loop ---------------------------------------------------------

    def _track_health(self, result: ScanResult) -> None:
        if result.failed_searches and len(result.failed_searches) == len(self.config.searches):
            self._failed_scans += 1
        else:
            if self._failure_reported:
                self._notify(Message("✅ בוט המציאות חזר לעבוד", "הסריקה מצליחה שוב."))
            self._failed_scans = 0
            self._failure_reported = False
            return
        limit = self.config.settings.failure_alert_after
        if limit and self._failed_scans >= limit and not self._failure_reported:
            self._failure_reported = True
            self._notify(Message(
                "⚠️ בוט המציאות לא מצליח לסרוק",
                f"{self._failed_scans} סריקות רצופות נכשלו (חסימה/קאפצ'ה או בעיית רשת). "
                "הבוט ממשיך לנסות עם המתנה ארוכה יותר; בדוק את הלוג.",
                urgent=True,
            ))

    def next_delay(self) -> float:
        settings = self.config.settings
        if self._failed_scans:
            return min(settings.interval_seconds * 2 ** self._failed_scans, MAX_BACKOFF_SECONDS)
        return settings.interval_seconds + self._rand() * settings.jitter_seconds

    def run_forever(self, max_scans: int | None = None) -> None:
        scans = 0
        while max_scans is None or scans < max_scans:
            result = self.scan_once()
            self._track_health(result)
            scans += 1
            if max_scans is not None and scans >= max_scans:
                break
            delay = self.next_delay()
            log.debug("sleeping %.0f s", delay)
            self._sleep(delay)
