"""Regression checks for hostile model output, source replay and STOP scope.

All effects use an in-memory simulation database and fictional .test contacts.
"""

import copy
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from first_business.common import RuleError
from first_business.engine import Engine
from first_business.fixtures import make_lead, proposal
from first_business.policy import load_policy
from first_business.store import Store


ROOT = Path(__file__).resolve().parents[1]
AT = datetime(2026, 10, 31, 9, tzinfo=timezone.utc)


class InputRegressionTests(unittest.TestCase):
    def setUp(self):
        self.store = Store(":memory:")
        self.policy = load_policy(ROOT / "config/demo.synthetic.json")
        self.engine = Engine(self.store, self.policy)
        self.lead = make_lead("input-regression", AT)
        self.lead_id = self.lead["lead_id"]
        self.engine.ingest("ingest", self.lead, AT)

    def tearDown(self):
        self.store.close()

    def _delivered(self):
        initial = self.engine.prepare_initial("initial", self.lead_id, AT)
        self.engine.simulated_send_receipt("initial-delivered", self.lead_id,
                                           initial["action_id"], "DELIVERED", AT)

    def _handoff(self):
        self._delivered()
        body = "I want to discuss purchasing this demonstration."
        return self.engine.reply("interest", self.lead_id, "interest-message", body,
                                 proposal("INTEREST", body), AT + timedelta(minutes=5))

    def _assert_model_rejected_without_losing_message(self, value, event, minute):
        body = "I would like details about the demonstration."
        message_id = "message-" + event
        result = self.engine.reply(event, self.lead_id, message_id, body, value,
                                   AT + timedelta(minutes=minute))
        self.assertEqual(result["status"], "REVIEW")
        self.assertTrue(result["message_saved"])
        history = self.store.history(self.engine.campaign_id, self.lead_id)
        self.assertEqual(history[-1]["message_id"], message_id)
        self.assertEqual(history[-1]["body"], body)
        self.assertEqual(history[-1]["direction"], "INBOUND")
        snapshot = self.store.snapshot(self.engine.campaign_id)
        self.assertEqual(snapshot["handoffs"], [])
        self.assertEqual(snapshot["outbox"], [])
        self.assertTrue(any(row["kind"] == "MODEL_PROPOSAL_REJECTED"
                            for row in snapshot["audit"]))

    def test_huge_confidence_rejection_preserves_inbound_history(self):
        value = proposal("INTEREST", "I would like details about the demonstration.")
        value["confidence"] = 10 ** 1000
        self._assert_model_rejected_without_losing_message(value, "huge-confidence", 1)

    def test_nan_confidence_rejection_preserves_inbound_history(self):
        value = proposal("INTEREST", "I would like details about the demonstration.")
        value["confidence"] = float("nan")
        self._assert_model_rejected_without_losing_message(value, "nan-confidence", 1)

    def test_non_json_proposal_and_nested_values_preserve_inbound_history(self):
        body = "I would like details about the demonstration."
        nested = proposal("INTEREST", body)
        nested["fact_ids"] = [object()]
        unhashable_intent = proposal("INTEREST", body)
        unhashable_intent["intent"] = ["INTEREST"]
        cyclic = {}
        cyclic["cycle"] = cyclic
        for index, value in enumerate((object(), {"INTEREST"}, nested,
                                       unhashable_intent, cyclic), start=1):
            with self.subTest(case=index):
                self._assert_model_rejected_without_losing_message(
                    value, "non-json-" + str(index), index)
        self.assertEqual(len(self.store.history(self.engine.campaign_id, self.lead_id)), 5)

    def test_explicit_refusal_has_priority_over_interest_fixture(self):
        self._delivered()
        body = "No thank you, I am not interested."
        result = self.engine.reply("refusal", self.lead_id, "refusal-message", body,
                                   proposal("INTEREST", body), AT + timedelta(minutes=1))
        self.assertEqual(result["status"], "STOPPED")
        self.assertEqual(result["intent"], "REFUSAL")
        snapshot = self.store.snapshot(self.engine.campaign_id)
        self.assertEqual(snapshot["handoffs"], [])
        self.assertFalse(any(row["kind"] == "INTEREST_CONFIRMED"
                             for row in snapshot["audit"]))
        self.assertEqual(self.store.history(self.engine.campaign_id, self.lead_id)[-1]["body"], body)

    def test_same_source_event_replayed_at_later_processing_time_is_idempotent(self):
        before = self.store.snapshot(self.engine.campaign_id)
        original = self.engine.ingest("ingest", self.lead, AT + timedelta(seconds=1))
        self.assertTrue(original["contact_allowed"])
        self.assertEqual(self.store.snapshot(self.engine.campaign_id), before)

    def test_replayed_reply_with_later_clock_and_bad_reclassification_is_idempotent(self):
        body = "What does this demonstration do?"
        value = proposal("QUESTION", body, ["fixture-fact-1"])
        original = self.engine.reply("question", self.lead_id, "question-message", body,
                                     value, AT + timedelta(minutes=1))
        before = self.store.snapshot(self.engine.campaign_id)
        replay = self.engine.reply("question", self.lead_id, "question-message", body,
                                   object(), AT + timedelta(minutes=1, seconds=1))
        self.assertEqual(replay, original)
        self.assertEqual(self.store.snapshot(self.engine.campaign_id), before)

    def test_replayed_message_after_delivered_answer_does_not_prepare_second_reply(self):
        self._delivered()
        body = "What does this demonstration do?"
        value = proposal("QUESTION", body, ["fixture-fact-1"])
        first = self.engine.reply("question-first", self.lead_id, "same-message", body,
                                  value, AT + timedelta(minutes=1))
        self.engine.simulated_send_receipt("answer-delivered", self.lead_id,
                                           first["action_id"], "DELIVERED",
                                           AT + timedelta(minutes=2))
        history_before = self.store.history(self.engine.campaign_id, self.lead_id)
        outbox_before = self.store.snapshot(self.engine.campaign_id)["outbox"]
        replay = self.engine.reply("new-source-envelope", self.lead_id, "same-message", body,
                                   value, AT + timedelta(minutes=3))
        self.assertEqual(replay["status"], "DUPLICATE_MESSAGE")
        self.assertEqual(self.store.history(self.engine.campaign_id, self.lead_id), history_before)
        self.assertEqual(self.store.snapshot(self.engine.campaign_id)["outbox"], outbox_before)

    def test_send_receipt_before_draft_creation_is_rejected_without_state_change(self):
        draft = self.engine.prepare_initial("initial", self.lead_id,
                                             AT + timedelta(minutes=2))
        before = self.store.snapshot(self.engine.campaign_id)
        with self.assertRaises(RuleError):
            self.engine.simulated_send_receipt("impossible-receipt", self.lead_id,
                                               draft["action_id"], "DELIVERED",
                                               AT + timedelta(minutes=1))
        self.assertEqual(self.store.snapshot(self.engine.campaign_id), before)

    def test_handoff_receipt_before_packet_creation_is_rejected(self):
        packet = self._handoff()
        before = self.store.snapshot(self.engine.campaign_id)
        with self.assertRaises(RuleError):
            self.engine.handoff_receipt("impossible-packet-receipt", self.lead_id,
                                        packet["handoff_id"], packet["content_sha256"],
                                        "DELIVERED", AT + timedelta(minutes=4))
        self.assertEqual(self.store.snapshot(self.engine.campaign_id), before)

    def test_manager_ack_before_packet_or_delivery_is_rejected(self):
        packet = self._handoff()
        self.engine.handoff_receipt("packet-delivered", self.lead_id, packet["handoff_id"],
                                    packet["content_sha256"], "DELIVERED",
                                    AT + timedelta(minutes=6))
        before = self.store.snapshot(self.engine.campaign_id)
        for event, minute, second in (("ack-before-created", 4, 0),
                                      ("ack-before-delivery", 5, 30)):
            with self.subTest(event=event):
                with self.assertRaises(RuleError):
                    self.engine.manager_ack(event, self.lead_id, packet["handoff_id"],
                                             packet["content_sha256"], packet["recipient_ref"],
                                             "ACCEPT", AT + timedelta(minutes=minute, seconds=second))
                self.assertEqual(self.store.snapshot(self.engine.campaign_id), before)

    def _other_campaign(self, label, *, other_client=False):
        policy = copy.deepcopy(self.policy)
        policy["campaign"]["runtime_campaign_id"] = str(uuid5(NAMESPACE_URL, "fixture:campaign:" + label))
        if other_client:
            policy["campaign"]["client_id"] = str(uuid5(NAMESPACE_URL, "fixture:client:" + label))
        engine = Engine(self.store, policy)
        lead = make_lead(label, AT)
        # One contact under separate campaign/tenant identities tests STOP scope.
        lead["email"] = self.lead["email"]
        engine.ingest("ingest-" + label, lead, AT)
        return engine, lead["lead_id"]

    def test_stop_applies_across_same_client_campaigns_but_not_other_tenant(self):
        sibling, sibling_id = self._other_campaign("same-client-campaign")
        other, other_id = self._other_campaign("other-client-campaign", other_client=True)
        first = self.engine.prepare_initial("initial", self.lead_id, AT)
        sibling_draft = sibling.prepare_initial("initial", sibling_id, AT)
        other_draft = other.prepare_initial("initial", other_id, AT)
        self.engine.reply("stop", self.lead_id, "stop-message", "STOP", None,
                           AT + timedelta(minutes=1))
        self.assertEqual(self.store.lead(self.engine.campaign_id, self.lead_id)["state"], "STOPPED")
        self.assertEqual(self.store.lead(sibling.campaign_id, sibling_id)["state"], "STOPPED")
        self.assertNotEqual(self.store.lead(other.campaign_id, other_id)["state"], "STOPPED")
        statuses = {row["action_id"]: row["status"] for engine in (self.engine, sibling, other)
                    for row in self.store.snapshot(engine.campaign_id)["outbox"]}
        self.assertEqual(statuses[first["action_id"]], "CANCELLED")
        self.assertEqual(statuses[sibling_draft["action_id"]], "CANCELLED")
        self.assertEqual(statuses[other_draft["action_id"]], "PREPARED")
        blocked = sibling.prepare_initial("after-stop", sibling_id,
                                           AT + timedelta(minutes=2))
        self.assertEqual(blocked["status"], "BLOCKED")
        allowed = other.simulated_send_receipt("other-delivered", other_id,
                                               other_draft["action_id"], "DELIVERED",
                                               AT + timedelta(minutes=2))
        self.assertEqual(allowed["status"], "DELIVERED")
        self.assertTrue(allowed["policy_allows_new_action"])


if __name__ == "__main__":
    unittest.main()
