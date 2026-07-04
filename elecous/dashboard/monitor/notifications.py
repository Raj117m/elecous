"""Notification Engine (spec 9.25).

ELECOUS never sends constant alerts. Notifications fire only when scientific
importance crosses configured thresholds: high-confidence opportunity,
mechanism failure, major knowledge revision, critical observation loss.
Everything else remains silent.
"""

from __future__ import annotations

import json
from typing import Any

from elecous.common import timeutil
from elecous.common.ids import new_id
from elecous.dashboard.schema import DASHBOARD_MIGRATION
from elecous.infrastructure.config import Config, active_config
from elecous.storage.database import DatabaseManager, global_databases


class NotificationEngine:
    def __init__(self, databases: DatabaseManager | None = None, config: Config | None = None):
        self.databases = databases or global_databases()
        self.databases.register_schema(DASHBOARD_MIGRATION)
        self.db = self.databases.get("dashboard")
        self.config = config or active_config()
        self.thresholds = self.config.get("dashboard.notifications", {})

    def _emit(self, kind: str, message: str, severity: str, context: dict[str, Any]) -> str:
        notification_id = new_id("event")
        self.db.execute(
            "INSERT INTO notifications VALUES (?, ?, ?, ?, ?, ?, ?)",
            [notification_id, kind, message, severity, json.dumps(context, default=str), timeutil.utcnow(), False],
        )
        return notification_id

    def emit_system_alert(self, kind: str, message: str, severity: str, context: dict[str, Any]) -> str:
        """Public entry point for infrastructure callers (e.g. the
        Autonomous Supervisor) that need to raise an alert outside the
        opportunity/mechanism/knowledge/observation threshold checks below."""
        return self._emit(kind, message, severity, context)

    def check_opportunity(self, opportunity: dict[str, Any]) -> str | None:
        threshold = float(self.thresholds.get("opportunity_confidence", 0.8))
        confidence = json.loads(opportunity["confidence"]).get("overall", 0.0)
        if confidence >= threshold:
            return self._emit(
                "high_confidence_opportunity",
                f"High-confidence opportunity in {json.loads(opportunity['categories'])} ({confidence:.0%})",
                "info",
                {"opportunity_id": opportunity["opportunity_id"], "confidence": confidence},
            )
        return None

    def check_mechanism_failure(self, mechanism_id: str, confidence_drop: float) -> str | None:
        threshold = float(self.thresholds.get("mechanism_failure_drop", 0.25))
        if confidence_drop >= threshold:
            return self._emit(
                "mechanism_failure",
                f"Mechanism {mechanism_id} confidence dropped {confidence_drop:.0%}",
                "warning",
                {"mechanism_id": mechanism_id, "drop": confidence_drop},
            )
        return None

    def check_knowledge_revision(self, object_type: str, object_id: str, major: bool) -> str | None:
        if major and self.thresholds.get("knowledge_revision_major", True):
            return self._emit(
                "knowledge_revision",
                f"Major knowledge revision: {object_type} {object_id}",
                "info",
                {"object_type": object_type, "object_id": object_id},
            )
        return None

    def check_observation_loss(self, capability: str, coverage_drop: float) -> str | None:
        threshold = float(self.thresholds.get("observation_loss_critical", 0.5))
        if coverage_drop >= threshold:
            return self._emit(
                "observation_loss",
                f"Critical observation loss in {capability} ({coverage_drop:.0%} drop)",
                "critical",
                {"capability": capability, "drop": coverage_drop},
            )
        return None

    def recent(self, limit: int = 50, unacknowledged_only: bool = False) -> list[dict[str, Any]]:
        sql = "SELECT * FROM notifications"
        if unacknowledged_only:
            sql += " WHERE NOT acknowledged"
        return self.db.query_dicts(sql + " ORDER BY created_at DESC LIMIT ?", [limit])

    def acknowledge(self, notification_id: str) -> None:
        self.db.execute("UPDATE notifications SET acknowledged = true WHERE notification_id = ?", [notification_id])
