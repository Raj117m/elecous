"""Evidence Coverage Audit — measurement and prioritization only.

No new engine, no new architecture, no new API. This is read-only analysis
over the real, permanent databases already built (observations, demand,
environment, mechanisms, signals, validation), extending the discovery
tooling already in expansion/entity_discovery.py and the earlier
scripts/evidence_report.py. Every number is computed directly from real
rows; nothing is estimated or invented.

Phases implemented (per the evidence-bottleneck-elimination directive):
  1. Evidence Coverage Report  — audit_coverage()
  2. Bottleneck ranking        — rank_bottlenecks()
  5. Coverage gap monitor      — coverage_gaps()  (subset of audit_coverage)
  6. Collection prioritization — prioritize_collection()
  7. Validation gating         — validation_gate()
  8. Final report              — main()
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from typing import Any

from elecous.bootstrap import build_stack
from elecous.common import timeutil
from elecous.environment.ontology.ontology import CATEGORY_FAMILIES

# Minimum evidence a category needs before mechanism discovery on it is
# scientifically meaningful. Read from configs/thresholds.yaml
# (environment.gate_min_*) — the exact same values the real
# EnvironmentalEngine._validation_gate() enforces in the pipeline, so this
# report can never drift out of sync with what actually gates discovery.


def _industry_of(category: str) -> str:
    """'Industry' has no dedicated field in the schema; the closest real,
    already-established taxonomic grouping is the Ontology's category
    family (spec 4.12 Environmental Ontology). Mapped explicitly here
    rather than inventing a new concept."""
    return CATEGORY_FAMILIES.get(category, "unclassified")


class EvidenceAuditor:
    def __init__(self):
        self.stack = build_stack()
        gate_thresholds = self.stack.config.load_file("thresholds.yaml")["environment"]
        self.min_entities = int(gate_thresholds["gate_min_entities"])
        self.min_episodes = int(gate_thresholds["gate_min_episodes"])
        self.min_seasons = int(gate_thresholds["gate_min_seasons"])

    # ------------------------------------------------------------ Phase 1/5
    def audit_coverage(self) -> dict[str, Any]:
        stack = self.stack
        obs_db = stack.observation.store.db

        total_observations = stack.observation.store.count()
        by_category = stack.observation.store.counts_by_category()

        bounds = obs_db.query_one("SELECT min(timestamp), max(timestamp) FROM observations")
        years_covered = sorted({r[0] for r in obs_db.query("SELECT DISTINCT extract(year FROM timestamp) FROM observations")})
        countries = [r[0] for r in obs_db.query("SELECT DISTINCT country FROM observations WHERE country IS NOT NULL")]

        topics = stack.config.load_file("entities.yaml").get("topics", [])
        entities_per_category: dict[str, int] = Counter(t.get("category", "unknown") for t in topics)
        discovered_count = sum(1 for t in topics if t.get("discovered"))

        # observations per entity (attention/media categories carry entity-level rows)
        obs_per_entity = obs_db.query_dicts(
            "SELECT entity, category, count(*) AS n FROM observations "
            "WHERE category IN ('attention', 'media') GROUP BY entity, category ORDER BY n DESC"
        )
        entity_obs_counts = {r["entity"]: r["n"] for r in obs_per_entity}
        entities_with_zero_observations = [
            t["entity"] for t in topics if t["entity"] not in entity_obs_counts
        ]

        episodes = stack.demand.episodes.list_latest(include_controls=False)
        factors = stack.environment.factors.list_latest()
        mechanisms = stack.mechanisms.store.list_latest()

        by_cat_episodes: dict[str, list[dict]] = defaultdict(list)
        for e in episodes:
            by_cat_episodes[e["category"]].append(e)

        phenomena_detail = []
        for category, group in sorted(by_cat_episodes.items()):
            entities = {e["entity"] for e in group}
            seasons = {timeutil.season_of(e["spike_start"].month) for e in group}
            years = {e["spike_start"].year for e in group}
            countries_seen = {e["country"] for e in group if e.get("country")}
            phenomena_detail.append(
                {
                    "category": category,
                    "industry": _industry_of(category),
                    "entity_count": len(entities),
                    "episode_count": len(group),
                    "years_covered": sorted(years),
                    "season_count": len(seasons),
                    "seasons": sorted(seasons),
                    "country_count": len(countries_seen) or None,  # None = global/unscoped signals
                    "tracked_entities_in_catalog": entities_per_category.get(category, 0),
                    "observed_entities": sorted(entities),
                }
            )

        # Statistical power proxy: geometric combination of the three
        # independent sample-size dimensions that matter for distinguishing
        # a real reusable mechanism from an idiosyncratic one-off (entity
        # count, episode count, seasonal spread). Capped at 1.0 at the
        # MIN_* thresholds so it reads as "how close to sufficiently
        # powered," not an unbounded score.
        for p in phenomena_detail:
            entity_power = min(1.0, p["entity_count"] / self.min_entities)
            episode_power = min(1.0, p["episode_count"] / self.min_episodes)
            season_power = min(1.0, p["season_count"] / self.min_seasons)
            p["statistical_power"] = round((entity_power * episode_power * season_power) ** (1 / 3), 3)

        all_categories_in_catalog = set(entities_per_category)
        categories_with_any_episode = set(by_cat_episodes)
        missing_categories = sorted(all_categories_in_catalog - categories_with_any_episode)

        expected_countries = set(stack.config.load_file("entities.yaml").get("countries", []))
        missing_countries = sorted(expected_countries - set(countries))

        return {
            "totals": {
                "total_observations": total_observations,
                "observations_by_source_category": by_category,
                "unique_tracked_entities": len(topics),
                "unique_discovered_entities": discovered_count,
                "unique_phenomena_with_episodes": len(by_cat_episodes),
                "unique_phenomena_in_catalog": len(all_categories_in_catalog),
                "unique_industries": len({_industry_of(c) for c in all_categories_in_catalog}),
                "unique_environmental_factors": len(factors),
                "unique_mechanisms": len(mechanisms),
                "years_covered": [int(y) for y in years_covered],
                "year_span": (int(years_covered[-1]) - int(years_covered[0]) + 1) if years_covered else 0,
                "countries_covered": sorted(countries),
                "real_data_range": [str(bounds[0]), str(bounds[1])] if bounds and bounds[0] else None,
            },
            "entity_level": {
                "entities_with_zero_real_observations": entities_with_zero_observations,
                "entities_with_zero_real_observations_count": len(entities_with_zero_observations),
                "top_10_most_observed_entities": obs_per_entity[:10],
                "bottom_10_observed_entities_with_data": obs_per_entity[-10:] if len(obs_per_entity) > 10 else obs_per_entity,
            },
            "phenomena": phenomena_detail,
            "gaps": {
                "missing_categories_no_episode_ever": missing_categories,
                "missing_countries_never_observed": missing_countries,
                "categories_below_min_entities": [p["category"] for p in phenomena_detail if p["entity_count"] < self.min_entities],
                "categories_below_min_episodes": [p["category"] for p in phenomena_detail if p["episode_count"] < self.min_episodes],
                "categories_below_min_seasons": [p["category"] for p in phenomena_detail if p["season_count"] < self.min_seasons],
            },
        }

    # ------------------------------------------------------------- Phase 2
    def rank_bottlenecks(self, coverage: dict[str, Any]) -> list[dict[str, Any]]:
        """Rank bottlenecks by estimated impact: how many categories they
        block, weighted by how far each category is from the threshold."""
        bottleneck_defs = [
            ("insufficient_entity_diversity", "entity_count", self.min_entities,
             "Only N distinct real entities observed to spike in this category; a mechanism cannot be shown to generalize beyond a single product with fewer than a handful of independent examples."),
            ("insufficient_episode_count", "episode_count", self.min_episodes,
             "Too few independent real spike events recorded; each mechanism claim needs repeated, independent confirmation, not a single occurrence."),
            ("insufficient_seasonal_diversity", "season_count", self.min_seasons,
             "All observed episodes cluster in the same season; cannot separate 'this phenomenon predicts demand' from 'this is just always true in month X'."),
        ]
        impacts = []
        for key, field, threshold, description in bottleneck_defs:
            affected = [p for p in coverage["phenomena"] if p[field] < threshold]
            total_deficit = sum(threshold - p[field] for p in affected)
            impacts.append(
                {
                    "bottleneck": key,
                    "description": description,
                    "categories_affected": len(affected),
                    "categories_affected_names": [p["category"] for p in affected],
                    "total_deficit_units": total_deficit,
                    "avg_deficit_per_affected_category": round(total_deficit / len(affected), 2) if affected else 0,
                }
            )
        # Categories with zero episodes at all are the most severe form of
        # every bottleneck simultaneously — surface them separately.
        zero_episode_categories = coverage["gaps"]["missing_categories_no_episode_ever"]
        impacts.append(
            {
                "bottleneck": "zero_episodes_whatsoever",
                "description": "Category has never produced a single real detected demand episode; every other metric is undefined for it.",
                "categories_affected": len(zero_episode_categories),
                "categories_affected_names": zero_episode_categories,
                "total_deficit_units": None,
                "avg_deficit_per_affected_category": None,
            }
        )
        impacts.sort(key=lambda b: -(b["categories_affected"]))
        return impacts

    # ------------------------------------------------------------- Phase 6
    def prioritize_collection(self, coverage: dict[str, Any]) -> list[dict[str, Any]]:
        """For each underpowered category, estimate the marginal value of
        collecting more evidence: how much statistical power would rise per
        additional real entity/episode, and how far it currently is from
        readiness. Ranking is by (readiness gap) x (tracked-but-unobserved
        headroom) — categories close to the threshold with real untapped
        catalog entities are the cheapest, highest-value next targets."""
        priorities = []
        for p in coverage["phenomena"]:
            entity_gap = max(0, self.min_entities - p["entity_count"])
            episode_gap = max(0, self.min_episodes - p["episode_count"])
            season_gap = max(0, self.min_seasons - p["season_count"])
            untapped_catalog = max(0, p["tracked_entities_in_catalog"] - p["entity_count"])
            if entity_gap == 0 and episode_gap == 0 and season_gap == 0:
                continue  # already validation-ready; nothing to prioritize
            # Cheapest win: entities already in the catalog that simply
            # haven't spiked/been observed yet need no new discovery work,
            # only more real elapsed time or backfill depth.
            effort_estimate = "low" if untapped_catalog >= entity_gap else "medium (needs entity discovery)"
            priorities.append(
                {
                    "category": p["category"],
                    "current_statistical_power": p["statistical_power"],
                    "entity_gap": entity_gap,
                    "episode_gap": episode_gap,
                    "season_gap": season_gap,
                    "untapped_catalog_entities": untapped_catalog,
                    "effort_estimate": effort_estimate,
                    "priority_score": round((entity_gap + episode_gap + season_gap) * (1.0 - p["statistical_power"]), 2),
                }
            )
        priorities.sort(key=lambda x: -x["priority_score"])
        return priorities

    # ------------------------------------------------------------- Phase 7
    def validation_gate(self, coverage: dict[str, Any], category: str) -> dict[str, Any]:
        """Explicit gate: should mechanism discovery even run for this
        category? Mirrors, and runs before, the Skeptic Engine's per-factor
        checks — this gates at the category level, before any candidate
        factor is even mined, avoiding wasted computation on evidence that
        cannot possibly pass regardless of pattern quality (see the real
        combinatorial-explosion incident this session, caused by mining
        against too-thin evidence)."""
        match = next((p for p in coverage["phenomena"] if p["category"] == category), None)
        if match is None:
            return {"category": category, "gate": "blocked", "reason": "zero real episodes ever recorded"}
        failures = []
        if match["entity_count"] < self.min_entities:
            failures.append(f"entities {match['entity_count']}/{self.min_entities}")
        if match["episode_count"] < self.min_episodes:
            failures.append(f"episodes {match['episode_count']}/{self.min_episodes}")
        if match["season_count"] < self.min_seasons:
            failures.append(f"seasons {match['season_count']}/{self.min_seasons}")
        gate = "blocked" if failures else "open"
        return {"category": category, "gate": gate, "unmet_requirements": failures, "statistical_power": match["statistical_power"]}

    def validation_gate_all(self, coverage: dict[str, Any]) -> dict[str, list[str]]:
        open_categories, blocked_categories = [], []
        for p in coverage["phenomena"]:
            result = self.validation_gate(coverage, p["category"])
            (open_categories if result["gate"] == "open" else blocked_categories).append(p["category"])
        return {"open": open_categories, "blocked": blocked_categories}


def main() -> None:
    auditor = EvidenceAuditor()
    coverage = auditor.audit_coverage()
    bottlenecks = auditor.rank_bottlenecks(coverage)
    priorities = auditor.prioritize_collection(coverage)
    gate = auditor.validation_gate_all(coverage)

    full_report = {
        "coverage": coverage,
        "bottlenecks_ranked": bottlenecks,
        "collection_priorities": priorities,
        "validation_gate": gate,
    }
    print(json.dumps(full_report, indent=2, default=str))

    print()
    print("=" * 78)
    print("EVIDENCE COVERAGE SUMMARY")
    print("=" * 78)
    t = coverage["totals"]
    print(f"Total real observations:        {t['total_observations']:,}")
    print(f"Years covered:                  {t['years_covered']} (span={t['year_span']})")
    print(f"Countries covered:              {len(t['countries_covered'])}")
    print(f"Tracked entities (catalog):     {t['unique_tracked_entities']} ({t['unique_discovered_entities']} auto-discovered)")
    print(f"Phenomena with >=1 episode:     {t['unique_phenomena_with_episodes']} of {t['unique_phenomena_in_catalog']} catalog categories")
    print(f"Industries represented:         {t['unique_industries']}")
    print(f"Environmental factors:          {t['unique_environmental_factors']}")
    print(f"Mechanisms:                     {t['unique_mechanisms']}")
    print()
    print(f"Validation gate OPEN for:       {gate['open'] or 'NONE'}")
    print(f"Validation gate BLOCKED for:    {len(gate['blocked'])} categories")
    print()
    print("Top 5 bottlenecks by impact:")
    for b in bottlenecks[:5]:
        print(f"  - {b['bottleneck']}: affects {b['categories_affected']} categories -> {b['categories_affected_names']}")
    print()
    print("Top 5 collection priorities:")
    for pr in priorities[:5]:
        print(f"  - {pr['category']}: power={pr['current_statistical_power']} entity_gap={pr['entity_gap']} episode_gap={pr['episode_gap']} effort={pr['effort_estimate']}")


if __name__ == "__main__":
    main()
