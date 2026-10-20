from __future__ import annotations

import json
import unittest
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import requests

from lead_responder.channels import SendError
from lead_responder.config import (AdminConfig, ClientConfig, Config, MetaConfig, OwnerConfig, Rules, ServerSettings,
                                   SheetsConfig, WhatsAppConfig)
from lead_responder.engine import Channels, Engine
from lead_responder.storage import Store

TZ = ZoneInfo("Asia/Jerusalem")
SECRET = "test-secret-0123456789"
# Tuesday 3 Nov 2026, 10:00 in Israel: a normal working morning, no quiet hours.
TUESDAY_10AM = datetime(2026, 11, 3, 8, 0, tzinfo=timezone.utc)


def make_client(slug: str = "clinic", *, whatsapp: bool = True, sheets: bool = False, **overrides) -> ClientConfig:
    values = dict(
        slug=slug,
        business_name="הקליניקה של דנה",
        key=f"{slug}-key-0123456789",
        owner=OwnerConfig(telegram_chat_ids=["42"]),
        rules=Rules(),
        whatsapp=WhatsAppConfig(phone_number_id="111", access_token="tok", ack_template="lead_ack",
                                followup_template="lead_followup") if whatsapp else None,
        sheets=SheetsConfig(url="https://script.example/exec", secret="sheet-secret-0123456789") if sheets else None,
        meta=MetaConfig(app_secret="app-secret"),
        telegram_bot_token="123:abc",
    )
    values.update(overrides)
    return ClientConfig(**values)


def make_config(*clients: ClientConfig, admin_chat: str = "999") -> Config:
    clients = clients or (make_client(),)
    return Config(
        server=ServerSettings(public_url="https://leads.example.com", secret=SECRET, verify_token="verify-me"),
        admin=AdminConfig(telegram_chat_id=admin_chat),
        clients={c.slug: c for c in clients},
        telegram_bot_token="123:abc",
        tz=TZ,
    )


class Clock:
    def __init__(self, now: datetime = TUESDAY_10AM):
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **delta) -> None:
        self.now += timedelta(**delta)


class FakeWhatsApp:
    name = "whatsapp"

    def __init__(self):
        self.sent: list[dict] = []
        self.errors: list[SendError] = []  # raised one per call, in order

    def send_template(self, to, template, params, button_param=""):
        if self.errors:
            raise self.errors.pop(0)
        self.sent.append({"to": to, "template": template, "params": list(params), "button": button_param})
        return f"wamid.{len(self.sent)}"


class FakeTelegram:
    name = "telegram"

    def __init__(self):
        self.sent: list[dict] = []
        self.errors: list[SendError] = []

    def send(self, chat_id, text, buttons=()):
        if self.errors:
            raise self.errors.pop(0)
        self.sent.append({"chat_id": chat_id, "text": text, "buttons": list(buttons)})


class FakeSheets:
    def __init__(self):
        self.rows: list[dict] = []

    def upsert(self, row):
        self.rows.append(row)


class FakeFacebook:
    def __init__(self, leads=None):
        self.leads = dict(leads or {})

    def fetch_lead(self, leadgen_id):
        return dict(self.leads[leadgen_id])


class Rig:
    """An engine wired to fakes, with a controllable clock."""

    def __init__(self, *clients: ClientConfig, clock: Clock | None = None):
        self.config = make_config(*clients)
        self.clock = clock or Clock()
        self.whatsapp = FakeWhatsApp()
        self.telegram = FakeTelegram()
        self.sheets = FakeSheets()
        self.facebook = FakeFacebook()
        self.admin = FakeTelegram()
        self.store = Store()
        channels = {slug: Channels(whatsapp=self.whatsapp, telegram=self.telegram, sheets=self.sheets,
                                   facebook=self.facebook) for slug in self.config.clients}
        self.engine = Engine(self.config, self.store, channels, admin=self.admin, clock=self.clock)

    def owner_texts(self) -> list[str]:
        return [m["text"] for m in self.telegram.sent]

    def pending(self, lead_id: int) -> list[str]:
        return [kind for kind, _ in self.store.pending_tasks(lead_id)]


class RigTest(unittest.TestCase):
    def rig(self, *clients: ClientConfig, clock: Clock | None = None) -> Rig:
        rig = Rig(*clients, clock=clock)
        self.addCleanup(rig.store.close)
        return rig


def make_response(status: int = 200, body: bytes | str = b"", content_type: str = "application/json") -> requests.Response:
    resp = requests.Response()
    resp.status_code = status
    resp._content = body.encode("utf-8") if isinstance(body, str) else body
    resp.headers["Content-Type"] = content_type
    return resp


class FakeSession:
    """Records requests and replies with queued (status, json-or-text) pairs or exceptions."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls: list[dict] = []

    def _reply(self, method, url, kwargs):
        self.calls.append({"method": method, "url": url, **kwargs})
        reply = self.replies.pop(0) if self.replies else (200, {"ok": True})
        if isinstance(reply, Exception):
            raise reply
        status, payload = reply
        if isinstance(payload, str):
            return make_response(status, payload, "text/html")
        return make_response(status, json.dumps(payload))

    def post(self, url, **kwargs):
        return self._reply("POST", url, kwargs)

    def get(self, url, **kwargs):
        return self._reply("GET", url, kwargs)
