"""Cohorts, evidence states, exact costs and latency definitions."""

from copy import deepcopy
from datetime import datetime, timezone
from decimal import localcontext
import unittest

from first_business.metrics import MetricsError, calculate_metrics


START = datetime(2026, 10, 31, tzinfo=timezone.utc)
END = datetime(2026, 11, 1, tzinfo=timezone.utc)


def event(kind, lead="lead-1", *, campaign="campaign-1", at="2026-10-31T10:00:00Z", **payload):
    return {"campaign_id": campaign, "lead_id": lead, "kind": kind,
            "occurred_at": at, "payload": payload}


def cost(amount, lead="lead-1", *, campaign="campaign-1", at="2026-10-31T12:00:00Z",
         currency="DEMO", category="llm"):
    return {"campaign_id": campaign, "lead_id": lead, "occurred_at": at,
            "amount": amount, "currency": currency, "category": category}


class MetricsTests(unittest.TestCase):
    def report(self, events, costs=None):
        return calculate_metrics(events, costs or [], start=START, end=END)

    def test_empty_sample_has_unknown_rates_and_costs(self):
        report = self.report([])
        self.assertEqual(report["scope"], "SIMULATION")
        self.assertIs(report["real_business_evidence"], False)
        self.assertEqual(report["rates"]["delivery"], {"numerator": 0, "denominator": 0, "value": None})
        self.assertIsNone(report["costs"]["per_qualified_lead"]["value"])
        self.assertIsNone(report["timing"]["interest_to_handoff"]["median_seconds"])
        self.assertTrue(all(target["value"] is None for target in report["targets"].values()))

    def test_qualified_and_contact_allowed_are_distinct_gates(self):
        report = self.report([
            event("LEAD_INGESTED", "a"), event("LEAD_INGESTED", "b"),
            event("LEAD_INGESTED", "c"), event("QUALIFIED", "a"),
            event("QUALIFIED", "b"), event("CONTACT_ALLOWED", "a"),
        ])
        self.assertEqual(report["rates"]["qualification"], {"numerator": 2, "denominator": 3, "value": 2 / 3})
        self.assertEqual(report["rates"]["contact_admission"], {"numerator": 1, "denominator": 2, "value": 0.5})

    def test_receipt_progress_is_one_lead_and_unknown_is_not_delivery(self):
        report = self.report([
            event("LEAD_INGESTED", "a"), event("LEAD_INGESTED", "b"), event("LEAD_INGESTED", "c"),
            event("SEND_RECEIPT", "a", at="2026-10-31T10:02:00Z", status="DELIVERED", simulation=True),
            event("SEND_RECEIPT", "a", at="2026-10-31T10:01:00Z", status="ACCEPTED", simulation=True),
            event("SEND_RECEIPT", "b", status="ACCEPTED", simulation=True),
            event("SEND_RECEIPT", "c", status="UNKNOWN", simulation=True),
        ])
        self.assertEqual(report["counts"]["send_receipt_leads"], 3)
        self.assertEqual(report["counts"]["send_accepted"], 1)
        self.assertEqual(report["counts"]["send_delivered"], 1)
        self.assertEqual(report["counts"]["send_unknown"], 1)
        self.assertEqual(report["rates"]["delivery"]["denominator"], 3)
        self.assertEqual(report["receipt_observations"]["SEND_RECEIPT"]["ACCEPTED"], 2)

    def test_reply_and_interest_denominators_are_delivered_cohort(self):
        report = self.report([
            event("LEAD_INGESTED", "a"), event("LEAD_INGESTED", "b"),
            event("SEND_RECEIPT", "a", status="DELIVERED", simulation=True),
            event("SEND_RECEIPT", "b", status="UNKNOWN", simulation=True),
            event("REPLY_RECEIVED", "a", intent="QUESTION"), event("REPLY_RECEIVED", "b", intent="INTEREST"),
            event("INTEREST_CONFIRMED", "b"),
        ])
        self.assertEqual(report["counts"]["replied_leads"], 2)
        self.assertEqual(report["rates"]["reply"], {"numerator": 1, "denominator": 1, "value": 1.0})
        self.assertEqual(report["rates"]["interest"], {"numerator": 0, "denominator": 1, "value": 0.0})

    def test_unknown_followup_does_not_erase_initial_delivered_contact(self):
        report = self.report([
            event("LEAD_INGESTED"),
            event("SEND_RECEIPT", at="2026-10-31T10:01:00Z", status="DELIVERED", simulation=True),
            event("REPLY_RECEIVED", at="2026-10-31T10:02:00Z", intent="INTEREST"),
            event("INTEREST_CONFIRMED", at="2026-10-31T10:02:00Z"),
            event("SEND_RECEIPT", at="2026-10-31T11:00:00Z", status="UNKNOWN", simulation=True),
        ])
        self.assertEqual(report["counts"]["send_unknown"], 1)
        self.assertEqual(report["counts"]["send_delivered"], 0)
        self.assertEqual(report["counts"]["ever_delivered_leads"], 1)
        self.assertEqual(report["rates"]["delivery"], {"numerator": 1, "denominator": 1, "value": 1.0})
        self.assertEqual(report["rates"]["reply"], {"numerator": 1, "denominator": 1, "value": 1.0})
        self.assertEqual(report["rates"]["interest"], {"numerator": 1, "denominator": 1, "value": 1.0})

    def test_auto_reply_and_uncertainty_are_not_human_response_conversion(self):
        report = self.report([
            event("LEAD_INGESTED", "a"), event("LEAD_INGESTED", "b"), event("LEAD_INGESTED", "c"),
            event("SEND_RECEIPT", "a", status="DELIVERED", simulation=True),
            event("SEND_RECEIPT", "b", status="DELIVERED", simulation=True),
            event("SEND_RECEIPT", "c", status="DELIVERED", simulation=True),
            event("REPLY_RECEIVED", "a", intent="AUTO_REPLY"),
            event("REPLY_RECEIVED", "b", intent="UNCERTAIN"),
            event("REPLY_RECEIVED", "c"),
        ])
        self.assertEqual(report["counts"]["reply_observation_leads"], 3)
        self.assertEqual(report["counts"]["auto_reply_leads"], 1)
        self.assertEqual(report["counts"]["uncertain_reply_leads"], 1)
        self.assertEqual(report["counts"]["unclassified_reply_leads"], 1)
        self.assertEqual(report["rates"]["reply"], {"numerator": 0, "denominator": 3, "value": 0.0})

    def test_refusal_and_stop_are_human_replies_not_interests(self):
        report = self.report([
            event("LEAD_INGESTED", "a"), event("LEAD_INGESTED", "b"),
            event("SEND_RECEIPT", "a", status="DELIVERED", simulation=True),
            event("SEND_RECEIPT", "b", status="DELIVERED", simulation=True),
            event("REPLY_RECEIVED", "a", intent="REFUSAL"), event("REPLY_RECEIVED", "b", intent="STOP"),
        ])
        self.assertEqual(report["rates"]["reply"]["value"], 1.0)
        self.assertEqual(report["rates"]["interest"]["value"], 0.0)

    def test_campaign_and_lead_make_composite_cohort_identity(self):
        report = self.report([
            event("LEAD_INGESTED", campaign="x"), event("LEAD_INGESTED", campaign="y"),
            event("QUALIFIED", campaign="x"), event("QUALIFIED", campaign="z"),
        ], [cost("0.1", campaign="x"), cost("0.2", campaign="y"), cost("999", campaign="z")])
        self.assertEqual(report["counts"]["leads_ingested"], 2)
        self.assertEqual(report["counts"]["qualified_leads"], 1)
        self.assertEqual(report["costs"]["total_amount"], "0.3")

    def test_first_ingestion_prevents_recounting_older_lead(self):
        report = self.report([
            event("LEAD_INGESTED", at="2026-10-30T23:59:59Z"), event("LEAD_INGESTED"),
            event("QUALIFIED"), event("LEAD_INGESTED", "at-end", at="2026-11-01T00:00:00Z"),
        ])
        self.assertEqual(report["counts"]["leads_ingested"], 0)
        self.assertEqual(report["counts"]["qualified_leads"], 0)

    def test_ingestion_is_half_open_observation_end_is_inclusive(self):
        report = self.report([
            event("LEAD_INGESTED", at="2026-10-31T00:00:00Z"),
            event("QUALIFIED", at="2026-11-01T00:00:00Z"),
            event("CONTACT_ALLOWED", at="2026-11-01T00:00:00.000001Z"),
            event("DRAFT_PREPARED", at="2026-10-30T23:59:59Z"),
        ], [cost("1", at="2026-11-01T00:00:00Z"), cost("100", at="2026-11-01T00:00:00.000001Z")])
        self.assertEqual(report["counts"]["qualified_leads"], 1)
        self.assertEqual(report["counts"]["contacts_allowed"], 0)
        self.assertEqual(report["counts"]["drafts_prepared"], 0)
        self.assertEqual(report["costs"]["total_amount"], "1")

    def test_exact_decimal_total_and_cost_per_result(self):
        records = [event("LEAD_INGESTED"), event("QUALIFIED"), event("HANDOFF_PREPARED"),
                   event("MANAGER_ACCEPTED", simulation=True)]
        with localcontext() as context:
            context.prec = 2
            report = self.report(records, [cost("0.1"), cost("0.2"), cost("1000.001", category="labor")])
        self.assertEqual(report["costs"]["total_amount"], "1000.301")
        self.assertEqual(report["costs"]["by_category"], {"labor": "1000.001", "llm": "0.3"})
        self.assertEqual(report["costs"]["per_qualified_lead"]["value"], "1000.301")
        self.assertEqual(report["costs"]["per_manager_accepted_lead"]["value"], "1000.301")

    def test_currency_mismatch_and_non_decimal_amount_rejected(self):
        records = [event("LEAD_INGESTED")]
        with self.assertRaisesRegex(MetricsError, "CURRENCY_MISMATCH"):
            self.report(records, [cost("1", currency="USD")])
        for amount in (0.1, "NaN", "Infinity", "-1", "1e6", "1,23", True):
            with self.subTest(amount=amount), self.assertRaisesRegex(MetricsError, "INVALID_COST_AMOUNT"):
                self.report(records, [cost(amount)])

    def test_unrelated_cost_currency_is_excluded_not_mixed(self):
        report = self.report([event("LEAD_INGESTED")], [cost("1"), cost("100", lead="outside", currency="USD")])
        self.assertEqual(report["costs"]["total_amount"], "1")
        self.assertEqual(report["costs"]["excluded_entries"], 1)

    def test_receipt_does_not_confirm_manager_acceptance(self):
        report = self.report([
            event("LEAD_INGESTED"), event("HANDOFF_PREPARED"),
            event("HANDOFF_RECEIPT", status="DELIVERED", simulation=True),
        ])
        self.assertEqual(report["counts"]["handoff_delivered"], 1)
        self.assertEqual(report["counts"]["manager_accepted"], 0)
        self.assertEqual(report["rates"]["manager_acceptance"]["value"], 0.0)

    def test_latency_median_max_and_unaccepted_handoffs(self):
        records = []
        for lead, handoff, accepted in (("a", "10:02", "10:05"), ("b", "10:04", "10:09")):
            records.extend([
                event("LEAD_INGESTED", lead), event("INTEREST_CONFIRMED", lead),
                event("HANDOFF_PREPARED", lead, at=f"2026-10-31T{handoff}:00Z"),
                event("MANAGER_ACCEPTED", lead, at=f"2026-10-31T{accepted}:00Z", simulation=True),
            ])
        records.extend([event("LEAD_INGESTED", "c"), event("INTEREST_CONFIRMED", "c"),
                        event("HANDOFF_PREPARED", "c", at="2026-10-31T10:06:00Z")])
        report = self.report(records)
        self.assertEqual(report["timing"]["interest_to_handoff"],
                         {"count": 3, "median_seconds": 240.0, "max_seconds": 360.0, "pending_count": 0})
        self.assertEqual(report["timing"]["handoff_to_manager_acceptance"],
                         {"count": 2, "median_seconds": 240.0, "max_seconds": 300.0, "pending_count": 1})

    def test_no_negative_latency_and_no_false_completion(self):
        report = self.report([
            event("LEAD_INGESTED"), event("MANAGER_ACCEPTED", simulation=True),
            event("HANDOFF_PREPARED", at="2026-10-31T10:05:00Z"),
        ])
        self.assertIsNone(report["timing"]["handoff_to_manager_acceptance"]["median_seconds"])
        self.assertEqual(report["timing"]["handoff_to_manager_acceptance"]["pending_count"], 1)

    def test_interest_review_only_counts_reviewed_confirmed_interests(self):
        report = self.report([
            event("LEAD_INGESTED", "a"), event("LEAD_INGESTED", "b"), event("LEAD_INGESTED", "c"),
            event("INTEREST_CONFIRMED", "a"), event("INTEREST_CONFIRMED", "b"),
            event("INTEREST_REVIEW", "a", correct=False), event("INTEREST_REVIEW", "b", correct=True),
            event("INTEREST_REVIEW", "c", correct=False),
        ])
        self.assertEqual(report["rates"]["false_interest"], {"numerator": 1, "denominator": 2, "value": 0.5})

    def test_guard_counts_are_observations_not_guarantees(self):
        report = self.report([
            event("LEAD_INGESTED"), event("OUTBOUND_DUPLICATE_OBSERVED"),
            event("STOP_VIOLATION_OBSERVED"), event("STOP_VIOLATION_OBSERVED"),
        ])
        self.assertEqual(report["observed_guards"]["stop_violations"],
                         {"observed_count": 2, "absence_guaranteed": False})
        self.assertEqual(report["observed_guards"]["history_loss"],
                         {"observed_count": 0, "absence_guaranteed": False})

    def test_non_simulated_effects_and_bad_receipt_states_rejected(self):
        for kind in ("SEND_RECEIPT", "HANDOFF_RECEIPT", "MANAGER_ACCEPTED"):
            with self.subTest(kind=kind), self.assertRaisesRegex(MetricsError, "NON_SIMULATED_EFFECT"):
                self.report([event("LEAD_INGESTED"), event(kind, status="DELIVERED")])
        with self.assertRaisesRegex(MetricsError, "INVALID_RECEIPT_STATUS"):
            self.report([event("LEAD_INGESTED"), event("SEND_RECEIPT", status="SENT", simulation=True)])

    def test_bad_intervals_naive_timestamps_and_review_values_rejected(self):
        with self.assertRaisesRegex(MetricsError, "INVALID_INTERVAL"):
            calculate_metrics([], [], start=END, end=START)
        with self.assertRaisesRegex(MetricsError, "INVALID_INTERVAL"):
            calculate_metrics([], [], start=datetime(2026, 10, 31), end=END)
        with self.assertRaisesRegex(MetricsError, "INVALID_TIMESTAMP"):
            self.report([event("LEAD_INGESTED", at="2026-10-31T10:00:00")])
        with self.assertRaisesRegex(MetricsError, "INVALID_INTEREST_REVIEW"):
            self.report([event("LEAD_INGESTED"), event("INTEREST_REVIEW", correct="true")])

    def test_timezone_normalization_and_input_immutability(self):
        records = [event("LEAD_INGESTED", at="2026-10-31T13:00:00+03:00"), event("QUALIFIED")]
        expenses = [cost("0.2")]
        before = deepcopy((records, expenses))
        report = self.report(records, expenses)
        self.assertEqual(report["counts"]["qualified_leads"], 1)
        self.assertEqual((records, expenses), before)


if __name__ == "__main__":
    unittest.main()
