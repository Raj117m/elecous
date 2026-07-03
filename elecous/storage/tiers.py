"""Hot / Warm / Cold storage tiers (spec 2.20, 10.15).

Hot: recent observations live inside observations.duckdb — very fast.
Warm: observations older than ``storage.hot_days`` are exported to
      compressed Parquet (still directly queryable by DuckDB).
Cold: Parquet older than ``storage.warm_days`` is recompressed with zstd
      at maximum level and rarely touched.

Knowledge is never tiered away: mechanisms, factors and validation history
remain permanently available in their own databases. Only raw observations —
the bulky layer — migrate. Understanding is kept, noise is compressed
(spec 12.24, Appendix A Principle 3).
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

from elecous.common import timeutil
from elecous.infrastructure.config import Config, active_config
from elecous.infrastructure.logging import get_logger
from elecous.storage.database import Database


class TierManager:
    def __init__(self, config: Config | None = None):
        self.config = config or active_config()
        self.log = get_logger("infrastructure")

    # ------------------------------------------------------------------ paths
    def warm_path(self, day: date) -> Path:
        return self.config.tier_dir("warm") / f"observations-{day.isoformat()}.parquet"

    def cold_path(self, day: date) -> Path:
        return self.config.tier_dir("cold") / f"observations-{day.isoformat()}.parquet"

    # ------------------------------------------------------------- migration
    def migrate_hot_to_warm(self, observations_db: Database) -> int:
        """Export observation days older than hot_days to Parquet and delete
        them from the hot store. Provenance is preserved byte-for-byte."""
        hot_days = int(self.config.get("storage.hot_days", 30))
        cutoff = timeutil.days_ago(hot_days).date()
        days = [
            r[0]
            for r in observations_db.query(
                "SELECT DISTINCT CAST(timestamp AS DATE) AS d FROM observations WHERE CAST(timestamp AS DATE) < ? ORDER BY d",
                [cutoff],
            )
        ]
        moved = 0
        for day in days:
            target = self.warm_path(day)
            observations_db.execute(
                f"COPY (SELECT * FROM observations WHERE CAST(timestamp AS DATE) = ?) TO '{target}' (FORMAT PARQUET, COMPRESSION SNAPPY)",
                [day],
            )
            observations_db.execute("DELETE FROM observations WHERE CAST(timestamp AS DATE) = ?", [day])
            moved += 1
            self.log.info("tier migration hot->warm", day=str(day), file=str(target))
        return moved

    def migrate_warm_to_cold(self, observations_db: Database) -> int:
        warm_days = int(self.config.get("storage.warm_days", 365))
        cutoff = timeutil.days_ago(warm_days).date()
        moved = 0
        for path in sorted(self.config.tier_dir("warm").glob("observations-*.parquet")):
            day = date.fromisoformat(path.stem.replace("observations-", ""))
            if day >= cutoff:
                continue
            target = self.cold_path(day)
            observations_db.execute(
                f"COPY (SELECT * FROM '{path}') TO '{target}' (FORMAT PARQUET, COMPRESSION ZSTD, COMPRESSION_LEVEL 19)"
            )
            path.unlink()
            moved += 1
            self.log.info("tier migration warm->cold", day=str(day), file=str(target))
        return moved

    # ---------------------------------------------------------------- queries
    def tier_glob(self) -> list[str]:
        """Parquet globs for querying warm+cold alongside hot (lazy loading:
        DuckDB only reads the row groups a query touches)."""
        globs = []
        warm = self.config.tier_dir("warm")
        cold = self.config.tier_dir("cold")
        if any(warm.glob("*.parquet")):
            globs.append(str(warm / "*.parquet"))
        if any(cold.glob("*.parquet")):
            globs.append(str(cold / "*.parquet"))
        return globs

    def full_observation_relation(self) -> str:
        """SQL relation spanning all tiers, usable as a FROM clause."""
        parts = ["SELECT * FROM observations"]
        for pattern in self.tier_glob():
            parts.append(f"SELECT * FROM '{pattern}'")
        return "(" + " UNION ALL ".join(parts) + ")"

    def report(self) -> dict[str, Any]:
        warm_files = list(self.config.tier_dir("warm").glob("*.parquet"))
        cold_files = list(self.config.tier_dir("cold").glob("*.parquet"))
        return {
            "warm_files": len(warm_files),
            "warm_bytes": sum(f.stat().st_size for f in warm_files),
            "cold_files": len(cold_files),
            "cold_bytes": sum(f.stat().st_size for f in cold_files),
        }
