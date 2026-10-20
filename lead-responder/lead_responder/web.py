"""HTTP endpoints.

    POST /c/<client>/lead?key=...   any form / Make / Zapier sends leads here
    GET  /c/<client>/meta           Meta's webhook handshake
    POST /c/<client>/meta           WhatsApp replies + delivery statuses, Facebook Lead Ads
    GET  /d/<token>                 the owner's "mark as handled" page (link in every alert)
    POST /d/<token>
    GET  /health
"""

from __future__ import annotations

import json
import logging
from typing import Callable

from flask import Flask, abort, redirect, render_template_string, request

from .engine import Engine
from .leads import parse_lead
from .messages import STATUS_LABELS, summary
from .models import HANDLED, IRRELEVANT, NEW
from .phone import is_mobile, local_format
from .security import meta_signature_ok, read_token, same_secret

log = logging.getLogger(__name__)

_LEAD_PAGE = """<!doctype html>
<html lang="he" dir="rtl">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{{ lead.name or "ליד" }} · {{ business }}</title>
<style>
  body { font-family: system-ui, -apple-system, "Segoe UI", Arial, sans-serif; background: #f4f5f7; color: #1d2330;
         margin: 0; padding: 16px; }
  .card { max-width: 480px; margin: 0 auto; background: #fff; border-radius: 14px; padding: 20px;
          box-shadow: 0 1px 4px rgba(0,0,0,.08); }
  h1 { font-size: 1.35rem; margin: 0 0 4px; }
  .muted { color: #687083; font-size: .9rem; }
  .status { display: inline-block; margin: 12px 0; padding: 4px 12px; border-radius: 99px; background: #eef1f6; }
  .status.handled { background: #e3f6e8; color: #17692f; }
  .row { margin: 6px 0; }
  .actions { display: grid; gap: 10px; margin-top: 18px; }
  a.btn, button { display: block; width: 100%; box-sizing: border-box; text-align: center; font-size: 1.05rem;
                  padding: 13px; border-radius: 10px; border: 0; cursor: pointer; text-decoration: none; }
  .primary { background: #1f8f45; color: #fff; }
  .secondary { background: #eef1f6; color: #1d2330; }
  .saved { background: #e3f6e8; color: #17692f; padding: 10px; border-radius: 10px; margin-bottom: 12px; }
</style>
</head>
<body>
<div class="card">
  {% if saved %}<div class="saved">✅ נשמר.{% if lead.status != "new" %} התזכורות לליד הזה הופסקו.{% endif %}</div>{% endif %}
  <div class="muted">{{ business }} · ליד #{{ lead.id }}</div>
  <h1>{{ lead.name or "ללא שם" }}</h1>
  <div class="status {{ lead.status }}">{{ status }}</div>
  {% if phone %}<div class="row">📞 <a href="tel:+{{ lead.phone }}">{{ phone }}</a></div>{% endif %}
  {% if lead.email %}<div class="row">✉️ {{ lead.email }}</div>{% endif %}
  {% if details %}<div class="row muted">{{ details }}</div>{% endif %}
  <div class="actions">
    {% if whatsapp %}<a class="btn secondary" href="https://wa.me/{{ lead.phone }}">💬 לפתוח וואטסאפ עם הלקוח</a>{% endif %}
    <form method="post"><input type="hidden" name="status" value="handled">
      <button class="primary">✅ טופל, דיברנו</button></form>
    <form method="post"><input type="hidden" name="status" value="irrelevant">
      <button class="secondary">🚫 לא רלוונטי</button></form>
    {% if lead.status != "new" %}<form method="post"><input type="hidden" name="status" value="new">
      <button class="secondary">↩️ להחזיר לפתוח</button></form>{% endif %}
  </div>
</div>
</body>
</html>"""


def _form_payload() -> dict:
    payload = request.get_json(silent=True)
    if isinstance(payload, dict):
        return payload
    # Keep multi-value fields (checkbox groups) instead of silently dropping all but the first.
    return {key: ", ".join(values) for key, values in request.form.to_dict(flat=False).items()}


def create_app(engine: Engine, wake: Callable[[], None] = lambda: None) -> Flask:
    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = 512 * 1024
    config = engine.config

    def client_or_404(slug: str):
        client = config.clients.get(slug)
        if client is None:
            abort(404)
        return client

    @app.get("/health")
    def health():
        return {"ok": True, "clients": len(config.clients)}

    @app.post("/c/<slug>/lead")
    def lead(slug: str):
        client = client_or_404(slug)
        payload = _form_payload()
        given = request.args.get("key") or request.headers.get("X-Lead-Key") or str(payload.get("key", ""))
        if not same_secret(given, client.key):
            return {"ok": False, "error": "bad key"}, 403
        incoming = parse_lead(payload, client.fields, source=request.args.get("source", ""))
        if not (incoming.phone_raw or incoming.email or incoming.name or incoming.extra):
            return {"ok": False, "error": "empty lead"}, 422
        if not incoming.phone_raw:
            # Still accepted (the form must not show the visitor an error), but worth a look:
            # add the field's label to [fields] phone in the client file.
            log.warning("%s: no phone found in fields %s", slug, sorted(payload))
        result = engine.intake(slug, incoming)
        wake()
        return {"ok": True, "status": result.outcome, "lead_id": result.lead.id if result.lead else None}

    @app.get("/c/<slug>/meta")
    def meta_verify(slug: str):
        client_or_404(slug)
        if request.args.get("hub.mode") == "subscribe" and \
                same_secret(request.args.get("hub.verify_token", ""), config.server.verify_token):
            return request.args.get("hub.challenge", ""), 200, {"Content-Type": "text/plain"}
        return "forbidden", 403

    @app.post("/c/<slug>/meta")
    def meta_event(slug: str):
        client = client_or_404(slug)
        body = request.get_data()
        if not meta_signature_ok(client.meta.app_secret, body, request.headers.get("X-Hub-Signature-256", "")):
            if not client.meta.app_secret:
                log.warning("%s: Meta webhook refused, set [meta] app_secret in the client file", slug)
            return "bad signature", 403
        try:
            payload = json.loads(body)
        except ValueError:
            return "bad json", 400
        engine.handle_meta(slug, payload)
        wake()
        return "ok"

    def lead_from_token(token: str):
        lead_id = read_token(config.server.secret, token)
        found = engine.store.get_lead(lead_id) if lead_id else None
        if found is None or found.client not in config.clients:
            abort(404)
        return found

    @app.get("/d/<token>")
    def lead_page(token: str):
        found = lead_from_token(token)
        return render_template_string(
            _LEAD_PAGE,
            lead=found,
            business=config.clients[found.client].business_name,
            status=STATUS_LABELS.get(found.status, found.status),
            phone=local_format(found.phone) if found.phone else found.phone_raw,
            whatsapp=is_mobile(found.phone),
            details=summary(found),
            saved=request.args.get("saved") == "1",
        )

    @app.post("/d/<token>")
    def lead_update(token: str):
        found = lead_from_token(token)
        status = request.form.get("status", "")
        if status not in (HANDLED, IRRELEVANT, NEW):
            abort(400)
        engine.set_status(found.id, status)
        wake()
        return redirect(f"/d/{token}?saved=1", code=303)

    return app
