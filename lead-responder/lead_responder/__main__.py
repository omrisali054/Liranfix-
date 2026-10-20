"""Command line: ``python -m lead_responder [-c config.toml] {serve,check,test-lead,telegram-chats,leads,report,failed}``."""

from __future__ import annotations

import argparse
import html
import logging
import re
import sys

from .channels import (ConsoleSheets, ConsoleTelegram, ConsoleWhatsApp, FacebookClient, SendError, SheetsClient,
                       TelegramClient, WhatsAppClient)
from .config import Config, ConfigError, load_config
from .engine import REPORT, Channels, Engine, Worker
from .messages import STATUS_LABELS
from .models import LeadInput
from .phone import local_format
from .storage import Store

log = logging.getLogger("lead_responder")


def build_channels(config: Config, *, dry_run: bool = False) -> dict[str, Channels]:
    version = config.server.graph_version
    bots: dict[str, TelegramClient] = {}  # clients on the same bot share one client
    channels = {}
    for slug, client in config.clients.items():
        if dry_run:
            channels[slug] = Channels(
                whatsapp=ConsoleWhatsApp() if client.whatsapp else None,
                telegram=ConsoleTelegram() if client.owner.telegram_chat_ids else None,
                sheets=ConsoleSheets() if client.sheets else None,
            )
            continue
        token = client.telegram_bot_token
        if token and client.owner.telegram_chat_ids and token not in bots:
            bots[token] = TelegramClient(token)
        channels[slug] = Channels(
            whatsapp=WhatsAppClient(client.whatsapp, version) if client.whatsapp else None,
            telegram=bots.get(token) if client.owner.telegram_chat_ids else None,
            sheets=SheetsClient(client.sheets) if client.sheets else None,
            facebook=FacebookClient(client.meta.page_access_token, version) if client.meta.page_access_token else None,
        )
    return channels


def build_engine(config: Config, *, dry_run: bool = False) -> Engine:
    store = Store(":memory:" if dry_run else config.db_path)
    admin = None
    if config.admin.telegram_chat_id and config.telegram_bot_token:
        admin = ConsoleTelegram() if dry_run else TelegramClient(config.telegram_bot_token)
    return Engine(config, store, build_channels(config, dry_run=dry_run), admin=admin)


def _client(config: Config, slug: str):
    if slug not in config.clients:
        known = ", ".join(config.clients) or "none yet, add clients/<name>.toml"
        raise SystemExit(f"unknown client {slug!r} (clients: {known})")
    return config.clients[slug]


def _plain(text: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", text))


def cmd_serve(config: Config, args: argparse.Namespace) -> int:
    from waitress import serve

    from .web import create_app

    engine = build_engine(config, dry_run=args.dry_run)
    worker = Worker(engine)
    worker.start()
    app = create_app(engine, wake=worker.wake)
    log.info("serving %d client(s) on %s:%d%s", len(config.clients), config.server.host, config.server.port,
             " (dry run: nothing is really sent)" if args.dry_run else "")
    try:
        serve(app, host=config.server.host, port=config.server.port, threads=8, ident="lead-responder")
    except KeyboardInterrupt:
        pass
    finally:
        worker.stop()
    return 0


def cmd_check(config: Config, args: argparse.Namespace) -> int:
    url = config.server.public_url
    print(f"config OK · {len(config.clients)} client(s) · db {config.db_path}")
    if not url.startswith("https://"):
        print("  ! public_url is not https: Meta webhooks and the Telegram 'handled' button need https")
    if not config.server.verify_token:
        print("  ! [server] verify_token is empty: you can't subscribe Meta webhooks without it")
    for slug, client in config.clients.items():
        wa = client.whatsapp
        print(f"\n■ {client.business_name}  ({slug})")
        print(f"  lead webhook : {url}/c/{slug}/lead?key={client.key}")
        print(f"  meta webhook : {url}/c/{slug}/meta   (verify token from [server])")
        print(f"  whatsapp     : {'ack=' + wa.ack_template if wa and wa.ack_template else 'no instant reply'}"
              f"{', follow-up=' + wa.followup_template if wa and wa.followup_template else ''}")
        owners = [f"telegram×{len(client.owner.telegram_chat_ids)}"] if client.owner.telegram_chat_ids else []
        owners += [f"whatsapp×{len(client.owner.whatsapp_numbers)}"] if client.owner.whatsapp_numbers else []
        print(f"  owner alerts : {', '.join(owners)}")
        print(f"  sheet        : {'yes' if client.sheets else 'no'} · lead ads direct: "
              f"{'yes' if client.meta.page_access_token else 'no (use Make/Zapier → lead webhook)'}")
        if client.meta.page_access_token and not client.meta.app_secret:
            print("  ! [meta] app_secret is empty: Meta webhooks for this client will be refused")
        if wa and not client.meta.app_secret:
            print("  ! no [meta] app_secret: WhatsApp replies and delivery failures won't reach you")
    return 0


def cmd_test_lead(config: Config, args: argparse.Namespace) -> int:
    _client(config, args.client)
    engine = build_engine(config, dry_run=args.dry_run)
    result = engine.intake(args.client, LeadInput(name=args.name, phone_raw=args.phone, email=args.email,
                                                  source="test", extra={"הערה": "ליד בדיקה מ-test-lead"}))
    print(f"lead: {result.outcome}" + (f" #{result.lead.id}" if result.lead else ""))
    if result.outcome == "duplicate":
        print("  same phone within dedupe_hours: only the owner hears about it, the lead gets no 2nd WhatsApp")
    engine.run_due()
    failed = [row for row in engine.store.failed_tasks() if result.lead and row[2] == result.lead.id]
    for _, kind, _, _, error in failed:
        print(f"FAILED {kind}: {error}")
    if result.lead:
        print(f"handled link: {engine.done_url(result.lead.id)}")
        pending = engine.store.pending_tasks(result.lead.id)
        for kind, due in pending:
            print(f"  later: {kind} at {due.astimezone(config.tz):%a %d/%m %H:%M}")
    return 1 if failed else 0


def cmd_telegram_chats(config: Config, args: argparse.Namespace) -> int:
    token = config.telegram_bot_token
    if args.client:
        token = _client(config, args.client).telegram_bot_token
    if not token:
        print("no bot token: set [telegram] bot_token in config.toml (or TELEGRAM_BOT_TOKEN)", file=sys.stderr)
        return 2
    try:
        chats = TelegramClient(token).recent_chats()
    except SendError as exc:
        print(f"telegram: {exc}", file=sys.stderr)
        return 1
    if not chats:
        print("nobody wrote to the bot lately: ask the owner to open the bot and press Start, then run this again")
    for chat_id, who in chats:
        print(f"{chat_id:>15}  {who}")
    return 0


def cmd_leads(config: Config, args: argparse.Namespace) -> int:
    _client(config, args.client)
    store = Store(config.db_path)
    for lead in store.recent_leads(args.client, args.n):
        status = STATUS_LABELS.get(lead.status, lead.status) + (" (חוזר)" if lead.duplicate_of else "")
        phone = local_format(lead.phone) or lead.phone_raw
        print(f"#{lead.id:<5} {lead.created_at.astimezone(config.tz):%d/%m %H:%M}  {status:<12} "
              f"{lead.name or '-':<20} {phone:<13} {lead.source_label}")
    return 0


def cmd_report(config: Config, args: argparse.Namespace) -> int:
    _client(config, args.client)
    engine = build_engine(config)
    if args.send:
        engine.store.add_task(args.client, REPORT, engine.clock())
        engine.run_due()
        print("sent")
    else:
        print(_plain(engine.report_text(args.client)))
    return 0


def cmd_failed(config: Config, args: argparse.Namespace) -> int:
    store = Store(config.db_path)
    for client, kind, lead_id, due, error in store.failed_tasks(args.n):
        print(f"{due}  {client:<16} {kind:<9} lead #{lead_id}  {error}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m lead_responder",
                                     description="Instant WhatsApp replies to new leads, for many businesses at once.")
    parser.add_argument("-c", "--config", default="config.toml", help="server config (default: config.toml)")
    parser.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    parser.add_argument("--log-file", help="write the log to this file instead of the console")
    sub = parser.add_subparsers(dest="command", required=True)
    serve = sub.add_parser("serve", help="run the web server and the sender")
    serve.add_argument("--dry-run", action="store_true", help="print messages instead of sending, keep nothing")
    sub.add_parser("check", help="validate the config and print every client's webhook URLs")
    test = sub.add_parser("test-lead", help="push a fake lead through a client's whole flow")
    test.add_argument("client")
    test.add_argument("--phone", required=True, help="your own mobile, e.g. 0501234567")
    test.add_argument("--name", default="ליד בדיקה")
    test.add_argument("--email", default="")
    test.add_argument("--dry-run", action="store_true", help="print instead of sending")
    chats = sub.add_parser("telegram-chats", help="show chat ids of people who wrote to the bot")
    chats.add_argument("client", nargs="?")
    leads = sub.add_parser("leads", help="list a client's recent leads")
    leads.add_argument("client")
    leads.add_argument("-n", type=int, default=20)
    report = sub.add_parser("report", help="show (or --send) a client's weekly report")
    report.add_argument("client")
    report.add_argument("--send", action="store_true")
    failed = sub.add_parser("failed", help="messages that could not be sent")
    failed.add_argument("-n", type=int, default=20)
    args = parser.parse_args(argv)

    try:
        config = load_config(args.config)
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 2

    level = "DEBUG" if args.verbose else config.server.log_level
    logging.basicConfig(level=level, format="%(asctime)s %(levelname)-7s %(message)s",
                        datefmt="%Y-%m-%d %H:%M:%S", filename=args.log_file, encoding="utf-8")
    logging.getLogger("urllib3").setLevel(logging.WARNING)

    commands = {"serve": cmd_serve, "check": cmd_check, "test-lead": cmd_test_lead,
                "telegram-chats": cmd_telegram_chats, "leads": cmd_leads, "report": cmd_report, "failed": cmd_failed}
    return commands[args.command](config, args)


if __name__ == "__main__":
    sys.exit(main())
