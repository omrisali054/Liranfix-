import hashlib
import hmac
import unittest

import requests

from lead_responder.channels import (FacebookClient, SendError, SheetsClient, TelegramClient, WhatsAppClient,
                                     clean_param)
from lead_responder.config import SheetsConfig, WhatsAppConfig
from lead_responder.security import make_token, meta_signature_ok, read_token

from .helpers import FakeSession

WA = WhatsAppConfig(phone_number_id="111", access_token="SECRET-TOKEN")


class WhatsAppTest(unittest.TestCase):
    def test_template_payload(self):
        session = FakeSession((200, {"messages": [{"id": "wamid.1"}]}))
        message_id = WhatsAppClient(WA, session=session).send_template(
            "972501234567", "lead_ack", ["דנה", "שורה\nשנייה"], button_param="5-abc")
        self.assertEqual(message_id, "wamid.1")
        call = session.calls[0]
        self.assertEqual(call["url"], "https://graph.facebook.com/v25.0/111/messages")
        self.assertEqual(call["headers"], {"Authorization": "Bearer SECRET-TOKEN"})
        template = call["json"]["template"]
        self.assertEqual(template["name"], "lead_ack")
        self.assertEqual(template["language"], {"code": "he"})
        body, button = template["components"]
        self.assertEqual([p["text"] for p in body["parameters"]], ["דנה", "שורה שנייה"])
        self.assertEqual(button["parameters"], [{"type": "text", "text": "5-abc"}])

    def test_error_kinds(self):
        cases = [
            ((400, {"error": {"code": 132001, "message": "Template name does not exist"}}), True),
            ((401, {"error": {"code": 190, "message": "Session has expired"}}), True),
            ((400, {"error": {"code": 131056, "message": "pair rate limit"}}), False),
            ((503, "Service Unavailable"), False),
        ]
        for reply, permanent in cases:
            with self.subTest(reply=reply):
                with self.assertRaises(SendError) as ctx:
                    WhatsAppClient(WA, session=FakeSession(reply)).send_template("972501234567", "t", [])
                self.assertEqual(ctx.exception.permanent, permanent)
        with self.assertRaisesRegex(SendError, "access token rejected"):
            WhatsAppClient(WA, session=FakeSession(cases[1][0])).send_template("972501234567", "t", [])

    def test_network_errors_are_retryable_and_quiet(self):
        session = FakeSession(requests.ConnectionError("https://graph.facebook.com/... SECRET-TOKEN"))
        with self.assertRaises(SendError) as ctx:
            WhatsAppClient(WA, session=session).send_template("972501234567", "t", [])
        self.assertFalse(ctx.exception.permanent)
        self.assertNotIn("SECRET-TOKEN", str(ctx.exception))

    def test_clean_param(self):
        self.assertEqual(clean_param("a\n\tb      c"), "a b   c")
        self.assertEqual(clean_param("   "), "-")


class TelegramTest(unittest.TestCase):
    def test_buttons(self):
        session = FakeSession()
        TelegramClient("BOT-TOKEN", session=session).send("42", "<b>hi</b>", [("✅", "https://x.example/d/1")])
        call = session.calls[0]
        self.assertTrue(call["url"].endswith("/sendMessage"))
        self.assertEqual(call["json"]["parse_mode"], "HTML")
        self.assertEqual(call["json"]["reply_markup"],
                         {"inline_keyboard": [[{"text": "✅", "url": "https://x.example/d/1"}]]})

    def test_errors_do_not_leak_the_token(self):
        session = FakeSession(requests.ConnectionError("https://api.telegram.org/botBOT-TOKEN/sendMessage"))
        with self.assertRaises(SendError) as ctx:
            TelegramClient("BOT-TOKEN", session=session).send("42", "x")
        self.assertNotIn("BOT-TOKEN", str(ctx.exception))

    def test_bad_chat_is_permanent(self):
        session = FakeSession((400, {"ok": False, "description": "Bad Request: chat not found"}))
        with self.assertRaises(SendError) as ctx:
            TelegramClient("t", session=session).send("1", "x")
        self.assertTrue(ctx.exception.permanent)

    def test_recent_chats(self):
        session = FakeSession((200, {"ok": True, "result": [
            {"message": {"chat": {"id": 42, "first_name": "Dana", "username": "dana"}}},
            {"message": {"chat": {"id": -100, "title": "צוות"}}},
        ]}))
        self.assertEqual(TelegramClient("t", session=session).recent_chats(), [("42", "Dana @dana"), ("-100", "צוות")])


class SheetsTest(unittest.TestCase):
    config = SheetsConfig(url="https://script.example/exec", secret="s3cret-0123456789")

    def test_upsert(self):
        session = FakeSession((200, {"ok": True}))
        SheetsClient(self.config, session=session).upsert({"מזהה": 1})
        self.assertEqual(session.calls[0]["json"], {"secret": "s3cret-0123456789", "row": {"מזהה": 1}})

    def test_login_page_means_wrong_deployment(self):
        with self.assertRaises(SendError) as ctx:
            SheetsClient(self.config, session=FakeSession((200, "<html>Sign in</html>"))).upsert({})
        self.assertTrue(ctx.exception.permanent)


class FacebookTest(unittest.TestCase):
    def test_fetch_lead(self):
        session = FakeSession((200, {"id": "9", "field_data": []}))
        self.assertEqual(FacebookClient("PAGE-TOKEN", session=session).fetch_lead("9")["id"], "9")
        call = session.calls[0]
        self.assertEqual(call["url"], "https://graph.facebook.com/v25.0/9")
        self.assertEqual(call["headers"], {"Authorization": "Bearer PAGE-TOKEN"})
        self.assertIn("field_data", call["params"]["fields"])


class SecurityTest(unittest.TestCase):
    def test_tokens(self):
        token = make_token("secret-0123456789", 42)
        self.assertEqual(read_token("secret-0123456789", token), 42)
        self.assertIsNone(read_token("other-secret-0123", token))
        self.assertIsNone(read_token("secret-0123456789", token.replace("42-", "43-")))
        self.assertIsNone(read_token("secret-0123456789", "junk"))

    def test_meta_signature(self):
        body = b'{"a":1}'
        real = "sha256=" + hmac.new(b"secret", body, hashlib.sha256).hexdigest()
        self.assertTrue(meta_signature_ok("secret", body, real))
        self.assertFalse(meta_signature_ok("other", body, real))
        self.assertFalse(meta_signature_ok("secret", b'{"a":2}', real))
        self.assertFalse(meta_signature_ok("", body, real))  # no app secret configured → refuse


if __name__ == "__main__":
    unittest.main()
