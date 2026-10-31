"""A deterministic, fictional run demonstrating both success and failure paths."""

from datetime import datetime, timedelta, timezone
from pathlib import Path

from .engine import Engine
from .fixtures import make_lead, proposal
from .metrics import calculate_metrics
from .policy import check_policy, load_policy
from .store import Store


def run_demo(db_path):
    root = Path(__file__).resolve().parents[1]
    policy = load_policy(root / "config/demo.synthetic.json")
    start = datetime(2026, 10, 31, 9, tzinfo=timezone.utc)
    store = Store(db_path)
    try:
        engine = Engine(store, policy)
        if store.snapshot(engine.campaign_id)["leads"]:
            raise ValueError("DEMO_REQUIRES_EMPTY_DATABASE")
        outcomes = {}
        for label in ["alpha", "beta", "gamma", "delta", "epsilon"]:
            lead = make_lead(label, start)
            if label == "gamma":
                lead["legal_basis"]["approved"] = False
            engine.ingest("ingest-" + label, lead, start)
            lead_id = lead["lead_id"]
            draft = engine.prepare_initial("initial-" + label, lead_id, start)
            outcomes[label] = {"lead_id": lead_id, "initial": draft}
            if draft["status"] == "BLOCKED":
                continue
            receipt = "UNKNOWN" if label == "delta" else "DELIVERED"
            engine.simulated_send_receipt("receipt-" + label, lead_id, draft["action_id"], receipt, start + timedelta(seconds=10))
            engine.add_cost("cost-" + label, lead_id, "cost-" + label, "ai", "0.25", start + timedelta(seconds=20))

        alpha = outcomes["alpha"]["lead_id"]
        question = "What is included?"
        answer = engine.reply("question-alpha", alpha, "message-alpha-1", question, proposal("QUESTION", question, ["fixture-fact-1"]), start + timedelta(minutes=1))
        engine.simulated_send_receipt("answer-receipt", alpha, answer["action_id"], "DELIVERED", start + timedelta(minutes=2))
        interest = "I want to discuss purchasing this demonstration."
        packet = engine.reply("interest-alpha", alpha, "message-alpha-2", interest, proposal("INTEREST", interest), start + timedelta(minutes=3))
        if packet["status"] != "PREPARED":
            raise ValueError("DEMO_HANDOFF_FAILED:" + str(packet))
        engine.handoff_receipt("packet-receipt", alpha, packet["handoff_id"], packet["content_sha256"], "DELIVERED", start + timedelta(minutes=4))
        outcomes["alpha"]["ack"] = engine.manager_ack("manager-ack", alpha, packet["handoff_id"], packet["content_sha256"], packet["recipient_ref"], "ACCEPT", start + timedelta(minutes=5))

        beta = outcomes["beta"]["lead_id"]
        engine.reply("stop-beta", beta, "message-beta-1", "STOP", None, start + timedelta(minutes=1))
        outcomes["beta"]["late_interest"] = engine.reply("late-beta", beta, "message-beta-2", interest, proposal("INTEREST", interest), start + timedelta(minutes=2))
        outcomes["beta"]["followup"] = engine.follow_up("followup-beta", beta, start + timedelta(days=3))
        outcomes["delta"]["followup"] = engine.follow_up("followup-delta", outcomes["delta"]["lead_id"], start + timedelta(days=3))
        outcomes["epsilon"]["followup"] = engine.follow_up("followup-epsilon", outcomes["epsilon"]["lead_id"], start + timedelta(hours=49))
        # Replaying an identical input must not create a sixth lead or a second action.
        engine.ingest("ingest-alpha", make_lead("alpha", start), start)

        end = start + timedelta(days=7)
        return {
            "schema_version": "FIRST_BUSINESS_DEMO_REPORT_V1",
            "mode": "SIMULATION", "real_business_evidence": False,
            "live_outbound_enabled": False, "clock": "fixed synthetic clock",
            "outcomes": outcomes,
            "metrics": calculate_metrics(store.audit(engine.campaign_id), store.costs(engine.campaign_id), start=start, end=end),
            "snapshot": store.snapshot(engine.campaign_id),
            "pending_profile": check_policy(load_policy(root / "config/campaign.pending.json")),
        }
    finally:
        store.close()
