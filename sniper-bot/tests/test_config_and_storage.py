import tempfile
import unittest
from pathlib import Path

from sniper_bot.config import ConfigError, load_config
from sniper_bot.storage import Store

from .helpers import make_listing

EXAMPLE = Path(__file__).parent.parent / "config.example.toml"


class ConfigTest(unittest.TestCase):
    def write(self, text):
        tmp = tempfile.NamedTemporaryFile("w", suffix=".toml", delete=False, encoding="utf-8")
        tmp.write(text)
        tmp.close()
        self.addCleanup(Path(tmp.name).unlink)
        return tmp.name

    def test_example_config_loads(self):
        config = load_config(EXAMPLE, env={"NTFY_TOPIC": "my-secret-topic"})
        self.assertEqual(len(config.searches), 4)
        self.assertEqual(config.ntfy.topic, "my-secret-topic")
        self.assertIsNone(config.telegram)
        self.assertEqual(config.db_path, EXAMPLE.resolve().parent / "sniper.db")
        laptops = config.searches[0]
        self.assertEqual((laptops.source, laptops.max_price, laptops.product_types), ("yad2", 900, ["328"]))
        self.assertTrue(laptops.require_fault)

    def test_example_topic_placeholder_is_rejected(self):
        with self.assertRaisesRegex(ConfigError, "pick your own topic"):
            load_config(EXAMPLE, env={})

    def test_env_supplies_telegram(self):
        path = self.write('[[search]]\nname="a"\nsource="yad2"\nquery="x"\nmax_price=100\n')
        config = load_config(path, env={"TELEGRAM_BOT_TOKEN": "t", "TELEGRAM_CHAT_ID": "42"})
        self.assertEqual((config.telegram.bot_token, config.telegram.chat_id), ("t", "42"))

    def test_errors(self):
        cases = {
            "unknown key": '[[search]]\nname="a"\nsource="yad2"\nquery="x"\nmax_price=100\nmaxprice=5\n',
            "need a query": '[[search]]\nname="a"\nsource="yad2"\nmax_price=100\n',
            "need a url": '[[search]]\nname="a"\nsource="rss"\nmax_price=100\n',
            "max_price": '[[search]]\nname="a"\nsource="yad2"\nquery="x"\n',
            "source must be": '[[search]]\nname="a"\nsource="ebay"\nquery="x"\nmax_price=1\n',
            "unique name": '[[search]]\nname="a"\nsource="yad2"\nquery="x"\nmax_price=1\n' * 2,
            "at least one": "[settings]\ninterval_seconds = 60\n",
            "both bot_token": '[notify.telegram]\nbot_token="t"\n[[search]]\nname="a"\nsource="yad2"\nquery="x"\nmax_price=1\n',
        }
        for message, text in cases.items():
            with self.subTest(message), self.assertRaisesRegex(ConfigError, message):
                load_config(self.write(text), env={})

    def test_numeric_ids_become_strings(self):
        path = self.write('[[search]]\nname="a"\nsource="yad2"\nquery="x"\nmax_price=100\nconditions=[4]\n')
        self.assertEqual(load_config(path, env={}).searches[0].conditions, ["4"])


class StoreTest(unittest.TestCase):
    def test_seen_alerted_and_seeded(self):
        store = Store()
        listing = make_listing("1", 300)
        self.assertFalse(store.is_unchanged("s", listing.key, 300))
        store.mark_seen("s", listing.key, 300)
        self.assertTrue(store.is_unchanged("s", listing.key, 300))
        self.assertFalse(store.is_unchanged("s", listing.key, 250))
        self.assertFalse(store.is_unchanged("other", listing.key, 300))
        store.mark_seen("s", "fake:2", None)
        self.assertTrue(store.is_unchanged("s", "fake:2", None))

        store.mark_alerted("s", listing)
        self.assertTrue(store.was_alerted(listing.key))
        self.assertEqual(store.recent_alerts()[0][1:], ("s", "מחשב נייד", 300, "https://board.example/1"))

        self.assertFalse(store.is_seeded("s"))
        store.mark_seeded("s")
        self.assertTrue(store.is_seeded("s"))

    def test_persists_to_disk(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sniper.db"
            store = Store(path)
            store.mark_seeded("s")
            store.close()
            self.assertTrue(Store(path).is_seeded("s"))


if __name__ == "__main__":
    unittest.main()
