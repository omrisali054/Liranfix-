"""Phone notifications: ntfy (push app) and Telegram, plus a console printer."""

from __future__ import annotations

import html
import logging
from dataclasses import dataclass
from typing import Protocol

import requests

from .config import NtfyConfig, TelegramConfig

log = logging.getLogger(__name__)


class NotifyError(RuntimeError):
    pass


@dataclass
class Message:
    title: str
    body: str
    url: str = ""
    image_url: str = ""
    urgent: bool = False


class Notifier(Protocol):
    name: str

    def send(self, message: Message) -> None: ...


class ConsoleNotifier:
    name = "console"

    def send(self, message: Message) -> None:
        print(f"\n{message.title}\n{message.body}" + (f"\n{message.url}" if message.url else ""), flush=True)


class NtfyNotifier:
    """https://ntfy.sh — install the app, subscribe to your topic, done."""

    name = "ntfy"

    def __init__(self, config: NtfyConfig, session: requests.Session | None = None, timeout: float = 15):
        self._config = config
        self._session = session or requests.Session()
        self._timeout = timeout

    def send(self, message: Message) -> None:
        # JSON publishing keeps Hebrew titles intact (HTTP headers are latin-1 only).
        payload: dict = {
            "topic": self._config.topic,
            "title": message.title,
            "message": message.body,
            "tags": ["dart"],
            "priority": 5 if message.urgent else 4,
        }
        if message.url:
            payload["click"] = message.url
            payload["actions"] = [{"action": "view", "label": "פתח מודעה", "url": message.url}]
        if message.image_url:
            payload["attach"] = message.image_url
        headers = {"Authorization": f"Bearer {self._config.token}"} if self._config.token else {}
        try:
            resp = self._session.post(
                self._config.server.rstrip("/") + "/", json=payload, headers=headers, timeout=self._timeout
            )
        except requests.RequestException as exc:
            raise NotifyError(f"ntfy: {exc}") from None
        if resp.status_code >= 400:
            raise NotifyError(f"ntfy: HTTP {resp.status_code} {resp.text[:200]}")


class TelegramNotifier:
    """A Telegram bot that messages you (create one with @BotFather)."""

    name = "telegram"
    _API = "https://api.telegram.org/bot{token}/{method}"
    _CAPTION_LIMIT = 1024

    def __init__(self, config: TelegramConfig, session: requests.Session | None = None, timeout: float = 15):
        self._config = config
        self._session = session or requests.Session()
        self._timeout = timeout

    def _call(self, method: str, **params: object) -> None:
        url = self._API.format(token=self._config.bot_token, method=method)
        try:
            resp = self._session.post(url, data={"chat_id": self._config.chat_id, **params}, timeout=self._timeout)
            data = resp.json()
        except (requests.RequestException, ValueError) as exc:
            # Don't echo the exception text: it contains the URL, i.e. the bot token.
            raise NotifyError(f"telegram {method}: {type(exc).__name__}") from None
        if not data.get("ok"):
            raise NotifyError(f"telegram {method}: {data.get('description', 'unknown error')}")

    def send(self, message: Message) -> None:
        text = f"<b>{html.escape(message.title)}</b>\n{html.escape(message.body)}"
        if message.url:
            text += f'\n<a href="{html.escape(message.url, quote=True)}">פתח מודעה</a>'
        if message.image_url and len(text) <= self._CAPTION_LIMIT:
            try:
                self._call("sendPhoto", photo=message.image_url, caption=text, parse_mode="HTML")
                return
            except NotifyError as exc:
                log.debug("sendPhoto failed (%s), sending text only", exc)
        self._call("sendMessage", text=text, parse_mode="HTML")
