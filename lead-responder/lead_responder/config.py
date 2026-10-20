"""Loads ``config.toml`` (your server) and ``clients/*.toml`` (one file per business you sell to)."""

from __future__ import annotations

import os
import re
import tomllib
from dataclasses import dataclass, field, fields
from datetime import time
from pathlib import Path
from typing import Any, Mapping
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .phone import normalize_phone

# Placeholders a template parameter may use, e.g. ack_params = ["{first_name}", "{business}"].
TEMPLATE_FIELDS = ("name", "first_name", "phone", "email", "source", "summary", "business", "booking_url")

_SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{1,40}$")


class ConfigError(ValueError):
    pass


@dataclass
class ServerSettings:
    public_url: str  # https://leads.example.com, used in buttons and the webhook URLs you hand out
    secret: str  # signs the "mark as handled" links
    verify_token: str = ""  # the token you type when subscribing Meta webhooks
    host: str = "0.0.0.0"
    port: int = 8080
    db_path: str = "leads.db"
    clients_dir: str = "clients"
    timezone: str = "Asia/Jerusalem"
    graph_version: str = "v25.0"
    log_level: str = "INFO"


@dataclass
class AdminConfig:
    telegram_chat_id: str = ""  # you, the operator: failures (expired token, rate limit) land here


@dataclass
class WhatsAppConfig:
    phone_number_id: str
    access_token: str
    language: str = "he"
    ack_template: str = ""  # sent to the lead seconds after the form is filled
    ack_params: list[str] = field(default_factory=lambda: ["{first_name}", "{business}"])
    followup_template: str = ""  # sent when nobody handled the lead after rules.followup_hours
    followup_params: list[str] = field(default_factory=lambda: ["{first_name}", "{business}"])
    owner_template: str = ""  # alerts to the owner's own WhatsApp (4 params + a URL button)
    name_fallback: str = "🙂"  # {first_name} when the form had no name
    forward_replies: bool = True  # forward incoming WhatsApp messages to the owner


@dataclass
class OwnerConfig:
    telegram_chat_ids: list[str] = field(default_factory=list)
    whatsapp_numbers: list[str] = field(default_factory=list)  # needs whatsapp.owner_template


@dataclass
class Rules:
    owner_reminder_minutes: list[int] = field(default_factory=lambda: [15, 60])
    followup_hours: list[float] = field(default_factory=lambda: [24])
    dedupe_hours: float = 24  # same phone again within this window: tell the owner, don't message the lead twice
    max_leads_per_hour: int = 30  # beyond this (a bot hammering the form) nothing is sent
    quiet_hours: str = "21:00-08:00"  # reminders and follow-ups wait; "" to disable
    shabbat: bool = True  # no reminders / follow-ups from Friday shabbat_start to Saturday shabbat_end
    shabbat_start: str = "15:00"
    shabbat_end: str = "20:30"
    weekly_report: bool = True  # Sunday 09:00 summary on Telegram


@dataclass
class SheetsConfig:
    url: str  # the Apps Script web app URL (apps-script/sheet-webhook.gs)
    secret: str


@dataclass
class MetaConfig:
    app_secret: str = ""  # verifies the webhooks Meta sends to /c/<slug>/meta
    page_access_token: str = ""  # reads Facebook / Instagram Lead Ads directly


@dataclass
class ClientConfig:
    slug: str
    business_name: str
    key: str  # secret part of the lead webhook URL
    owner: OwnerConfig
    rules: Rules
    whatsapp: WhatsAppConfig | None = None
    sheets: SheetsConfig | None = None
    meta: MetaConfig = field(default_factory=MetaConfig)
    booking_url: str = ""
    telegram_bot_token: str = ""  # the client's own bot, else the server's
    fields: dict[str, list[str]] = field(default_factory=dict)  # extra form field names: name / phone / email


@dataclass
class Config:
    server: ServerSettings
    admin: AdminConfig
    clients: dict[str, ClientConfig]
    telegram_bot_token: str = ""
    db_path: Path = Path("leads.db")
    tz: ZoneInfo = field(default_factory=lambda: ZoneInfo("Asia/Jerusalem"))


_LIST_OF_STR = {"ack_params", "followup_params", "telegram_chat_ids", "whatsapp_numbers"}
_CLIENT_SECTIONS = {"owner", "rules", "whatsapp", "sheets", "meta", "fields"}
_CLIENT_KEYS = {"business_name", "key", "booking_url", "telegram_bot_token"}


def _build(cls: type, raw: Any, where: str) -> Any:
    if not isinstance(raw, Mapping):
        raise ConfigError(f"{where} must be a table")
    known = {f.name for f in fields(cls)}
    unknown = set(raw) - known
    if unknown:
        raise ConfigError(f"{where}: unknown key(s) {', '.join(sorted(unknown))}")
    values = dict(raw)
    for key in _LIST_OF_STR & set(values):
        if not isinstance(values[key], list):
            raise ConfigError(f"{where}: {key} must be a list")
        values[key] = [str(v) for v in values[key]]
    try:
        return cls(**values)
    except TypeError as exc:  # missing required keys
        raise ConfigError(f"{where}: {exc}") from None


def _read_toml(path: Path) -> dict:
    try:
        with path.open("rb") as fh:
            return tomllib.load(fh)
    except FileNotFoundError:
        raise ConfigError(f"config file not found: {path}") from None
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{path}: {exc}") from None


def _check_secret(value: str, where: str) -> None:
    if "CHANGE-ME" in value:
        raise ConfigError(f"{where}: replace CHANGE-ME with your own random value "
                          "(python -c \"import secrets; print(secrets.token_urlsafe(24))\")")
    if len(value) < 16:
        raise ConfigError(f"{where}: use at least 16 random characters")


def parse_clock(value: str, where: str) -> time:
    try:
        hours, minutes = value.split(":")
        return time(int(hours), int(minutes))
    except ValueError:
        raise ConfigError(f"{where}: expected HH:MM, got {value!r}") from None


def parse_quiet_hours(value: str, where: str) -> tuple[time, time] | None:
    if not value.strip():
        return None
    try:
        start, end = value.split("-")
    except ValueError:
        raise ConfigError(f"{where}: quiet_hours must look like 21:00-08:00") from None
    return parse_clock(start.strip(), f"{where} quiet_hours"), parse_clock(end.strip(), f"{where} quiet_hours")


def _check_params(params: list[str], where: str) -> None:
    sample = {name: "x" for name in TEMPLATE_FIELDS}
    for param in params:
        try:
            param.format(**sample)
        except (KeyError, IndexError, ValueError) as exc:
            raise ConfigError(f"{where}: bad placeholder in {param!r} ({exc}); "
                              f"available: {', '.join('{' + f + '}' for f in TEMPLATE_FIELDS)}") from None


def _validate_rules(rules: Rules, where: str) -> None:
    if any(m <= 0 for m in rules.owner_reminder_minutes):
        raise ConfigError(f"{where}: owner_reminder_minutes must be positive")
    if any(h <= 0 for h in rules.followup_hours):
        raise ConfigError(f"{where}: followup_hours must be positive")
    if rules.max_leads_per_hour <= 0:
        raise ConfigError(f"{where}: max_leads_per_hour must be positive")
    parse_quiet_hours(rules.quiet_hours, where)
    parse_clock(rules.shabbat_start, f"{where} shabbat_start")
    parse_clock(rules.shabbat_end, f"{where} shabbat_end")


def load_client(path: Path, server_bot_token: str = "") -> ClientConfig:
    slug = path.stem
    where = f"clients/{path.name}"
    if not _SLUG.match(slug):
        raise ConfigError(f"{where}: file name must be lowercase letters, digits and dashes (it becomes the URL)")
    raw = _read_toml(path)
    top = {k: v for k, v in raw.items() if k not in _CLIENT_SECTIONS}
    unknown = set(top) - _CLIENT_KEYS
    if unknown:
        raise ConfigError(f"{where}: unknown key(s) {', '.join(sorted(unknown))}")
    for required in ("business_name", "key"):
        if not str(top.get(required, "")).strip():
            raise ConfigError(f"{where}: {required} is required")

    owner = _build(OwnerConfig, raw.get("owner", {}), f"{where} [owner]")
    rules = _build(Rules, raw.get("rules", {}), f"{where} [rules]")
    _validate_rules(rules, f"{where} [rules]")
    whatsapp = _build(WhatsAppConfig, raw["whatsapp"], f"{where} [whatsapp]") if "whatsapp" in raw else None
    sheets = _build(SheetsConfig, raw["sheets"], f"{where} [sheets]") if "sheets" in raw else None
    meta = _build(MetaConfig, raw.get("meta", {}), f"{where} [meta]")

    field_map = raw.get("fields", {})
    if not isinstance(field_map, Mapping) or set(field_map) - {"name", "phone", "email"}:
        raise ConfigError(f"{where} [fields]: only name / phone / email lists are allowed")
    field_map = {k: [str(v) for v in (vals if isinstance(vals, list) else [vals])] for k, vals in field_map.items()}

    client = ClientConfig(slug=slug, owner=owner, rules=rules, whatsapp=whatsapp, sheets=sheets,
                          meta=meta, fields=field_map, **{k: str(v) for k, v in top.items()})

    _check_secret(client.key, f"{where} key")
    if whatsapp:
        _check_params(whatsapp.ack_params, f"{where} [whatsapp] ack_params")
        _check_params(whatsapp.followup_params, f"{where} [whatsapp] followup_params")
        if not whatsapp.phone_number_id or not whatsapp.access_token:
            raise ConfigError(f"{where} [whatsapp]: phone_number_id and access_token are required")
    for number in owner.whatsapp_numbers:
        if not normalize_phone(number):
            raise ConfigError(f"{where} [owner]: {number!r} is not a phone number")
    if sheets:
        _check_secret(sheets.secret, f"{where} [sheets] secret")

    bot_token = client.telegram_bot_token or server_bot_token
    can_telegram = bool(bot_token and owner.telegram_chat_ids)
    can_whatsapp = bool(owner.whatsapp_numbers and whatsapp and whatsapp.owner_template)
    if owner.telegram_chat_ids and not bot_token:
        raise ConfigError(f"{where}: telegram_chat_ids need a bot token ([telegram] in config.toml)")
    if owner.whatsapp_numbers and not (whatsapp and whatsapp.owner_template):
        raise ConfigError(f"{where}: owner whatsapp_numbers need [whatsapp] owner_template")
    if not (can_telegram or can_whatsapp):
        raise ConfigError(f"{where}: no way to reach the owner, add [owner] telegram_chat_ids or whatsapp_numbers")
    client.telegram_bot_token = bot_token
    return client


def load_config(path: str | Path, env: Mapping[str, str] = os.environ) -> Config:
    path = Path(path)
    raw = _read_toml(path)

    server_raw = dict(raw.get("server", {}))
    for key, var in (("secret", "LEAD_SECRET"), ("verify_token", "META_VERIFY_TOKEN")):
        if env.get(var):
            server_raw[key] = env[var]
    server = _build(ServerSettings, server_raw, "[server]")
    _check_secret(server.secret, "[server] secret")
    if not server.public_url.startswith(("http://", "https://")):
        raise ConfigError("[server] public_url must start with https://")
    server.public_url = server.public_url.rstrip("/")
    try:
        tz = ZoneInfo(server.timezone)
    except (ZoneInfoNotFoundError, ValueError):
        raise ConfigError(f"[server] unknown timezone {server.timezone!r} (pip install tzdata on Windows)") from None

    admin = _build(AdminConfig, raw.get("admin", {}), "[admin]")
    bot_token = env.get("TELEGRAM_BOT_TOKEN") or str(raw.get("telegram", {}).get("bot_token", ""))

    base = path.resolve().parent
    clients_dir = Path(server.clients_dir)
    clients_dir = clients_dir if clients_dir.is_absolute() else base / clients_dir
    clients = {}
    for client_path in sorted(clients_dir.glob("*.toml")):
        client = load_client(client_path, bot_token)
        clients[client.slug] = client
    keys = [c.key for c in clients.values()]
    if len(set(keys)) != len(keys):
        raise ConfigError("every client needs its own key")

    db_path = Path(server.db_path)
    return Config(
        server=server,
        admin=admin,
        clients=clients,
        telegram_bot_token=bot_token,
        db_path=db_path if db_path.is_absolute() else base / db_path,
        tz=tz,
    )
