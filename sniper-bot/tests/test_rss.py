import unittest

from sniper_bot.fetch import SourceError
from sniper_bot.sources.rss import parse_feed

from .helpers import FIXTURES


class RssTest(unittest.TestCase):
    def test_rss_items(self):
        listings = parse_feed((FIXTURES / "feed_rss.xml").read_bytes())
        self.assertEqual([l.price for l in listings], [350, 1200, 50])
        first = listings[0]
        self.assertEqual(first.listing_id, "https://board.example/ad/1")
        self.assertEqual(first.url, "https://board.example/ad/1")
        self.assertEqual(first.description, "המחשב לא נטען , כנראה שקע טעינה.")
        self.assertEqual(first.image_url, "https://board.example/img/1.jpg")
        self.assertEqual(listings[1].image_url, "https://board.example/img/2.jpg")
        self.assertEqual(listings[1].listing_id, "https://board.example/ad/2")  # no guid -> link

    def test_atom_entries(self):
        [entry] = parse_feed((FIXTURES / "feed_atom.xml").read_bytes())
        self.assertEqual(entry.listing_id, "tag:board.example,2026:ad-7")
        self.assertEqual(entry.url, "https://board.example/ad/7")
        self.assertEqual(entry.price, 200)

    def test_invalid_xml(self):
        with self.assertRaises(SourceError):
            parse_feed(b"<html><body>not a feed")


if __name__ == "__main__":
    unittest.main()
