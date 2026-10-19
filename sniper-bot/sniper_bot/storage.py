"""SQLite memory: which listings were already seen, and which were alerted."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .models import Listing

_SCHEMA = """
CREATE TABLE IF NOT EXISTS seen (
    search     TEXT NOT NULL,
    key        TEXT NOT NULL,
    price      INTEGER,
    first_seen TEXT NOT NULL,
    last_seen  TEXT NOT NULL,
    PRIMARY KEY (search, key)
);
CREATE TABLE IF NOT EXISTS alerts (
    key     TEXT PRIMARY KEY,
    search  TEXT NOT NULL,
    title   TEXT NOT NULL,
    url     TEXT NOT NULL,
    price   INTEGER,
    sent_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS seeded (
    search    TEXT PRIMARY KEY,
    seeded_at TEXT NOT NULL
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Store:
    def __init__(self, path: str | Path = ":memory:"):
        self._db = sqlite3.connect(str(path), isolation_level=None)  # autocommit
        self._db.executescript(_SCHEMA)

    def close(self) -> None:
        self._db.close()

    def is_seeded(self, search: str) -> bool:
        return self._db.execute("SELECT 1 FROM seeded WHERE search = ?", (search,)).fetchone() is not None

    def mark_seeded(self, search: str) -> None:
        self._db.execute("INSERT OR IGNORE INTO seeded VALUES (?, ?)", (search, _now()))

    def was_alerted(self, key: str) -> bool:
        return self._db.execute("SELECT 1 FROM alerts WHERE key = ?", (key,)).fetchone() is not None

    def is_unchanged(self, search: str, key: str, price: int | None) -> bool:
        """Seen before by this search at the same price, so nothing new to judge."""
        row = self._db.execute(
            "SELECT 1 FROM seen WHERE search = ? AND key = ? AND price IS ?", (search, key, price)
        ).fetchone()
        return row is not None

    def mark_seen(self, search: str, key: str, price: int | None) -> None:
        now = _now()
        self._db.execute(
            "INSERT INTO seen VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT (search, key) DO UPDATE SET price = excluded.price, last_seen = excluded.last_seen",
            (search, key, price, now, now),
        )

    def forget_seen(self, search: str, key: str) -> None:
        self._db.execute("DELETE FROM seen WHERE search = ? AND key = ?", (search, key))

    def mark_alerted(self, search: str, listing: Listing) -> None:
        self._db.execute(
            "INSERT OR REPLACE INTO alerts VALUES (?, ?, ?, ?, ?, ?)",
            (listing.key, search, listing.title, listing.url, listing.price, _now()),
        )

    def recent_alerts(self, limit: int = 20) -> list[tuple[str, str, str, int | None, str]]:
        return self._db.execute(
            "SELECT sent_at, search, title, price, url FROM alerts ORDER BY sent_at DESC LIMIT ?", (limit,)
        ).fetchall()
