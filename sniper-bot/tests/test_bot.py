import unittest

from sniper_bot.bot import Alert, SniperBot, format_alert
from sniper_bot.matcher import FaultHit, KeywordMatcher
from sniper_bot.storage import Store

from .helpers import BrokenSource, FakeNotifier, FakeSource, make_config, make_listing, make_search


def make_bot(source, *searches, seed=True, **settings):
    config = make_config(*searches, **settings)
    notifier = FakeNotifier()
    sleeps = []
    bot = SniperBot(config, Store(), {"fake": source}, [notifier], KeywordMatcher(),
                    seed=seed, sleep=sleeps.append, rand=lambda: 0.5)
    return bot, notifier, sleeps


class ScanTest(unittest.TestCase):
    def test_first_scan_only_remembers_existing_ads(self):
        source = FakeSource([make_listing("1", 300), make_listing("2", 400)])
        bot, notifier, _ = make_bot(source)
        self.assertEqual(bot.scan_once().alerts, [])
        self.assertEqual(notifier.messages, [])

        source.listings.append(make_listing("3", 350, title="מחשב נייד Asus"))
        alerts = bot.scan_once().alerts
        self.assertEqual([a.listing.listing_id for a in alerts], ["3"])
        self.assertEqual(len(notifier.messages), 1)
        self.assertIn("Asus", notifier.messages[0].title)

    def test_each_ad_alerts_only_once(self):
        source = FakeSource([make_listing("1", 300)])
        bot, notifier, _ = make_bot(source, seed=False)
        bot.scan_once()
        bot.scan_once()
        self.assertEqual(len(notifier.messages), 1)

    def test_same_ad_in_two_searches_alerts_once(self):
        source = FakeSource([make_listing("1", 300)])
        bot, notifier, _ = make_bot(source, make_search(name="a"), make_search(name="b"), seed=False)
        bot.scan_once()
        self.assertEqual(len(notifier.messages), 1)

    def test_price_drop_below_ceiling_triggers_alert(self):
        source = FakeSource([make_listing("1", 1200)])
        bot, notifier, _ = make_bot(source)
        bot.scan_once()  # seed
        bot.scan_once()  # too expensive
        self.assertEqual(notifier.messages, [])
        source.listings[0].price = 700
        source.listings[0].previous_price = 1200
        bot.scan_once()
        self.assertEqual(len(notifier.messages), 1)
        self.assertIn("ירד מ-1,200 ₪", notifier.messages[0].body)

    def test_require_fault_reads_full_description(self):
        source = FakeSource(
            [make_listing("1", 400, title="MacBook Pro 2015"), make_listing("2", 400, title="Dell XPS")],
            descriptions={"1": "אינו נדלק כי המטען תקול", "2": "עובד מצוין"},
        )
        bot, notifier, _ = make_bot(source, make_search(require_fault=True), seed=False)
        alerts = bot.scan_once().alerts
        self.assertEqual([a.listing.listing_id for a in alerts], ["1"])
        self.assertEqual(source.enriched, ["1", "2"])
        self.assertTrue(notifier.messages[0].urgent)
        self.assertIn("טעינה: מטען תקול", notifier.messages[0].body)

    def test_details_are_not_fetched_for_ads_over_the_ceiling(self):
        source = FakeSource([make_listing("1", 5000)])
        bot, _, _ = make_bot(source, seed=False)
        bot.scan_once()
        self.assertEqual(source.enriched, [])

    def test_rejected_ad_is_not_rechecked_while_unchanged(self):
        source = FakeSource([make_listing("1", 400, title="Dell")], descriptions={"1": "עובד"})
        bot, _, _ = make_bot(source, make_search(require_fault=True), seed=False)
        bot.scan_once()
        bot.scan_once()
        self.assertEqual(source.enriched, ["1"])

    def test_failed_notification_is_retried_next_scan(self):
        source = FakeSource([make_listing("1", 300)])
        bot, notifier, _ = make_bot(source, seed=False)
        notifier.fail = True
        self.assertEqual(bot.scan_once().alerts, [])
        notifier.fail = False
        self.assertEqual(len(bot.scan_once().alerts), 1)

    def test_broken_source_is_reported_not_raised(self):
        bot, _, _ = make_bot(BrokenSource())
        self.assertEqual(bot.scan_once().failed_searches, ["laptops"])


class LoopTest(unittest.TestCase):
    def test_backoff_and_failure_notification(self):
        source = BrokenSource()
        bot, notifier, sleeps = make_bot(source, failure_alert_after=2)
        bot.run_forever(max_scans=3)
        self.assertEqual(sleeps, [200, 400])  # 100 s interval doubled per failed scan
        self.assertEqual(len(notifier.messages), 1)
        self.assertIn("לא מצליח לסרוק", notifier.messages[0].title)

        source.error = None
        bot.run_forever(max_scans=1)
        self.assertIn("חזר לעבוד", notifier.messages[-1].title)
        self.assertEqual(bot.next_delay(), 100 + 0.5 * bot.config.settings.jitter_seconds)

    def test_normal_delay_has_jitter(self):
        bot, _, sleeps = make_bot(FakeSource(), jitter_seconds=60)
        bot.run_forever(max_scans=2)
        self.assertEqual(sleeps, [130])


class FormatAlertTest(unittest.TestCase):
    def test_message_content(self):
        listing = make_listing("1", 400, title="MacBook Pro", city="חיפה", condition="דרוש תיקון/שיפוץ",
                               image_url="https://img.example/1.jpg")
        alert = Alert(make_search(max_price=900), listing,
                      [FaultHit("טעינה", "מטען תקול"), FaultHit("טעינה", "לא נטען"), FaultHit("כללי", "תקול")])
        message = format_alert(alert)
        self.assertEqual(message.title, "🎯 MacBook Pro – 400 ₪")
        self.assertIn("💰 400 ₪ (תקרה: 900 ₪)", message.body)
        self.assertIn("🔧 טעינה: מטען תקול, לא נטען | כללי: תקול", message.body)
        self.assertIn("📍 חיפה · דרוש תיקון/שיפוץ", message.body)
        self.assertEqual(message.url, "https://board.example/1")
        self.assertEqual(message.image_url, "https://img.example/1.jpg")
        self.assertTrue(message.urgent)


if __name__ == "__main__":
    unittest.main()
