"""Fictional inputs for repeatable local verification, never prospect data."""

from datetime import timedelta
from uuid import NAMESPACE_URL, uuid5

from .common import iso


def make_lead(label, now):
    return {
        "lead_id": str(uuid5(NAMESPACE_URL, "fixture:first-business:" + label)),
        "company_name": "SYNTHETIC company " + label,
        "country": "ZZ", "industry": "synthetic-automation", "company_size": 20,
        "contact_role": "synthetic-owner", "contact_id": "fixture:contact:" + label,
        "email": label + "@example.test", "source_url": "fixture:source:" + label,
        "source_observed_at": iso(now - timedelta(hours=1)),
        "source_verified": True, "contact_verified": True,
        "legal_basis": {"approved": True, "evidence_ref": "fixture:not-real-consent:" + label,
                        "country": "ZZ", "channel": "email", "expires_at": "2027-01-01T00:00:00Z"},
    }


def proposal(intent, body, fact_ids=None):
    return {"intent": intent, "confidence": 0.99, "evidence_quote": body,
            "fact_ids": [] if fact_ids is None else fact_ids}
