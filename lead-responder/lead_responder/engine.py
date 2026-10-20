"""The heart of it: a lead comes in → queue the instant reply, the owner alert, reminders and
follow-ups → a worker thread sends whatever is due, retries failures, and stops chasing a lead
the moment the owner marks it handled or the lead answers."""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable, Mapping

from .channels import LeadFetcher, SendError, SheetSender, TelegramSender, WhatsAppSender
from .config import ClientConfig, Config
from .leads import parse_facebook_lead
from .messages import (OwnerNotice, fill, inbound_text, owner_notice, sheet_row, telegram_buttons, telegram_text,
                       weekly_report, whatsapp_owner_params)
from .models import BLOCKED, NEW, Lead, LeadInput, Task
from .phone import is_mobile, normalize_phone
from .schedule import QuietTimes
from .security import make_token
from .storage import Store

log = logging.getLogger(__name__)

ACK, NOTIFY, REMIND, FOLLOWUP, SHEET, FETCH_FB, REPORT = (
    "ack", "notify", "remind", "followup", "sheet", "fetch_fb", "report")
RETRY_MINUTES = (1, 5, 15, 60)  # after the 1st, 2nd, 3rd, 4th failure; the 5th failure is final
INBOUND_MATCH_DAYS = 30  # a WhatsApp message from a number that left a lead this recently is a reply
REPORT_WEEKDAY, REPORT_HOUR = 6, 9  # Sunday 09:00


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class Channels:
    whatsapp: WhatsAppSender | None = None
    telegram: TelegramSender | None = None
    sheets: SheetSender | None = None
    facebook: LeadFetcher | None = None


@dataclass
class Intake:
    lead: Lead | None
    outcome: str  # created / duplicate / already-received / rate-limited


class Postpone(Exception):
    def __init__(self, until: datetime):
        self.until = until


class Engine:
    def __init__(
        self,
        config: Config,
        store: Store,
        channels: Mapping[str, Channels],
        *,
        admin: TelegramSender | None = None,
        clock: Callable[[], datetime] = utcnow,
    ):
        self.config = config
        self.store = store
        self.channels = channels
        self.admin = admin
        self.clock = clock
        self.quiet = {slug: QuietTimes.from_rules(c.rules, config.tz) for slug, c in config.clients.items()}
        self._intake_lock = threading.Lock()
        self._executors: dict[str, Callable[[Task, ClientConfig, Channels, Lead | None], None]] = {
            ACK: self._do_ack, NOTIFY: self._do_notify, REMIND: self._do_remind, FOLLOWUP: self._do_followup,
            SHEET: self._do_sheet, FETCH_FB: self._do_fetch_fb, REPORT: self._do_report,
        }

    def done_url(self, lead_id: int) -> str:
        return f"{self.config.server.public_url}/d/{make_token(self.config.server.secret, lead_id)}"

    # -- a new lead ---------------------------------------------------------

    def intake(self, slug: str, incoming: LeadInput, *, send_ack: bool = True) -> Intake:
        # One at a time: a double-clicked submit button must not produce two WhatsApp replies.
        with self._intake_lock:
            return self._intake(slug, incoming, send_ack)

    def _intake(self, slug: str, incoming: LeadInput, send_ack: bool) -> Intake:
        client = self.config.clients[slug]
        rules = client.rules
        now = self.clock()
        if incoming.external_id and self.store.has_external_id(slug, incoming.external_id):
            return Intake(None, "already-received")
        phone = normalize_phone(incoming.phone_raw)

        if self.store.count_since(slug, now - timedelta(hours=1)) >= rules.max_leads_per_hour:
            lead = self.store.add_lead(slug, incoming, phone, now, status=BLOCKED)
            log.warning("%s: over %d leads/hour, stored without sending", slug, rules.max_leads_per_hour)
            self._alert_admin(f"flood:{slug}", f"🚨 {client.business_name}: יותר מ-{rules.max_leads_per_hour} "
                                               "לידים בשעה. לא נשלחות הודעות; בדוק אם מישהו מציף את הטופס.")
            return Intake(lead, "rate-limited")

        previous = self.store.latest_by_phone(slug, phone, now - timedelta(hours=rules.dedupe_hours)) if phone else None
        original_id = (previous.duplicate_of or previous.id) if previous else None
        lead = self.store.add_lead(slug, incoming, phone, now, duplicate_of=original_id)
        if lead is None:
            return Intake(None, "already-received")

        add = self.store.add_task
        if previous:
            add(slug, NOTIFY, now, lead.id, {"type": "duplicate"})
            if client.sheets:
                add(slug, SHEET, now, lead.id)
            log.info("%s: lead #%d is a repeat of #%d", slug, lead.id, original_id)
            return Intake(lead, "duplicate")

        if send_ack:
            add(slug, ACK, now, lead.id)
        add(slug, NOTIFY, now, lead.id, {"type": "new"})
        quiet = self.quiet[slug]
        # A lead at 23:00 pushes every reminder to 08:00; one reminder then is enough.
        for due in sorted({quiet.next_allowed(now + timedelta(minutes=m)) for m in rules.owner_reminder_minutes}):
            add(slug, REMIND, due, lead.id)
        if client.whatsapp and client.whatsapp.followup_template and is_mobile(phone):
            for due in sorted({quiet.next_allowed(now + timedelta(hours=h)) for h in rules.followup_hours}):
                add(slug, FOLLOWUP, due, lead.id)
        if client.sheets:
            add(slug, SHEET, now, lead.id)
        log.info("%s: new lead #%d %r from %s", slug, lead.id, lead.name, lead.source or "?")
        return Intake(lead, "created")

    # -- the owner taps "handled" ---------------------------------------------

    def set_status(self, lead_id: int, status: str) -> Lead | None:
        lead = self.store.get_lead(lead_id)
        if lead is None:
            return None
        now = self.clock()
        targets = [lead.id] + ([lead.duplicate_of] if lead.duplicate_of else [])
        for target in targets:
            if self.store.set_status(target, status, now):
                self.store.cancel_tasks(target, (REMIND, FOLLOWUP))
                if self.config.clients.get(lead.client) and self.config.clients[lead.client].sheets:
                    self.store.add_task(lead.client, SHEET, now, target)
        log.info("%s: lead #%d marked %s", lead.client, lead.id, status)
        return self.store.get_lead(lead_id)

    # -- webhooks from Meta ---------------------------------------------------

    def handle_meta(self, slug: str, payload: Mapping) -> None:
        for entry in payload.get("entry", []) or []:
            for change in entry.get("changes", []) or []:
                value = change.get("value") or {}
                if payload.get("object") == "whatsapp_business_account" and change.get("field") == "messages":
                    self._on_whatsapp(slug, value)
                elif payload.get("object") == "page" and change.get("field") == "leadgen":
                    self._on_leadgen(slug, value)

    def _on_leadgen(self, slug: str, value: Mapping) -> None:
        leadgen_id = str(value.get("leadgen_id", ""))
        now = self.clock()
        if leadgen_id and self.store.mark_once(f"leadgen:{slug}:{leadgen_id}", now):
            self.store.add_task(slug, FETCH_FB, now, payload={"leadgen_id": leadgen_id})

    def _on_whatsapp(self, slug: str, value: Mapping) -> None:
        client = self.config.clients[slug]
        now = self.clock()
        for status in value.get("statuses", []) or []:
            message_id, state = str(status.get("id", "")), str(status.get("status", ""))
            lead = self.store.update_wa_status(message_id, state)
            if state == "failed" and lead and lead.status == NEW and self.store.mark_once(f"wafail:{message_id}", now):
                error = (status.get("errors") or [{}])[0]
                reason = error.get("title") or error.get("message") or ""
                self.store.add_task(slug, NOTIFY, now, lead.id, {"type": "undelivered", "text": reason})

        forward = bool(client.whatsapp and client.whatsapp.forward_replies)
        owner_phones = {normalize_phone(number) for number in client.owner.whatsapp_numbers}
        names = {c.get("wa_id"): (c.get("profile") or {}).get("name", "") for c in value.get("contacts", []) or []}
        for message in value.get("messages", []) or []:
            sender = str(message.get("from", ""))
            if not sender or not self.store.mark_once(f"wamsg:{message.get('id', '')}", now):
                continue
            text = inbound_text(message)
            phone = normalize_phone("+" + sender)
            if phone in owner_phones:  # the owner answering an alert is not a lead
                continue
            lead = self.store.latest_by_phone(slug, phone, now - timedelta(days=INBOUND_MATCH_DAYS))
            if lead and lead.duplicate_of:  # the follow-ups hang on the first lead of this person
                lead = self.store.get_lead(lead.duplicate_of) or lead
            if lead:
                first_reply = self.store.mark_replied(lead.id, now)
                self.store.cancel_tasks(lead.id, (FOLLOWUP,))
                if forward:
                    self.store.add_task(slug, NOTIFY, now, lead.id, {"type": "reply", "text": text})
                if first_reply and client.sheets:
                    self.store.add_task(slug, SHEET, now, lead.id)
            elif forward:  # someone wrote to the business number out of the blue: that's a lead too
                self.intake(slug, LeadInput(name=names.get(sender, ""), phone_raw="+" + sender,
                                            source="whatsapp", extra={"הודעה": text}), send_ack=False)

    # -- the worker -------------------------------------------------------------

    def tick(self) -> int:
        self.queue_weekly_reports()
        return self.run_due()

    def run_due(self, limit: int = 50) -> int:
        tasks = self.store.due_tasks(self.clock(), limit)
        for task in tasks:
            client = self.config.clients.get(task.client)
            if client is None:
                self.store.fail_task(task.id, "client config removed")
                continue
            lead = self.store.get_lead(task.lead_id) if task.lead_id else None
            try:
                self._executors[task.kind](task, client, self.channels.get(task.client, Channels()), lead)
            except Postpone as later:
                self.store.postpone_task(task.id, later.until)
                continue
            except SendError as exc:
                self._failed(task, client, lead, exc)
                continue
            except Exception as exc:  # noqa: BLE001 - a bug in one task must not stop the others
                log.exception("task #%d (%s) crashed", task.id, task.kind)
                self._failed(task, client, lead, SendError(f"{type(exc).__name__}: {exc}"))
                continue
            self.store.finish_task(task.id)
        return len(tasks)

    def _failed(self, task: Task, client: ClientConfig, lead: Lead | None, exc: SendError) -> None:
        now = self.clock()
        if not exc.permanent and task.attempts < len(RETRY_MINUTES):
            delay = timedelta(minutes=RETRY_MINUTES[task.attempts])
            self.store.retry_task(task.id, now + delay, str(exc))
            log.warning("%s: %s for lead #%s failed (%s), retrying in %s", client.slug, task.kind, task.lead_id, exc, delay)
            return
        self.store.fail_task(task.id, str(exc))
        log.error("%s: %s for lead #%s failed for good: %s", client.slug, task.kind, task.lead_id, exc)
        self._alert_admin(f"fail:{client.slug}:{task.kind}", f"⚠️ {client.business_name}: {task.kind} נכשל\n{exc}")
        if task.kind in (ACK, FOLLOWUP) and lead and lead.status == NEW:
            self.store.add_task(client.slug, NOTIFY, now, lead.id, {"type": "undelivered", "text": str(exc)[:200]})

    def _alert_admin(self, key: str, text: str) -> None:
        """You (the operator) hear about broken tokens and floods before your client does. Once an hour per issue."""
        chat = self.config.admin.telegram_chat_id
        now = self.clock()
        if not (self.admin and chat) or not self.store.mark_once(f"admin:{key}:{now:%Y%m%d%H}", now):
            return
        try:
            self.admin.send(chat, text)
        except SendError as exc:
            log.error("admin alert failed: %s", exc)

    # -- what each task does ------------------------------------------------------

    def _require(self, lead: Lead | None, task: Task) -> Lead:
        if lead is None:
            raise SendError(f"lead #{task.lead_id} not found", permanent=True)
        return lead

    def _do_ack(self, task: Task, client: ClientConfig, channels: Channels, lead: Lead | None) -> None:
        lead = self._require(lead, task)
        wa = client.whatsapp
        if not (channels.whatsapp and wa and wa.ack_template and is_mobile(lead.phone)):
            return
        message_id = channels.whatsapp.send_template(lead.phone, wa.ack_template, fill(wa.ack_params, lead, client))
        self.store.set_wa_message(lead.id, message_id, "sent")

    def _do_followup(self, task: Task, client: ClientConfig, channels: Channels, lead: Lead | None) -> None:
        lead = self._require(lead, task)
        wa = client.whatsapp
        if lead.status != NEW or lead.replied_at or not (channels.whatsapp and wa and wa.followup_template):
            return
        self._wait_if_quiet(client)
        message_id = channels.whatsapp.send_template(lead.phone, wa.followup_template,
                                                     fill(wa.followup_params, lead, client))
        self.store.set_wa_message(lead.id, message_id, "sent")

    def _do_remind(self, task: Task, client: ClientConfig, channels: Channels, lead: Lead | None) -> None:
        lead = self._require(lead, task)
        if lead.status != NEW:
            return
        self._wait_if_quiet(client)
        self._tell_owner(client, channels, owner_notice("remind", lead, client, self.clock()))

    def _do_notify(self, task: Task, client: ClientConfig, channels: Channels, lead: Lead | None) -> None:
        lead = self._require(lead, task)
        original = self.store.get_lead(lead.duplicate_of) if lead.duplicate_of else None
        notice = owner_notice(task.payload.get("type", "new"), lead, client, self.clock(),
                              original=original, text=task.payload.get("text", ""))
        self._tell_owner(client, channels, notice)

    def _do_sheet(self, task: Task, client: ClientConfig, channels: Channels, lead: Lead | None) -> None:
        lead = self._require(lead, task)
        if channels.sheets:
            channels.sheets.upsert(sheet_row(lead, self.config.tz))

    def _do_fetch_fb(self, task: Task, client: ClientConfig, channels: Channels, lead: Lead | None) -> None:
        if not channels.facebook:
            raise SendError("got a Lead Ads webhook but [meta] page_access_token is not set", permanent=True)
        data = channels.facebook.fetch_lead(task.payload["leadgen_id"])
        data.setdefault("id", task.payload["leadgen_id"])
        self.intake(client.slug, parse_facebook_lead(data))

    def _do_report(self, task: Task, client: ClientConfig, channels: Channels, lead: Lead | None) -> None:
        if not channels.telegram:
            return
        text = self.report_text(client.slug)
        for chat_id in client.owner.telegram_chat_ids:
            channels.telegram.send(chat_id, text)

    def _wait_if_quiet(self, client: ClientConfig) -> None:
        now = self.clock()
        quiet = self.quiet[client.slug]
        if quiet.is_quiet(now):
            raise Postpone(quiet.next_allowed(now))

    def _tell_owner(self, client: ClientConfig, channels: Channels, notice: OwnerNotice) -> None:
        """Delivered if at least one of the owner's chats got it; otherwise the task is retried."""
        lead = notice.lead
        attempts: list[Callable[[], None]] = []
        if channels.telegram:
            text, buttons = telegram_text(notice, client), telegram_buttons(lead, self.done_url(lead.id))
            for chat_id in client.owner.telegram_chat_ids:
                attempts.append(lambda chat_id=chat_id: channels.telegram.send(chat_id, text, buttons))
        wa = client.whatsapp
        if channels.whatsapp and wa and wa.owner_template:
            params = whatsapp_owner_params(notice)
            token = make_token(self.config.server.secret, lead.id)
            for number in client.owner.whatsapp_numbers:
                to = normalize_phone(number)
                attempts.append(lambda to=to: channels.whatsapp.send_template(to, wa.owner_template, params, token))
        errors: list[SendError] = []
        for attempt in attempts:
            try:
                attempt()
            except SendError as exc:
                errors.append(exc)
        if errors and len(errors) == len(attempts):
            raise errors[-1]
        for exc in errors:
            log.error("%s: one owner channel failed: %s", client.slug, exc)

    # -- weekly report ------------------------------------------------------------

    def report_text(self, slug: str) -> str:
        end = self.clock()
        start = end - timedelta(days=7)
        leads = self.store.leads_between(slug, start, end)
        return weekly_report(self.config.clients[slug], leads, start, end, self.config.tz)

    def queue_weekly_reports(self) -> None:
        now = self.clock()
        local = now.astimezone(self.config.tz)
        if local.weekday() != REPORT_WEEKDAY or local.hour < REPORT_HOUR:
            return
        for slug, client in self.config.clients.items():
            if client.rules.weekly_report and client.owner.telegram_chat_ids:
                if self.store.mark_once(f"report:{slug}:{local.date()}", now):
                    self.store.add_task(slug, REPORT, now)


class Worker(threading.Thread):
    """Runs the engine every ``interval`` seconds, or right away when :meth:`wake` is called."""

    def __init__(self, engine: Engine, interval: float = 20):
        super().__init__(name="lead-worker", daemon=True)
        self.engine = engine
        self.interval = interval
        self._wake = threading.Event()
        self._stopping = threading.Event()

    def wake(self) -> None:
        self._wake.set()

    def stop(self) -> None:
        self._stopping.set()
        self._wake.set()

    def run(self) -> None:
        while not self._stopping.is_set():
            try:
                while self.engine.tick() and not self._stopping.is_set():
                    pass  # a full batch: there may be more due
            except Exception:  # noqa: BLE001 - keep the worker alive whatever happens
                log.exception("worker tick failed")
            self._wake.wait(self.interval)
            self._wake.clear()
