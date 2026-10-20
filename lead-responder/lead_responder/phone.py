"""Phone numbers: whatever people type in a form → digits WhatsApp accepts (``972501234567``)."""

from __future__ import annotations

import re

_NON_DIGITS = re.compile(r"\D")


def normalize_phone(raw: str, default_country: str = "972") -> str:
    """International digits without '+', or '' when the input can't be a phone number."""
    raw = (raw or "").strip()
    digits = _NON_DIGITS.sub("", raw)
    if not digits:
        return ""
    if raw.startswith("+"):
        pass
    elif digits.startswith("00"):
        digits = digits[2:]
    elif digits.startswith("0"):
        digits = default_country + digits[1:]
    elif default_country == "972" and len(digits) == 9 and digits[0] == "5":
        digits = "972" + digits  # 501234567: a mobile number typed without the leading 0
    elif not digits.startswith(default_country):
        digits = default_country + digits
    if digits.startswith("9720"):
        digits = "972" + digits[4:]  # +972 (0)50-...
    if digits.startswith("972") and len(digits) not in (11, 12):
        return ""
    return digits if 8 <= len(digits) <= 15 else ""


def is_mobile(phone: str) -> bool:
    """Can this number get WhatsApp? Israeli landlines (02/03/04/08/09) can't; foreign numbers we trust."""
    if phone.startswith("972"):
        return len(phone) == 12 and phone[3] == "5"
    return bool(phone)


def local_format(phone: str) -> str:
    """How an Israeli reads it: 050-1234567 / 03-1234567, else +<international>."""
    if phone.startswith("972"):
        national = "0" + phone[3:]
        split = 3 if len(national) == 10 else 2
        return f"{national[:split]}-{national[split:]}"
    return f"+{phone}" if phone else ""
