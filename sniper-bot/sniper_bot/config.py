"""Loads and validates ``config.toml``."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any, Mapping

SOURCES = ("yad2", "rss")


class ConfigError(ValueError):
    pass


@dataclass
class Settings:
    interval_seconds: int = 120
    jitter_seconds: int = 60
    request_delay_seconds: float = 3.0
    db_path: str = "sniper.db"
    seed_on_first_run: bool = True
    fetch_details: bool = True
    failure_alert_after: int = 5
    log_level: str = "INFO"


@dataclass
class SearchConfig:
    name: str
    source: str
    max_price: int
    min_price: int = 0
    query: str = ""  # yad2: the search box text
    url: str = ""  # rss: feed URL
    keywords: list[str] = field(default_factory=list)  # at least one must appear
    exclude: list[str] = field(default_factory=list)
    conditions: list[str] = field(default_factory=list)  # yad2 condition ids
    product_types: list[str] = field(default_factory=list)  # yad2 product type ids
    require_fault: bool = False
    allow_no_price: bool = False
    include_business: bool = False
    pages: int = 1


@dataclass
class TelegramConfig:
    bot_token: str
    chat_id: str


@dataclass
class NtfyConfig:
    topic: str
    server: str = "https://ntfy.sh"
    token: str = ""


@dataclass
class Config:
    settings: Settings
    searches: list[SearchConfig]
    telegram: TelegramConfig | None = None
    ntfy: NtfyConfig | None = None
    extra_faults: list[str] = field(default_factory=list)
    extra_exclude: list[str] = field(default_factory=list)
    db_path: Path = Path("sniper.db")


_LIST_OF_STR = {"keywords", "exclude", "conditions", "product_types"}


def _build(cls: type, raw: Mapping[str, Any], where: str) -> Any:
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


def _validate_search(search: SearchConfig, where: str) -> None:
    if search.source not in SOURCES:
        raise ConfigError(f"{where}: source must be one of {', '.join(SOURCES)}")
    if search.source == "yad2" and not search.query.strip():
        raise ConfigError(f"{where}: yad2 searches need a query")
    if search.source == "rss" and not search.url.strip():
        raise ConfigError(f"{where}: rss searches need a url")
    if not isinstance(search.max_price, int) or search.max_price <= 0:
        raise ConfigError(f"{where}: max_price must be a positive whole number")
    if not 0 <= search.min_price <= search.max_price:
        raise ConfigError(f"{where}: min_price must be between 0 and max_price")
    if not 1 <= search.pages <= 10:
        raise ConfigError(f"{where}: pages must be between 1 and 10")


def load_config(path: str | Path, env: Mapping[str, str] = os.environ) -> Config:
    path = Path(path)
    try:
        with path.open("rb") as fh:
            raw = tomllib.load(fh)
    except FileNotFoundError:
        raise ConfigError(f"config file not found: {path}") from None
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{path}: {exc}") from None

    settings = _build(Settings, raw.get("settings", {}), "[settings]")

    searches = []
    for i, raw_search in enumerate(raw.get("search", []), start=1):
        where = f"[[search]] #{i} ({raw_search.get('name', 'unnamed')})"
        search = _build(SearchConfig, raw_search, where)
        _validate_search(search, where)
        searches.append(search)
    if not searches:
        raise ConfigError("add at least one [[search]] section")
    names = [s.name for s in searches]
    if len(set(names)) != len(names):
        raise ConfigError("every [[search]] needs a unique name")

    notify = raw.get("notify", {})
    tg_raw = notify.get("telegram", {})
    tg_token = env.get("TELEGRAM_BOT_TOKEN") or tg_raw.get("bot_token", "")
    tg_chat = env.get("TELEGRAM_CHAT_ID") or str(tg_raw.get("chat_id", ""))
    if bool(tg_token) != bool(tg_chat):
        raise ConfigError("telegram needs both bot_token and chat_id")
    telegram = TelegramConfig(tg_token, tg_chat) if tg_token else None

    ntfy_raw = dict(notify.get("ntfy", {}))
    for key, var in (("topic", "NTFY_TOPIC"), ("token", "NTFY_TOKEN")):
        if env.get(var):
            ntfy_raw[key] = env[var]
    ntfy = _build(NtfyConfig, ntfy_raw, "[notify.ntfy]") if ntfy_raw.get("topic") else None
    if ntfy and "CHANGE-ME" in ntfy.topic:
        raise ConfigError("[notify.ntfy]: pick your own topic name (anyone who knows a topic can read it)")

    keywords = raw.get("keywords", {})
    db_path = Path(settings.db_path)
    if not db_path.is_absolute():
        db_path = path.resolve().parent / db_path

    return Config(
        settings=settings,
        searches=searches,
        telegram=telegram,
        ntfy=ntfy,
        extra_faults=[str(k) for k in keywords.get("extra_faults", [])],
        extra_exclude=[str(k) for k in keywords.get("extra_exclude", [])],
        db_path=db_path,
    )
