"""Small, deterministic contracts used only by the offline prototype."""

import hashlib
import json
from datetime import datetime, timezone


class RuleError(ValueError):
    pass


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def timestamp(value):
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise RuleError("INVALID_TIMESTAMP") from exc
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise RuleError("AWARE_TIMESTAMP_REQUIRED")
    return value.astimezone(timezone.utc)


def iso(value):
    return timestamp(value).isoformat().replace("+00:00", "Z")


def identifier(value):
    if not isinstance(value, str) or not 1 <= len(value) <= 128 or value != value.strip():
        raise RuleError("INVALID_IDENTIFIER")
    if any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise RuleError("INVALID_IDENTIFIER")
    return value
