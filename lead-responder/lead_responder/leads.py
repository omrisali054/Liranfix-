"""Turns whatever a form builder POSTs (Elementor, Wix, WordPress, Make, Zapier, Facebook Lead Ads)
into a :class:`LeadInput`: name, phone, email, and everything else as labelled extras."""

from __future__ import annotations

import re
from typing import Any, Iterable, Mapping

from .models import LeadInput
from .phone import is_mobile, normalize_phone

_ALIASES = {
    "name": ("name", "fullname", "yourname", "contactname", "customername",
             "שם", "שםמלא", "שםהלקוח", "שםולשםמשפחה"),
    "first_name": ("firstname", "fname", "givenname", "שםפרטי"),
    "last_name": ("lastname", "lname", "surname", "familyname", "שםמשפחה"),
    "phone": ("phone", "phonenumber", "tel", "telephone", "mobile", "mobilephone", "cell", "cellphone",
              "yourphone", "yourtel", "whatsapp", "whatsappnumber",
              "טלפון", "נייד", "טלפוןנייד", "מספרטלפון", "מספרנייד", "פלאפון", "סלולרי", "וואטסאפ"),
    "email": ("email", "emailaddress", "mail", "youremail", "אימייל", "מייל", "דואל", "דואראלקטרוני",
              "כתובתמייל"),
    "source": ("source", "utmsource", "leadsource", "formname", "מקור"),
}
_IGNORED = {"key", "formid", "queriedid", "postid", "referertitle", "action", "grecaptcharesponse",
            "wpnonce", "wpcf7", "wpcf7unitid", "wpcf7containerpost", "wpcf7locale", "wpcf7version",
            "submit", "honeypot"}
_PHONE_HINTS = ("phone", "tel", "mobile", "טלפון", "נייד", "פלאפון", "סלולר")
_PHONE_SHAPE = re.compile(r"^[\d\s+\-().]{9,20}$")
_LABEL_KEYS = ("name", "label", "title", "key", "id")
_BRACKETS = re.compile(r"\[([^\]]*)\]")

MAX_EXTRA_FIELDS = 25
MAX_VALUE_LENGTH = 500


def _norm(label: str) -> str:
    label = label.lower().removeprefix("field:")  # Wix: "field:email"
    return re.sub(r"[\W_]", "", label)


_ROLE_BY_ALIAS = {alias: role for role, aliases in _ALIASES.items() for alias in aliases}


def _unbracket(form: Mapping[str, Any]) -> dict:
    """``fields[email][value]=x`` (Elementor, PHP-style forms) → ``{"fields": {"email": {"value": "x"}}}``."""
    nested: dict = {}
    for key, value in form.items():
        head = key.split("[", 1)[0]
        parts = [head, *_BRACKETS.findall(key[len(head):])]
        node = nested
        for part in parts[:-1]:
            child = node.setdefault(part, {})
            if not isinstance(child, dict):
                child = node[part] = {"": child}
            node = child
        node[parts[-1]] = value
    return nested


def _scalar(value: Any) -> str:
    if isinstance(value, list):
        return ", ".join(_scalar(v) for v in value if not isinstance(v, (Mapping, list)))
    return "" if value is None else str(value)


def _collect(key: str, value: Any, out: list[tuple[str, str]]) -> None:
    if isinstance(value, Mapping):
        # A {"name"/"label"/"title": ..., "value(s)": ...} pair: Facebook field_data, Elementor, Wix.
        label = next((value[k] for k in _LABEL_KEYS if isinstance(value.get(k), str) and value[k]), None)
        if label and ("value" in value or "values" in value):
            out.append((label, _scalar(value.get("value", value.get("values")))))
            return
        for sub_key, sub_value in value.items():
            _collect(str(sub_key), sub_value, out)
    elif isinstance(value, list) and any(isinstance(v, (Mapping, list)) for v in value):
        for item in value:
            _collect(key, item, out)
    else:
        out.append((key, _scalar(value)))


def _pairs(payload: Mapping[str, Any]) -> tuple[list[tuple[str, str]], str]:
    data = _unbracket(payload) if any("[" in str(k) for k in payload) else dict(payload)
    form_name = ""
    if isinstance(data.get("form"), Mapping):  # Elementor sends form[id] / form[name]
        form_name = _scalar(data.pop("form").get("name", ""))
    if isinstance(data.get("meta"), Mapping):  # Elementor's date / user agent / IP: noise for the owner
        data.pop("meta")
    out: list[tuple[str, str]] = []
    for key, value in data.items():
        _collect(str(key), value, out)
    return out, form_name


def parse_lead(payload: Mapping[str, Any], field_map: Mapping[str, Iterable[str]] | None = None,
               source: str = "") -> LeadInput:
    overrides = {_norm(label): role for role, labels in (field_map or {}).items() for label in labels}
    pairs, form_name = _pairs(payload)
    found: dict[str, str] = {}
    extra: dict[str, str] = {}
    for label, value in pairs:
        value = value.strip()
        norm = _norm(label)
        if not value or not norm or norm in _IGNORED:
            continue
        role = overrides.get(norm) or _ROLE_BY_ALIAS.get(norm)
        if role and role not in found:
            found[role] = value
        elif len(extra) < MAX_EXTRA_FIELDS and label not in extra:
            extra[label.strip()[:60]] = value[:MAX_VALUE_LENGTH]

    if "phone" not in found:
        # "מספר טלפון לחזרה", "Phone (mobile)": a hint in the label and a phone-shaped value;
        # failing that ("מספר לחזרה"), any value that is nothing but a mobile number.
        phone_like = [(label, value) for label, value in extra.items()
                      if _PHONE_SHAPE.match(value) and normalize_phone(value)]
        hinted = [(label, value) for label, value in phone_like if any(h in _norm(label) for h in _PHONE_HINTS)]
        mobile = [(label, value) for label, value in phone_like if is_mobile(normalize_phone(value))]
        for label, _ in (hinted or mobile)[:1]:
            found["phone"] = extra.pop(label)
    if "email" not in found:
        for label, value in list(extra.items()):
            if "mail" in _norm(label) or "מייל" in label:
                if "@" in value:
                    found["email"] = extra.pop(label)
                    break

    name = found.get("name") or " ".join(p for p in (found.get("first_name"), found.get("last_name")) if p)
    return LeadInput(
        name=name[:120],
        phone_raw=found.get("phone", "")[:40],
        email=found.get("email", "")[:120],
        source=(source or found.get("source") or form_name)[:60],
        extra=extra,
    )


def parse_facebook_lead(lead: Mapping[str, Any]) -> LeadInput:
    """A lead object from the Graph API (``GET /<leadgen_id>?fields=field_data,...``)."""
    parsed = parse_lead({"field_data": lead.get("field_data", [])})
    parsed.source = "instagram" if lead.get("platform") == "ig" else "facebook"
    parsed.external_id = f"fb:{lead.get('id', '')}"
    for label, key in (("קמפיין", "campaign_name"), ("מודעה", "ad_name")):
        if lead.get(key):
            parsed.extra[label] = str(lead[key])
    return parsed
