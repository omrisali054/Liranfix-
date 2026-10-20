"""SQLite: the leads, the queue of messages to send, and a few "already done" markers."""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

from .models import NEW, Lead, LeadInput, Task

_SCHEMA = """
CREATE TABLE IF NOT EXISTS leads (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    client        TEXT NOT NULL,
    created_at    TEXT NOT NULL,
    name          TEXT NOT NULL,
    phone         TEXT NOT NULL,
    phone_raw     TEXT NOT NULL,
    email         TEXT NOT NULL,
    source        TEXT NOT NULL,
    extra         TEXT NOT NULL,
    external_id   TEXT,
    status        TEXT NOT NULL,
    status_at     TEXT,
    replied_at    TEXT,
    wa_message_id TEXT NOT NULL DEFAULT '',
    wa_status     TEXT NOT NULL DEFAULT '',
    duplicate_of  INTEGER,
    UNIQUE (client, external_id)
);
CREATE INDEX IF NOT EXISTS leads_by_phone ON leads (client, phone, created_at);
CREATE INDEX IF NOT EXISTS leads_by_wa_message ON leads (wa_message_id);
CREATE TABLE IF NOT EXISTS tasks (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    client     TEXT NOT NULL,
    kind       TEXT NOT NULL,
    lead_id    INTEGER,
    payload    TEXT NOT NULL,
    due_at     TEXT NOT NULL,
    attempts   INTEGER NOT NULL DEFAULT 0,
    state      TEXT NOT NULL DEFAULT 'pending',
    last_error TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS tasks_due ON tasks (state, due_at);
CREATE TABLE IF NOT EXISTS once (
    key TEXT PRIMARY KEY,
    at  TEXT NOT NULL
);
"""

_LEAD_COLUMNS = ("id, client, created_at, name, phone, phone_raw, email, source, extra, status, status_at, "
                 "replied_at, wa_message_id, wa_status, duplicate_of, external_id")


def _ts(when: datetime) -> str:
    return when.astimezone(timezone.utc).isoformat(timespec="seconds")


def _dt(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def _lead(row: tuple) -> Lead:
    (id_, client, created_at, name, phone, phone_raw, email, source, extra, status, status_at,
     replied_at, wa_message_id, wa_status, duplicate_of, external_id) = row
    return Lead(id=id_, client=client, created_at=_dt(created_at), name=name, phone=phone, phone_raw=phone_raw,
                email=email, source=source, extra=json.loads(extra), status=status, status_at=_dt(status_at),
                replied_at=_dt(replied_at), wa_message_id=wa_message_id, wa_status=wa_status,
                duplicate_of=duplicate_of, external_id=external_id or "")


class Store:
    """One connection shared by the web threads and the worker, serialised by a lock."""

    def __init__(self, path: str | Path = ":memory:"):
        self._db = sqlite3.connect(str(path), isolation_level=None, check_same_thread=False)  # autocommit
        self._lock = threading.Lock()
        if str(path) != ":memory:":
            self._db.execute("PRAGMA journal_mode=WAL")
        self._db.executescript(_SCHEMA)

    def close(self) -> None:
        self._db.close()

    def _run(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        with self._lock:
            return self._db.execute(sql, params)

    def _fetch(self, sql: str, params: tuple = ()) -> list[tuple]:
        with self._lock:
            return self._db.execute(sql, params).fetchall()

    def _leads(self, where: str, params: tuple) -> list[Lead]:
        return [_lead(row) for row in self._fetch(f"SELECT {_LEAD_COLUMNS} FROM leads WHERE {where}", params)]

    # -- leads ------------------------------------------------------------

    def add_lead(self, client: str, lead: LeadInput, phone: str, now: datetime, *,
                 status: str = NEW, duplicate_of: int | None = None) -> Lead | None:
        """None when this external_id was already stored (a webhook delivered twice)."""
        try:
            cur = self._run(
                "INSERT INTO leads (client, created_at, name, phone, phone_raw, email, source, extra, "
                "external_id, status, duplicate_of) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (client, _ts(now), lead.name, phone, lead.phone_raw, lead.email, lead.source,
                 json.dumps(lead.extra, ensure_ascii=False), lead.external_id or None, status, duplicate_of),
            )
        except sqlite3.IntegrityError:
            return None
        return self.get_lead(cur.lastrowid)

    def get_lead(self, lead_id: int) -> Lead | None:
        found = self._leads("id = ?", (lead_id,))
        return found[0] if found else None

    def has_external_id(self, client: str, external_id: str) -> bool:
        return bool(self._leads("client = ? AND external_id = ?", (client, external_id)))

    def latest_by_phone(self, client: str, phone: str, since: datetime) -> Lead | None:
        found = self._leads("client = ? AND phone = ? AND created_at >= ? ORDER BY created_at DESC, id DESC LIMIT 1",
                            (client, phone, _ts(since)))
        return found[0] if found else None

    def count_since(self, client: str, since: datetime) -> int:
        return self._fetch("SELECT COUNT(*) FROM leads WHERE client = ? AND created_at >= ?",
                           (client, _ts(since)))[0][0]

    def set_status(self, lead_id: int, status: str, now: datetime) -> bool:
        cur = self._run("UPDATE leads SET status = ?, status_at = ? WHERE id = ? AND status != ?",
                        (status, _ts(now), lead_id, status))
        return cur.rowcount > 0

    def mark_replied(self, lead_id: int, now: datetime) -> bool:
        """True the first time this lead answers us."""
        cur = self._run("UPDATE leads SET replied_at = ? WHERE id = ? AND replied_at IS NULL", (_ts(now), lead_id))
        return cur.rowcount > 0

    def set_wa_message(self, lead_id: int, message_id: str, status: str) -> None:
        self._run("UPDATE leads SET wa_message_id = ?, wa_status = ? WHERE id = ?", (message_id, status, lead_id))

    def update_wa_status(self, message_id: str, status: str) -> Lead | None:
        if not message_id:
            return None
        self._run("UPDATE leads SET wa_status = ? WHERE wa_message_id = ?", (status, message_id))
        found = self._leads("wa_message_id = ?", (message_id,))
        return found[0] if found else None

    def recent_leads(self, client: str, limit: int = 20) -> list[Lead]:
        return self._leads("client = ? ORDER BY created_at DESC, id DESC LIMIT ?", (client, limit))

    def leads_between(self, client: str, start: datetime, end: datetime) -> list[Lead]:
        return self._leads("client = ? AND created_at >= ? AND created_at < ? ORDER BY created_at",
                           (client, _ts(start), _ts(end)))

    # -- tasks ------------------------------------------------------------

    def add_task(self, client: str, kind: str, due_at: datetime, lead_id: int | None = None,
                 payload: dict | None = None) -> int:
        cur = self._run("INSERT INTO tasks (client, kind, lead_id, payload, due_at) VALUES (?, ?, ?, ?, ?)",
                        (client, kind, lead_id, json.dumps(payload or {}, ensure_ascii=False), _ts(due_at)))
        return cur.lastrowid

    def due_tasks(self, now: datetime, limit: int = 50) -> list[Task]:
        rows = self._fetch(
            "SELECT id, client, kind, lead_id, payload, due_at, attempts FROM tasks "
            "WHERE state = 'pending' AND due_at <= ? ORDER BY due_at, id LIMIT ?", (_ts(now), limit),
        )
        return [Task(id=r[0], client=r[1], kind=r[2], lead_id=r[3], payload=json.loads(r[4]),
                     due_at=_dt(r[5]), attempts=r[6]) for r in rows]

    def pending_tasks(self, lead_id: int) -> list[tuple[str, datetime]]:
        rows = self._fetch("SELECT kind, due_at FROM tasks WHERE lead_id = ? AND state = 'pending' "
                           "ORDER BY due_at, id", (lead_id,))
        return [(kind, _dt(due)) for kind, due in rows]

    def finish_task(self, task_id: int) -> None:
        self._run("UPDATE tasks SET state = 'done' WHERE id = ?", (task_id,))

    def postpone_task(self, task_id: int, due_at: datetime) -> None:
        self._run("UPDATE tasks SET due_at = ? WHERE id = ?", (_ts(due_at), task_id))

    def retry_task(self, task_id: int, due_at: datetime, error: str) -> None:
        self._run("UPDATE tasks SET due_at = ?, attempts = attempts + 1, last_error = ? WHERE id = ?",
                  (_ts(due_at), error[:500], task_id))

    def fail_task(self, task_id: int, error: str) -> None:
        self._run("UPDATE tasks SET state = 'failed', attempts = attempts + 1, last_error = ? WHERE id = ?",
                  (error[:500], task_id))

    def cancel_tasks(self, lead_id: int, kinds: tuple[str, ...]) -> int:
        marks = ", ".join("?" for _ in kinds)
        cur = self._run(f"UPDATE tasks SET state = 'cancelled' WHERE lead_id = ? AND state = 'pending' "
                        f"AND kind IN ({marks})", (lead_id, *kinds))
        return cur.rowcount

    def failed_tasks(self, limit: int = 20) -> list[tuple[str, str, int | None, str, str]]:
        return self._fetch("SELECT client, kind, lead_id, due_at, last_error FROM tasks WHERE state = 'failed' "
                           "ORDER BY id DESC LIMIT ?", (limit,))

    # -- markers ----------------------------------------------------------

    def mark_once(self, key: str, now: datetime) -> bool:
        """True the first time a key is seen: webhook retries, one alert per hour, one report per week."""
        cur = self._run("INSERT OR IGNORE INTO once VALUES (?, ?)", (key, _ts(now)))
        return cur.rowcount > 0
