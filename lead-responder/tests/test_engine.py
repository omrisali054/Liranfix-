import threading
import unittest
from dataclasses import replace
from datetime import datetime, timezone

from lead_responder.channels import SendError
from lead_responder.config import OwnerConfig, Rules, WhatsAppConfig
from lead_responder.models import BLOCKED, HANDLED, LeadInput

from .helpers import TZ, Clock, RigTest, make_client

DANA = LeadInput(name="דנה כהן", phone_raw="050-1234567", source="facebook", extra={"שירות": "הסרת שיער"})


def local(when: datetime) -> str:
    return f"{when.astimezone(TZ):%a %H:%M}"


class NewLeadTest(RigTest):
    def test_instant_reply_and_owner_alert(self):
        rig = self.rig()
        result = rig.engine.intake("clinic", DANA)
        self.assertEqual(result.outcome, "created")
        rig.engine.run_due()

        self.assertEqual(rig.whatsapp.sent, [{"to": "972501234567", "template": "lead_ack",
                                              "params": ["דנה", "הקליניקה של דנה"], "button": ""}])
        [alert] = rig.telegram.sent
        self.assertEqual(alert["chat_id"], "42")
        self.assertIn("ליד חדש · דנה כהן", alert["text"])
        self.assertIn("050-1234567", alert["text"])
        self.assertIn("שירות: הסרת שיער", alert["text"])
        self.assertIn("✅ נשלחה ללקוח הודעת וואטסאפ", alert["text"])
        labels = [label for label, _ in alert["buttons"]]
        self.assertEqual(labels, ["💬 וואטסאפ ללקוח", "✅ סמן כטופל"])
        self.assertTrue(alert["buttons"][1][1].startswith(f"https://leads.example.com/d/{result.lead.id}-"))
        self.assertEqual(rig.pending(result.lead.id), ["remind", "remind", "followup"])

    def test_reminders_stop_once_handled(self):
        rig = self.rig()
        lead = rig.engine.intake("clinic", DANA).lead
        rig.engine.run_due()
        rig.clock.advance(minutes=15)
        rig.engine.run_due()
        self.assertIn("תזכורת", rig.owner_texts()[-1])

        rig.engine.set_status(lead.id, HANDLED)
        rig.clock.advance(days=2)
        rig.engine.run_due()
        self.assertEqual(len(rig.telegram.sent), 2)  # alert + one reminder
        self.assertEqual(len(rig.whatsapp.sent), 1)  # no follow-up
        self.assertEqual(rig.pending(lead.id), [])

    def test_follow_up_when_nobody_handled_it(self):
        rig = self.rig()
        rig.engine.intake("clinic", DANA)
        rig.clock.advance(hours=24)
        rig.engine.run_due()
        self.assertEqual([m["template"] for m in rig.whatsapp.sent], ["lead_ack", "lead_followup"])

    def test_landline_gets_no_whatsapp(self):
        rig = self.rig()
        rig.engine.intake("clinic", LeadInput(name="משרד", phone_raw="03-6123456"))
        rig.engine.run_due()
        self.assertEqual(rig.whatsapp.sent, [])
        self.assertIn("מספר קווי", rig.owner_texts()[0])
        self.assertEqual([label for label, _ in rig.telegram.sent[0]["buttons"]], ["✅ סמן כטופל"])

    def test_without_whatsapp_only_the_owner_hears(self):
        rig = self.rig(make_client(whatsapp=False))
        lead = rig.engine.intake("clinic", DANA).lead
        rig.engine.run_due()
        self.assertEqual(rig.whatsapp.sent, [])
        self.assertEqual(len(rig.telegram.sent), 1)
        self.assertEqual(rig.pending(lead.id), ["remind", "remind"])

    def test_sheet_row_on_create_and_on_status_change(self):
        rig = self.rig(make_client(sheets=True))
        lead = rig.engine.intake("clinic", DANA).lead
        rig.engine.run_due()
        rig.engine.set_status(lead.id, HANDLED)
        rig.engine.run_due()
        self.assertEqual([row["סטטוס"] for row in rig.sheets.rows], ["חדש", "טופל"])
        self.assertEqual(rig.sheets.rows[0]["טלפון"], "050-1234567")
        self.assertEqual(rig.sheets.rows[0]["מקור"], "פייסבוק")


class RepeatsAndFloodsTest(RigTest):
    def test_same_phone_twice_messages_the_lead_once(self):
        rig = self.rig()
        first = rig.engine.intake("clinic", DANA).lead
        rig.clock.advance(hours=3)
        second = rig.engine.intake("clinic", replace(DANA, phone_raw="+972 50 123 4567"))
        rig.engine.run_due()
        self.assertEqual(second.outcome, "duplicate")
        self.assertEqual(second.lead.duplicate_of, first.id)
        self.assertEqual(len(rig.whatsapp.sent), 1)
        self.assertIn("פנייה חוזרת", rig.owner_texts()[-1])
        self.assertIn("לפני 3 שעות", rig.owner_texts()[-1])
        self.assertEqual(rig.pending(second.lead.id), [])

        rig.engine.set_status(second.lead.id, HANDLED)  # handling the repeat handles the person
        self.assertEqual(rig.store.get_lead(first.id).status, HANDLED)
        self.assertNotIn("remind", rig.pending(first.id))

    def test_reply_to_a_repeat_stops_the_original_follow_up(self):
        rig = self.rig()
        first = rig.engine.intake("clinic", DANA).lead
        rig.engine.intake("clinic", DANA)
        rig.engine.handle_meta("clinic", whatsapp_event({"messages": [
            {"from": "972501234567", "id": "wamid.r", "type": "text", "text": {"body": "כן"}}]}))
        self.assertIsNotNone(rig.store.get_lead(first.id).replied_at)
        self.assertNotIn("followup", rig.pending(first.id))

    def test_concurrent_double_submit_replies_once(self):
        rig = self.rig()
        threads = [threading.Thread(target=rig.engine.intake, args=("clinic", DANA)) for _ in range(5)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        rig.engine.run_due()
        self.assertEqual(len(rig.whatsapp.sent), 1)

    def test_same_external_id_is_ignored(self):
        rig = self.rig()
        lead = replace(DANA, external_id="fb:1")
        self.assertEqual(rig.engine.intake("clinic", lead).outcome, "created")
        self.assertEqual(rig.engine.intake("clinic", lead).outcome, "already-received")

    def test_flood_is_stored_but_not_sent(self):
        rig = self.rig(make_client(rules=Rules(max_leads_per_hour=2)))
        for n in range(4):
            rig.engine.intake("clinic", LeadInput(name=f"bot {n}", phone_raw=f"05012345{n:02}"))
        rig.engine.run_due()
        self.assertEqual(len(rig.whatsapp.sent), 2)
        self.assertEqual([lead.status for lead in rig.store.recent_leads("clinic")][:2], [BLOCKED, BLOCKED])
        self.assertEqual(len(rig.admin.sent), 1)  # one admin alert per hour, not one per lead
        self.assertIn("לידים בשעה", rig.admin.sent[0]["text"])


class FailuresTest(RigTest):
    def test_temporary_failure_is_retried(self):
        rig = self.rig()
        rig.whatsapp.errors = [SendError("timeout")]
        rig.engine.intake("clinic", DANA)
        rig.engine.run_due()
        self.assertEqual(rig.whatsapp.sent, [])
        self.assertIn("בדרך", rig.owner_texts()[0])
        rig.clock.advance(minutes=1)
        rig.engine.run_due()
        self.assertEqual(len(rig.whatsapp.sent), 1)

    def test_gives_up_after_five_tries_and_tells_everyone(self):
        rig = self.rig()
        rig.whatsapp.errors = [SendError("down")] * 5
        rig.engine.intake("clinic", DANA)
        for minutes in (0, 1, 5, 15, 60):
            rig.clock.advance(minutes=minutes)
            rig.engine.run_due()
        rig.engine.run_due()  # the "undelivered" alert queued by the last failure
        self.assertEqual(rig.whatsapp.sent, [])
        self.assertIn("לא נמסרה", rig.owner_texts()[-1])
        self.assertIn("ack נכשל", rig.admin.sent[0]["text"])
        self.assertEqual(len(rig.store.failed_tasks()), 1)

    def test_permanent_failure_is_not_retried(self):
        rig = self.rig()
        rig.whatsapp.errors = [SendError("template rejected", permanent=True)]
        rig.engine.intake("clinic", DANA)
        rig.engine.run_due()
        rig.engine.run_due()
        self.assertEqual(len(rig.store.failed_tasks()), 1)
        self.assertIn("template rejected", rig.owner_texts()[-1])

    def test_one_working_owner_channel_is_enough(self):
        client = make_client(owner=OwnerConfig(telegram_chat_ids=["42"], whatsapp_numbers=["0549999999"]),
                             whatsapp=True)
        client.whatsapp.owner_template = "owner_lead_alert"
        rig = self.rig(client)
        rig.telegram.errors = [SendError("bot blocked", permanent=True)]
        lead = rig.engine.intake("clinic", DANA).lead
        rig.engine.run_due()
        owner_msg = [m for m in rig.whatsapp.sent if m["template"] == "owner_lead_alert"]
        self.assertEqual(owner_msg[0]["to"], "972549999999")
        self.assertEqual(owner_msg[0]["params"][:3], ["ליד חדש", "דנה כהן", "050-1234567"])
        self.assertTrue(owner_msg[0]["button"].startswith(f"{lead.id}-"))
        self.assertEqual(rig.store.failed_tasks(), [])


class QuietHoursTest(RigTest):
    def test_lead_at_night_waits_for_the_morning(self):
        rig = self.rig(clock=Clock(datetime(2026, 11, 3, 21, 0, tzinfo=timezone.utc)))  # 23:00 Israel
        lead = rig.engine.intake("clinic", DANA).lead
        rig.engine.run_due()
        self.assertEqual(len(rig.whatsapp.sent), 1)  # the instant reply never waits
        self.assertEqual(len(rig.telegram.sent), 1)
        due = rig.store.pending_tasks(lead.id)
        self.assertEqual([(kind, local(when)) for kind, when in due],
                         [("remind", "Wed 08:00"), ("followup", "Thu 08:00")])

    def test_shabbat(self):
        rig = self.rig(clock=Clock(datetime(2026, 11, 6, 14, 0, tzinfo=timezone.utc)))  # Friday 16:00
        lead = rig.engine.intake("clinic", DANA).lead
        reminders = [local(when) for kind, when in rig.store.pending_tasks(lead.id) if kind == "remind"]
        self.assertEqual(reminders, ["Sat 20:30"])

    def test_server_catching_up_at_night_waits_too(self):
        rig = self.rig()
        lead = rig.engine.intake("clinic", DANA).lead
        rig.clock.advance(hours=13)  # the server was down; back at 23:00 with everything overdue
        rig.engine.run_due()
        self.assertEqual(len(rig.whatsapp.sent), 1)  # the instant reply still goes
        self.assertFalse(any("תזכורת" in text for text in rig.owner_texts()))
        reminders = [local(when) for kind, when in rig.store.pending_tasks(lead.id) if kind == "remind"]
        self.assertEqual(reminders, ["Wed 08:00", "Wed 08:00"])


def whatsapp_event(value):
    return {"object": "whatsapp_business_account",
            "entry": [{"id": "waba", "changes": [{"field": "messages", "value": value}]}]}


class InboundWhatsAppTest(RigTest):
    def test_reply_stops_follow_ups_and_reaches_the_owner(self):
        rig = self.rig()
        lead = rig.engine.intake("clinic", DANA).lead
        rig.engine.run_due()
        rig.engine.handle_meta("clinic", whatsapp_event({
            "contacts": [{"wa_id": "972501234567", "profile": {"name": "Dana"}}],
            "messages": [{"from": "972501234567", "id": "wamid.in1", "type": "text", "text": {"body": "כן, תתקשרו"}}],
        }))
        rig.engine.run_due()
        self.assertIsNotNone(rig.store.get_lead(lead.id).replied_at)
        self.assertNotIn("followup", rig.pending(lead.id))
        self.assertIn("«כן, תתקשרו»", rig.owner_texts()[-1])

    def test_message_from_a_stranger_becomes_a_lead(self):
        rig = self.rig()
        rig.engine.handle_meta("clinic", whatsapp_event({
            "contacts": [{"wa_id": "972521112222", "profile": {"name": "יוסי"}}],
            "messages": [{"from": "972521112222", "id": "wamid.in2", "type": "text", "text": {"body": "כמה עולה?"}}],
        }))
        rig.engine.run_due()
        [lead] = rig.store.recent_leads("clinic")
        self.assertEqual((lead.name, lead.phone, lead.source), ("יוסי", "972521112222", "whatsapp"))
        self.assertEqual(rig.whatsapp.sent, [])  # they wrote to us, no template reply
        self.assertIn("כמה עולה?", rig.owner_texts()[0])

    def test_owner_writing_to_the_number_is_not_a_lead(self):
        client = make_client(owner=OwnerConfig(telegram_chat_ids=["42"], whatsapp_numbers=["0549999999"]))
        client.whatsapp.owner_template = "owner_lead_alert"
        rig = self.rig(client)
        rig.engine.handle_meta("clinic", whatsapp_event({
            "messages": [{"from": "972549999999", "id": "wamid.in3", "type": "text", "text": {"body": "תודה"}}]}))
        self.assertEqual(rig.store.recent_leads("clinic"), [])

    def test_delivery_failure_tells_the_owner_once(self):
        rig = self.rig()
        lead = rig.engine.intake("clinic", DANA).lead
        rig.engine.run_due()
        event = whatsapp_event({"statuses": [{"id": "wamid.1", "status": "failed",
                                              "errors": [{"code": 131026, "title": "Message undeliverable"}]}]})
        rig.engine.handle_meta("clinic", event)
        rig.engine.handle_meta("clinic", event)  # Meta retries webhooks
        rig.engine.run_due()
        self.assertEqual(rig.store.get_lead(lead.id).wa_status, "failed")
        undelivered = [t for t in rig.owner_texts() if "לא נמסרה" in t]
        self.assertEqual(len(undelivered), 1)
        self.assertIn("Message undeliverable", undelivered[0])

    def test_replay_of_the_same_message_is_ignored(self):
        rig = self.rig()
        event = whatsapp_event({"messages": [{"from": "972521112222", "id": "wamid.x", "type": "text",
                                              "text": {"body": "hi"}}]})
        rig.engine.handle_meta("clinic", event)
        rig.engine.handle_meta("clinic", event)
        self.assertEqual(len(rig.store.recent_leads("clinic")), 1)


class LeadAdsTest(RigTest):
    def test_leadgen_webhook_fetches_the_lead(self):
        rig = self.rig()
        rig.facebook.leads["777"] = {
            "id": "777", "platform": "ig", "campaign_name": "קיץ",
            "field_data": [{"name": "full_name", "values": ["רונית לוי"]},
                           {"name": "phone_number", "values": ["+972541234567"]}],
        }
        event = {"object": "page", "entry": [{"id": "page1", "changes": [
            {"field": "leadgen", "value": {"leadgen_id": "777", "page_id": "page1"}}]}]}
        rig.engine.handle_meta("clinic", event)
        rig.engine.handle_meta("clinic", event)
        rig.engine.run_due()  # fetch → intake
        rig.engine.run_due()  # ack + alert
        [lead] = rig.store.recent_leads("clinic")
        self.assertEqual((lead.name, lead.phone, lead.source, lead.external_id),
                         ("רונית לוי", "972541234567", "instagram", "fb:777"))
        self.assertEqual(lead.extra, {"קמפיין": "קיץ"})
        self.assertEqual(rig.whatsapp.sent[0]["params"][0], "רונית")


class WeeklyReportTest(RigTest):
    def test_sunday_morning_once(self):
        rig = self.rig()
        handled = rig.engine.intake("clinic", DANA).lead
        rig.clock.advance(minutes=12)
        rig.engine.set_status(handled.id, HANDLED)
        rig.engine.intake("clinic", LeadInput(name="יוסי", phone_raw="0521112222", source="website"))
        rig.engine.intake("clinic", replace(DANA, name="דנה שוב"))
        rig.engine.run_due()
        rig.telegram.sent.clear()

        rig.clock.now = datetime(2026, 11, 8, 7, 30, tzinfo=timezone.utc)  # Sunday 09:30
        rig.engine.tick()
        rig.engine.tick()
        reports = [t for t in rig.owner_texts() if "סיכום שבועי" in t]
        self.assertEqual(len(reports), 1)
        report = reports[0]
        self.assertIn("לידים חדשים: 2", report)
        self.assertIn("טופלו: 1 (50%)", report)
        self.assertIn("זמן עד טיפול (חציון): 12 דק'", report)
        self.assertIn("פניות חוזרות: 1", report)
        self.assertIn("עדיין פתוחים: יוסי", report)

    def test_not_on_other_days(self):
        rig = self.rig()
        rig.engine.tick()
        self.assertEqual(rig.telegram.sent, [])


class TemplateParamsTest(RigTest):
    def test_name_fallback_and_booking_url(self):
        client = make_client(booking_url="https://cal.example/dana")
        client.whatsapp = WhatsAppConfig(phone_number_id="1", access_token="t", ack_template="ack",
                                         ack_params=["{first_name}", "{booking_url}"])
        rig = self.rig(client)
        rig.engine.intake("clinic", LeadInput(phone_raw="0501234567"))
        rig.engine.run_due()
        self.assertEqual(rig.whatsapp.sent[0]["params"], ["🙂", "https://cal.example/dana"])


if __name__ == "__main__":
    unittest.main()
