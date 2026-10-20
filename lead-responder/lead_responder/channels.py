"""Talking to the outside world: WhatsApp Cloud API, Telegram, a Google Sheet, Facebook Lead Ads.

Every sender raises :class:`SendError`. ``permanent=True`` means retrying won't help (bad number,
rejected template, wrong token); anything else is retried by the worker with a growing delay."""

from __future__ import annotations

import html
import logging
import re
from typing import Protocol, Sequence

import requests

from .config import SheetsConfig, WhatsAppConfig

log = logging.getLogger(__name__)

GRAPH = "https://graph.facebook.com/{version}/{path}"
# Graph error codes that are about load, not about the request itself.
_RETRYABLE_GRAPH_CODES = {1, 2, 4, 17, 341, 80007, 130429, 131000, 131048, 131056, 133004}


class SendError(RuntimeError):
    def __init__(self, message: str, *, permanent: bool = False):
        super().__init__(message)
        self.permanent = permanent


class WhatsAppSender(Protocol):
    name: str

    def send_template(self, to: str, template: str, params: Sequence[str], button_param: str = "") -> str: ...


class TelegramSender(Protocol):
    name: str

    def send(self, chat_id: str, text: str, buttons: Sequence[tuple[str, str]] = ()) -> None: ...


class SheetSender(Protocol):
    def upsert(self, row: dict) -> None: ...


class LeadFetcher(Protocol):
    def fetch_lead(self, leadgen_id: str) -> dict: ...


def clean_param(text: str, limit: int = 900) -> str:
    """WhatsApp rejects template parameters with newlines, tabs, 4+ spaces in a row, or nothing at all."""
    text = re.sub(r"[\r\n\t]+", " ", str(text))
    text = re.sub(r" {4,}", "   ", text).strip()
    return text[:limit] or "-"


def _graph_error(resp: requests.Response, what: str) -> SendError:
    try:
        error = resp.json().get("error", {})
    except ValueError:
        error = {}
    code = error.get("code")
    detail = error.get("error_data", {}).get("details") or error.get("message") or resp.text[:200]
    permanent = resp.status_code in (400, 401, 403, 404) and code not in _RETRYABLE_GRAPH_CODES
    if resp.status_code == 401 or code == 190:
        detail = f"access token rejected, create a new one ({detail})"
    return SendError(f"{what}: HTTP {resp.status_code} code {code}: {detail}", permanent=permanent)


class WhatsAppClient:
    """WhatsApp Cloud API. Business-initiated messages must be approved templates."""

    name = "whatsapp"

    def __init__(self, config: WhatsAppConfig, graph_version: str = "v25.0",
                 session: requests.Session | None = None, timeout: float = 15):
        self._config = config
        self._url = GRAPH.format(version=graph_version, path=f"{config.phone_number_id}/messages")
        self._session = session or requests.Session()
        self._timeout = timeout

    def send_template(self, to: str, template: str, params: Sequence[str], button_param: str = "") -> str:
        components: list[dict] = []
        if params:
            components.append({"type": "body",
                               "parameters": [{"type": "text", "text": clean_param(p)} for p in params]})
        if button_param:
            components.append({"type": "button", "sub_type": "url", "index": "0",
                               "parameters": [{"type": "text", "text": button_param}]})
        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to,
            "type": "template",
            "template": {"name": template, "language": {"code": self._config.language}, "components": components},
        }
        headers = {"Authorization": f"Bearer {self._config.access_token}"}
        try:
            resp = self._session.post(self._url, json=payload, headers=headers, timeout=self._timeout)
        except requests.RequestException as exc:
            raise SendError(f"whatsapp: {type(exc).__name__}") from None
        if resp.status_code >= 400:
            raise _graph_error(resp, f"whatsapp template {template!r}")
        try:
            return resp.json()["messages"][0]["id"]
        except (ValueError, KeyError, IndexError):
            raise SendError(f"whatsapp: unexpected reply {resp.text[:200]}") from None


class TelegramClient:
    """A Telegram bot (create one with @BotFather). One bot can serve all your clients."""

    name = "telegram"
    _API = "https://api.telegram.org/bot{token}/{method}"
    _TEXT_LIMIT = 4000

    def __init__(self, bot_token: str, session: requests.Session | None = None, timeout: float = 15):
        self._token = bot_token
        self._session = session or requests.Session()
        self._timeout = timeout

    def _call(self, method: str, payload: dict) -> dict:
        url = self._API.format(token=self._token, method=method)
        try:
            resp = self._session.post(url, json=payload, timeout=self._timeout)
            data = resp.json()
        except (requests.RequestException, ValueError) as exc:
            # Don't echo the exception text: it contains the URL, i.e. the bot token.
            raise SendError(f"telegram {method}: {type(exc).__name__}") from None
        if not data.get("ok"):
            permanent = resp.status_code in (400, 401, 403, 404)
            raise SendError(f"telegram {method}: {data.get('description', 'unknown error')}", permanent=permanent)
        return data

    def send(self, chat_id: str, text: str, buttons: Sequence[tuple[str, str]] = ()) -> None:
        payload: dict = {
            "chat_id": chat_id,
            "text": text[: self._TEXT_LIMIT],
            "parse_mode": "HTML",
            "link_preview_options": {"is_disabled": True},
        }
        if buttons:
            payload["reply_markup"] = {"inline_keyboard": [[{"text": label, "url": url} for label, url in buttons]]}
        self._call("sendMessage", payload)

    def recent_chats(self) -> list[tuple[str, str]]:
        """(chat_id, who) for everyone who recently wrote to the bot: how you find an owner's chat id."""
        chats: dict[str, str] = {}
        for update in self._call("getUpdates", {"limit": 100}).get("result", []):
            message = update.get("message") or update.get("my_chat_member") or {}
            chat = message.get("chat") or {}
            if "id" in chat:
                who = chat.get("title") or " ".join(filter(None, (chat.get("first_name"), chat.get("last_name"))))
                chats[str(chat["id"])] = (who + (f" @{chat['username']}" if chat.get("username") else "")).strip()
        return list(chats.items())


class SheetsClient:
    """A Google Sheet as a simple CRM, through the Apps Script web app in apps-script/sheet-webhook.gs."""

    def __init__(self, config: SheetsConfig, session: requests.Session | None = None, timeout: float = 30):
        self._config = config
        self._session = session or requests.Session()
        self._timeout = timeout

    def upsert(self, row: dict) -> None:
        try:
            resp = self._session.post(self._config.url, json={"secret": self._config.secret, "row": row},
                                      timeout=self._timeout)
        except requests.RequestException as exc:
            raise SendError(f"sheets: {type(exc).__name__}") from None
        if resp.status_code >= 500:
            raise SendError(f"sheets: HTTP {resp.status_code}")
        try:
            data = resp.json()
        except ValueError:
            raise SendError("sheets: reply is not JSON; deploy the Apps Script as a web app with access "
                            "'Anyone' and use the /exec URL", permanent=True) from None
        if not data.get("ok"):
            raise SendError(f"sheets: {data.get('error', 'unknown error')}", permanent=True)


class FacebookClient:
    """Reads a Lead Ads submission after Meta's ``leadgen`` webhook tells us its id."""

    FIELDS = "field_data,created_time,form_id,campaign_name,ad_name,platform"

    def __init__(self, page_access_token: str, graph_version: str = "v25.0",
                 session: requests.Session | None = None, timeout: float = 15):
        self._token = page_access_token
        self._version = graph_version
        self._session = session or requests.Session()
        self._timeout = timeout

    def fetch_lead(self, leadgen_id: str) -> dict:
        url = GRAPH.format(version=self._version, path=leadgen_id)
        try:
            resp = self._session.get(url, params={"fields": self.FIELDS},
                                     headers={"Authorization": f"Bearer {self._token}"}, timeout=self._timeout)
        except requests.RequestException as exc:
            raise SendError(f"facebook lead: {type(exc).__name__}") from None
        if resp.status_code >= 400:
            raise _graph_error(resp, f"facebook lead {leadgen_id}")
        try:
            return resp.json()
        except ValueError:
            raise SendError("facebook lead: reply is not JSON") from None


# -- dry run: print instead of sending ------------------------------------


class ConsoleWhatsApp:
    name = "whatsapp (dry run)"

    def __init__(self):
        self._sent = 0

    def send_template(self, to: str, template: str, params: Sequence[str], button_param: str = "") -> str:
        self._sent += 1
        shown = ", ".join(clean_param(p) for p in params)
        print(f"\n[whatsapp → +{to}] template {template!r} ({shown})" + (f" button={button_param}" if button_param else ""),
              flush=True)
        return f"dry-run-{self._sent}"


class ConsoleTelegram:
    name = "telegram (dry run)"

    def send(self, chat_id: str, text: str, buttons: Sequence[tuple[str, str]] = ()) -> None:
        plain = html.unescape(re.sub(r"<[^>]+>", "", text))
        links = "".join(f"\n  [{label}] {url}" for label, url in buttons)
        print(f"\n[telegram → {chat_id}]\n{plain}{links}", flush=True)

    def recent_chats(self) -> list[tuple[str, str]]:
        return []


class ConsoleSheets:
    def upsert(self, row: dict) -> None:
        print(f"\n[sheet] {row}", flush=True)
