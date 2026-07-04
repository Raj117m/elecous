"""Concept Drift Engine (spec 6.22, 12.15).

Consumer behaviour changes; mechanisms must be caught drifting. Compares
historical prospective accuracy against a recent trailing window; a large
enough drop demotes the mechanism back to Experimental for re-proof.
"""

from __future__ import annotations

from typing import Any

from elecous.common import timeutil
from elecous.common.ids import new_id
from elecous.common.vocabulary import Lifecycle, ValidationStatus
from elecous.infrastructure.config import Config, active_config
from elecous.infrastructure.logging import get_logger
from elecous.storage.database import DatabaseManager, global_databases
from elecous.validation.schema import VALIDATION_MIGRATION


class ConceptDriftEngine:
    def __init__(
        self,
        mechanisms,
        prospective,
        databases: DatabaseManager | None = None,
        config: Config | None = None,
    ):
        self.mechanisms = mechanisms  # MechanismStore
        self.prospective = prospective  # ProspectiveValidator
        self.databases = databases or global_databases()
        self.databases.register_schema(VALIDATION_MIGRATION)
        self.db = self.databases.get("validation")
        self.config = config or active_config()
        self.drop_threshold = float(self.config.load_file("thresholds.yaml")["validation"]["concept_drift_drop"])
        self.log = get_logger("validation")

    def check(self, mechanism_id: str, recent_n: int = 5) -> dict[str, Any]:
        rows = self.prospective.db.query_dicts(
            "SELECT outcome FROM prospective_predictions WHERE mechanism_id = ? AND outcome IS NOT NULL "
            "ORDER BY resolved_at",
            [mechanism_id],
        )
        if len(rows) < recent_n + 2:
            return {"drift": False, "reason": "insufficient_history"}
        outcomes = [1.0 if r["outcome"] == "success" else 0.0 for r in rows]
        historical = sum(outcomes[:-recent_n]) / len(outcomes[:-recent_n])
        recent = sum(outcomes[-recent_n:]) / recent_n
        drop = historical - recent
        action = "none"
        if drop >= self.drop_threshold:
            mechanism = self.mechanisms.latest(mechanism_id)
            if mechanism and mechanism["lifecycle"] in (Lifecycle.TRUSTED.value, Lifecycle.MONITORING.value):
                self.mechanisms.promote(
                    mechanism_id, Lifecycle.DECLINING, ValidationStatus.FAILED, actor="concept_drift_engine"
                )
                action = "demoted"
            else:
                action = "review"
        self.db.execute(
            "INSERT INTO drift_reports VALUES (?, ?, ?, ?, ?, ?, ?)",
            [new_id("validation"), mechanism_id, historical, recent, drop, action, timeutil.utcnow()],
        )
        result = {
            "drift": drop >= self.drop_threshold,
            "historical_accuracy": round(historical, 4),
            "recent_accuracy": round(recent, 4),
            "drop": round(drop, 4),
            "action": action,
        }
        if result["drift"]:
            self.log.warning("concept drift detected", mechanism_id=mechanism_id, **result)
        return result

    def scan_all(self) -> list[dict[str, Any]]:
        results = []
        for mechanism in self.mechanisms.list_latest():
            report = self.check(mechanism["mechanism_id"])
            if report.get("drift") is not None and "reason" not in report:
                results.append({"mechanism_id": mechanism["mechanism_id"], **report})
        return results
