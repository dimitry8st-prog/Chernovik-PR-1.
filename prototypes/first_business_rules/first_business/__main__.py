"""Local CLI. Explicit fixture events; no live provider endpoints or secrets."""

import argparse
import json
import sys
from pathlib import Path

from .adapters import prohibit_live_outbound
from .common import timestamp
from .demo import run_demo
from .metrics import calculate_metrics
from .policy import check_policy, load_policy
from .store import Store


def main(argv=None):
    parser = argparse.ArgumentParser(description="FIRST offline business rules (NO_OUTBOUND)")
    commands = parser.add_subparsers(dest="command", required=True)
    demo = commands.add_parser("demo", help="run a synthetic end-to-end fixture")
    demo.add_argument("--db", required=True, type=Path)
    demo.add_argument("--output", type=Path)
    validate = commands.add_parser("check-policy", help="show missing business inputs")
    validate.add_argument("path", type=Path)
    validate.add_argument("--require-complete", action="store_true")
    report = commands.add_parser("report", help="project a stored simulation journal")
    report.add_argument("--db", required=True, type=Path)
    report.add_argument("--campaign", required=True)
    report.add_argument("--start", required=True)
    report.add_argument("--end", required=True)
    commands.add_parser("live-send", help="always fails with NO_OUTBOUND_HOLD")
    args = parser.parse_args(argv)
    try:
        if args.command == "live-send":
            prohibit_live_outbound()
        if args.command == "demo":
            if args.db.exists():
                raise ValueError("DEMO_REQUIRES_NEW_DATABASE_PATH")
            if args.output and args.output.exists():
                raise ValueError("OUTPUT_ALREADY_EXISTS")
            result = run_demo(args.db)
            rendered = json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False)
            if args.output:
                # Exclusive creation avoids overwriting unrelated user artifacts.
                with args.output.open("x", encoding="utf-8") as stream:
                    stream.write(rendered + "\n")
            print(json.dumps({"mode": result["mode"], "real_business_evidence": False,
                              "outcomes": result["outcomes"], "metrics": result["metrics"],
                              "report_file": str(args.output) if args.output else None}, ensure_ascii=False, indent=2))
            return 0
        if args.command == "check-policy":
            result = check_policy(load_policy(args.path))
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 2 if args.require_complete and result["unresolved"] else 0
        if not args.db.is_file():
            raise ValueError("DATABASE_NOT_FOUND")
        store = Store(args.db)
        try:
            if not store.one("SELECT campaign_id FROM demo_campaigns WHERE campaign_id=?", (args.campaign,)):
                raise ValueError("CAMPAIGN_NOT_FOUND")
            result = calculate_metrics(store.audit(args.campaign), store.costs(args.campaign), start=timestamp(args.start), end=timestamp(args.end))
            print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
        finally:
            store.close()
        return 0
    except (ValueError, RuntimeError) as exc:
        print(json.dumps({"error": str(exc), "live_outbound_enabled": False}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
