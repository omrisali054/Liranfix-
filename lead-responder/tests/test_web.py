import hashlib
import hmac
import json

from lead_responder.models import HANDLED, LeadInput
from lead_responder.security import make_token
from lead_responder.web import create_app

from .helpers import SECRET, RigTest

KEY = "clinic-key-0123456789"


class WebTest(RigTest):
    def setUp(self):
        self.r = self.rig()
        self.wakes = []
        app = create_app(self.r.engine, wake=lambda: self.wakes.append(1))
        app.testing = True
        self.http = app.test_client()

    def test_health(self):
        self.assertEqual(self.http.get("/health").json, {"ok": True, "clients": 1})

    def test_json_lead(self):
        resp = self.http.post(f"/c/clinic/lead?key={KEY}&source=website",
                              json={"name": "דנה", "phone": "0501234567", "service": "פילינג"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json["status"], "created")
        lead = self.r.store.get_lead(resp.json["lead_id"])
        self.assertEqual((lead.name, lead.phone, lead.source), ("דנה", "972501234567", "website"))
        self.assertEqual(self.wakes, [1])

    def test_form_post_with_key_header(self):
        resp = self.http.post("/c/clinic/lead", headers={"X-Lead-Key": KEY},
                              data={"שם": "יוסי", "טלפון": "052-1112222", "שירותים": ["א", "ב"]})
        self.assertEqual(resp.status_code, 200)
        lead = self.r.store.get_lead(resp.json["lead_id"])
        self.assertEqual(lead.extra, {"שירותים": "א, ב"})

    def test_wrong_key_or_client(self):
        self.assertEqual(self.http.post("/c/clinic/lead?key=nope", json={"phone": "0501234567"}).status_code, 403)
        self.assertEqual(self.http.post("/c/clinic/lead", json={"phone": "0501234567"}).status_code, 403)
        self.assertEqual(self.http.post(f"/c/other/lead?key={KEY}", json={"phone": "1"}).status_code, 404)
        self.assertEqual(self.r.store.recent_leads("clinic"), [])

    def test_empty_lead(self):
        self.assertEqual(self.http.post(f"/c/clinic/lead?key={KEY}", json={"g-recaptcha-response": "x"}).status_code,
                         422)

    def test_lead_without_a_phone_is_still_accepted(self):
        resp = self.http.post(f"/c/clinic/lead?key={KEY}", json={"name": "דנה", "עיר": "חיפה"})
        self.assertEqual(resp.status_code, 200)

    def test_meta_handshake(self):
        ok = self.http.get("/c/clinic/meta?hub.mode=subscribe&hub.verify_token=verify-me&hub.challenge=1158201444")
        self.assertEqual((ok.status_code, ok.text), (200, "1158201444"))
        bad = self.http.get("/c/clinic/meta?hub.mode=subscribe&hub.verify_token=nope&hub.challenge=1")
        self.assertEqual(bad.status_code, 403)

    def meta_post(self, payload, secret="app-secret"):
        body = json.dumps(payload).encode()
        signature = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        return self.http.post("/c/clinic/meta", data=body, content_type="application/json",
                              headers={"X-Hub-Signature-256": signature})

    def test_meta_event_needs_a_valid_signature(self):
        event = {"object": "whatsapp_business_account", "entry": [{"changes": [{"field": "messages", "value": {
            "messages": [{"from": "972521112222", "id": "m1", "type": "text", "text": {"body": "שלום"}}]}}]}]}
        self.assertEqual(self.meta_post(event, secret="wrong").status_code, 403)
        self.assertEqual(self.r.store.recent_leads("clinic"), [])
        self.assertEqual(self.meta_post(event).status_code, 200)
        self.assertEqual(len(self.r.store.recent_leads("clinic")), 1)

    def test_handled_page(self):
        lead = self.r.engine.intake("clinic", LeadInput(name="דנה <b>", phone_raw="0501234567")).lead
        token = make_token(SECRET, lead.id)

        page = self.http.get(f"/d/{token}")
        self.assertEqual(page.status_code, 200)
        self.assertIn("דנה &lt;b&gt;", page.text)  # escaped
        self.assertIn("https://wa.me/972501234567", page.text)

        resp = self.http.post(f"/d/{token}", data={"status": "handled"})
        self.assertEqual(resp.status_code, 303)
        self.assertEqual(self.r.store.get_lead(lead.id).status, HANDLED)
        self.assertIn("נשמר", self.http.get(resp.headers["Location"]).text)

    def test_forged_or_unknown_token(self):
        self.assertEqual(self.http.get("/d/1-forged").status_code, 404)
        self.assertEqual(self.http.get(f"/d/{make_token(SECRET, 999)}").status_code, 404)
        self.assertEqual(self.http.post(f"/d/{make_token(SECRET, 999)}", data={"status": "handled"}).status_code, 404)
