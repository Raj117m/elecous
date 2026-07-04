"""Benchmark Engine (spec 6.23, 11.22, 12.12).

Every software version must replay history and match or beat its
predecessor before promotion. Benchmarks aggregate historical-replay
metrics, prospective track records and calibration error into one stored,
comparable record per platform version.
"""

from __future__ import annotations

import json
from typing import Any

from elecous.common import timeutil
from elecous.common.ids import new_id
from elecous.infrastructure.config import Config, active_config
from elecous.infrastructure.logging import get_logger
from elecous.storage.database import DatabaseManager, global_databases
from elecous.validation.schema import VALIDATION_MIGRATION


class BenchmarkEngine:
    def __init__(self, databases: DatabaseManager | None = None, config: Config | None = None):
        self.databases = databases or global_databases()
        self.databases.register_schema(VALIDATION_MIGRATION)
        self.db = self.databases.get("validation")
        self.config = config or active_config()
        self.log = get_logger("validation")

    def record(self, suite: str, metrics: dict[str, Any]) -> str:
        benchmark_id = new_id("validation")
        self.db.execute(
            "INSERT INTO benchmarks VALUES (?, ?, ?, ?, ?)",
            [
                benchmark_id,
                str(self.config.get("platform.version", "0")),
                suite,
                json.dumps(metrics, default=str),
                timeutil.utcnow(),
            ],
        )
        self.log.info("benchmark recorded", suite=suite, version=self.config.get("platform.version"))
        return benchmark_id

    def latest(self, suite: str, platform_version: str | None = None) -> dict[str, Any] | None:
        sql = "SELECT * FROM benchmarks WHERE suite = ?"
        params: list[Any] = [suite]
        if platform_version:
            sql += " AND platform_version = ?"
            params.append(platform_version)
        rows = self.db.query_dicts(sql + " ORDER BY created_at DESC LIMIT 1", params)
        if not rows:
            return None
        row = rows[0]
        row["metrics"] = json.loads(row["metrics"])
        return row

    def compare_to_previous(self, suite: str) -> dict[str, Any]:
        """Only better (or equal) versions survive (6.23). Compared metrics:
        precision, recall, calibration error (lower is better)."""
        current_version = str(self.config.get("platform.version", "0"))
        current = self.latest(suite, current_version)
        rows = self.db.query_dicts(
            "SELECT * FROM benchmarks WHERE suite = ? AND platform_version <> ? ORDER BY created_at DESC LIMIT 1",
            [suite, current_version],
        )
        if current is None:
            return {"verdict": "no_current_benchmark"}
        if not rows:
            return {"verdict": "no_previous_version", "current": current["metrics"]}
        previous = json.loads(rows[0]["metrics"])
        cur = current["metrics"]

        def better_or_equal(key: str, higher_is_better: bool = True) -> bool | None:
            a, b = cur.get(key), previous.get(key)
            if a is None or b is None:
                return None
            return a >= b if higher_is_better else a <= b

        checks = {
            "precision": better_or_equal("precision"),
            "recall": better_or_equal("recall"),
            "calibration_error": better_or_equal("calibration_error", higher_is_better=False),
        }
        decided = [v for v in checks.values() if v is not None]
        verdict = "promote" if all(decided) and decided else "investigate"
        return {"verdict": verdict, "checks": checks, "current": cur, "previous": previous}
