"""Pure, cohort-based projections of a synthetic FIRST event journal.

No counters in this module are evidence of a real delivery or a real sale.
An eligible cohort is formed by the earliest LEAD_INGESTED event for each
(campaign_id, lead_id). Events and allocated costs are observed through ``end``
inclusive; ingestion itself uses the half-open interval [start, end).
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, localcontext
import re
from statistics import median


class MetricsError(ValueError):
    """Invalid metric input; the message is a stable code without input data."""


_CURRENCY = "DEMO"
_AMOUNT = re.compile(r"[0-9]{1,100}(?:\.[0-9]{1,100})?\Z", re.ASCII)
_RECEIPT_STATES = frozenset({"ACCEPTED", "DELIVERED", "UNKNOWN"})
_EFFECT_KINDS = frozenset({"SEND_RECEIPT", "HANDOFF_RECEIPT", "MANAGER_ACCEPTED"})
_HUMAN_INTENTS = frozenset({"QUESTION", "REQUEST_INFO", "OBJECTION", "REFUSAL", "STOP", "INTEREST"})
_GUARD_KINDS = {
    "OUTBOUND_DUPLICATE_OBSERVED": "duplicate_outbound",
    "STOP_VIOLATION_OBSERVED": "stop_violations",
    "HISTORY_LOSS_OBSERVED": "history_loss",
}


def _aware(value: datetime, code: str) -> datetime:
    if not isinstance(value, datetime):
        raise MetricsError(code)
    try:
        if value.tzinfo is None or value.utcoffset() is None:
            raise MetricsError(code)
        return value.astimezone(timezone.utc)
    except (OverflowError, ValueError, TypeError) as exc:
        raise MetricsError(code) from exc


def _timestamp(value: object) -> datetime:
    if type(value) is not str or not 1 <= len(value) <= 64:
        raise MetricsError("INVALID_TIMESTAMP")
    try:
        result = datetime.fromisoformat(value[:-1] + "+00:00" if value.endswith("Z") else value)
    except ValueError as exc:
        raise MetricsError("INVALID_TIMESTAMP") from exc
    return _aware(result, "INVALID_TIMESTAMP")


def _text(value: object, code: str) -> str:
    if type(value) is not str or not value.strip() or value != value.strip():
        raise MetricsError(code)
    return value


def _identity(record: dict) -> tuple[str, str]:
    return (
        _text(record.get("campaign_id"), "INVALID_IDENTITY"),
        _text(record.get("lead_id"), "INVALID_IDENTITY"),
    )


def _rate(numerator: int, denominator: int) -> dict:
    return {
        "numerator": numerator,
        "denominator": denominator,
        "value": numerator / denominator if denominator else None,
    }


def _number(value: Decimal) -> str:
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def _cost_rate(total: Decimal, denominator: int) -> dict:
    with localcontext() as context:
        context.prec = 28
        value = _number(total / Decimal(denominator)) if denominator else None
    return {
        "numerator": _number(total),
        "denominator": denominator,
        "value": value,
        "currency": _CURRENCY,
        "division_precision": 28,
    }


def _timing(origin: dict, destination: dict) -> dict:
    seconds = [
        (destination[key] - started).total_seconds()
        for key, started in origin.items()
        if key in destination and destination[key] >= started
    ]
    return {
        "count": len(seconds),
        "median_seconds": median(seconds) if seconds else None,
        "max_seconds": max(seconds) if seconds else None,
        "pending_count": len(origin) - len(seconds),
    }


def calculate_metrics(events: list[dict], costs: list[dict], *, start: datetime, end: datetime) -> dict:
    """Return a JSON-compatible SIMULATION report, without modifying inputs.

    Receipt reconciliation states are projected to the last observation per
    lead. Conversion rates use ever-confirmed-delivered contacts: an UNKNOWN
    follow-up cannot erase an earlier confirmed initial delivery. Raw receipt
    observations are reported separately. None of these projections substitute
    for a provider-action/attempt accounting contract.
    """
    start_utc = _aware(start, "INVALID_INTERVAL")
    end_utc = _aware(end, "INVALID_INTERVAL")
    if start_utc >= end_utc:
        raise MetricsError("INVALID_INTERVAL")
    if type(events) is not list or type(costs) is not list:
        raise MetricsError("INVALID_INPUT")

    parsed = []
    ingested = {}
    for index, event in enumerate(events):
        if type(event) is not dict or type(event.get("payload")) is not dict:
            raise MetricsError("INVALID_EVENT")
        key = _identity(event)
        kind = _text(event.get("kind"), "INVALID_EVENT")
        occurred = _timestamp(event.get("occurred_at"))
        parsed.append((occurred, index, key, kind, event["payload"]))
        if kind == "LEAD_INGESTED":
            ingested[key] = min(ingested.get(key, occurred), occurred)

    cohort = {key: value for key, value in ingested.items() if start_utc <= value < end_utc}
    observations = [
        item for item in parsed
        if item[2] in cohort and cohort[item[2]] <= item[0] <= end_utc
    ]
    observations.sort(key=lambda item: (item[0], item[1]))
    by_kind: dict[str, set] = {}
    first: dict[str, dict] = {}
    receipts = {"SEND_RECEIPT": {}, "HANDOFF_RECEIPT": {}}
    receipt_observations = {
        kind: {status: 0 for status in sorted(_RECEIPT_STATES)} for kind in receipts
    }
    interest_reviews = {}
    guard_counts = {name: 0 for name in _GUARD_KINDS.values()}
    ever_delivered = set()
    human_replies = set()
    auto_replies = set()
    uncertain_replies = set()
    unclassified_replies = set()
    for occurred, _index, key, kind, payload in observations:
        if kind in _EFFECT_KINDS and payload.get("simulation") is not True:
            raise MetricsError("NON_SIMULATED_EFFECT")
        by_kind.setdefault(kind, set()).add(key)
        first.setdefault(kind, {}).setdefault(key, occurred)
        if kind in receipts:
            status = payload.get("status")
            if type(status) is not str or status not in _RECEIPT_STATES:
                raise MetricsError("INVALID_RECEIPT_STATUS")
            receipts[kind][key] = status
            receipt_observations[kind][status] += 1
            if kind == "SEND_RECEIPT" and status == "DELIVERED":
                ever_delivered.add(key)
        if kind == "REPLY_RECEIVED":
            intent = payload.get("intent")
            if type(intent) is str and intent in _HUMAN_INTENTS:
                human_replies.add(key)
            elif intent == "AUTO_REPLY":
                auto_replies.add(key)
            elif intent == "UNCERTAIN":
                uncertain_replies.add(key)
            else:
                unclassified_replies.add(key)
        if kind == "INTEREST_REVIEW":
            correct = payload.get("correct")
            if type(correct) is not bool:
                raise MetricsError("INVALID_INTEREST_REVIEW")
            interest_reviews[key] = correct
        if kind in _GUARD_KINDS:
            guard_counts[_GUARD_KINDS[kind]] += 1

    def leads(kind: str) -> set:
        return by_kind.get(kind, set())

    send = receipts["SEND_RECEIPT"]
    handoff = receipts["HANDOFF_RECEIPT"]
    latest_delivered = {key for key, status in send.items() if status == "DELIVERED"}
    replied = human_replies
    interested = leads("INTEREST_CONFIRMED")
    prepared = leads("HANDOFF_PREPARED")
    accepted = leads("MANAGER_ACCEPTED")
    reviewed = interested.intersection(interest_reviews)
    reviewed_incorrect = {key for key in reviewed if not interest_reviews[key]}

    allocated = []
    excluded_cost_entries = 0
    for cost in costs:
        if type(cost) is not dict:
            raise MetricsError("INVALID_COST")
        key = _identity(cost)
        occurred = _timestamp(cost.get("occurred_at"))
        if key not in cohort or not cohort[key] <= occurred <= end_utc:
            excluded_cost_entries += 1
            continue
        category = _text(cost.get("category"), "INVALID_COST_CATEGORY")
        if cost.get("currency") != _CURRENCY:
            raise MetricsError("CURRENCY_MISMATCH")
        raw = cost.get("amount")
        if type(raw) is not str or _AMOUNT.fullmatch(raw) is None:
            raise MetricsError("INVALID_COST_AMOUNT")
        try:
            amount = Decimal(raw)
        except InvalidOperation as exc:
            raise MetricsError("INVALID_COST_AMOUNT") from exc
        if not amount.is_finite() or amount < 0:
            raise MetricsError("INVALID_COST_AMOUNT")
        allocated.append((category, amount))

    # With bounded 100+100 digit amounts, this precision covers every possible
    # exact sum of the supplied entries, independent of Decimal's ambient context.
    with localcontext() as context:
        context.prec = 205 + len(str(max(1, len(allocated))))
        total = sum((amount for _category, amount in allocated), Decimal(0))
        by_category: dict[str, Decimal] = {}
        for category, amount in allocated:
            by_category[category] = by_category.get(category, Decimal(0)) + amount

    counts = {
        "leads_ingested": len(cohort),
        "qualified_leads": len(leads("QUALIFIED")),
        "contacts_allowed": len(leads("CONTACT_ALLOWED")),
        "drafts_prepared": len(leads("DRAFT_PREPARED")),
        "send_receipt_leads": len(send),
        "send_accepted": sum(status == "ACCEPTED" for status in send.values()),
        "send_delivered": len(latest_delivered),
        "send_unknown": sum(status == "UNKNOWN" for status in send.values()),
        "ever_delivered_leads": len(ever_delivered),
        "reply_observation_leads": len(leads("REPLY_RECEIVED")),
        "replied_leads": len(replied),
        "auto_reply_leads": len(auto_replies),
        "uncertain_reply_leads": len(uncertain_replies),
        "unclassified_reply_leads": len(unclassified_replies),
        "replied_delivered_leads": len(replied.intersection(ever_delivered)),
        "interested_leads": len(interested),
        "interested_delivered_leads": len(interested.intersection(ever_delivered)),
        "handoff_prepared": len(prepared),
        "handoff_accepted": sum(status == "ACCEPTED" for status in handoff.values()),
        "handoff_delivered": sum(status == "DELIVERED" for status in handoff.values()),
        "handoff_unknown": sum(status == "UNKNOWN" for status in handoff.values()),
        "manager_accepted": len(accepted),
        "manager_declined": len(leads("MANAGER_DECLINED")),
        "handoff_escalated": len(leads("HANDOFF_ESCALATED")),
        "stopped_leads": len(leads("STOP_APPLIED")),
        "reviewed_interests": len(reviewed),
    }
    rates = {
        "qualification": _rate(len(leads("QUALIFIED")), len(cohort)),
        "contact_admission": _rate(
            len(leads("CONTACT_ALLOWED").intersection(leads("QUALIFIED"))),
            len(leads("QUALIFIED")),
        ),
        "delivery": _rate(len(ever_delivered), len(send)),
        "reply": _rate(len(replied.intersection(ever_delivered)), len(ever_delivered)),
        "interest": _rate(len(interested.intersection(ever_delivered)), len(ever_delivered)),
        "manager_acceptance": _rate(len(accepted.intersection(prepared)), len(prepared)),
        "false_interest": _rate(len(reviewed_incorrect), len(reviewed)),
    }
    target_names = list(rates) + [
        "handoff_prepared", "cost_per_qualified_lead", "cost_per_manager_accepted_lead",
        "interest_to_handoff", "handoff_to_manager_acceptance", "interest_to_manager_acceptance",
        "duplicate_outbound", "stop_violations", "history_loss",
    ]
    return {
        "scope": "SIMULATION",
        "real_business_evidence": False,
        "period": {"start": start_utc.isoformat(), "end": end_utc.isoformat()},
        "cohort": {
            "identity": ["campaign_id", "lead_id"],
            "ingestion_interval": "start <= first_ingestion < end",
            "observation_interval": "first_ingestion <= occurred_at <= end",
        },
        "counts": counts,
        "rates": rates,
        "receipt_observations": receipt_observations,
        "costs": {
            "currency": _CURRENCY,
            "total_amount": _number(total),
            "by_category": {category: _number(value) for category, value in sorted(by_category.items())},
            "included_entries": len(allocated),
            "excluded_entries": excluded_cost_entries,
            "coverage": "Only supplied, lead-allocated cohort costs; no estimate of missing expenses.",
            "per_qualified_lead": _cost_rate(total, len(leads("QUALIFIED"))),
            "per_manager_accepted_lead": _cost_rate(total, len(accepted.intersection(prepared))),
        },
        "timing": {
            "interest_to_handoff": _timing(first.get("INTEREST_CONFIRMED", {}), first.get("HANDOFF_PREPARED", {})),
            "handoff_to_manager_acceptance": _timing(first.get("HANDOFF_PREPARED", {}), first.get("MANAGER_ACCEPTED", {})),
            "interest_to_manager_acceptance": _timing(first.get("INTEREST_CONFIRMED", {}), first.get("MANAGER_ACCEPTED", {})),
        },
        "observed_guards": {
            key: {"observed_count": value, "absence_guaranteed": False}
            for key, value in guard_counts.items()
        },
        "targets": {key: {"value": None, "status": "PENDING_APPROVAL"} for key in target_names},
        "limitations": [
            "Synthetic events and DEMO costs do not establish commercial performance.",
            "Receipt projections are per lead, not per provider action or attempt.",
            "No event or cost entry ID is required by this API; raw entries are not implicitly deduplicated.",
            "Seven calendar days alone do not establish an adequate commercial sample.",
        ],
    }
