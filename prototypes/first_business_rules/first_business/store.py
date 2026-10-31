"""SQLite storage for a local simulation, never the production FIRST database."""

import json
import sqlite3
from contextlib import contextmanager

from .common import RuleError, canonical, digest


SCHEMA = """
CREATE TABLE IF NOT EXISTS demo_campaigns (
 campaign_id TEXT PRIMARY KEY, profile_hash TEXT NOT NULL, profile TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS demo_leads (
 campaign_id TEXT NOT NULL, lead_id TEXT NOT NULL, contact_key TEXT NOT NULL,
 payload TEXT NOT NULL, state TEXT NOT NULL, qualified INTEGER NOT NULL,
 contact_allowed INTEGER NOT NULL, version INTEGER NOT NULL DEFAULT 0,
 created_at TEXT NOT NULL, first_outbound_at TEXT, last_outbound_at TEXT,
 followups INTEGER NOT NULL DEFAULT 0,
 PRIMARY KEY(campaign_id,lead_id), UNIQUE(campaign_id,contact_key)
);
CREATE TABLE IF NOT EXISTS demo_events (
 campaign_id TEXT NOT NULL, event_id TEXT NOT NULL, payload_hash TEXT NOT NULL,
 result TEXT NOT NULL, PRIMARY KEY(campaign_id,event_id)
);
CREATE TABLE IF NOT EXISTS demo_audit (
 id INTEGER PRIMARY KEY, campaign_id TEXT NOT NULL, lead_id TEXT NOT NULL,
 kind TEXT NOT NULL, occurred_at TEXT NOT NULL, payload TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS demo_history (
 campaign_id TEXT NOT NULL, lead_id TEXT NOT NULL, message_id TEXT NOT NULL,
 sequence INTEGER NOT NULL, message TEXT NOT NULL,
 PRIMARY KEY(campaign_id,lead_id,message_id), UNIQUE(campaign_id,lead_id,sequence)
);
CREATE TABLE IF NOT EXISTS demo_outbox (
 action_id TEXT PRIMARY KEY, campaign_id TEXT NOT NULL, lead_id TEXT NOT NULL,
 kind TEXT NOT NULL, ordinal INTEGER NOT NULL, status TEXT NOT NULL,
 payload TEXT NOT NULL, created_at TEXT NOT NULL,
 UNIQUE(campaign_id,lead_id,kind,ordinal)
);
CREATE TABLE IF NOT EXISTS demo_handoffs (
 handoff_id TEXT PRIMARY KEY, campaign_id TEXT NOT NULL, lead_id TEXT NOT NULL,
 content_sha256 TEXT NOT NULL, recipient_ref TEXT NOT NULL, intent TEXT NOT NULL,
 status TEXT NOT NULL, created_at TEXT NOT NULL, due_at TEXT NOT NULL,
 UNIQUE(campaign_id,lead_id)
);
CREATE TABLE IF NOT EXISTS demo_suppressions (
 client_id TEXT NOT NULL, contact_key TEXT NOT NULL, reason TEXT NOT NULL,
 created_at TEXT NOT NULL, PRIMARY KEY(client_id,contact_key)
);
CREATE TABLE IF NOT EXISTS demo_control (
 campaign_id TEXT PRIMARY KEY, stopped INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS demo_costs (
 campaign_id TEXT NOT NULL, cost_id TEXT NOT NULL, lead_id TEXT NOT NULL,
 occurred_at TEXT NOT NULL, category TEXT NOT NULL, amount TEXT NOT NULL,
 currency TEXT NOT NULL, PRIMARY KEY(campaign_id,cost_id)
);
"""


class Store:
    def __init__(self, path):
        self.connection = sqlite3.connect(str(path), timeout=10, isolation_level=None)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys=ON")
        self.connection.executescript(SCHEMA)

    def close(self):
        self.connection.close()

    @contextmanager
    def transaction(self):
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            yield
            self.connection.execute("COMMIT")
        except BaseException:
            self.connection.execute("ROLLBACK")
            raise

    def ensure_campaign(self, policy):
        campaign = policy["campaign"]["runtime_campaign_id"]
        fingerprint = digest(policy)
        with self.transaction():
            existing = self.one("SELECT * FROM demo_campaigns WHERE campaign_id=?", (campaign,))
            if existing and existing["profile_hash"] != fingerprint:
                raise RuleError("PROFILE_CHANGED_FOR_EXISTING_CAMPAIGN")
            if not existing:
                self.connection.execute("INSERT INTO demo_campaigns VALUES (?,?,?)", (campaign, fingerprint, canonical(policy)))
                self.connection.execute("INSERT INTO demo_control VALUES (?,0)", (campaign,))

    def one(self, sql, params=()):
        row = self.connection.execute(sql, params).fetchone()
        return dict(row) if row else None

    def rows(self, sql, params=()):
        return [dict(row) for row in self.connection.execute(sql, params).fetchall()]

    def journal(self, campaign_id, lead_id, kind, at, payload=None):
        self.connection.execute("INSERT INTO demo_audit(campaign_id,lead_id,kind,occurred_at,payload) VALUES (?,?,?,?,?)", (campaign_id, lead_id, kind, at, canonical(payload or {})))

    def lead(self, campaign_id, lead_id):
        row = self.one("SELECT * FROM demo_leads WHERE campaign_id=? AND lead_id=?", (campaign_id, lead_id))
        if not row:
            raise RuleError("LEAD_NOT_FOUND")
        row["payload"] = json.loads(row["payload"])
        return row

    def update_lead(self, campaign_id, lead_id, **values):
        allowed = {"state", "qualified", "contact_allowed", "first_outbound_at", "last_outbound_at", "followups"}
        if not values or set(values) - allowed:
            raise RuleError("INVALID_LEAD_UPDATE")
        fields = ",".join(f"{key}=?" for key in values)
        self.connection.execute(f"UPDATE demo_leads SET {fields},version=version+1 WHERE campaign_id=? AND lead_id=?", (*values.values(), campaign_id, lead_id))

    def history(self, campaign_id, lead_id):
        return [json.loads(row["message"]) for row in self.rows("SELECT message FROM demo_history WHERE campaign_id=? AND lead_id=? ORDER BY sequence", (campaign_id, lead_id))]

    def append_message(self, campaign_id, lead_id, message_id, direction, actor, body, fact_ids=None):
        old = self.one("SELECT * FROM demo_history WHERE campaign_id=? AND lead_id=? AND message_id=?", (campaign_id, lead_id, message_id))
        message = {"message_id": message_id, "sequence": 0, "direction": direction, "actor": actor, "body": body, "fact_ids": fact_ids or [], "material_ids": []}
        if old:
            message["sequence"] = old["sequence"]
            if canonical(message) != old["message"]:
                raise RuleError("MESSAGE_ID_CONFLICT")
            return
        sequence = len(self.history(campaign_id, lead_id)) + 1
        message["sequence"] = sequence
        self.connection.execute("INSERT INTO demo_history VALUES (?,?,?,?,?)", (campaign_id, lead_id, message_id, sequence, canonical(message)))

    def audit(self, campaign_id):
        rows = self.rows("SELECT campaign_id,lead_id,kind,occurred_at,payload FROM demo_audit WHERE campaign_id=? ORDER BY id", (campaign_id,))
        for row in rows:
            row["payload"] = json.loads(row["payload"])
        return rows

    def costs(self, campaign_id):
        return self.rows("SELECT campaign_id,lead_id,occurred_at,category,amount,currency FROM demo_costs WHERE campaign_id=? ORDER BY occurred_at,cost_id", (campaign_id,))

    def snapshot(self, campaign_id):
        def decode(rows, fields):
            for row in rows:
                for field in fields:
                    row[field] = json.loads(row[field])
            return rows
        return {
            "mode": "SIMULATION", "live_outbound_enabled": False,
            "leads": decode(self.rows("SELECT * FROM demo_leads WHERE campaign_id=? ORDER BY lead_id", (campaign_id,)), ["payload"]),
            "outbox": decode(self.rows("SELECT * FROM demo_outbox WHERE campaign_id=? ORDER BY created_at,action_id", (campaign_id,)), ["payload"]),
            "handoffs": decode(self.rows("SELECT * FROM demo_handoffs WHERE campaign_id=? ORDER BY created_at", (campaign_id,)), ["intent"]),
            "history": decode(self.rows("SELECT * FROM demo_history WHERE campaign_id=? ORDER BY lead_id,sequence", (campaign_id,)), ["message"]),
            "audit": self.audit(campaign_id),
        }
