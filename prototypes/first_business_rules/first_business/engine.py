"""Executable offline business flow. No network/model/SMTP clients are present."""

import json
import math
import re
from datetime import timedelta
from decimal import Decimal, InvalidOperation

from .adapters import HandoffAdapter, prohibit_live_outbound
from .common import RuleError, canonical, digest, identifier, iso, timestamp
from .policy import check_policy, evaluate_lead


INTENTS = {"QUESTION", "REQUEST_INFO", "AUTO_REPLY", "OBJECTION", "REFUSAL", "STOP", "INTEREST", "UNCERTAIN"}
STOP_TEXTS = {"stop", "unsubscribe", "please stop contacting me", "не пишите мне", "отписаться"}
REFUSAL_TEXT = re.compile(r"\bnot interested\b|\bno (?:thank you|thanks)\b|не интересует|не заинтересован|не нужно", re.IGNORECASE)
PAUSED_STATES = {"STOPPED", "HANDOFF_PENDING", "MANAGER_ACCEPTED", "MANAGER_DECLINED", "ESCALATED"}


class Engine:
    def __init__(self, store, policy, adapter=None):
        checked = check_policy(policy)
        if not checked["valid"]:
            raise RuleError("INVALID_PROFILE:" + ",".join(checked["errors"]))
        # A caller cannot mutate a trusted profile through a retained dictionary.
        self.policy = json.loads(canonical(policy))
        self.store = store
        self.campaign_id = self.policy["campaign"]["runtime_campaign_id"]
        self.client_id = self.policy["campaign"]["client_id"]
        self.adapter = adapter
        self.store.ensure_campaign(self.policy)

    def _journal(self, lead_id, kind, now, payload=None):
        self.store.journal(self.campaign_id, lead_id, kind, iso(now), payload)

    def _run(self, event_id, lead_id, operation, payload, now, action):
        identifier(event_id)
        identifier(lead_id)
        now = timestamp(now)
        # Processing time is not part of the source event's identity. A retry at
        # a later clock value returns the original result without repeating work.
        fingerprint = digest({"lead_id": lead_id, "operation": operation, "payload": payload})
        with self.store.transaction():
            old = self.store.one("SELECT * FROM demo_events WHERE campaign_id=? AND event_id=?", (self.campaign_id, event_id))
            if old:
                if old["payload_hash"] != fingerprint:
                    raise RuleError("EVENT_ID_CONFLICT")
                return json.loads(old["result"])
            previous = self.store.one("SELECT occurred_at FROM demo_audit WHERE campaign_id=? AND lead_id=? ORDER BY id DESC LIMIT 1", (self.campaign_id, lead_id))
            if previous and now < timestamp(previous["occurred_at"]):
                raise RuleError("PROCESSING_TIME_MOVED_BACKWARDS")
            result = action(now)
            self._journal(lead_id, "COMMAND_PROCESSED", now, {"operation": operation})
            self.store.connection.execute("INSERT INTO demo_events VALUES (?,?,?,?)", (self.campaign_id, event_id, fingerprint, canonical(result)))
            return result

    def _lead(self, lead_id):
        return self.store.lead(self.campaign_id, lead_id)

    def _can_act(self, lead, now):
        if self.store.one("SELECT stopped FROM demo_control WHERE campaign_id=?", (self.campaign_id,))["stopped"]:
            return False, ["EMERGENCY_STOP"]
        if self.store.one("SELECT reason FROM demo_suppressions WHERE client_id=? AND contact_key=?", (self.client_id, lead["contact_key"])):
            return False, ["CONTACT_SUPPRESSED"]
        check = evaluate_lead(self.policy, lead["payload"], now)
        return check["contact_allowed"], check["reasons"]

    def ingest(self, event_id, lead, now):
        if type(lead) is not dict:
            raise RuleError("INVALID_LEAD")
        lead_id = identifier(lead.get("lead_id"))
        if not isinstance(lead.get("email"), str) or not isinstance(lead.get("company_name"), str):
            raise RuleError("INVALID_LEAD")
        contact_key = digest({"email": lead["email"].strip().casefold()})

        def action(at):
            if self.store.one("SELECT lead_id FROM demo_leads WHERE campaign_id=? AND contact_key=?", (self.campaign_id, contact_key)):
                raise RuleError("CONTACT_ALREADY_REGISTERED")
            check = evaluate_lead(self.policy, lead, at)
            self.store.connection.execute("INSERT INTO demo_leads(campaign_id,lead_id,contact_key,payload,state,qualified,contact_allowed,created_at) VALUES (?,?,?,?,?,?,?,?)", (self.campaign_id, lead_id, contact_key, canonical(lead), "QUALIFIED" if check["contact_allowed"] else "REVIEW", int(check["qualified"]), int(check["contact_allowed"]), iso(at)))
            self._journal(lead_id, "LEAD_INGESTED", at)
            if check["qualified"]:
                self._journal(lead_id, "QUALIFIED", at)
            if check["contact_allowed"]:
                self._journal(lead_id, "CONTACT_ALLOWED", at, {"simulation": True})
            return {"lead_id": lead_id, **check, "simulation": True}
        return self._run(event_id, lead_id, "INGEST", lead, now, action)

    def _draft(self, lead_id, kind, ordinal, at, fact_ids=None):
        lead = self._lead(lead_id)
        allowed, reasons = self._can_act(lead, at)
        if not allowed:
            return {"status": "BLOCKED", "reasons": reasons}
        facts = self.policy["product"]["approved_facts"]
        known = {fact["fact_id"]: fact["text"] for fact in facts}
        chosen = list(known) if fact_ids is None else fact_ids
        if any(fact not in known for fact in chosen):
            raise RuleError("UNAPPROVED_FACT")
        # The proposal can select an approved fact, but cannot supply new claims,
        # a body, recipient, price, URL, template, or policy override.
        introductions = {
            "INITIAL": "SYNTHETIC OFFLINE EXAMPLE. Would you like information about this demonstration?",
            "FOLLOWUP": "SYNTHETIC OFFLINE FOLLOW-UP. Would you like to continue? You can reply STOP.",
            "QUESTION": "SYNTHETIC OFFLINE ANSWER. The approved information is:",
            "REQUEST_INFO": "SYNTHETIC OFFLINE INFORMATION. The approved information is:",
            "OBJECTION": "SYNTHETIC OFFLINE DRAFT FOR HUMAN REVIEW. Approved information:",
        }
        body = introductions[kind] + "\n" + "\n".join(known[key] for key in chosen)
        payload = {"body": body, "fact_ids": chosen, "recipient": lead["payload"]["email"], "profile_hash": digest(self.policy), "simulation": True}
        action_id = "draft-" + digest([self.campaign_id, lead_id, kind, ordinal])
        old = self.store.one("SELECT * FROM demo_outbox WHERE action_id=?", (action_id,))
        if old:
            if old["payload"] != canonical(payload):
                raise RuleError("ACTION_ID_CONFLICT")
            return {"status": old["status"], "action_id": action_id, "simulation": True}
        self.store.connection.execute("INSERT INTO demo_outbox VALUES (?,?,?,?,?,?,?,?)", (action_id, self.campaign_id, lead_id, kind, ordinal, "PREPARED", canonical(payload), iso(at)))
        self._journal(lead_id, "DRAFT_PREPARED", at, {"action_id": action_id, "kind": kind, "simulation": True})
        return {"status": "PREPARED", "action_id": action_id, "simulation": True}

    def prepare_initial(self, event_id, lead_id, now):
        def action(at):
            lead = self._lead(lead_id)
            if lead["state"] not in {"QUALIFIED", "AWAIT_DELIVERY", "WAITING_REPLY"}:
                return {"status": "BLOCKED", "reasons": ["LEAD_NOT_READY"]}
            result = self._draft(lead_id, "INITIAL", 0, at)
            if result["status"] == "PREPARED":
                self.store.update_lead(self.campaign_id, lead_id, state="AWAIT_DELIVERY")
            return result
        return self._run(event_id, lead_id, "PREPARE_INITIAL", {}, now, action)

    def simulated_send_receipt(self, event_id, lead_id, action_id, status, now):
        if status not in {"ACCEPTED", "DELIVERED", "UNKNOWN"}:
            raise RuleError("INVALID_RECEIPT_STATUS")
        def action(at):
            row = self.store.one("SELECT * FROM demo_outbox WHERE action_id=? AND campaign_id=? AND lead_id=?", (action_id, self.campaign_id, lead_id))
            if not row:
                raise RuleError("ACTION_SCOPE_MISMATCH")
            if at < timestamp(row["created_at"]):
                raise RuleError("RECEIPT_BEFORE_ACTION")
            if row["status"] == "CANCELLED":
                self._journal(lead_id, "SEND_RECEIPT", at, {"action_id": action_id, "status": status, "simulation": True, "requires_reconciliation": True})
                self._journal(lead_id, "LATE_RECEIPT_REQUIRES_REVIEW", at, {"action_id": action_id, "status": status})
                return {"status": "REVIEW", "simulation": True}
            if row["status"] == "DELIVERED":
                if status != "DELIVERED":
                    raise RuleError("RECEIPT_DOWNGRADE")
                return {"status": "DELIVERED", "simulation": True}
            # Recording an already observed result is not authorization to send.
            # Preserve receipts even after an approval expires or a hold is set.
            self.store.connection.execute("UPDATE demo_outbox SET status=? WHERE action_id=?", (status, action_id))
            self._journal(lead_id, "SEND_RECEIPT", at, {"action_id": action_id, "status": status, "simulation": True})
            lead = self._lead(lead_id)
            allowed, reasons = self._can_act(lead, at)
            if status == "UNKNOWN":
                if lead["state"] not in PAUSED_STATES:
                    self.store.update_lead(self.campaign_id, lead_id, state="RECONCILE")
            elif status == "DELIVERED":
                payload = json.loads(row["payload"])
                self.store.append_message(self.campaign_id, lead_id, action_id, "OUTBOUND", "SYSTEM", payload["body"], payload["fact_ids"])
                state = lead["state"]
                if state in {"QUALIFIED", "AWAIT_DELIVERY", "WAITING_REPLY", "RECONCILE"} and row["kind"] in {"INITIAL", "FOLLOWUP"}:
                    state = "WAITING_REPLY" if allowed else "REVIEW"
                changes = {"state": state, "last_outbound_at": iso(at)}
                if row["kind"] == "INITIAL":
                    changes["first_outbound_at"] = iso(at)
                if row["kind"] == "FOLLOWUP":
                    changes["followups"] = row["ordinal"]
                self.store.update_lead(self.campaign_id, lead_id, **changes)
            return {"status": status, "simulation": True, "policy_allows_new_action": allowed, "policy_reasons": reasons}
        return self._run(event_id, lead_id, "SEND_RECEIPT", {"action_id": action_id, "status": status}, now, action)

    def _suppress(self, lead, reason, at):
        self.store.connection.execute("INSERT OR IGNORE INTO demo_suppressions VALUES (?,?,?,?)", (self.client_id, lead["contact_key"], reason, iso(at)))
        # Suppression applies across this client's campaigns, never another client.
        for row in self.store.rows("SELECT l.campaign_id,l.lead_id,c.profile FROM demo_leads l JOIN demo_campaigns c ON l.campaign_id=c.campaign_id WHERE l.contact_key=?", (lead["contact_key"],)):
            if json.loads(row["profile"])["campaign"]["client_id"] != self.client_id:
                continue
            self.store.update_lead(row["campaign_id"], row["lead_id"], state="STOPPED", contact_allowed=0)
            self.store.connection.execute("UPDATE demo_outbox SET status='CANCELLED' WHERE campaign_id=? AND lead_id=? AND status IN ('PREPARED','ACCEPTED')", (row["campaign_id"], row["lead_id"]))
            self.store.connection.execute("UPDATE demo_handoffs SET status='CANCELLED' WHERE campaign_id=? AND lead_id=? AND status NOT IN ('MANAGER_ACCEPTED','MANAGER_DECLINED')", (row["campaign_id"], row["lead_id"]))
        self._journal(lead["lead_id"], "STOP_APPLIED", at, {"reason": reason})

    def _proposal(self, proposal, body):
        if type(proposal) is not dict or set(proposal) != {"intent", "confidence", "evidence_quote", "fact_ids"}:
            raise RuleError("INVALID_MODEL_PROPOSAL")
        if proposal["intent"] not in INTENTS:
            raise RuleError("INVALID_INTENT")
        confidence = proposal["confidence"]
        if type(confidence) not in (int, float) or not 0 <= confidence <= 1 or not math.isfinite(confidence):
            raise RuleError("INVALID_CONFIDENCE")
        if type(proposal["evidence_quote"]) is not str or not proposal["evidence_quote"] or proposal["evidence_quote"] not in body:
            raise RuleError("EVIDENCE_NOT_IN_MESSAGE")
        known = {f["fact_id"] for f in self.policy["product"]["approved_facts"]}
        facts = proposal["fact_ids"]
        if type(facts) is not list or any(type(f) is not str or f not in known for f in facts) or len(facts) != len(set(facts)):
            raise RuleError("UNAPPROVED_FACT")
        if confidence < self.policy["ai"]["min_confidence"]:
            raise RuleError("LOW_CONFIDENCE")
        return proposal["intent"]

    def reply(self, event_id, lead_id, message_id, body, proposal, now):
        identifier(message_id)
        if type(body) is not str or not body.strip() or len(body) > 4000:
            raise RuleError("INVALID_MESSAGE")
        def action(at):
            lead = self._lead(lead_id)
            previous_message = self.store.one("SELECT message_id FROM demo_history WHERE campaign_id=? AND lead_id=? AND message_id=?", (self.campaign_id, lead_id, message_id))
            self.store.append_message(self.campaign_id, lead_id, message_id, "INBOUND", "CLIENT", body)
            if previous_message:
                return {"status": "DUPLICATE_MESSAGE", "message_saved": True}
            if body.strip().casefold().rstrip(".! ") in STOP_TEXTS:
                intent = "STOP"
            elif REFUSAL_TEXT.search(body):
                # A narrow conservative safety rule, not an NLP classifier.
                intent = "REFUSAL"
            else:
                try:
                    intent = self._proposal(proposal, body)
                except (RuleError, TypeError) as exc:
                    self._journal(lead_id, "MODEL_PROPOSAL_REJECTED", at, {"message_id": message_id, "reason": str(exc)})
                    if lead["state"] not in PAUSED_STATES:
                        self.store.update_lead(self.campaign_id, lead_id, state="REVIEW")
                    return {"status": "REVIEW", "message_saved": True}
            self._journal(lead_id, "REPLY_RECEIVED", at, {"message_id": message_id, "intent": intent})
            if intent in {"STOP", "REFUSAL"}:
                self._suppress(lead, intent, at)
                return {"status": "STOPPED", "intent": intent}
            existing_handoff = self.store.one("SELECT handoff_id FROM demo_handoffs WHERE campaign_id=? AND lead_id=?", (self.campaign_id, lead_id))
            if lead["state"] in PAUSED_STATES or existing_handoff:
                return {"status": "ARCHIVED", "intent": intent}
            allowed, reasons = self._can_act(lead, at)
            if not allowed:
                self.store.update_lead(self.campaign_id, lead_id, state="REVIEW")
                return {"status": "REVIEW", "reasons": reasons}
            if intent == "AUTO_REPLY":
                return {"status": "AUTO_REPLY", "interest_confirmed": False}
            self.store.connection.execute("UPDATE demo_outbox SET status='CANCELLED' WHERE campaign_id=? AND lead_id=? AND kind='FOLLOWUP' AND status='PREPARED'", (self.campaign_id, lead_id))
            self.store.update_lead(self.campaign_id, lead_id, state="RESPONDED")
            if intent == "UNCERTAIN":
                self.store.update_lead(self.campaign_id, lead_id, state="REVIEW")
                return {"status": "REVIEW", "interest_confirmed": False}
            if intent == "INTEREST":
                self._journal(lead_id, "INTEREST_CONFIRMED", at, {"message_id": message_id, "evidence_quote": proposal["evidence_quote"], "classification_source": "SYNTHETIC_FIXTURE", "simulation": True})
                try:
                    adapter = self.adapter or HandoffAdapter()
                    scoped_event_id = "handoff-" + digest([self.campaign_id, lead_id, event_id])
                    intent_object = adapter.prepare(lead=lead["payload"], policy=self.policy, history=self.store.history(self.campaign_id, lead_id), event_id=scoped_event_id, evidence_ref=message_id)
                    if intent_object["status"] != "PREPARED":
                        raise RuleError("HANDOFF_NOT_PREPARED")
                except (ValueError, RuntimeError) as exc:
                    self.store.update_lead(self.campaign_id, lead_id, state="REVIEW")
                    self._journal(lead_id, "HANDOFF_PREPARATION_FAILED", at, {"reason": str(exc)})
                    return {"status": "REVIEW", "message_saved": True, "reason": str(exc)}
                handoff_id = intent_object["handoff_id"]
                due_at = at + timedelta(minutes=self.policy["handoff"]["ack_timeout_minutes"])
                self.store.connection.execute("INSERT INTO demo_handoffs VALUES (?,?,?,?,?,?,?,?,?)", (handoff_id, self.campaign_id, lead_id, intent_object["content_sha256"], intent_object["recipient_ref"], canonical(intent_object), "PREPARED", iso(at), iso(due_at)))
                self.store.update_lead(self.campaign_id, lead_id, state="HANDOFF_PENDING")
                self._journal(lead_id, "HANDOFF_PREPARED", at, {"handoff_id": handoff_id, "content_sha256": intent_object["content_sha256"], "simulation": True})
                return {"status": "PREPARED", "handoff_id": handoff_id, "content_sha256": intent_object["content_sha256"], "recipient_ref": intent_object["recipient_ref"], "delivery_status": "NOT_DISPATCHED"}
            ordinal = len(self.store.history(self.campaign_id, lead_id))
            return {**self._draft(lead_id, intent, ordinal, at, proposal["fact_ids"]), "intent": intent, "requires_human_review": True}
        # The inbound identity comes from the source message, not from a model's
        # mutable reclassification of the same message.
        return self._run(event_id, lead_id, "REPLY", {"message_id": message_id, "body": body}, now, action)

    def follow_up(self, event_id, lead_id, now):
        def action(at):
            lead = self._lead(lead_id)
            allowed, reasons = self._can_act(lead, at)
            if not allowed:
                return {"status": "BLOCKED", "reasons": reasons}
            if lead["state"] != "WAITING_REPLY" or not lead["first_outbound_at"]:
                return {"status": "BLOCKED", "reasons": ["NO_CONFIRMED_DELIVERY_OR_DIALOGUE_PAUSED"]}
            count = lead["followups"]
            policy = self.policy["follow_up"]
            if count >= policy["max_attempts"]:
                return {"status": "EXHAUSTED"}
            if self.store.one("SELECT action_id FROM demo_outbox WHERE campaign_id=? AND lead_id=? AND status IN ('PREPARED','ACCEPTED','UNKNOWN')", (self.campaign_id, lead_id)):
                return {"status": "BLOCKED", "reasons": ["UNRESOLVED_PREVIOUS_ACTION"]}
            due = timestamp(lead["first_outbound_at"]) + timedelta(hours=policy["offset_hours"][count])
            if at < due:
                return {"status": "WAIT", "due_at": iso(due)}
            return self._draft(lead_id, "FOLLOWUP", count + 1, at)
        return self._run(event_id, lead_id, "FOLLOWUP", {}, now, action)

    def handoff_receipt(self, event_id, lead_id, handoff_id, content_sha256, status, now):
        if status not in {"ACCEPTED", "DELIVERED", "UNKNOWN"}:
            raise RuleError("INVALID_RECEIPT_STATUS")
        def action(at):
            row = self._handoff(lead_id, handoff_id, content_sha256)
            if at < timestamp(row["created_at"]):
                raise RuleError("RECEIPT_BEFORE_HANDOFF")
            if row["status"] in {"CANCELLED", "MANAGER_ACCEPTED", "MANAGER_DECLINED", "ESCALATED"}:
                return {"status": "ARCHIVED"}
            if row["status"] == "DELIVERED" and status != "DELIVERED":
                raise RuleError("RECEIPT_DOWNGRADE")
            self.store.connection.execute("UPDATE demo_handoffs SET status=? WHERE handoff_id=?", (status, handoff_id))
            self._journal(lead_id, "HANDOFF_RECEIPT", at, {"handoff_id": handoff_id, "status": status, "simulation": True})
            return {"status": status, "simulation": True, "manager_accepted": False}
        return self._run(event_id, lead_id, "HANDOFF_RECEIPT", {"handoff_id": handoff_id, "hash": content_sha256, "status": status}, now, action)

    def _handoff(self, lead_id, handoff_id, content_sha256):
        row = self.store.one("SELECT * FROM demo_handoffs WHERE campaign_id=? AND lead_id=? AND handoff_id=?", (self.campaign_id, lead_id, handoff_id))
        if not row or row["content_sha256"] != content_sha256:
            raise RuleError("HANDOFF_SCOPE_OR_HASH_MISMATCH")
        return row

    def manager_ack(self, event_id, lead_id, handoff_id, content_sha256, actor_ref, decision, now):
        if decision not in {"ACCEPT", "DECLINE"}:
            raise RuleError("INVALID_MANAGER_DECISION")
        def action(at):
            row = self._handoff(lead_id, handoff_id, content_sha256)
            if actor_ref != row["recipient_ref"]:
                raise RuleError("MANAGER_SCOPE_MISMATCH")
            if row["status"] != "DELIVERED":
                raise RuleError("HANDOFF_NOT_READY_FOR_ACK")
            if at > timestamp(row["due_at"]):
                self._escalate(row, at)
                self._journal(lead_id, "LATE_MANAGER_ACK_REQUIRES_REVIEW", at, {"handoff_id": handoff_id, "decision": decision})
                return {"status": "ESCALATED", "simulation": True}
            state = "MANAGER_ACCEPTED" if decision == "ACCEPT" else "MANAGER_DECLINED"
            self.store.connection.execute("UPDATE demo_handoffs SET status=? WHERE handoff_id=?", (state, handoff_id))
            self.store.update_lead(self.campaign_id, lead_id, state=state)
            self._journal(lead_id, state, at, {"handoff_id": handoff_id, "actor_ref": actor_ref, "identity_source": "LOCAL_UNAUTHENTICATED_FIXTURE", "simulation": True})
            return {"status": state, "simulation": True}
        return self._run(event_id, lead_id, "MANAGER_ACK", {"handoff_id": handoff_id, "hash": content_sha256, "actor_ref": actor_ref, "decision": decision}, now, action)

    def _escalate(self, row, at):
        self.store.connection.execute("UPDATE demo_handoffs SET status='ESCALATED' WHERE handoff_id=?", (row["handoff_id"],))
        self.store.update_lead(self.campaign_id, row["lead_id"], state="ESCALATED")
        self._journal(row["lead_id"], "HANDOFF_ESCALATED", at, {"handoff_id": row["handoff_id"], "simulation": True})

    def expire_handoff(self, event_id, lead_id, now):
        def action(at):
            row = self.store.one("SELECT * FROM demo_handoffs WHERE campaign_id=? AND lead_id=?", (self.campaign_id, lead_id))
            if not row:
                raise RuleError("HANDOFF_NOT_FOUND")
            if row["status"] in {"CANCELLED", "MANAGER_ACCEPTED", "MANAGER_DECLINED", "ESCALATED"}:
                return {"status": row["status"]}
            if at <= timestamp(row["due_at"]):
                return {"status": "WAIT", "due_at": row["due_at"]}
            self._escalate(row, at)
            return {"status": "ESCALATED"}
        return self._run(event_id, lead_id, "EXPIRE_HANDOFF", {}, now, action)

    def add_cost(self, event_id, lead_id, cost_id, category, amount, now):
        identifier(cost_id)
        if category not in {"ai", "search", "validation", "infrastructure", "delivery", "operator"}:
            raise RuleError("INVALID_COST_CATEGORY")
        try:
            value = Decimal(amount) if type(amount) is str else Decimal("NaN")
        except InvalidOperation:
            value = Decimal("NaN")
        if not value.is_finite() or value < 0 or value.as_tuple().exponent < -6:
            raise RuleError("INVALID_COST")
        def action(at):
            self._lead(lead_id)
            currency = self.policy["pilot"]["currency"]
            values = (self.campaign_id, cost_id, lead_id, iso(at), category, str(value), currency)
            old = self.store.one("SELECT * FROM demo_costs WHERE campaign_id=? AND cost_id=?", (self.campaign_id, cost_id))
            if old:
                expected = dict(zip(("campaign_id", "cost_id", "lead_id", "occurred_at", "category", "amount", "currency"), values))
                if old != expected:
                    raise RuleError("COST_ID_CONFLICT")
                return {"status": "RECORDED", "amount": str(value), "currency": currency}
            total = sum((Decimal(row["amount"]) for row in self.store.costs(self.campaign_id)), Decimal(0))
            if total + value > Decimal(str(self.policy["pilot"]["budget"])):
                raise RuleError("SIMULATION_BUDGET_EXCEEDED")
            self.store.connection.execute("INSERT INTO demo_costs VALUES (?,?,?,?,?,?,?)", values)
            return {"status": "RECORDED", "amount": str(value), "currency": currency}
        return self._run(event_id, lead_id, "COST", {"cost_id": cost_id, "category": category, "amount": amount}, now, action)

    def emergency_stop(self, event_id, now):
        def action(at):
            self.store.connection.execute("UPDATE demo_control SET stopped=1 WHERE campaign_id=?", (self.campaign_id,))
            self.store.connection.execute("UPDATE demo_outbox SET status='CANCELLED' WHERE campaign_id=? AND status IN ('PREPARED','ACCEPTED')", (self.campaign_id,))
            self._journal("CAMPAIGN", "EMERGENCY_STOP", at)
            return {"status": "STOPPED"}
        return self._run(event_id, "CAMPAIGN", "EMERGENCY_STOP", {}, now, action)

    @staticmethod
    def send_live(*args, **kwargs):
        prohibit_live_outbound(*args, **kwargs)
