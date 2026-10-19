import unittest

import requests

from sniper_bot.config import NtfyConfig, TelegramConfig
from sniper_bot.fetch import BlockedError, HttpClient, SourceError
from sniper_bot.notifiers import Message, NotifyError, NtfyNotifier, TelegramNotifier

from .helpers import FakeSession, make_response

MESSAGE = Message("🎯 MacBook – 400 ₪", "💰 400 ₪", url="https://ad.example/1",
                  image_url="https://img.example/1.jpg", urgent=True)


class NtfyTest(unittest.TestCase):
    def test_publishes_json(self):
        session = FakeSession()
        NtfyNotifier(NtfyConfig(topic="my-topic", token="tk"), session=session).send(MESSAGE)
        call = session.calls[0]
        self.assertEqual(call["url"], "https://ntfy.sh/")
        self.assertEqual(call["headers"], {"Authorization": "Bearer tk"})
        payload = call["json"]
        self.assertEqual(payload["topic"], "my-topic")
        self.assertEqual(payload["title"], MESSAGE.title)
        self.assertEqual(payload["click"], "https://ad.example/1")
        self.assertEqual(payload["attach"], "https://img.example/1.jpg")
        self.assertEqual(payload["priority"], 5)

    def test_http_error(self):
        session = FakeSession((500, {"error": "down"}))
        with self.assertRaises(NotifyError):
            NtfyNotifier(NtfyConfig(topic="t"), session=session).send(MESSAGE)


class TelegramTest(unittest.TestCase):
    def notifier(self, session):
        return TelegramNotifier(TelegramConfig(bot_token="SECRET", chat_id="42"), session=session)

    def test_sends_photo_with_caption(self):
        session = FakeSession()
        self.notifier(session).send(MESSAGE)
        self.assertTrue(session.calls[0]["url"].endswith("/sendPhoto"))
        data = session.calls[0]["data"]
        self.assertEqual(data["chat_id"], "42")
        self.assertIn('<a href="https://ad.example/1">', data["caption"])

    def test_falls_back_to_text_when_photo_fails(self):
        session = FakeSession((400, {"ok": False, "description": "bad photo"}), (200, {"ok": True}))
        self.notifier(session).send(MESSAGE)
        self.assertTrue(session.calls[1]["url"].endswith("/sendMessage"))

    def test_errors_do_not_leak_the_token(self):
        session = FakeSession(requests.ConnectionError("https://api.telegram.org/botSECRET/sendMessage"))
        with self.assertRaises(NotifyError) as ctx:
            self.notifier(session).send(Message("t", "b"))
        self.assertNotIn("SECRET", str(ctx.exception))


class ScriptedSession(requests.Session):
    def __init__(self, *responses):
        super().__init__()
        self.responses = list(responses)
        self.calls = 0

    def get(self, url, **kwargs):
        self.calls += 1
        reply = self.responses.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


class HttpClientTest(unittest.TestCase):
    def client(self, *responses, clock=lambda: 0.0):
        self.sleeps = []
        self.session = ScriptedSession(*responses)
        return HttpClient(delay_seconds=3, session=self.session, sleep=self.sleeps.append, clock=clock)

    def test_hebrew_defaults_to_utf8(self):
        client = self.client(make_response(200, "מחשב נייד".encode("utf-8"), "text/html"))
        self.assertEqual(client.get_text("https://x"), "מחשב נייד")

    def test_403_is_blocked(self):
        with self.assertRaises(BlockedError):
            self.client(make_response(403)).get("https://x")

    def test_retries_server_errors(self):
        client = self.client(make_response(503), make_response(200, b"ok"))
        self.assertEqual(client.get_text("https://x"), "ok")
        self.assertEqual(self.session.calls, 2)

    def test_gives_up_after_retries(self):
        client = self.client(*[requests.ConnectionError("down")] * 3)
        with self.assertRaises(SourceError):
            client.get("https://x")

    def test_spaces_out_requests(self):
        client = self.client(make_response(200, b"a"), make_response(200, b"b"))
        client.get("https://x")
        client.get("https://x")
        self.assertEqual(self.sleeps, [3.0])

    def test_sends_browser_headers(self):
        client = self.client(make_response(200, b"a"))
        self.assertIn("he-IL", self.session.headers["Accept-Language"])
        client.get("https://x")


if __name__ == "__main__":
    unittest.main()
