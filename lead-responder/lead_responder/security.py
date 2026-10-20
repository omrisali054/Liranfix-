"""Signed "mark as handled" links, and checking that a webhook really came from Meta."""

from __future__ import annotations

import base64
import hashlib
import hmac


def _sign(secret: str, message: str) -> str:
    digest = hmac.new(secret.encode(), message.encode(), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).decode().rstrip("=")[:22]


def make_token(secret: str, lead_id: int) -> str:
    """``<lead id>-<signature>``: short enough for a WhatsApp URL button, impossible to guess."""
    return f"{lead_id}-{_sign(secret, f'done:{lead_id}')}"


def read_token(secret: str, token: str) -> int | None:
    lead_id, _, signature = token.partition("-")
    if not lead_id.isdigit() or not signature:
        return None
    if not hmac.compare_digest(signature, _sign(secret, f"done:{lead_id}")):
        return None
    return int(lead_id)


def meta_signature_ok(app_secret: str, body: bytes, header: str) -> bool:
    """Meta signs every webhook body with the app secret: ``X-Hub-Signature-256: sha256=<hex>``."""
    if not app_secret or not header.startswith("sha256="):
        return False
    expected = hmac.new(app_secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(header[len("sha256="):], expected)


def same_secret(given: str, expected: str) -> bool:
    return bool(given) and hmac.compare_digest(given.encode(), expected.encode())
