import contextlib
import io
import json
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from first_business.__main__ import main


ROOT = Path(__file__).resolve().parents[1]


class CLITests(unittest.TestCase):
    def test_demo_has_one_handoff_no_live_evidence_and_replay_does_not_add_leads(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / "demo.db"
            output = Path(directory) / "report.json"
            with contextlib.redirect_stdout(io.StringIO()), patch("socket.socket", side_effect=AssertionError("NETWORK_DISABLED_IN_DEMO_TEST")), patch("socket.getaddrinfo", side_effect=AssertionError("DNS_DISABLED_IN_DEMO_TEST")):
                self.assertEqual(main(["demo", "--db", str(db), "--output", str(output)]), 0)
            report = json.loads(output.read_text())
            self.assertFalse(report["real_business_evidence"])
            self.assertFalse(report["live_outbound_enabled"])
            self.assertEqual(len(report["snapshot"]["leads"]), 5)
            self.assertEqual(len(report["snapshot"]["handoffs"]), 1)
            self.assertEqual(report["outcomes"]["alpha"]["ack"]["status"], "MANAGER_ACCEPTED")
            self.assertEqual(report["outcomes"]["beta"]["late_interest"]["status"], "ARCHIVED")
            self.assertEqual(report["outcomes"]["gamma"]["initial"]["status"], "BLOCKED")
            self.assertEqual(report["outcomes"]["delta"]["followup"]["status"], "BLOCKED")
            self.assertEqual(report["outcomes"]["epsilon"]["followup"]["status"], "PREPARED")
            original = db.read_bytes()
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(main(["demo", "--db", str(db)]), 2)
            self.assertEqual(db.read_bytes(), original)

    def test_pending_profile_is_structurally_valid_but_incomplete(self):
        with contextlib.redirect_stdout(io.StringIO()) as capture:
            code = main(["check-policy", str(ROOT / "config/campaign.pending.json"), "--require-complete"])
        self.assertEqual(code, 2)
        report = json.loads(capture.getvalue())
        self.assertTrue(report["valid"])
        self.assertFalse(report["live_ready"])
        self.assertGreater(len(report["unresolved"]), 0)

    def test_live_send_always_fails(self):
        with contextlib.redirect_stderr(io.StringIO()) as capture:
            self.assertEqual(main(["live-send"]), 2)
        self.assertIn("NO_OUTBOUND_HOLD", capture.getvalue())


if __name__ == "__main__":
    unittest.main()
