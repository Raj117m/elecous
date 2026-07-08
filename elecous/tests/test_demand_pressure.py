"""DPI (Demand Pressure Index) guards.

DPI v1 was measured at chance level as a ranker (scripts/validate_dpi.py,
2026-07-08) and removed from the live prediction path — but the module stays
for an evidence-grounded v2, so its constitution guarantees must hold: an
episode that hadn't begun by as_of must contribute nothing (rule 3, no
future leakage). This is the exact bug the validation run would have been
poisoned by: the original age-only filter counted future episodes into
"current" pressure.
"""

from __future__ import annotations

from datetime import timedelta

from elecous.common import timeutil
from elecous.common.ids import new_id
from elecous.demand.pressure.dpi import DemandPressureIndex
from elecous.demand.schema import DEMAND_MIGRATION, DEMAND_MIGRATION_0002, DEMAND_MIGRATION_0003
from elecous.storage.database import global_databases


def _insert_episode(db, entity, category, accel, peak):
    db.execute(
        "INSERT INTO research_episodes (episode_id, version, entity, category, traits, country, region, "
        "baseline, window_days, spike_start, spike_peak, recovery_end, demand_score, evidence, timeline, "
        "signal_agreement, confidence, overall_confidence, control_episodes, mechanisms_found, "
        "false_spike_checks, outcome, validation_status, lifecycle, is_control, is_negative, created_at) "
        "VALUES (?, 1, ?, ?, '[]', 'US', NULL, '{}', 28, ?, ?, NULL, 2.0, '{}', ?, "
        "'{\"pageviews_daily\": 40.0}', '{}', 0.8, '[]', '[]', '{}', NULL, 'pending', 'active', FALSE, FALSE, ?)",
        [
            new_id("episode"), entity, category, accel, peak,
            f'{{"acceleration": "{timeutil.to_iso(accel)}", "peak": "{timeutil.to_iso(peak)}"}}',
            accel,
        ],
    )


def test_dpi_never_counts_future_episodes(isolated_config):
    dbs = global_databases()
    for m in (DEMAND_MIGRATION, DEMAND_MIGRATION_0002, DEMAND_MIGRATION_0003):
        dbs.register_schema(m)
    db = dbs.get("demand")
    now = timeutil.utcnow()

    # one real past episode, one that only exists in the future
    _insert_episode(db, "Air conditioner", "cooling", now - timedelta(days=10), now - timedelta(days=8))
    _insert_episode(db, "Air conditioner", "cooling", now + timedelta(days=5), now + timedelta(days=7))

    dpi = DemandPressureIndex(dbs)
    with_future_visible = dpi.compute("Air conditioner", as_of=now)
    past_only = dpi.compute("Air conditioner", as_of=now - timedelta(days=7))
    nothing_yet = dpi.compute("Air conditioner", as_of=now - timedelta(days=60))

    assert nothing_yet == 0.0                    # before any episode existed
    assert past_only > 0.0                       # sees only the past episode (mid-spike)
    assert with_future_visible == past_only      # the future episode adds nothing
