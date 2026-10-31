"""Strict, offline-only business profiles and evidence-based qualification.

SYNTHETIC approval is fixture metadata, never authority for real outreach.
No function performs network I/O, grants dispatch permission, or reads a clock.
"""

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID


_TOP = {
    "schema_version", "profile_id", "version", "execution_mode", "approval",
    "campaign", "product", "target", "legal", "follow_up", "handoff", "ai", "pilot",
}
_SECTIONS = {
    "approval": {"status", "approved_by"},
    "campaign": {"client_id", "runtime_campaign_id", "mailbox_ref", "thread_prefix", "locale", "language_pack_version"},
    "product": {"product_id", "name", "price", "terms", "approved_facts"},
    "target": {"countries", "industries", "contact_roles", "min_company_size", "max_company_size"},
    "legal": {"channel", "approval_ref", "valid_until"},
    "follow_up": {"offset_hours", "max_attempts"},
    "handoff": {"recipient_ref", "approval_ref", "ack_timeout_minutes"},
    "ai": {"min_confidence"},
    "pilot": {"currency", "budget"},
}
_LEAD = {
    "lead_id", "company_name", "country", "industry", "company_size", "contact_role",
    "contact_id", "email", "source_url", "source_observed_at", "source_verified",
    "contact_verified", "legal_basis",
}
_BASIS = {"approved", "evidence_ref", "country", "channel", "expires_at"}
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z")
_COUNTRY = re.compile(r"[A-Z]{2}\Z")
_TIMESTAMP = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,6})?(?:Z|[+-][0-9]{2}:[0-9]{2})\Z")


def _text(value, maximum=2000):
    if (type(value) is not str or not 0 < len(value) <= maximum
            or value != value.strip()
            or any(ord(c) < 32 or ord(c) == 127 for c in value)):
        return False
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


def _uuid(value):
    if type(value) is not str:
        return False
    try:
        return str(UUID(value)) == value
    except (ValueError, AttributeError):
        return False


def _aware(value):
    if type(value) is not str or not _TIMESTAMP.fullmatch(value) or value.endswith("-00:00"):
        raise ValueError("INVALID_TIMESTAMP")
    if not value.endswith("Z") and (int(value[-5:-3]) > 23 or int(value[-2:]) > 59):
        raise ValueError("INVALID_TIMESTAMP")
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
    except (ValueError, OverflowError):
        raise ValueError("INVALID_TIMESTAMP") from None


def _duplicate_free_object(pairs):
    obj = {}
    for key, value in pairs:
        if key in obj:
            raise ValueError("DUPLICATE_JSON_KEY")
        obj[key] = value
    return obj


def load_policy(path):
    """Read a bounded UTF-8 profile; incomplete pending profiles may be loaded."""
    try:
        with Path(path).open("rb") as stream:
            raw = stream.read(131073)
        if len(raw) > 131072:
            raise ValueError("POLICY_TOO_LARGE")
        policy = json.loads(raw.decode("utf-8"), object_pairs_hook=_duplicate_free_object,
                            parse_constant=lambda _: (_ for _ in ()).throw(ValueError("NON_FINITE_JSON")))
    except (OSError, UnicodeError, json.JSONDecodeError, RecursionError) as error:
        raise ValueError("INVALID_POLICY_FILE") from error
    report = check_policy(policy)
    if not report["valid"]:
        raise ValueError("INVALID_POLICY: " + ", ".join(report["errors"]))
    return policy


def check_policy(policy):
    """Validate structure; unresolved business inputs remain explicit.

    Date syntax is checked here. Expiration is checked against the explicit
    ``at`` passed to evaluate_lead, avoiding hidden wall-clock dependencies.
    """
    errors, unresolved = [], []
    result = {"valid": False, "errors": errors, "unresolved": unresolved, "live_ready": False}
    if type(policy) is not dict or set(policy) != _TOP:
        errors.append("profile: exact fields required")
        return result
    for section, fields in _SECTIONS.items():
        if type(policy[section]) is not dict or set(policy[section]) != fields:
            errors.append(section + ": exact fields required")
    if errors:
        return result
    if type(policy["schema_version"]) is not str or policy["schema_version"] != "FIRST_BUSINESS_PROFILE_V1":
        errors.append("schema_version: unsupported")
    if type(policy["execution_mode"]) is not str or policy["execution_mode"] != "SIMULATION":
        errors.append("execution_mode: SIMULATION required")
    for key in ("profile_id", "version"):
        if type(policy[key]) is not str or not _ID.fullmatch(policy[key]):
            errors.append(key + ": invalid identifier")
    status = policy["approval"]["status"]
    if type(status) is not str or status not in ("SYNTHETIC", "PENDING_OWNER_INPUT"):
        errors.append("approval.status: unsupported")

    def required(section, key, maximum=2000):
        value = policy[section][key]
        path = section + "." + key
        if value is None or (type(value) is str and value == ""):
            unresolved.append(path)
        elif not _text(value, maximum):
            errors.append(path + ": invalid text")

    required("approval", "approved_by", 128)
    for key in ("client_id", "runtime_campaign_id"):
        if not _uuid(policy["campaign"][key]):
            errors.append("campaign." + key + ": canonical UUID required")
    for key in ("mailbox_ref", "thread_prefix", "locale", "language_pack_version"):
        if type(policy["campaign"][key]) is not str or not _ID.fullmatch(policy["campaign"][key]):
            errors.append("campaign." + key + ": invalid identifier")
    for key in ("product_id", "name", "price", "terms"):
        required("product", key)
    facts = policy["product"]["approved_facts"]
    if type(facts) is not list:
        errors.append("product.approved_facts: list required")
    elif not facts:
        unresolved.append("product.approved_facts")
    elif len(facts) > 100:
        errors.append("product.approved_facts: at most 100 facts")
    else:
        seen = set()
        for fact in facts:
            if type(fact) is not dict or set(fact) != {"fact_id", "text", "source_ref"}:
                errors.append("product.approved_facts: invalid fact fields")
                continue
            if type(fact["fact_id"]) is not str or not _ID.fullmatch(fact["fact_id"]) or fact["fact_id"] in seen:
                errors.append("product.approved_facts: invalid or duplicate fact_id")
            elif not _text(fact["text"]) or not _text(fact["source_ref"], 512):
                errors.append("product.approved_facts: invalid fact text/source")
            else:
                seen.add(fact["fact_id"])
                if status == "SYNTHETIC" and not fact["source_ref"].startswith("fixture:"):
                    errors.append("product.approved_facts: synthetic fixture source required")
    for key in ("countries", "industries", "contact_roles"):
        values = policy["target"][key]
        if type(values) is not list or len(values) > 100 or any(not _text(v, 128) for v in values):
            errors.append("target." + key + ": bounded string list required")
        elif len(set(values)) != len(values):
            errors.append("target." + key + ": duplicates forbidden")
        elif not values:
            unresolved.append("target." + key)
        elif key == "countries" and any(not _COUNTRY.fullmatch(v) for v in values):
            errors.append("target.countries: uppercase two-letter codes required")
    for key in ("min_company_size", "max_company_size"):
        value = policy["target"][key]
        if value is None:
            unresolved.append("target." + key)
        elif type(value) is not int or not 1 <= value <= 10000000:
            errors.append("target." + key + ": bounded positive integer required")
    low, high = policy["target"]["min_company_size"], policy["target"]["max_company_size"]
    if type(low) is int and type(high) is int and low > high:
        errors.append("target: min_company_size exceeds max_company_size")
    if type(policy["legal"]["channel"]) is not str or policy["legal"]["channel"] != "email":
        errors.append("legal.channel: email required")
    required("legal", "approval_ref", 512)
    value = policy["legal"]["valid_until"]
    if value is None or value == "":
        unresolved.append("legal.valid_until")
    else:
        try:
            _aware(value)
        except ValueError:
            errors.append("legal.valid_until: aware timestamp required")
    offsets, attempts = policy["follow_up"]["offset_hours"], policy["follow_up"]["max_attempts"]
    if type(attempts) is not int or not 0 <= attempts <= 10:
        errors.append("follow_up.max_attempts: integer 0..10 required")
    if (type(offsets) is not list or len(offsets) > 10
            or any(type(v) is not int or not 1 <= v <= 720 for v in offsets)):
        errors.append("follow_up.offset_hours: bounded integer list required")
    elif offsets != sorted(set(offsets)) or len(offsets) != attempts:
        errors.append("follow_up: offsets must be increasing and match max_attempts")
    for key in ("recipient_ref", "approval_ref"):
        required("handoff", key, 512)
    timeout = policy["handoff"]["ack_timeout_minutes"]
    if type(timeout) is not int or not 1 <= timeout <= 10080:
        errors.append("handoff.ack_timeout_minutes: integer 1..10080 required")
    confidence = policy["ai"]["min_confidence"]
    if type(confidence) not in (int, float) or not 0.5 <= confidence <= 1:
        errors.append("ai.min_confidence: finite number 0.5..1 required")
    if type(policy["pilot"]["currency"]) is not str or policy["pilot"]["currency"] != "DEMO":
        errors.append("pilot.currency: DEMO required")
    budget = policy["pilot"]["budget"]
    if budget is None:
        unresolved.append("pilot.budget")
    elif type(budget) not in (int, float) or not 0 <= budget <= 1000000:
        errors.append("pilot.budget: finite number 0..1000000 required")
    if status == "SYNTHETIC":
        for section, key in (("approval", "approved_by"), ("legal", "approval_ref"), ("handoff", "approval_ref")):
            value = policy[section][key]
            if _text(value, 512) and not value.startswith("fixture:"):
                errors.append(section + "." + key + ": synthetic fixture reference required")
    result["valid"] = not errors
    return result


def evaluate_lead(policy, lead, at):
    """Return ICP qualification separately from permission to simulate contact."""
    reasons = []
    result = {"qualified": False, "contact_allowed": False, "reasons": reasons}
    if type(at) is not datetime:
        reasons.append("INVALID_TIME")
        return result
    try:
        if at.utcoffset() is None:
            raise ValueError("naive")
        at = at.astimezone(timezone.utc)
    except (ValueError, TypeError, OverflowError):
        reasons.append("INVALID_TIME")
        return result
    report = check_policy(policy)
    if not report["valid"]:
        reasons.append("INVALID_POLICY")
        return result
    if type(lead) is not dict or set(lead) != _LEAD:
        reasons.append("INVALID_LEAD_FIELDS")
        return result
    for key in ("lead_id", "contact_id"):
        if type(lead[key]) is not str or not _ID.fullmatch(lead[key]):
            reasons.append("INVALID_" + key.upper())
    for key in ("company_name", "country", "industry", "contact_role", "email", "source_url"):
        if not _text(lead[key], 2000):
            reasons.append("INVALID_" + key.upper())
    if type(lead["company_size"]) is not int or not 1 <= lead["company_size"] <= 10000000:
        reasons.append("INVALID_COMPANY_SIZE")
    for key in ("source_verified", "contact_verified"):
        if type(lead[key]) is not bool:
            reasons.append("INVALID_" + key.upper())
    basis = lead["legal_basis"]
    if type(basis) is not dict or set(basis) != _BASIS or type(basis.get("approved")) is not bool:
        reasons.append("INVALID_LEGAL_BASIS")
    try:
        observed = _aware(lead["source_observed_at"])
        if observed > at:
            reasons.append("SOURCE_OBSERVED_IN_FUTURE")
    except ValueError:
        reasons.append("INVALID_SOURCE_OBSERVED_AT")
    if reasons:
        return result
    for key, field, reason in (("countries", "country", "COUNTRY_OUTSIDE_TARGET"),
                               ("industries", "industry", "INDUSTRY_OUTSIDE_TARGET"),
                               ("contact_roles", "contact_role", "ROLE_OUTSIDE_TARGET")):
        if lead[field] not in policy["target"][key]:
            reasons.append(reason)
    low, high = policy["target"]["min_company_size"], policy["target"]["max_company_size"]
    if type(low) is not int or type(high) is not int or not low <= lead["company_size"] <= high:
        reasons.append("COMPANY_SIZE_OUTSIDE_TARGET")
    if not lead["source_verified"]:
        reasons.append("SOURCE_NOT_VERIFIED")
    if policy["approval"]["status"] == "SYNTHETIC" and not lead["source_url"].startswith("fixture:"):
        reasons.append("SYNTHETIC_SOURCE_REQUIRED")
    result["qualified"] = not reasons
    if report["unresolved"]:
        reasons.append("POLICY_INPUTS_UNRESOLVED")
    if policy["approval"]["status"] != "SYNTHETIC":
        reasons.append("POLICY_PENDING_OWNER_INPUT")
    if not lead["contact_verified"]:
        reasons.append("CONTACT_NOT_VERIFIED")
    email = lead["email"]
    local, separator, domain = email.rpartition("@")
    if (not separator or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._+-]{0,63}", local)
            or not re.fullmatch(r"(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+test", domain)
            or ".." in local or local.endswith(".") or len(email) > 254):
        reasons.append("SYNTHETIC_EMAIL_REQUIRED")
    if not basis["approved"]:
        reasons.append("LEGAL_BASIS_NOT_APPROVED")
    if not _text(basis["evidence_ref"], 512) or not basis["evidence_ref"].startswith("fixture:"):
        reasons.append("SYNTHETIC_LEGAL_EVIDENCE_REQUIRED")
    if basis["country"] != lead["country"]:
        reasons.append("LEGAL_COUNTRY_MISMATCH")
    if basis["channel"] != policy["legal"]["channel"]:
        reasons.append("LEGAL_CHANNEL_MISMATCH")
    for value, reason in ((basis["expires_at"], "LEGAL_BASIS_EXPIRED_OR_INVALID"),
                          (policy["legal"]["valid_until"], "POLICY_LEGAL_APPROVAL_EXPIRED_OR_INVALID")):
        try:
            if _aware(value) <= at:
                reasons.append(reason)
        except ValueError:
            reasons.append(reason)
    result["contact_allowed"] = result["qualified"] and not reasons
    return result
