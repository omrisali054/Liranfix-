import unittest

from sniper_bot.fetch import BlockedError
from sniper_bot.sources.yad2 import (
    Yad2Source, parse_item_description, parse_search_page, search_params,
)

from .helpers import fixture, make_search


class ParseSearchPageTest(unittest.TestCase):
    def test_listing_fields(self):
        listings = parse_search_page(fixture("yad2_search.html"))
        self.assertEqual([l.listing_id for l in listings], ["1001", "1002", "1003", "1004", "1005"])
        mac = listings[0]
        self.assertEqual(mac.key, "yad2:1001")
        self.assertEqual(mac.title, "MacBook Pro Retina 15 2015")
        self.assertEqual(mac.url, "https://www.yad2.co.il/market/item/1001")
        self.assertEqual(mac.price, 400)
        self.assertEqual(mac.city, "פרדס חנה כרכור")
        self.assertEqual((mac.condition, mac.condition_id), ("דרוש תיקון/שיפוץ", "4"))
        self.assertEqual((mac.product_type, mac.product_type_id), ("מחשבים ניידים", "328"))
        self.assertEqual(mac.image_url, "https://cdn.example.com/1001.jpg")
        self.assertFalse(mac.is_business)

    def test_previous_price_business_and_zero_price(self):
        listings = {l.listing_id: l for l in parse_search_page(fixture("yad2_search.html"))}
        self.assertEqual(listings["1002"].previous_price, 900)
        self.assertTrue(listings["1003"].is_business)
        self.assertIsNone(listings["1004"].price)

    def test_dehydrated_state_fallback(self):
        page = fixture("yad2_search.html").replace('"initialFeed"', '"somethingElse"')
        page = page.replace('"pageProps": {', '"pageProps": {"dehydratedState": {"queries": [{"state": {"data": '
                            '{"pages": [{"items": [{"id": 7, "title": "x", "price": 10}]}]}}}]}, ', 1)
        self.assertEqual([l.listing_id for l in parse_search_page(page)], ["7"])

    def test_captcha_page_is_reported_as_blocked(self):
        with self.assertRaises(BlockedError):
            parse_search_page(fixture("yad2_blocked.html"))


class ItemPageTest(unittest.TestCase):
    def test_description_from_next_data(self):
        self.assertIn("אינו נדלק כי המטען תקול", parse_item_description(fixture("yad2_item.html")))

    def test_meta_description_fallback(self):
        page = '<html><head><meta name="description" content="מסך סדוק &amp; לא נטען"></head></html>'
        self.assertEqual(parse_item_description(page), "מסך סדוק & לא נטען")


class FakeHttp:
    def __init__(self, pages):
        self.pages = list(pages)
        self.requests = []

    def get_text(self, url, params=None):
        self.requests.append((url, params))
        return self.pages.pop(0) if len(self.pages) > 1 else self.pages[0]


class Yad2SourceTest(unittest.TestCase):
    def test_params_use_newest_first_and_price_cap(self):
        search = make_search(source="yad2", max_price=900)
        self.assertEqual(search_params(search), {"q": "מחשב נייד", "sortOption": "newest", "maxPrice": 900})
        self.assertEqual(search_params(search, page=2)["page"], 2)

    def test_stops_paging_when_a_page_repeats(self):
        http = FakeHttp([fixture("yad2_search.html")])
        listings = Yad2Source(http).search(make_search(source="yad2", pages=3))
        self.assertEqual(len(listings), 5)
        self.assertEqual(len(http.requests), 2)

    def test_later_page_failure_keeps_first_page(self):
        http = FakeHttp([fixture("yad2_search.html"), fixture("yad2_blocked.html")])
        self.assertEqual(len(Yad2Source(http).search(make_search(source="yad2", pages=2))), 5)

    def test_enrich_fetches_item_page(self):
        http = FakeHttp([fixture("yad2_item.html")])
        listing = parse_search_page(fixture("yad2_search.html"))[0]
        Yad2Source(http).enrich(listing)
        self.assertEqual(http.requests[0][0], "https://www.yad2.co.il/market/item/1001")
        self.assertIn("המטען תקול", listing.description)


if __name__ == "__main__":
    unittest.main()
