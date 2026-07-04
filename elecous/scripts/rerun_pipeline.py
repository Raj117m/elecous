"""Re-run the full real-data pipeline after entity/evidence expansion.

Demand scan -> environmental research cycle -> mechanism generation ->
validation -> knowledge sync -> prediction, against the permanent database,
using whatever real data currently exists. Prints exact real counts and
outcomes at every stage; fabricates nothing.
"""

from __future__ import annotations

import json
from datetime import timedelta

from elecous.bootstrap import build_stack
from elecous.common import timeutil
from elecous.common.vocabulary import Lifecycle


def main() -> None:
    stack = build_stack()

    bounds = stack.demand.store.db.query_one("SELECT min(timestamp), max(timestamp) FROM observations")
    print(f"real observation range: {bounds[0]} -> {bounds[1]}")
    print(f"total real observations: {stack.demand.store.count()}")

    start = timeutil.ensure_utc(bounds[0]) + timedelta(days=45)
    end = timeutil.ensure_utc(bounds[1])
    created_total = []
    as_of = start
    while as_of <= end:
        created_total.extend(stack.demand.scan(as_of=as_of))
        as_of += timedelta(days=7)
    print(f"DEMAND_SCAN_DONE episodes_created={len(created_total)}")

    episodes = stack.demand.episodes.list_latest(include_controls=False)
    by_cat = {}
    for e in episodes:
        by_cat.setdefault(e["category"], set()).add(e["entity"])
    print("entities per category (real episodes only):")
    for cat, entities in sorted(by_cat.items(), key=lambda x: -len(x[1])):
        print(f"  {cat:25s} {len(entities):3d} distinct entities: {sorted(entities)[:5]}{'...' if len(entities) > 5 else ''}")

    summary = stack.environment.research_cycle()
    print(f"RESEARCH_CYCLE_DONE episodes={summary['episodes']} factors={len(summary['factors'])} interactions={summary['interactions']}")

    created_mechanisms = stack.mechanisms.generate_from_factors()
    print(f"MECHANISM_GEN_DONE created={len(created_mechanisms)}")

    factor_outcomes = {"passed": 0, "failed": 0}
    factor_fail_reasons: dict[str, int] = {}
    for factor in stack.environment.factors.list_latest(lifecycle=Lifecycle.CANDIDATE.value):
        report = stack.validation.validate_factor(factor["factor_id"])
        factor_outcomes[report["outcome"]] += 1
        if report["outcome"] == "failed":
            for check, ok in report["checks"].items():
                if not ok:
                    factor_fail_reasons[check] = factor_fail_reasons.get(check, 0) + 1

    mech_outcomes = {"passed": 0, "failed": 0}
    mech_fail_reasons: dict[str, int] = {}
    for mech in stack.mechanisms.store.list_latest(lifecycle=Lifecycle.CANDIDATE.value):
        report = stack.validation.validate_mechanism(mech["mechanism_id"])
        mech_outcomes[report["outcome"]] += 1
        if report["outcome"] == "failed":
            for check, ok in report["checks"].items():
                if not ok:
                    mech_fail_reasons[check] = mech_fail_reasons.get(check, 0) + 1

    print("VALIDATION_DONE")
    print(json.dumps({
        "factor_outcomes": factor_outcomes,
        "factor_fail_reasons": factor_fail_reasons,
        "mechanism_outcomes": mech_outcomes,
        "mechanism_fail_reasons": mech_fail_reasons,
    }, indent=2))

    stack.knowledge.sync()
    opportunity_ids = stack.prediction.predict()
    print(f"PREDICTION_DONE opportunities_created={len(opportunity_ids)}")

    audit = stack.validation.self_audit()
    print("SELF_AUDIT:", json.dumps(audit))


if __name__ == "__main__":
    main()
