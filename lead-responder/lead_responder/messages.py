"""Every text a human reads: owner alerts, the weekly report, the Google Sheet row."""

from __future__ import annotations

import html
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta
from statistics import median
from zoneinfo import ZoneInfo

from .config import ClientConfig
from .models import BLOCKED, HANDLED, IRRELEVANT, NEW, Lead
from .phone import is_mobile, local_format

STATUS_LABELS = {NEW: "חדש", HANDLED: "טופל", IRRELEVANT: "לא רלוונטי", BLOCKED: "נחסם (הצפה)"}
WA_STATUS_LABELS = {"sent": "נשלח", "delivered": "נמסר", "read": "נקרא", "failed": "נכשל"}
_MEDIA_LABELS = {"image": "תמונה", "video": "סרטון", "audio": "הודעה קולית", "voice": "הודעה קולית",
                 "document": "מסמך", "sticker": "סטיקר", "location": "מיקום", "contacts": "איש קשר",
                 "reaction": "תגובה"}


def duration(delta: timedelta) -> str:
    minutes = max(0, round(delta.total_seconds() / 60))
    if minutes < 60:
        return f"{minutes} דק'"
    hours = minutes / 60
    if hours < 48:
        return "שעה" if round(hours, 1) == 1 else f"{hours:.1f}".removesuffix(".0") + " שעות"
    return f"{round(hours / 24)} ימים"


def summary(lead: Lead, limit: int = 500) -> str:
    parts = [lead.source_label] if lead.source else []
    parts += [f"{label}: {value}" for label, value in lead.extra.items()]
    text = " · ".join(parts)
    return text if len(text) <= limit else text[: limit - 1] + "…"


def template_context(lead: Lead, client: ClientConfig) -> dict[str, str]:
    fallback = client.whatsapp.name_fallback if client.whatsapp else ""
    return {
        "name": lead.name or fallback,
        "first_name": lead.first_name or fallback,
        "phone": local_format(lead.phone) or lead.phone_raw,
        "email": lead.email,
        "source": lead.source_label,
        "summary": summary(lead),
        "business": client.business_name,
        "booking_url": client.booking_url,
    }


def fill(params: list[str], lead: Lead, client: ClientConfig) -> list[str]:
    context = template_context(lead, client)
    return [param.format(**context) for param in params]


def inbound_text(message: dict) -> str:
    """The readable part of an incoming WhatsApp message."""
    kind = message.get("type", "")
    body = message.get(kind) if isinstance(message.get(kind), dict) else {}
    if kind == "text":
        return body.get("body", "")
    if kind == "button":
        return body.get("text", "")
    if kind == "interactive":
        return (body.get("button_reply") or body.get("list_reply") or {}).get("title", "")
    if kind == "reaction":
        return f"[תגובה {body.get('emoji', '')}]"
    caption = body.get("caption", "")
    return f"[{_MEDIA_LABELS.get(kind, kind or 'הודעה')}]" + (f" {caption}" if caption else "")


# -- owner alerts -----------------------------------------------------------


@dataclass
class OwnerNotice:
    emoji: str
    headline: str
    lead: Lead
    note: str = ""
    status_line: str = ""


def _ack_status(lead: Lead, client: ClientConfig) -> str:
    if not lead.phone:
        return f"⚠️ מספר הטלפון לא תקין ({lead.phone_raw})" if lead.phone_raw else "⚠️ לא הושאר מספר טלפון"
    if not is_mobile(lead.phone):
        return "☎️ מספר קווי: לא נשלח וואטסאפ, צריך להתקשר"
    if not (client.whatsapp and client.whatsapp.ack_template) or lead.source == "whatsapp":
        return ""
    if lead.wa_status == "failed":
        return "⚠️ הודעת הוואטסאפ לא נמסרה, כדאי להתקשר"
    if lead.wa_status:
        return "✅ נשלחה ללקוח הודעת וואטסאפ אוטומטית"
    return "⏳ הודעת וואטסאפ ללקוח בדרך"


def owner_notice(kind: str, lead: Lead, client: ClientConfig, now: datetime, *,
                 original: Lead | None = None, text: str = "") -> OwnerNotice:
    if kind == "duplicate":
        note = ""
        if original:
            note = (f"אותו מספר השאיר פרטים לפני {duration(now - original.created_at)} "
                    f"(סטטוס: {STATUS_LABELS.get(original.status, original.status)}). לא נשלחה הודעה נוספת ללקוח.")
        return OwnerNotice("🔁", "פנייה חוזרת", lead, note)
    if kind == "remind":
        return OwnerNotice("⏰", "תזכורת: ליד שלא טופל", lead,
                           f"נכנס לפני {duration(now - lead.created_at)} ועוד לא סומן כטופל.")
    if kind == "reply":
        return OwnerNotice("💬", "תשובה חדשה בוואטסאפ", lead, f"«{text}»")
    if kind == "undelivered":
        return OwnerNotice("⚠️", "הודעת הוואטסאפ לא נמסרה", lead, f"כדאי להתקשר. ({text})" if text else "כדאי להתקשר.")
    return OwnerNotice("⚡", "ליד חדש", lead, status_line=_ack_status(lead, client))


def telegram_text(notice: OwnerNotice, client: ClientConfig) -> str:
    lead = notice.lead
    e = html.escape
    lines = [f"{notice.emoji} <b>{e(notice.headline)} · {e(lead.name or 'ללא שם')}</b>"]
    phone = local_format(lead.phone) or lead.phone_raw
    if phone:
        lines.append(f"📞 {e(phone)}")
    if lead.email:
        lines.append(f"✉️ {e(lead.email)}")
    if lead.source:
        lines.append(f"📣 {e(lead.source_label)}")
    lines += [f"• {e(label)}: {e(value)}" for label, value in lead.extra.items()]
    if notice.status_line:
        lines.append(e(notice.status_line))
    if notice.note:
        lines.append(f"<i>{e(notice.note)}</i>")
    lines.append(f"🏷 {e(client.business_name)}")
    return "\n".join(lines)


def telegram_buttons(lead: Lead, done_url: str) -> list[tuple[str, str]]:
    buttons = []
    if is_mobile(lead.phone):
        buttons.append(("💬 וואטסאפ ללקוח", f"https://wa.me/{lead.phone}"))
    if done_url.startswith("https://"):  # Telegram refuses buttons to localhost / plain http
        buttons.append(("✅ סמן כטופל", done_url))
    return buttons


def whatsapp_owner_params(notice: OwnerNotice) -> list[str]:
    """Matches the owner template in the README: {{1}} headline, {{2}} name, {{3}} phone, {{4}} details."""
    lead = notice.lead
    details = " · ".join(p for p in (notice.status_line, notice.note, summary(lead)) if p)
    return [notice.headline, lead.name or "ללא שם", local_format(lead.phone) or lead.phone_raw or "-", details or "-"]


# -- weekly report & sheet ----------------------------------------------------


def weekly_report(client: ClientConfig, leads: list[Lead], start: datetime, end: datetime, tz: ZoneInfo) -> str:
    e = html.escape
    period = f"{start.astimezone(tz):%d.%m}–{(end - timedelta(seconds=1)).astimezone(tz):%d.%m}"
    lines = [f"📊 <b>סיכום שבועי · {e(client.business_name)}</b>", period]
    fresh = [lead for lead in leads if lead.duplicate_of is None and lead.status != BLOCKED]
    if not fresh:
        lines.append("השבוע לא נכנסו לידים חדשים.")
        return "\n".join(lines)

    dealt = [lead for lead in fresh if lead.status in (HANDLED, IRRELEVANT)]
    sources = Counter(lead.source_label or "לא ידוע" for lead in fresh)
    lines.append(f"לידים חדשים: {len(fresh)}")
    lines.append("מקורות: " + " · ".join(f"{e(name)} {count}" for name, count in sources.most_common()))
    lines.append(f"טופלו: {len(dealt)} ({round(100 * len(dealt) / len(fresh))}%)")
    waits = [lead.status_at - lead.created_at for lead in dealt if lead.status_at]
    if waits:
        lines.append(f"זמן עד טיפול (חציון): {duration(median(waits))}")
    replied = sum(1 for lead in fresh if lead.replied_at)
    if replied:
        lines.append(f"ענו בוואטסאפ: {replied}")
    repeats = sum(1 for lead in leads if lead.duplicate_of and lead.status != BLOCKED)
    if repeats:
        lines.append(f"פניות חוזרות: {repeats}")
    still_open = [lead.name or local_format(lead.phone) or "ללא שם" for lead in fresh if lead.status == NEW]
    if still_open:
        shown = ", ".join(e(name) for name in still_open[:5])
        more = f" ועוד {len(still_open) - 5}" if len(still_open) > 5 else ""
        lines.append(f"⏳ עדיין פתוחים: {shown}{more}")
    return "\n".join(lines)


def _local(when: datetime | None, tz: ZoneInfo) -> str:
    return f"{when.astimezone(tz):%d/%m/%Y %H:%M}" if when else ""


def sheet_row(lead: Lead, tz: ZoneInfo) -> dict[str, str | int]:
    """Column names must match COLUMNS in apps-script/sheet-webhook.gs."""
    status = "פנייה חוזרת" if lead.duplicate_of and lead.status == NEW else STATUS_LABELS.get(lead.status, lead.status)
    return {
        "מזהה": lead.id,
        "נוצר": _local(lead.created_at, tz),
        "שם": lead.name,
        "טלפון": local_format(lead.phone) or lead.phone_raw,
        "אימייל": lead.email,
        "מקור": lead.source_label,
        "סטטוס": status,
        "עודכן": _local(lead.status_at, tz),
        "ענה בוואטסאפ": _local(lead.replied_at, tz),
        "וואטסאפ": WA_STATUS_LABELS.get(lead.wa_status, lead.wa_status),
        "פרטים": " | ".join(f"{label}: {value}" for label, value in lead.extra.items()),
    }
