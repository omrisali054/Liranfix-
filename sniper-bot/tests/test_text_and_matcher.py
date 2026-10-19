import unittest

from sniper_bot.matcher import KeywordMatcher, evaluate
from sniper_bot.text import normalize, parse_price, strip_html

from .helpers import make_listing, make_search


class NormalizeTest(unittest.TestCase):
    def test_final_letters_niqqud_and_punctuation(self):
        self.assertEqual(normalize("המָסָךְ שבור!!"), normalize("המסכ שבור"))
        self.assertEqual(normalize("תיקון/שיפוץ"), "תיקונ שיפוצ")
        self.assertEqual(normalize("  Won't   Charge "), "won t charge")

    def test_strip_html(self):
        self.assertEqual(strip_html("<p>לא <b>נטען</b> &amp; זהו</p>"), "לא נטען & זהו")


class ParsePriceTest(unittest.TestCase):
    def test_formats(self):
        cases = {
            "מחשב נייד 400 ₪": 400,
            "₪1,200 למכירה": 1200,
            'רק 350 ש"ח': 350,
            "350 ש״ח": 350,
            "מחיר: 750": 750,
            "Lenovo, price 200 NIS": 200,
            "1.500 שקל": 1500,
        }
        for text, price in cases.items():
            with self.subTest(text=text):
                self.assertEqual(parse_price(text), price)

    def test_no_price(self):
        self.assertIsNone(parse_price("מחשב נייד i5 15.6 אינץ"))


class FaultKeywordTest(unittest.TestCase):
    def setUp(self):
        self.matcher = KeywordMatcher()

    def phrases(self, text):
        return {hit.phrase for hit in self.matcher.faults(text)}

    def test_hebrew_prefixes_and_suffixes(self):
        self.assertIn("מסך שבור", self.phrases("המחשב עובד והמסך שבור בפינה"))
        self.assertIn("סוללה חלשה", self.phrases("סוללה חלשהה"))  # stray suffix letters still match
        self.assertIn("לא נטען", self.phrases("המכשיר לא נטען בכלל"))

    def test_negation_is_ignored(self):
        self.assertEqual(self.phrases("אין בעיית טעינה"), set())
        self.assertEqual(self.phrases("מחשב תקין, לא תקול, אין שום תקלה, ללא סדק במסך"), set())

    def test_negation_only_cancels_that_occurrence(self):
        self.assertIn("תקול", self.phrases("המסך לא תקול אבל המטען תקול"))

    def test_battery_needs_a_fault_word(self):
        self.assertEqual(self.phrases("סוללה מחזיקה 8 שעות"), set())
        self.assertIn("סוללה חלשה", self.phrases("הסוללה חלשה"))

    def test_english_word_boundaries(self):
        self.assertEqual(self.phrases("laptop has issues with wifi"), set())
        self.assertIn("as is", self.phrases("Sold as is"))
        self.assertIn("won't charge", self.phrases("Dell, WON'T CHARGE"))

    def test_categories(self):
        hits = self.matcher.faults("MacBook אינו נדלק כי המטען תקול")
        self.assertEqual({h.category for h in hits}, {"טעינה", "לא נדלק", "כללי"})

    def test_custom_faults(self):
        matcher = KeywordMatcher(extra_faults=["רמקול לא עובד"])
        self.assertEqual([h.category for h in matcher.faults("הרמקול לא עובד")], ["מותאם אישית"])


class ExcludeTest(unittest.TestCase):
    def test_buyers_and_locked_devices(self):
        matcher = KeywordMatcher()
        self.assertEqual(matcher.excluded_by("קונים מחשבים ניידים תקולים במזומן"), "קונים מחשב")
        self.assertEqual(matcher.excluded_by("iPhone 12, iCloud locked"), "icloud")
        self.assertIsNone(matcher.excluded_by("מחשב נייד, קניתי חדש ולכן מוכר"))

    def test_per_search_exclude(self):
        self.assertEqual(KeywordMatcher().excluded_by("מחשב מחודש", ["מחודש"]), "מחודש")


class EvaluateTest(unittest.TestCase):
    def setUp(self):
        self.matcher = KeywordMatcher()

    def check(self, listing, **search):
        return evaluate(listing, make_search(**search), self.matcher)

    def test_price_window(self):
        self.assertTrue(self.check(make_listing("1", 800)).ok)
        self.assertEqual(self.check(make_listing("1", 801)).reason, "price 801 > 800")
        self.assertFalse(self.check(make_listing("1", 20), min_price=50).ok)

    def test_missing_price(self):
        self.assertEqual(self.check(make_listing("1", None)).reason, "no price")
        self.assertTrue(self.check(make_listing("1", None), allow_no_price=True).ok)

    def test_business_condition_and_type_filters(self):
        self.assertEqual(self.check(make_listing("1", is_business=True)).reason, "business seller")
        self.assertTrue(self.check(make_listing("1", is_business=True), include_business=True).ok)
        self.assertFalse(self.check(make_listing("1", condition_id="2"), conditions=["4"]).ok)
        self.assertTrue(self.check(make_listing("1", condition_id="4"), conditions=["4"]).ok)
        self.assertFalse(self.check(make_listing("1", product_type_id="279"), product_types=["328"]).ok)

    def test_require_fault(self):
        plain = make_listing("1", title="מחשב נייד Dell")
        self.assertEqual(self.check(plain, require_fault=True).reason, "no fault keyword")
        self.assertTrue(evaluate(plain, make_search(require_fault=True), self.matcher, enforce_fault=False).ok)
        broken = make_listing("2", title="מחשב נייד Dell", description="המסך סדוק")
        verdict = self.check(broken, require_fault=True)
        self.assertTrue(verdict.ok)
        self.assertEqual([h.phrase for h in verdict.faults], ["מסך סדוק"])

    def test_condition_text_counts_as_fault(self):
        listing = make_listing("1", title="MacBook Pro", condition="דרוש תיקון/שיפוץ", condition_id="4")
        self.assertTrue(self.check(listing, require_fault=True).ok)

    def test_search_keywords_and_exclusions(self):
        self.assertFalse(self.check(make_listing("1", title="אופניים"), keywords=["מחשב נייד", "laptop"]).ok)
        self.assertTrue(self.check(make_listing("1", title="Laptop Asus"), keywords=["laptop"]).ok)
        self.assertIn("excluded", self.check(make_listing("1", title="קונה מחשבים ניידים")).reason)


if __name__ == "__main__":
    unittest.main()
