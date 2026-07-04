"""ELECOUS command-line interface.

Usage:
    elecous bootstrap              seed the source registry, do nothing else
    elecous backfill [--days N]    real historical backfill for all active sources
    elecous run                    start the scheduler + dashboard (foreground)
    elecous dashboard              start only the dashboard API + frontend
    elecous status                 print a one-shot system health report
    elecous audit <prediction_id>  print the full scientific audit trail
"""

from __future__ import annotations

import argparse
import json
import sys

from elecous.bootstrap import build_stack
from elecous.infrastructure.logging import get_logger

log = get_logger("infrastructure")


def cmd_bootstrap(args: argparse.Namespace) -> None:
    stack = build_stack()
    print(f"Source registry seeded: {len(stack.observation.registry.list())} sources")


def cmd_backfill(args: argparse.Namespace) -> None:
    stack = build_stack()
    for source in stack.observation.registry.list(status="active"):
        if args.days:
            cfg = json.loads(source["connector_config"])
            cfg["backfill_days"] = args.days
            stack.observation.registry.db.execute(
                "UPDATE source_registry SET connector_config = ? WHERE source_id = ?",
                [json.dumps(cfg), source["source_id"]],
            )
    results = stack.observation.backfill()
    for result in results:
        status = "OK" if result["success"] else f"FAILED: {result['error']}"
        print(f"{result['source_id']:25s} {status}")


def cmd_run(args: argparse.Namespace) -> None:
    from elecous.dashboard.api.app import create_app
    from elecous.scheduler.registration import register_all_schedules

    stack = build_stack()
    register_all_schedules(stack)
    stack.scheduler.start()
    print("ELECOUS scheduler started. Dashboard: http://%s:%s" % (
        stack.config.get("dashboard.host", "127.0.0.1"), stack.config.get("dashboard.port", 8321)
    ))
    import uvicorn

    app = create_app(stack)
    try:
        uvicorn.run(app, host=stack.config.get("dashboard.host", "127.0.0.1"), port=int(stack.config.get("dashboard.port", 8321)))
    finally:
        stack.scheduler.stop()


def cmd_dashboard(args: argparse.Namespace) -> None:
    from elecous.dashboard.api.app import main as dashboard_main

    dashboard_main()


def cmd_status(args: argparse.Namespace) -> None:
    stack = build_stack()
    print(json.dumps(stack.supervisor.status(), indent=2, default=str))


def cmd_audit(args: argparse.Namespace) -> None:
    stack = build_stack()
    rows = stack.prediction.db.query_dicts(
        "SELECT * FROM predictions WHERE prediction_id = ?", [args.prediction_id]
    )
    if not rows:
        print(f"no prediction found: {args.prediction_id}", file=sys.stderr)
        sys.exit(1)
    print(json.dumps(rows[0], indent=2, default=str))


def main() -> None:
    parser = argparse.ArgumentParser(prog="elecous", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("bootstrap").set_defaults(func=cmd_bootstrap)

    backfill_parser = sub.add_parser("backfill")
    backfill_parser.add_argument("--days", type=int, default=None)
    backfill_parser.set_defaults(func=cmd_backfill)

    sub.add_parser("run").set_defaults(func=cmd_run)
    sub.add_parser("dashboard").set_defaults(func=cmd_dashboard)
    sub.add_parser("status").set_defaults(func=cmd_status)

    audit_parser = sub.add_parser("audit")
    audit_parser.add_argument("prediction_id")
    audit_parser.set_defaults(func=cmd_audit)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
