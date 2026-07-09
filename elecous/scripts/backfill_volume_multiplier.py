"""P4: backfill measured spike magnitudes for historical episodes.

New episodes compute volume_multiplier at creation (signal integral over
baseline integral between acceleration and peak — episodes.py). Historical
rows predate that code and sit at the migration default 1.0, which the
dashboard correctly treats as 'unknown'. Their frozen evidence bundles
DO store the raw series and baseline stats, so the same quantity is
recomputed here from stored data — with the flat stored baseline median in
place of the live trend-aware baseline (cruder, real, labeled).

Rows whose evidence lacks a usable series/timeline stay at 1.0 = unknown.
Run only while nothing holds the demand DB. Idempotent.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from elecous.common import timeutil  # noqa: E402
from elecous.storage.database import global_databases  # noqa: E402


def recompute(evidence: dict, timeline: dict) -> float | None:
    accel_iso, peak_iso = timeline.get("acceleration"), timeline.get("peak")
    if not accel_iso or not peak_iso:
        return None
    accel, peak = timeutil.from_iso(accel_iso), timeutil.from_iso(peak_iso)
    signals = evidence.get("signals") or []
    strongest = max(signals, key=lambda s: s.get("evidence") or 0.0, default=None)
    if not strongest:
        return None
    series = strongest.get("series") or []
    baseline = strongest.get("baseline") or {}
    median = baseline.get("median")
    if not series or median is None or median <= 0:
        return None
    integral_s = n = 0
    for point in series:
        try:
            d_iso, v = point[0], float(point[1])
        except (TypeError, ValueError, IndexError):
            continue
        d = timeutil.from_iso(d_iso)
        if accel <= d <= peak:
            integral_s += v
            n += 1
    if n == 0:
        return None
    return round(integral_s / (median * n), 3)


def main() -> None:
    db = global_databases().get("demand")
    rows = db.query_dicts(
        "SELECT episode_id, version, evidence, timeline FROM research_episodes WHERE volume_multiplier = 1.0"
    )
    updated = failed = 0
    values = []
    for r in rows:
        try:
            m = recompute(json.loads(r["evidence"]), json.loads(r["timeline"]))
        except Exception:
            m = None
        if m is None or m <= 0 or abs(m - 1.0) < 1e-9:
            failed += 1
            continue
        db.execute(
            "UPDATE research_episodes SET volume_multiplier = ? WHERE episode_id = ? AND version = ?",
            [m, r["episode_id"], r["version"]],
        )
        values.append(m)
        updated += 1
    values.sort()
    print(f"rows at default: {len(rows)}, backfilled with measured values: {updated}, underivable (stay unknown): {failed}")
    if values:
        print(f"measured multipliers — min {values[0]}, median {values[len(values)//2]}, max {values[-1]}")


if __name__ == "__main__":
    main()
