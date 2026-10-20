import unittest
from datetime import datetime, timezone

from lead_responder.config import Rules
from lead_responder.leads import parse_facebook_lead, parse_lead
from lead_responder.phone import is_mobile, local_format, normalize_phone
from lead_responder.schedule import QuietTimes

from .helpers import TZ


class PhoneTest(unittest.TestCase):
    def test_israeli_formats(self):
        for raw in ("050-1234567", "0501234567", "050 123 4567", "+972 50-123-4567", "972501234567",
                    "+972 (0)50 1234567", "00972501234567", "501234567", "‎050-1234567‏"):
            self.assertEqual(normalize_phone(raw), "972501234567", raw)

    def test_landlines_and_foreign(self):
        self.assertEqual(normalize_phone("03-6123456"), "97236123456")
        self.assertFalse(is_mobile("97236123456"))
        self.assertFalse(is_mobile("972771234567"))  # 077 VoIP
        self.assertEqual(normalize_phone("+1 415 555 1234"), "14155551234")
        self.assertTrue(is_mobile("14155551234"))

    def test_garbage(self):
        for raw in ("", "abc", "1234", "050-12345", "05012345678901"):
            self.assertEqual(normalize_phone(raw), "", raw)

    def test_local_format(self):
        self.assertEqual(local_format("972501234567"), "050-1234567")
        self.assertEqual(local_format("97236123456"), "03-6123456")
        self.assertEqual(local_format("14155551234"), "+14155551234")


class ParseLeadTest(unittest.TestCase):
    def test_plain_json(self):
        lead = parse_lead({"name": "דנה", "phone": "0501234567", "email": "d@x.co", "service": "פילינג"})
        self.assertEqual((lead.name, lead.phone_raw, lead.email), ("דנה", "0501234567", "d@x.co"))
        self.assertEqual(lead.extra, {"service": "פילינג"})

    def test_hebrew_labels_from_a_simple_form(self):
        lead = parse_lead({"שם מלא": "יוסי לוי", "טלפון נייד": "052-1112222", 'דוא"ל': "y@x.co",
                           "הודעה": "מתי יש תור?", "form_name": "דף נחיתה", "g-recaptcha-response": "xyz"})
        self.assertEqual((lead.name, lead.phone_raw, lead.email), ("יוסי לוי", "052-1112222", "y@x.co"))
        self.assertEqual(lead.source, "דף נחיתה")
        self.assertEqual(lead.extra, {"הודעה": "מתי יש תור?"})

    def test_elementor_advanced_data(self):
        form = {
            "form[id]": "abc", "form[name]": "צור קשר",
            "fields[name][id]": "name", "fields[name][title]": "שם", "fields[name][value]": "רונית",
            "fields[field_1][id]": "field_1", "fields[field_1][title]": "מספר לחזרה",
            "fields[field_1][value]": "054-1234567",
            "fields[field_2][id]": "field_2", "fields[field_2][title]": "עיר", "fields[field_2][value]": "חיפה",
            "meta[remote_ip][title]": "IP", "meta[remote_ip][value]": "1.2.3.4",
        }
        lead = parse_lead(form, source="website")
        self.assertEqual((lead.name, lead.phone_raw, lead.source), ("רונית", "054-1234567", "website"))
        self.assertEqual(lead.extra, {"עיר": "חיפה"})

    def test_first_and_last_name(self):
        lead = parse_lead({"first_name": "Dana", "last_name": "Cohen", "mobile": "0501234567"})
        self.assertEqual(lead.name, "Dana Cohen")

    def test_field_map_for_odd_labels(self):
        lead = parse_lead({"איך קוראים לך": "שני", "where can we reach you": "0501234567"},
                          {"name": ["איך קוראים לך"], "phone": ["where can we reach you"]})
        self.assertEqual((lead.name, lead.phone_raw), ("שני", "0501234567"))

    def test_nested_and_lists(self):
        lead = parse_lead({"data": {"contact": {"fullName": "Avi", "phoneNumber": "0501234567"},
                                    "interests": ["a", "b"]}})
        self.assertEqual((lead.name, lead.phone_raw), ("Avi", "0501234567"))
        self.assertEqual(lead.extra, {"interests": "a, b"})

    def test_facebook_lead(self):
        lead = parse_facebook_lead({
            "id": "123", "platform": "fb", "ad_name": "מודעה 1",
            "field_data": [{"name": "full_name", "values": ["Dana Cohen"]},
                           {"name": "phone_number", "values": ["+972501234567"]},
                           {"name": "email", "values": ["d@x.co"]},
                           {"name": "מה_הכי_חשוב_לך?", "values": ["מחיר"]}],
        })
        self.assertEqual((lead.name, lead.phone_raw, lead.email), ("Dana Cohen", "+972501234567", "d@x.co"))
        self.assertEqual((lead.source, lead.external_id), ("facebook", "fb:123"))
        self.assertEqual(lead.extra, {"מה_הכי_חשוב_לך?": "מחיר", "מודעה": "מודעה 1"})

    def test_long_values_are_cut(self):
        lead = parse_lead({"phone": "0501234567", "notes": "x" * 2000})
        self.assertEqual(len(lead.extra["notes"]), 500)


def utc(*args) -> datetime:
    return datetime(*args, tzinfo=timezone.utc)


class QuietTimesTest(unittest.TestCase):
    quiet = QuietTimes.from_rules(Rules(), TZ)  # 21:00-08:00, Shabbat Fri 15:00 → Sat 20:30

    def local(self, when: datetime) -> str:
        return f"{when.astimezone(TZ):%a %d/%m %H:%M}"

    def test_daytime_is_fine(self):
        when = utc(2026, 11, 3, 8, 0)  # Tue 10:00
        self.assertFalse(self.quiet.is_quiet(when))
        self.assertEqual(self.quiet.next_allowed(when), when)

    def test_night_crosses_midnight(self):
        self.assertEqual(self.local(self.quiet.next_allowed(utc(2026, 11, 3, 20, 30))), "Wed 04/11 08:00")  # 22:30
        self.assertEqual(self.local(self.quiet.next_allowed(utc(2026, 11, 4, 3, 0))), "Wed 04/11 08:00")  # 05:00

    def test_shabbat(self):
        self.assertFalse(self.quiet.is_quiet(utc(2026, 11, 6, 12, 0)))  # Fri 14:00
        self.assertEqual(self.local(self.quiet.next_allowed(utc(2026, 11, 6, 14, 0))), "Sat 07/11 20:30")
        self.assertEqual(self.local(self.quiet.next_allowed(utc(2026, 11, 7, 10, 0))), "Sat 07/11 20:30")

    def test_shabbat_ending_inside_the_night(self):
        quiet = QuietTimes.from_rules(Rules(shabbat_end="22:00"), TZ)
        self.assertEqual(self.local(quiet.next_allowed(utc(2026, 11, 7, 10, 0))), "Sun 08/11 08:00")

    def test_all_off(self):
        quiet = QuietTimes.from_rules(Rules(quiet_hours="", shabbat=False), TZ)
        self.assertFalse(quiet.is_quiet(utc(2026, 11, 7, 1, 0)))

    def test_daytime_window(self):
        quiet = QuietTimes.from_rules(Rules(quiet_hours="13:00-16:00", shabbat=False), TZ)
        self.assertEqual(self.local(quiet.next_allowed(utc(2026, 11, 3, 12, 0))), "Tue 03/11 16:00")


if __name__ == "__main__":
    unittest.main()
