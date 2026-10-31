"""Offline adapter to the unchanged, accepted F1-09 and F1-19 modules.

The adapter only prepares a synthetic handoff context.  The caller supplies a
trusted, already checked interest decision and recipient configuration.  Neither
an evidence identifier nor a PREPARED result authorizes dispatch or proves that a
manager has accepted the lead.
"""

from __future__ import annotations

from copy import deepcopy
import importlib.util
from pathlib import Path
import sys
from threading import RLock
from types import ModuleType
from typing import Callable


_RUNTIME_DIRECTORY = "strativon-sales-runtime-r3-2-auto"
_SCOPE_FIELDS = (
    "client_id", "runtime_campaign_id", "lead_id", "mailbox_ref", "thread_id",
)
_SOURCE_IMPORT_LOCK = RLock()
_MISSING = object()


class SourceDependencyError(RuntimeError):
    """A required accepted SOURCE module is unavailable or cannot be loaded."""


class AdapterInputError(ValueError):
    """An adapter input is incomplete; the exception contains only a code."""


def prohibit_live_outbound(*args: object, **kwargs: object) -> None:
    """There is deliberately no live transport in this prototype."""
    raise RuntimeError("NO_OUTBOUND_HOLD")


def _load_source_module(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise SourceDependencyError("ACCEPTED_SOURCE_LOAD_FAILED")
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception as exc:
        raise SourceDependencyError("ACCEPTED_SOURCE_LOAD_FAILED") from exc
    return module


def _resolve_runtime(runtime_root: Path | None) -> Path:
    if runtime_root is not None:
        candidate = Path(runtime_root).resolve()
        if not candidate.is_dir():
            raise SourceDependencyError("ACCEPTED_SOURCE_NOT_FOUND")
        return candidate
    for ancestor in Path(__file__).resolve().parents:
        candidate = ancestor / "SOURCE" / _RUNTIME_DIRECTORY
        if candidate.is_dir():
            return candidate
    raise SourceDependencyError("ACCEPTED_SOURCE_NOT_FOUND")


def _load_builder(runtime_root: Path) -> Callable[..., dict]:
    handoff_path = runtime_root / "sales_first_handoff.py"
    intent_path = runtime_root / "sales_handoff_intent.py"
    if not handoff_path.is_file() or not intent_path.is_file():
        raise SourceDependencyError("ACCEPTED_HANDOFF_DEPENDENCY_MISSING")

    # F1-19 uses the original absolute module name for its accepted dependency.
    # Bind that name only while loading the module, then restore it exactly.  The
    # resulting callable retains the real F1-09 function, without altering global
    # sys.path or reusing an unrelated installed module with the same name.
    with _SOURCE_IMPORT_LOCK:
        handoff = _load_source_module("_first_business_source_f109", handoff_path)
        previous = sys.modules.get("sales_first_handoff", _MISSING)
        sys.modules["sales_first_handoff"] = handoff
        try:
            intent = _load_source_module("_first_business_source_f119", intent_path)
        finally:
            if previous is _MISSING:
                sys.modules.pop("sales_first_handoff", None)
            else:
                sys.modules["sales_first_handoff"] = previous
    builder = getattr(intent, "build_handoff_intent", None)
    if not callable(builder):
        raise SourceDependencyError("ACCEPTED_HANDOFF_API_MISSING")
    return builder


def _required(record: dict, key: str) -> object:
    if key not in record:
        raise AdapterInputError("INCOMPLETE_ADAPTER_INPUT")
    return record[key]


def _identifier(value: object) -> str:
    if (
        type(value) is not str
        or not 1 <= len(value) <= 128
        or value != value.strip()
        or any(ord(char) < 32 or ord(char) == 127 for char in value)
    ):
        raise AdapterInputError("INVALID_ADAPTER_IDENTIFIER")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        raise AdapterInputError("INVALID_ADAPTER_IDENTIFIER") from None
    return value


class HandoffAdapter:
    """Map the prototype's trusted inputs to the original F1-19 public API."""

    def __init__(self, runtime_root: Path | None = None) -> None:
        self.runtime_root = _resolve_runtime(runtime_root)
        self._build_handoff_intent = _load_builder(self.runtime_root)

    def prepare(
        self,
        *,
        lead: dict,
        policy: dict,
        history: list[dict],
        event_id: str,
        evidence_ref: str,
    ) -> dict:
        """Return the original FIRST_HANDOFF_INTENT_V1 dictionary unchanged.

        Call this only after the business-rules layer confirms interest.  The
        adapter is not a classifier and does not authenticate approval_ref or
        evidence_ref.  Native F1-09/F1-19 contract exceptions are propagated.
        """
        if type(lead) is not dict or type(policy) is not dict or type(history) is not list:
            raise AdapterInputError("INVALID_ADAPTER_INPUT")
        campaign = _required(policy, "campaign")
        product = _required(policy, "product")
        handoff = _required(policy, "handoff")
        if any(type(record) is not dict for record in (campaign, product, handoff)):
            raise AdapterInputError("INVALID_ADAPTER_INPUT")

        lead_id = _identifier(_required(lead, "lead_id"))
        prefix = _required(campaign, "thread_prefix")
        if type(prefix) is not str:
            raise AdapterInputError("INVALID_ADAPTER_INPUT")
        scope = {
            "client_id": _required(campaign, "client_id"),
            "runtime_campaign_id": _required(campaign, "runtime_campaign_id"),
            "lead_id": lead_id,
            "mailbox_ref": _required(campaign, "mailbox_ref"),
            "thread_id": prefix + lead_id,
        }
        for name, expected in scope.items():
            if name in lead and lead[name] != expected:
                raise AdapterInputError("LEAD_SCOPE_MISMATCH")

        event_id = _identifier(event_id)
        evidence_ref = _identifier(evidence_ref)
        locale = _required(campaign, "locale")
        pack_version = _required(campaign, "language_pack_version")
        if type(locale) is not str:
            raise AdapterInputError("INVALID_ADAPTER_INPUT")

        messages = []
        actors = {"CLIENT": "BUYER", "SYSTEM": "AUTOMATION"}
        for entry in history:
            if type(entry) is not dict:
                raise AdapterInputError("INVALID_ADAPTER_HISTORY")
            actor = _required(entry, "actor")
            if type(actor) is not str or actor not in actors:
                raise AdapterInputError("INVALID_ADAPTER_ACTOR")
            # Preserve any supplied scope for the accepted validator to reject a
            # foreign message; never silently relabel it as belonging to this lead.
            message = {name: entry.get(name, value) for name, value in scope.items()}
            message.update({
                "message_id": _required(entry, "message_id"),
                "sequence": _required(entry, "sequence"),
                "direction": _required(entry, "direction"),
                "actor": actors[actor],
                "body": _required(entry, "body"),
                "fact_ids": deepcopy(_required(entry, "fact_ids")),
                "material_ids": deepcopy(_required(entry, "material_ids")),
            })
            messages.append(message)

        context = {
            "schema_version": "FIRST_HANDOFF_INPUT_V1",
            **scope,
            "handoff_event_id": event_id,
            "product_id": _required(product, "product_id"),
            "policy_version": _required(policy, "version"),
            "status": "ACTIVE",
            "reason": "INTEREST",
            "locale": locale,
            "language_pack_version": pack_version,
            "language_packs": {locale: pack_version},
            "summary": "Синтетический пакет передачи. Основание интереса: " + evidence_ref,
            "next_step": "Проверить пакет вручную. Отправка и принятие менеджером не выполнены.",
            "history": messages,
            "facts": deepcopy(_required(product, "approved_facts")),
            "materials": [],
        }
        recipient = {
            "schema_version": "FIRST_HANDOFF_RECIPIENT_V1",
            "client_id": scope["client_id"],
            "runtime_campaign_id": scope["runtime_campaign_id"],
            "recipient_ref": _required(handoff, "recipient_ref"),
            "approval_ref": _required(handoff, "approval_ref"),
        }
        interest = {
            "schema_version": "FIRST_INTEREST_EVIDENCE_V1",
            "client_id": scope["client_id"],
            "runtime_campaign_id": scope["runtime_campaign_id"],
            "lead_id": scope["lead_id"],
            "decision": "INTEREST",
            "evidence_ref": evidence_ref,
        }
        return self._build_handoff_intent(context, recipient, interest)
