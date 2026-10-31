"""Business-profile gates: ICP fit never substitutes for contact evidence."""

import copy
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from first_business.policy import check_policy, evaluate_lead, load_policy


ROOT = Path(__file__).resolve().parents[1]
AT = datetime(2026, 10, 31, 9, tzinfo=timezone.utc)


def demo_lead():
    return {
        "lead_id": "77777777-7777-4777-8777-777777777777",
        "company_name": "SYNTHETIC demo company",
        "country": "ZZ", "industry": "synthetic-automation", "company_size": 20,
        "contact_role": "synthetic-owner", "contact_id": "88888888-8888-4888-8888-888888888888",
        "email": "owner@company.test", "source_url": "fixture:synthetic-company",
        "source_observed_at": "2026-10-30T09:00:00Z", "source_verified": True,
        "contact_verified": True,
        "legal_basis": {"approved": True, "evidence_ref": "fixture:synthetic-contact-basis",
                        "country": "ZZ", "channel": "email", "expires_at": "2027-01-01T00:00:00Z"},
    }


class PolicyTests(unittest.TestCase):
    def setUp(self):
        self.policy = load_policy(ROOT / "config/demo.synthetic.json")

    def test_demo_is_valid_but_never_live(self):
        self.assertEqual(check_policy(self.policy), {"valid": True, "errors": [], "unresolved": [], "live_ready": False})

    def test_pending_is_structurally_valid_and_names_missing_inputs(self):
        policy = load_policy(ROOT / "config/campaign.pending.json")
        report = check_policy(policy)
        self.assertTrue(report["valid"])
        self.assertIn("product.price", report["unresolved"])
        self.assertIn("target.countries", report["unresolved"])
        self.assertIn("legal.approval_ref", report["unresolved"])
        self.assertFalse(evaluate_lead(policy, demo_lead(), AT)["contact_allowed"])

    def test_complete_synthetic_profile_allows_only_simulated_contact(self):
        self.assertEqual(evaluate_lead(self.policy, demo_lead(), AT), {"qualified": True, "contact_allowed": True, "reasons": []})

    def test_qualification_does_not_grant_contact(self):
        lead = demo_lead()
        lead["legal_basis"]["approved"] = False
        result = evaluate_lead(self.policy, lead, AT)
        self.assertTrue(result["qualified"])
        self.assertFalse(result["contact_allowed"])
        self.assertIn("LEGAL_BASIS_NOT_APPROVED", result["reasons"])

    def test_rejected_and_unverified_sources_fail_qualification(self):
        for field, value in (("country", "XX"), ("industry", "other"),
                             ("contact_role", "other"), ("company_size", 501),
                             ("source_verified", False), ("source_url", "https://company.test")):
            with self.subTest(field=field):
                lead = demo_lead()
                lead[field] = value
                self.assertFalse(evaluate_lead(self.policy, lead, AT)["qualified"])

    def test_contact_country_channel_expiry_and_verification_gates(self):
        mutations = [
            lambda l: l.update(contact_verified=False),
            lambda l: l.update(email="owner@example.com"),
            lambda l: l["legal_basis"].update(country="XX"),
            lambda l: l["legal_basis"].update(channel="sms"),
            lambda l: l["legal_basis"].update(evidence_ref="unverified"),
            lambda l: l["legal_basis"].update(expires_at="2026-10-31T09:00:00Z"),
            lambda l: l["legal_basis"].update(expires_at="2027-01-01T00:00:00"),
        ]
        for mutate in mutations:
            with self.subTest(mutation=mutate):
                lead = demo_lead()
                mutate(lead)
                result = evaluate_lead(self.policy, lead, AT)
                self.assertTrue(result["qualified"])
                self.assertFalse(result["contact_allowed"])

    def test_policy_expiration_uses_explicit_time(self):
        result = evaluate_lead(self.policy, demo_lead(), datetime(2027, 1, 1, tzinfo=timezone.utc))
        self.assertTrue(result["qualified"])
        self.assertFalse(result["contact_allowed"])
        self.assertIn("POLICY_LEGAL_APPROVAL_EXPIRED_OR_INVALID", result["reasons"])

    def test_future_source_and_naive_operation_time_fail_closed(self):
        lead = demo_lead()
        lead["source_observed_at"] = "2026-11-01T00:00:00Z"
        self.assertFalse(evaluate_lead(self.policy, lead, AT)["qualified"])
        self.assertEqual(evaluate_lead(self.policy, demo_lead(), datetime(2026, 10, 31))["reasons"], ["INVALID_TIME"])

    def test_pending_cannot_be_unlocked_by_complete_fields(self):
        self.policy["approval"]["status"] = "PENDING_OWNER_INPUT"
        result = evaluate_lead(self.policy, demo_lead(), AT)
        self.assertTrue(result["qualified"])
        self.assertFalse(result["contact_allowed"])
        self.assertIn("POLICY_PENDING_OWNER_INPUT", result["reasons"])

    def test_strict_fields_and_no_live_execution_mode(self):
        for mutate in (lambda p: p.update(execution_mode="LIVE"),
                       lambda p: p.update(unknown=True),
                       lambda p: p["approval"].update(status="APPROVED"),
                       lambda p: p["product"].update(unknown=True),
                       lambda p: p["campaign"].update(runtime_campaign_id="bad-uuid")):
            with self.subTest(mutation=mutate):
                p = copy.deepcopy(self.policy)
                mutate(p)
                self.assertFalse(check_policy(p)["valid"])

    def test_boolean_and_unbounded_numeric_limits_are_rejected(self):
        cases = [("target", "min_company_size", True), ("follow_up", "max_attempts", True),
                 ("follow_up", "max_attempts", 1000), ("follow_up", "offset_hours", [True, 120]),
                 ("follow_up", "offset_hours", [120, 48]), ("follow_up", "offset_hours", [48, 48]),
                 ("handoff", "ack_timeout_minutes", False), ("ai", "min_confidence", True),
                 ("ai", "min_confidence", float("nan")), ("ai", "min_confidence", 0),
                 ("pilot", "budget", True), ("pilot", "budget", float("inf")),
                 ("pilot", "budget", 10 ** 1000), ("ai", "min_confidence", 10 ** 1000)]
        for section, field, value in cases:
            with self.subTest(section=section, field=field, value=value):
                p = copy.deepcopy(self.policy)
                p[section][field] = value
                self.assertFalse(check_policy(p)["valid"])

    def test_timestamp_syntax_and_offset_validation(self):
        for value in ("2026-10-31", "2026-10-31T09:00:00", "2026-10-31T09:00:00-00:00",
                      "2026-10-31T09:00:00+00:60", "2026-02-31T00:00:00Z", True):
            with self.subTest(value=value):
                p = copy.deepcopy(self.policy)
                p["legal"]["valid_until"] = value
                self.assertFalse(check_policy(p)["valid"])

    def test_synthetic_approval_and_fact_sources_cannot_claim_live_authority(self):
        for section, field in (("approval", "approved_by"), ("legal", "approval_ref"), ("handoff", "approval_ref")):
            p = copy.deepcopy(self.policy)
            p[section][field] = "a-real-person"
            self.assertFalse(check_policy(p)["valid"])
        self.policy["product"]["approved_facts"][0]["source_ref"] = "https://example.com"
        self.assertFalse(check_policy(self.policy)["valid"])

    def test_duplicate_facts_and_invalid_scalar_types_are_rejected(self):
        self.policy["product"]["approved_facts"] *= 2
        self.assertFalse(check_policy(self.policy)["valid"])
        for value in (None, [], True, "profile"):
            self.assertFalse(check_policy(value)["valid"])

    def test_malformed_lead_does_not_raise_or_admit_contact(self):
        mutations = [lambda l: l.update(extra=True), lambda l: l.update(company_size=True),
                     lambda l: l.update(source_verified=1), lambda l: l.update(legal_basis=[]),
                     lambda l: l["legal_basis"].update(approved=1)]
        for mutate in mutations:
            lead = demo_lead()
            mutate(lead)
            self.assertFalse(evaluate_lead(self.policy, lead, AT)["contact_allowed"])

    def test_inputs_are_not_mutated(self):
        lead = demo_lead()
        before = copy.deepcopy((self.policy, lead))
        evaluate_lead(self.policy, lead, AT)
        check_policy(self.policy)
        self.assertEqual((self.policy, lead), before)

    def test_malformed_test_addresses_and_surrogate_text_are_rejected(self):
        for address in ("owner@.test", "owner@a..test", "a..b@company.test", ".a@company.test", "a.@company.test"):
            lead = demo_lead()
            lead["email"] = address
            self.assertFalse(evaluate_lead(self.policy, lead, AT)["contact_allowed"])
        self.policy["product"]["name"] = "bad\ud800"
        self.assertFalse(check_policy(self.policy)["valid"])

    def test_load_policy_rejects_duplicate_json_keys_and_nonfinite(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "policy.json"
            for raw in ('{"schema_version":"a","schema_version":"b"}', '{"value":NaN}', '{}', 'not json'):
                path.write_text(raw, encoding="utf-8")
                with self.assertRaises(ValueError):
                    load_policy(path)

    def test_load_policy_rejects_oversized_input(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "policy.json"
            path.write_bytes(b" " * 131073)
            with self.assertRaisesRegex(ValueError, "POLICY_TOO_LARGE"):
                load_policy(path)


if __name__ == "__main__":
    unittest.main()
