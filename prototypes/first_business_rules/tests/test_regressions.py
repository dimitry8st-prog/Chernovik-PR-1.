"""Regressions for handoff ownership, event replay and late observations.

These tests exercise the real engine, SQLite transactions and accepted handoff
components.  All contacts, classifications, receipts and identities are local
synthetic fixtures; no transport is invoked.
"""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
import unittest

from first_business.adapters import HandoffAdapter
from first_business.common import RuleError
from first_business.engine import Engine
from first_business.fixtures import make_lead, proposal
from first_business.policy import load_policy
from first_business.store import Store


ROOT = Path(__file__).resolve().parents[1]


class HandoffReplayRegressionTests(unittest.TestCase):
    def setUp(self):
        self.store = Store(":memory:")
        self.policy = load_policy(ROOT / "config/demo.synthetic.json")
        self.engine = Engine(self.store, self.policy)
        self.at = datetime(2026, 10, 31, 9, tzinfo=timezone.utc)
        self.lead = make_lead("regression", self.at)
        self.lead_id = self.lead["lead_id"]
        self.engine.ingest("ingest", self.lead, self.at)

    def tearDown(self):
        self.store.close()

    def send_initial(self, receipt_status="DELIVERED"):
        draft = self.engine.prepare_initial("initial", self.lead_id, self.at)
        self.engine.simulated_send_receipt(
            "initial-receipt", self.lead_id, draft["action_id"], receipt_status, self.at,
        )
        return draft

    def interest(self, event_id="interest", message_id="interest-message", minutes=1):
        text = "SYNTHETIC: I want to discuss purchasing this demonstration."
        return self.engine.reply(
            event_id, self.lead_id, message_id, text, proposal("INTEREST", text),
            self.at + timedelta(minutes=minutes),
        )

    def test_late_initial_delivery_cannot_reopen_handoff_or_lose_next_reply(self):
        draft = self.send_initial("ACCEPTED")
        packet = self.interest()
        receipt = self.engine.simulated_send_receipt(
            "late-initial-delivered", self.lead_id, draft["action_id"], "DELIVERED",
            self.at + timedelta(minutes=2),
        )
        self.assertEqual(receipt["status"], "DELIVERED")
        self.assertEqual(self.store.lead(self.engine.campaign_id, self.lead_id)["state"], "HANDOFF_PENDING")
        self.assertEqual(
            self.engine.follow_up("late-followup", self.lead_id, self.at + timedelta(hours=50))["status"],
            "BLOCKED",
        )
        again = self.interest("second-interest", "second-interest-message", minutes=3001)
        self.assertEqual(again["status"], "ARCHIVED")
        snapshot = self.store.snapshot(self.engine.campaign_id)
        self.assertEqual(len(snapshot["handoffs"]), 1)
        self.assertEqual(snapshot["handoffs"][0]["handoff_id"], packet["handoff_id"])
        self.assertIn("second-interest-message", {item["message"]["message_id"] for item in snapshot["history"]})
        self.assertEqual({item["kind"] for item in snapshot["outbox"]}, {"INITIAL"})

    def test_late_unknown_send_result_cannot_replace_handoff_ownership(self):
        draft = self.send_initial("ACCEPTED")
        self.interest()
        result = self.engine.simulated_send_receipt(
            "late-unknown", self.lead_id, draft["action_id"], "UNKNOWN",
            self.at + timedelta(minutes=2),
        )
        self.assertEqual(result["status"], "UNKNOWN")
        self.assertEqual(self.store.lead(self.engine.campaign_id, self.lead_id)["state"], "HANDOFF_PENDING")
        self.assertEqual(len(self.store.snapshot(self.engine.campaign_id)["handoffs"]), 1)

    def test_malformed_reply_after_manager_decline_preserves_terminal_state(self):
        packet = self.interest()
        self.engine.handoff_receipt(
            "packet-delivered", self.lead_id, packet["handoff_id"], packet["content_sha256"],
            "DELIVERED", self.at + timedelta(minutes=2),
        )
        self.engine.manager_ack(
            "manager-declined", self.lead_id, packet["handoff_id"], packet["content_sha256"],
            packet["recipient_ref"], "DECLINE", self.at + timedelta(minutes=3),
        )
        malformed = self.engine.reply(
            "malformed-event", self.lead_id, "malformed-message", "SYNTHETIC follow-up information.", {},
            self.at + timedelta(minutes=4),
        )
        self.assertTrue(malformed["message_saved"])
        self.assertEqual(self.store.lead(self.engine.campaign_id, self.lead_id)["state"], "MANAGER_DECLINED")
        again = self.interest("interest-after-decline", "interest-message-after-decline", minutes=5)
        self.assertEqual(again["status"], "ARCHIVED")
        snapshot = self.store.snapshot(self.engine.campaign_id)
        self.assertEqual(len(snapshot["handoffs"]), 1)
        self.assertEqual(snapshot["handoffs"][0]["status"], "MANAGER_DECLINED")
        self.assertEqual(len(snapshot["history"]), 3)

    def test_repeated_message_under_new_event_does_not_prepare_another_draft(self):
        self.send_initial()
        question = "SYNTHETIC question about the demonstration."
        self.engine.reply(
            "question-event", self.lead_id, "question-message", question, proposal("QUESTION", question),
            self.at + timedelta(minutes=1),
        )
        auto = "SYNTHETIC out-of-office message."
        self.engine.reply(
            "auto-event", self.lead_id, "auto-message", auto, proposal("AUTO_REPLY", auto),
            self.at + timedelta(minutes=2),
        )
        before = self.store.snapshot(self.engine.campaign_id)
        result = self.engine.reply(
            "new-envelope-for-question", self.lead_id, "question-message", question,
            proposal("QUESTION", question), self.at + timedelta(minutes=3),
        )
        after = self.store.snapshot(self.engine.campaign_id)
        self.assertEqual(result["status"], "DUPLICATE_MESSAGE")
        self.assertEqual(after["history"], before["history"])
        self.assertEqual(after["outbox"], before["outbox"])
        self.assertEqual(after["handoffs"], before["handoffs"])
        self.assertEqual(
            sum(item["kind"] == "REPLY_RECEIVED" for item in after["audit"]),
            sum(item["kind"] == "REPLY_RECEIVED" for item in before["audit"]),
        )

    def test_conflicting_body_for_existing_message_is_rejected_without_data_loss(self):
        text = "SYNTHETIC first message."
        self.engine.reply(
            "first-question", self.lead_id, "same-message", text, proposal("QUESTION", text),
            self.at + timedelta(minutes=1),
        )
        before = self.store.snapshot(self.engine.campaign_id)
        changed = "SYNTHETIC changed contents under an existing message ID."
        with self.assertRaisesRegex(RuleError, "^MESSAGE_ID_CONFLICT$"):
            self.engine.reply(
                "conflicting-envelope", self.lead_id, "same-message", changed,
                proposal("INTEREST", changed), self.at + timedelta(minutes=2),
            )
        self.assertEqual(self.store.snapshot(self.engine.campaign_id), before)

    def test_same_provider_event_in_two_campaigns_gets_distinct_handoffs(self):
        first = self.interest("same-provider-event", "same-provider-message")
        other_policy = deepcopy(self.policy)
        other_policy["campaign"]["runtime_campaign_id"] = "77777777-7777-4777-8777-777777777777"
        other_engine = Engine(self.store, other_policy)
        # The same lead/contact and provider IDs are legal in the other campaign.
        other_engine.ingest("ingest", self.lead, self.at)
        text = "SYNTHETIC: I want to discuss purchasing this demonstration."
        second = other_engine.reply(
            "same-provider-event", self.lead_id, "same-provider-message", text,
            proposal("INTEREST", text), self.at + timedelta(minutes=1),
        )
        self.assertEqual(first["status"], "PREPARED")
        self.assertEqual(second["status"], "PREPARED")
        self.assertNotEqual(first["handoff_id"], second["handoff_id"])
        for engine in (self.engine, other_engine):
            snapshot = self.store.snapshot(engine.campaign_id)
            self.assertEqual(len(snapshot["handoffs"]), 1)
            self.assertEqual(len(snapshot["history"]), 1)
            self.assertEqual(snapshot["history"][0]["message"]["message_id"], "same-provider-message")

    def test_expired_permission_does_not_discard_observed_delivery(self):
        draft = self.engine.prepare_initial("initial", self.lead_id, self.at)
        later = datetime(2027, 1, 2, tzinfo=timezone.utc)
        result = self.engine.simulated_send_receipt(
            "observed-after-expiry", self.lead_id, draft["action_id"], "DELIVERED", later,
        )
        self.assertEqual(result["status"], "DELIVERED")
        self.assertFalse(result["policy_allows_new_action"])
        snapshot = self.store.snapshot(self.engine.campaign_id)
        self.assertEqual(snapshot["outbox"][0]["status"], "DELIVERED")
        self.assertTrue(any(
            item["kind"] == "SEND_RECEIPT"
            and item["payload"]["action_id"] == draft["action_id"]
            and item["payload"]["status"] == "DELIVERED"
            for item in snapshot["audit"]
        ))
        self.assertEqual(len(snapshot["history"]), 1)
        self.assertEqual(self.engine.follow_up("no-followup", self.lead_id, later + timedelta(hours=1))["status"], "BLOCKED")

    def test_cancelled_action_late_receipt_is_kept_for_reconciliation(self):
        draft = self.engine.prepare_initial("initial", self.lead_id, self.at)
        self.engine.reply("stop-event", self.lead_id, "stop-message", "STOP", None, self.at + timedelta(minutes=1))
        result = self.engine.simulated_send_receipt(
            "late-observation", self.lead_id, draft["action_id"], "DELIVERED",
            self.at + timedelta(minutes=2),
        )
        self.assertEqual(result["status"], "REVIEW")
        self.assertEqual(self.store.lead(self.engine.campaign_id, self.lead_id)["state"], "STOPPED")
        snapshot = self.store.snapshot(self.engine.campaign_id)
        self.assertEqual(snapshot["outbox"][0]["status"], "CANCELLED")
        self.assertTrue(any(
            item["kind"] == "SEND_RECEIPT"
            and item["payload"].get("requires_reconciliation") is True
            for item in snapshot["audit"]
        ))

    def test_over_100_late_messages_do_not_rebuild_or_truncate_handoff(self):
        class CountingAcceptedAdapter:
            def __init__(self):
                self.calls = 0
                self.real = HandoffAdapter()

            def prepare(self, **kwargs):
                self.calls += 1
                return self.real.prepare(**kwargs)

        adapter = CountingAcceptedAdapter()
        self.engine.adapter = adapter
        packet = self.interest()
        original_packet = deepcopy(self.store.snapshot(self.engine.campaign_id)["handoffs"][0])
        for index in range(101):
            result = self.interest(
                "late-event-" + str(index), "late-message-" + str(index), minutes=index + 2,
            )
            self.assertEqual(result["status"], "ARCHIVED")
        snapshot = self.store.snapshot(self.engine.campaign_id)
        self.assertEqual(adapter.calls, 1)
        self.assertEqual(len(snapshot["history"]), 102)
        self.assertEqual(len(snapshot["handoffs"]), 1)
        self.assertEqual(snapshot["handoffs"][0], original_packet)
        self.assertEqual(snapshot["handoffs"][0]["handoff_id"], packet["handoff_id"])
        self.assertEqual(self.store.lead(self.engine.campaign_id, self.lead_id)["state"], "HANDOFF_PENDING")

    def test_same_event_retry_at_later_processing_time_returns_original_result(self):
        first = self.engine.ingest("ingest", self.lead, self.at)
        before = self.store.snapshot(self.engine.campaign_id)
        replay = self.engine.ingest("ingest", self.lead, self.at + timedelta(days=1))
        self.assertEqual(first, replay)
        self.assertEqual(self.store.snapshot(self.engine.campaign_id), before)


if __name__ == "__main__":
    unittest.main()
