"""Integration tests using the real pinned F1-09 and F1-19 SOURCE modules."""

from copy import deepcopy
import sys
from pathlib import Path
import tempfile
from types import ModuleType
import unittest

from first_business.adapters import (
    AdapterInputError,
    HandoffAdapter,
    SourceDependencyError,
    prohibit_live_outbound,
)


def inputs():
    return {
        "lead": {"lead_id": "lead-demo", "company_name": "Synthetic Company", "contact_id": "contact-demo", "email": "demo@example.test"},
        "policy": {
            "version": "business-demo-v1",
            "campaign": {
                "client_id": "client-demo",
                "runtime_campaign_id": "11111111-1111-4111-8111-111111111111",
                "mailbox_ref": "mailbox-demo",
                "thread_prefix": "thread-",
                "locale": "ru-RU",
                "language_pack_version": "ru-demo-v1",
            },
            "product": {
                "product_id": "product-demo",
                "approved_facts": [{"fact_id": "fact-a", "text": "Синтетический факт для теста.", "source_ref": "fixture:fact-a"}],
            },
            "handoff": {"recipient_ref": "manager-demo", "approval_ref": "fixture:recipient-approval"},
        },
        "history": [{
            "message_id": "msg-demo", "sequence": 0, "direction": "INBOUND", "actor": "CLIENT",
            "body": "Синтетический пример: давайте обсудим предложение.", "fact_ids": [], "material_ids": [],
        }],
        "event_id": "handoff-demo",
        "evidence_ref": "fixture:confirmed-interest",
    }


class HandoffAdapterIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # A missing original is a failing integration check, never a skip/fake.
        cls.adapter = HandoffAdapter()

    def test_real_modules_prepare_context_without_dispatch_or_manager_ack(self):
        result = self.adapter.prepare(**inputs())
        self.assertEqual(result["schema_version"], "FIRST_HANDOFF_INTENT_V1")
        self.assertEqual(result["status"], "PREPARED")
        self.assertEqual(result["code"], "OK")
        self.assertEqual(result["recipient_ref"], "manager-demo")
        context = result["context"]
        self.assertEqual(context["schema_version"], "FIRST_HANDOFF_CONTEXT_V1")
        self.assertEqual(context["delivery_status"], "NOT_DISPATCHED")
        self.assertEqual(context["status"], "ACTIVE")
        self.assertEqual(context["thread_id"], "thread-lead-demo")
        self.assertEqual(context["policy_version"], "business-demo-v1")
        self.assertEqual(context["language_packs"], {"ru-RU": "ru-demo-v1"})
        self.assertEqual(context["history"][0]["actor"], "BUYER")
        self.assertNotIn("accepted_by", result)

    def test_inputs_are_preserved_and_result_is_independent(self):
        original = inputs()
        snapshot = deepcopy(original)
        result = self.adapter.prepare(**original)
        self.assertEqual(original, snapshot)
        result["context"]["facts"][0]["text"] = "Changed output"
        result["context"]["history"][0]["body"] = "Changed output"
        self.assertEqual(original, snapshot)

    def test_same_event_same_payload_is_stable_but_changed_body_changes_hash(self):
        data = inputs()
        first = self.adapter.prepare(**data)
        self.assertEqual(first, self.adapter.prepare(**data))
        data["history"][0]["body"] = "Синтетический пример с изменённым содержимым."
        changed = self.adapter.prepare(**data)
        self.assertEqual(first["handoff_id"], changed["handoff_id"])
        self.assertNotEqual(first["content_sha256"], changed["content_sha256"])

    def test_accepted_component_canonicalizes_order(self):
        data = inputs()
        data["history"].append({
            "message_id": "msg-next", "sequence": 1, "direction": "OUTBOUND", "actor": "SYSTEM",
            "body": "Синтетический черновик.", "fact_ids": ["fact-a"], "material_ids": [],
        })
        before = self.adapter.prepare(**data)
        data["history"].reverse()
        after = self.adapter.prepare(**data)
        self.assertEqual(before, after)
        self.assertEqual(after["context"]["history"][1]["actor"], "AUTOMATION")

    def test_foreign_message_is_rejected_by_actual_f109(self):
        data = inputs()
        data["history"][0]["client_id"] = "foreign-client"
        with self.assertRaises(ValueError) as caught:
            self.adapter.prepare(**data)
        self.assertEqual(getattr(caught.exception, "code", None), "MESSAGE_SCOPE_MISMATCH")

    def test_duplicate_message_is_rejected_by_actual_f109(self):
        data = inputs()
        data["history"].append(deepcopy(data["history"][0]))
        with self.assertRaises(ValueError) as caught:
            self.adapter.prepare(**data)
        self.assertEqual(getattr(caught.exception, "code", None), "DUPLICATE_ID")

    def test_history_over_limit_is_rejected_without_truncation(self):
        data = inputs()
        message = data["history"][0]
        data["history"] = [{**message, "message_id": f"msg-{i}", "sequence": i} for i in range(101)]
        snapshot = deepcopy(data)
        with self.assertRaises(ValueError) as caught:
            self.adapter.prepare(**data)
        self.assertEqual(getattr(caught.exception, "code", None), "INVALID_CONTEXT")
        self.assertEqual(data, snapshot)

    def test_missing_recipient_approval_is_rejected_by_actual_f119(self):
        data = inputs()
        data["policy"]["handoff"]["approval_ref"] = None
        with self.assertRaises(ValueError) as caught:
            self.adapter.prepare(**data)
        self.assertEqual(getattr(caught.exception, "code", None), "INVALID_INPUT")

    def test_conflicting_lead_scope_is_not_silently_relabelled(self):
        data = inputs()
        data["lead"]["runtime_campaign_id"] = "foreign-campaign"
        with self.assertRaisesRegex(AdapterInputError, "^LEAD_SCOPE_MISMATCH$"):
            self.adapter.prepare(**data)

    def test_missing_dependency_does_not_create_fake_prepared_result(self):
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(SourceDependencyError, "^ACCEPTED_HANDOFF_DEPENDENCY_MISSING$"):
                HandoffAdapter(Path(temporary))
            with self.assertRaisesRegex(SourceDependencyError, "^ACCEPTED_SOURCE_NOT_FOUND$"):
                HandoffAdapter(Path(temporary) / "missing")

    def test_installed_name_collision_does_not_replace_accepted_dependency(self):
        sentinel = ModuleType("sales_first_handoff")
        def wrong_dependency(_context):
            raise AssertionError("Unrelated installed module must never be called")
        sentinel.assemble_handoff_context = wrong_dependency
        previous = sys.modules.get("sales_first_handoff")
        sys.modules["sales_first_handoff"] = sentinel
        try:
            adapter = HandoffAdapter(self.adapter.runtime_root)
            self.assertEqual(adapter.prepare(**inputs())["status"], "PREPARED")
            self.assertIs(sys.modules["sales_first_handoff"], sentinel)
        finally:
            if previous is None:
                sys.modules.pop("sales_first_handoff", None)
            else:
                sys.modules["sales_first_handoff"] = previous

    def test_live_outbound_always_holds(self):
        with self.assertRaisesRegex(RuntimeError, "^NO_OUTBOUND_HOLD$"):
            prohibit_live_outbound(object(), enabled=True)


if __name__ == "__main__":
    unittest.main()
