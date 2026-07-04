"""Scientific Diversity & Validation Readiness report.

Pure analysis over the real, permanent databases — no new engine, no new
architecture. For every phenomenon (category) it measures the concrete
statistical power indicators that determine whether mechanism validation
can be scientifically meaningful for that category, per the evidence-
expansion directive's Phase 4 and Phase 6:

  entity count, independent episode count, geographic diversity, seasonal
  diversity, observation density, and a plain-language validation-readiness
  verdict with the exact reason.

Every number here is computed directly from real rows already collected;
nothing is estimated, interpolated or invented.
"""

from __future__ import annotations

import json
from collections import defaultdict
from typing import Any

from elecous.bootstrap import build_stack

#: Minimum independent entities per category before a mechanism claim can
#: even in principle be distinguished from "this is idiosyncratic to one
#: product" (spec 4.25 sampling_bias check already enforces >=2; we report
#: against a slightly higher, more defensible bar for real readiness).
MIN_ENTITIES_FOR_POWER = 3
MIN_EPISODES_FOR_POWER = 5
MIN_SEASONS_FOR_POWER = 2  # distinct calendar quarters represented


def _season_of(month: int) -> str:
    return ["winter", "winter", "spring", "spring", "spring", "summer",
            "summer", "summer", "autumn", "autumn", "autumn", "winter"][month - 1]


def build_report() -> dict[str, Any]:
    stack = build_stack()
    episodes = stack.demand.episodes.list_latest(include_controls=False)
    observations_total = stack.observation.store.count()
    sources = stack.observation.registry.list()

    by_category: dict[str, list[dict]] = defaultdict(list)
    for e in episodes:
        by_category[e["category"]].append(e)

    phenomena_report = []
    for category, group in sorted(by_category.items()):
        entities = {e["entity"] for e in group}
        seasons = {_season_of(e["spike_start"].month) for e in group}
        countries = {e["country"] for e in group if e.get("country")}
        confidences = [float(e["overall_confidence"]) for e in group]
        years = {e["spike_start"].year for e in group}

        ready = (
            len(entities) >= MIN_ENTITIES_FOR_POWER
            and len(group) >= MIN_EPISODES_FOR_POWER
            and len(seasons) >= MIN_SEASONS_FOR_POWER
        )
        reasons = []
        if len(entities) < MIN_ENTITIES_FOR_POWER:
            reasons.append(f"only {len(entities)} independent entities (need >= {MIN_ENTITIES_FOR_POWER})")
        if len(group) < MIN_EPISODES_FOR_POWER:
            reasons.append(f"only {len(group)} independent episodes (need >= {MIN_EPISODES_FOR_POWER})")
        if len(seasons) < MIN_SEASONS_FOR_POWER:
            reasons.append(f"only {len(seasons)} distinct season(s) represented (need >= {MIN_SEASONS_FOR_POWER})")

        phenomena_report.append(
            {
                "category": category,
                "entities": sorted(entities),
                "entity_count": len(entities),
                "episode_count": len(group),
                "years_covered": sorted(years),
                "seasonal_diversity": sorted(seasons),
                "geographic_diversity": sorted(countries) if countries else ["global/unscoped"],
                "mean_confidence": round(sum(confidences) / len(confidences), 3) if confidences else None,
                "validation_ready": ready,
                "readiness_blockers": reasons,
            }
        )

    total_entity_catalog = stack.config.load_file("entities.yaml").get("topics", [])
    catalog_by_category: dict[str, int] = defaultdict(int)
    for t in total_entity_catalog:
        catalog_by_category[t.get("category", "unknown")] += 1

    mechanisms = stack.mechanisms.store.list_latest()
    factors = stack.environment.factors.list_latest()
    validations = stack.validation.db.query_dicts("SELECT outcome, count(*) AS n FROM validations GROUP BY outcome")

    report = {
        "observation_layer": {
            "total_real_observations": observations_total,
            "active_sources": len([s for s in sources if s["status"] == "active"]),
            "suspended_sources": [s["source_id"] for s in sources if s["status"] == "suspended"],
            "categories_observed": stack.observation.store.counts_by_category(),
        },
        "entity_catalog": {
            "total_tracked_entities": len(total_entity_catalog),
            "entities_per_category": dict(catalog_by_category),
            "discovered_entity_count": sum(1 for t in total_entity_catalog if t.get("discovered")),
        },
        "phenomena": phenomena_report,
        "phenomena_count": len(phenomena_report),
        "phenomena_validation_ready": sum(1 for p in phenomena_report if p["validation_ready"]),
        "phenomena_underpowered": [p["category"] for p in phenomena_report if not p["validation_ready"]],
        "knowledge_layer": {
            "total_factors": len(factors),
            "total_mechanisms": len(mechanisms),
            "mechanism_lifecycle_distribution": stack.mechanisms.store.counts(),
        },
        "validation_layer": {row["outcome"]: row["n"] for row in validations},
    }
    return report


def main() -> None:
    report = build_report()
    print(json.dumps(report, indent=2, default=str))
    print()
    print("=" * 70)
    print("VALIDATION READINESS SUMMARY")
    print("=" * 70)
    print(f"Phenomena observed:            {report['phenomena_count']}")
    print(f"Phenomena statistically ready: {report['phenomena_validation_ready']}")
    print(f"Phenomena underpowered:        {report['phenomena_underpowered']}")
    print(f"Total tracked entities:        {report['entity_catalog']['total_tracked_entities']}")
    print(f"  of which newly discovered:   {report['entity_catalog']['discovered_entity_count']}")
    print(f"Total real observations:       {report['observation_layer']['total_real_observations']}")
    print(f"Total factors / mechanisms:    {report['knowledge_layer']['total_factors']} / {report['knowledge_layer']['total_mechanisms']}")
    print(f"Validation outcomes:           {report['validation_layer']}")


if __name__ == "__main__":
    main()
