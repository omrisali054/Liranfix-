import tempfile
import unittest
from pathlib import Path

from lead_responder.config import ConfigError, load_config

ROOT = Path(__file__).resolve().parent.parent

SERVER = """
[server]
public_url = "https://leads.example.com/"
secret = "server-secret-0123456789"
verify_token = "verify-0123456789"

[telegram]
bot_token = "123:abc"
"""

CLIENT = """
business_name = "הקליניקה"
key = "client-key-0123456789"

[owner]
telegram_chat_ids = [42]
"""


class ConfigTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        (self.dir / "clients").mkdir()

    def write(self, server: str = SERVER, **clients: str) -> Path:
        path = self.dir / "config.toml"
        path.write_text(server, encoding="utf-8")
        for name, text in clients.items():
            (self.dir / "clients" / f"{name.replace('_', '-')}.toml").write_text(text, encoding="utf-8")
        return path

    def test_minimal(self):
        config = load_config(self.write(dana_clinic=CLIENT), env={})
        client = config.clients["dana-clinic"]
        self.assertEqual(config.server.public_url, "https://leads.example.com")
        self.assertEqual(client.owner.telegram_chat_ids, ["42"])
        self.assertEqual(client.telegram_bot_token, "123:abc")  # inherited from the server
        self.assertIsNone(client.whatsapp)
        self.assertEqual(client.rules.owner_reminder_minutes, [15, 60])
        self.assertEqual(config.db_path, self.dir / "leads.db")

    def test_examples_are_valid_once_filled_in(self):
        server = (ROOT / "config.example.toml").read_text(encoding="utf-8")
        server = server.replace("CHANGE-ME", "filled-in-0123").replace('bot_token = ""', 'bot_token = "1:x"')
        client = (ROOT / "examples" / "client.example.toml").read_text(encoding="utf-8")
        client = client.replace("CHANGE-ME", "filled-in-0123")
        config = load_config(self.write(server, dana_clinic=client), env={})
        self.assertEqual(config.clients["dana-clinic"].whatsapp.ack_template, "lead_ack")

    def test_change_me_is_refused(self):
        with self.assertRaisesRegex(ConfigError, "CHANGE-ME"):
            load_config(self.write(dana_clinic=CLIENT.replace("client-key-0123456789", "CHANGE-ME-key-123456")),
                        env={})

    def test_env_overrides_secret(self):
        path = self.write(SERVER.replace("server-secret-0123456789", "CHANGE-ME"))
        config = load_config(path, env={"LEAD_SECRET": "from-env-0123456789"})
        self.assertEqual(config.server.secret, "from-env-0123456789")

    def test_errors(self):
        cases = {
            "unknown key": CLIENT + "\nbusines_name = 'typo'\n",
            "unknown key\\(s\\) delay": CLIENT + "\n[rules]\ndelay = 3\n",
            "bad placeholder": CLIENT + "\n[whatsapp]\nphone_number_id='1'\naccess_token='t'\n"
                                        "ack_template='a'\nack_params=['{firstname}']\n",
            "no way to reach the owner": CLIENT.replace("telegram_chat_ids = [42]", ""),
            "owner_template": CLIENT.replace("telegram_chat_ids = [42]", 'whatsapp_numbers = ["0501234567"]'),
            "quiet_hours": CLIENT + "\n[rules]\nquiet_hours = '21-8'\n",
            "business_name is required": CLIENT.replace('business_name = "הקליניקה"', ""),
        }
        for message, text in cases.items():
            with self.subTest(message):
                for old in (self.dir / "clients").glob("*.toml"):
                    old.unlink()
                with self.assertRaisesRegex(ConfigError, message):
                    load_config(self.write(clinic=text), env={})

    def test_bad_file_name(self):
        with self.assertRaisesRegex(ConfigError, "lowercase"):
            load_config(self.write(**{"Dana Clinic": CLIENT}), env={})

    def test_keys_must_differ(self):
        with self.assertRaisesRegex(ConfigError, "own key"):
            load_config(self.write(one=CLIENT, two=CLIENT), env={})


if __name__ == "__main__":
    unittest.main()
