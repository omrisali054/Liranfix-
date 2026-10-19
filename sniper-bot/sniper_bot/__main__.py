"""Command line: ``python -m sniper_bot [-c config.toml] {run,once,test-notify,check,history}``."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from .bot import SniperBot
from .config import Config, ConfigError, load_config
from .fetch import HttpClient
from .matcher import KeywordMatcher
from .notifiers import ConsoleNotifier, Message, Notifier, NtfyNotifier, TelegramNotifier
from .sources import build_sources
from .storage import Store

log = logging.getLogger("sniper_bot")


def build_notifiers(config: Config) -> list[Notifier]:
    # Console output is not counted as delivery when a phone notifier exists,
    # otherwise a failed push would still mark the ad as alerted.
    notifiers: list[Notifier] = []
    if config.ntfy:
        notifiers.append(NtfyNotifier(config.ntfy))
    if config.telegram:
        notifiers.append(TelegramNotifier(config.telegram))
    if not notifiers:
        log.warning("no phone notifier configured ([notify.ntfy] / [notify.telegram]); printing alerts only")
        notifiers.append(ConsoleNotifier())
    return notifiers


def build_matcher(config: Config | None) -> KeywordMatcher:
    if config is None:
        return KeywordMatcher()
    return KeywordMatcher(extra_faults=config.extra_faults, extra_exclude=config.extra_exclude)


def build_bot(config: Config, *, dry_run: bool = False) -> SniperBot:
    http = HttpClient(delay_seconds=config.settings.request_delay_seconds)
    store = Store(":memory:" if dry_run else config.db_path)
    notifiers = [ConsoleNotifier()] if dry_run else build_notifiers(config)
    return SniperBot(config, store, build_sources(http), notifiers, build_matcher(config),
                     seed=False if dry_run else None)


def cmd_run(config: Config, args: argparse.Namespace) -> int:
    bot = build_bot(config)
    log.info("watching %d search(es) every ~%d s; Ctrl+C to stop",
             len(config.searches), config.settings.interval_seconds)
    try:
        bot.run_forever()
    except KeyboardInterrupt:
        log.info("stopped")
    return 0


def cmd_once(config: Config, args: argparse.Namespace) -> int:
    bot = build_bot(config, dry_run=args.dry_run)
    result = bot.scan_once()
    print(f"\n{len(result.alerts)} deal(s) found" + (" (dry run)" if args.dry_run else ""))
    return 1 if result.failed_searches and len(result.failed_searches) == len(config.searches) else 0


def cmd_test_notify(config: Config, args: argparse.Namespace) -> int:
    notifiers = build_notifiers(config)
    message = Message(
        "🎯 בדיקה: בוט המציאות מחובר",
        "אם קיבלת את זה בטלפון — ההתראות עובדות.",
        url="https://www.yad2.co.il/market",
    )
    ok = True
    for notifier in notifiers:
        try:
            notifier.send(message)
            print(f"{notifier.name}: sent")
        except Exception as exc:  # noqa: BLE001 - report every notifier's outcome
            ok = False
            print(f"{notifier.name}: FAILED – {exc}")
    return 0 if ok else 1


def cmd_check(config: Config | None, args: argparse.Namespace) -> int:
    matcher = build_matcher(config)
    faults = matcher.faults(args.text)
    excluded = matcher.excluded_by(args.text)
    if faults:
        for hit in faults:
            print(f"fault   [{hit.category}] {hit.phrase}")
    else:
        print("fault   (none)")
    print(f"exclude {excluded!r}" if excluded else "exclude (none)")
    return 0


def cmd_history(config: Config, args: argparse.Namespace) -> int:
    store = Store(config.db_path)
    for sent_at, search, title, price, url in store.recent_alerts(args.n):
        price_text = f"{price:,} ₪" if price is not None else "-"
        print(f"{sent_at}  {price_text:>9}  [{search}] {title}\n    {url}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m sniper_bot", description="Deal alerts for fixable devices.")
    parser.add_argument("-c", "--config", default="config.toml", help="config file (default: config.toml)")
    parser.add_argument("-v", "--verbose", action="store_true", help="debug logging (shows why ads were skipped)")
    parser.add_argument("--log-file", help="write the log to this file instead of the console")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("run", help="scan forever and send alerts")
    once = sub.add_parser("once", help="scan one time (for cron / Task Scheduler)")
    once.add_argument("--dry-run", action="store_true",
                      help="print every current match; no phone alerts, nothing saved")
    sub.add_parser("test-notify", help="send a test notification to your phone")
    check = sub.add_parser("check", help="show which fault / exclude keywords a text triggers")
    check.add_argument("text")
    history = sub.add_parser("history", help="list recent alerts")
    history.add_argument("-n", type=int, default=20)
    args = parser.parse_args(argv)

    config: Config | None = None
    try:
        if args.command != "check" or Path(args.config).exists():
            config = load_config(args.config)
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 2

    level = "DEBUG" if args.verbose else (config.settings.log_level if config else "INFO")
    logging.basicConfig(level=level, format="%(asctime)s %(levelname)-7s %(message)s",
                        datefmt="%Y-%m-%d %H:%M:%S", filename=args.log_file, encoding="utf-8")
    logging.getLogger("urllib3").setLevel(logging.WARNING)

    commands = {"run": cmd_run, "once": cmd_once, "test-notify": cmd_test_notify,
                "check": cmd_check, "history": cmd_history}
    return commands[args.command](config, args)


if __name__ == "__main__":
    sys.exit(main())
