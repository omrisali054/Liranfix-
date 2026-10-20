from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

NEW, HANDLED, IRRELEVANT, BLOCKED = "new", "handled", "irrelevant", "blocked"

SOURCE_LABELS = {
    "facebook": "פייסבוק",
    "fb": "פייסבוק",
    "instagram": "אינסטגרם",
    "ig": "אינסטגרם",
    "website": "אתר",
    "site": "אתר",
    "google": "גוגל",
    "whatsapp": "וואטסאפ",
    "tiktok": "טיקטוק",
    "test": "בדיקה",
}


@dataclass
class LeadInput:
    """A lead as it arrived, before we store it."""

    name: str = ""
    phone_raw: str = ""
    email: str = ""
    source: str = ""
    external_id: str = ""  # e.g. the Facebook leadgen id, so webhook retries don't create twins
    extra: dict[str, str] = field(default_factory=dict)  # every other form field, label → value


@dataclass
class Lead:
    id: int
    client: str
    created_at: datetime
    name: str
    phone: str  # 972501234567, '' when missing / invalid
    phone_raw: str
    email: str
    source: str
    extra: dict[str, str]
    status: str = NEW
    status_at: datetime | None = None
    replied_at: datetime | None = None
    wa_message_id: str = ""
    wa_status: str = ""
    duplicate_of: int | None = None
    external_id: str = ""

    @property
    def first_name(self) -> str:
        return self.name.split()[0] if self.name.split() else ""

    @property
    def source_label(self) -> str:
        return SOURCE_LABELS.get(self.source.lower(), self.source)


@dataclass
class Task:
    """One thing to send, now or later. Every outgoing message goes through here, so it survives
    restarts and failed sends are retried."""

    id: int
    client: str
    kind: str
    lead_id: int | None
    payload: dict
    due_at: datetime
    attempts: int = 0
