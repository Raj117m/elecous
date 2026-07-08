"""R4: empirically validate the Demand Pressure Index against real history.

Question: 7 days before a real demand episode, does DPI rank the entity that
is about to spike above its category peers better than (a) chance and (b) a
trivial recency baseline ("whoever spiked most recently ranks first")?

Leakage guards:
- DPI.compute() itself now refuses future episodes (constitution rule 3).
- The target episode is additionally excluded by as_of being 7 days before
  its spike_start AND before its own acceleration for the vast majority of
  episodes; episodes whose acceleration began more than LEAD_DAYS before
  spike_start are skipped outright so the target can never score itself.

Outputs real numbers to docs/audit/dpi_validation_<date>.json.
Run with nothing else holding the demand DB (single-writer DuckDB).
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from elecous.common import timeutil  # noqa: E402
from elecous.demand.pressure.dpi import DemandPressureIndex  # noqa: E402
from elecous.infrastructure.config import active_config  # noqa: E402
from elecous.storage.database import global_databases  # noqa: E402

LEAD_DAYS = 7          # score the world as it looked one week before the spike
HOLDOUT = 400          # most recent N genuine episodes
MIN_PEERS = 4          # need a real ranking contest, not a 2-horse race


def main() -> None:
    dbs = global_databases()
    demand = dbs.get("demand")
    dpi = DemandPressureIndex(dbs)

    topics = active_config().load_file("entities.yaml").get("topics", [])
    roster_by_category: dict[str, list[str]] = defaultdict(list)
    for t in topics:
        roster_by_category[t.get("category", "unknown")].append(t["entity"])

    episodes = demand.query_dicts(
        """
        SELECT episode_id, entity, category, spike_start, timeline
        FROM research_episodes e
        WHERE version = (SELECT max(version) FROM research_episodes WHERE episode_id = e.episode_id)
          AND is_control = FALSE AND is_negative = FALSE
        ORDER BY spike_start DESC
        LIMIT ?
        """,
        [HOLDOUT],
    )

    # recency baseline needs each entity's prior spike times
    all_spikes = demand.query_dicts(
        """
        SELECT entity, spike_start FROM research_episodes e
        WHERE version = (SELECT max(version) FROM research_episodes WHERE episode_id = e.episode_id)
          AND is_control = FALSE AND is_negative = FALSE
        """
    )
    spikes_by_entity: dict[str, list] = defaultdict(list)
    for r in all_spikes:
        spikes_by_entity[r["entity"]].append(timeutil.ensure_utc(r["spike_start"]))

    def rank_of(target: str, scored: list[tuple[str, float]]) -> float:
        """Percentile rank of target (1.0 = ranked first). Ties share rank."""
        target_score = dict(scored)[target]
        better = sum(1 for _, s in scored if s > target_score)
        ties = sum(1 for _, s in scored if s == target_score) - 1
        n = len(scored)
        return 1.0 - (better + ties / 2.0) / max(1, n - 1)

    results = {"dpi": [], "recency": []}
    used = skipped_small = skipped_leak = 0

    for ep in episodes:
        peers = roster_by_category.get(ep["category"], [])
        if len(peers) < MIN_PEERS or ep["entity"] not in peers:
            skipped_small += 1
            continue
        spike_start = timeutil.ensure_utc(ep["spike_start"])
        as_of = spike_start - timedelta(days=LEAD_DAYS)
        timeline = json.loads(ep["timeline"])
        accel = timeline.get("acceleration")
        if accel and timeutil.ensure_utc(timeutil.from_iso(accel)) <= as_of:
            skipped_leak += 1  # target already accelerating at as_of -> self-scoring
            continue

        dpi_scores = [(p, dpi.compute(p, as_of=as_of)) for p in peers]
        rec_scores = [
            (p, max(((as_of - s).days * -1.0 for s in spikes_by_entity.get(p, []) if s < as_of), default=-9999.0))
            for p in peers
        ]
        results["dpi"].append(rank_of(ep["entity"], dpi_scores))
        results["recency"].append(rank_of(ep["entity"], rec_scores))
        used += 1

    def summarize(name: str, ranks: list[float]) -> dict:
        n = len(ranks)
        return {
            "mean_percentile": round(sum(ranks) / n, 4) if n else None,
            "top3_equivalent_rate": round(sum(1 for r in ranks if r >= 0.75) / n, 4) if n else None,
        }

    report = {
        "as_of": timeutil.to_iso(timeutil.utcnow()),
        "design": f"rank spiking entity among category peers at spike_start - {LEAD_DAYS}d",
        "episodes_scored": used,
        "skipped_small_category": skipped_small,
        "skipped_target_already_accelerating": skipped_leak,
        "random_expectation_mean_percentile": 0.5,
        "dpi": summarize("dpi", results["dpi"]),
        "recency_baseline": summarize("recency", results["recency"]),
    }
    out = Path(__file__).resolve().parents[2] / "docs" / "audit" / "dpi_validation_2026-07-08.json"
    out.write_text(json.dumps(report, indent=1))
    print(json.dumps(report, indent=1))


if __name__ == "__main__":
    main()
