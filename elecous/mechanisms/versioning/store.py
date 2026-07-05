"""Mechanism store (spec 5.4, 5.14-5.15, 5.19-5.24).

Mechanisms are living scientific objects: versioned forever, carrying five
confidence values, stability metrics, scientific reputation, hierarchy,
evidence links and validation history. They are never edited in place and
never promoted without passed validation.
"""

from __future__ import annotations

import json
from datetime import timedelta
from typing import Any

from elecous.common import timeutil
from elecous.common.ids import new_id
from elecous.common.vocabulary import Lifecycle, ValidationStatus
from elecous.governance import constitution
from elecous.infrastructure.cache import TTLCache
from elecous.infrastructure.event_bus import EventBus, EventType, global_bus
from elecous.infrastructure.logging import get_logger
from elecous.mechanisms.schema import MECHANISMS_MIGRATION
from elecous.storage.database import DatabaseManager, global_databases

MECHANISM_COLUMNS = 29


class MechanismStore:
    def __init__(self, databases: DatabaseManager | None = None, bus: EventBus | None = None):
        self.databases = databases or global_databases()
        self.databases.register_schema(MECHANISMS_MIGRATION)
        self.db = self.databases.get("mechanisms")
        self.bus = bus or global_bus()
        self.log = get_logger("mechanisms")
        # Real bottleneck found in the Final Implementation Phase profiling
        # sweep: find_by_factors() is called once per (factor, category)
        # pair inside generate_from_factors()'s main loop (up to ~550 real
        # calls in one run) and re-queried list_latest(category=...) fresh
        # every time (13.3ms/call measured, ~7.3s of real redundant work).
        # Same TTLCache already used for this exact reason elsewhere
        # (EnvironmentalSignalExtractor, SkepticEngine, EnvironmentalEngine).
        self._category_cache = TTLCache(ttl_seconds=30.0, max_entries=64)

    # -------------------------------------------------------------- creation
    def create(self, payload: dict[str, Any]) -> str:
        constitution.require_evidence("mechanism", payload.get("supporting_episodes", []))
        mechanism_id = new_id("mechanism")
        now = timeutil.utcnow()
        confidence = payload["confidence"]
        self.db.execute(
            f"INSERT INTO mechanisms VALUES ({', '.join('?' * MECHANISM_COLUMNS)})",
            [
                mechanism_id,
                1,
                payload["name"],
                json.dumps(payload["environmental_factors"]),
                json.dumps(payload["supporting_signals"]),
                json.dumps(payload["behavioural_chain"]),
                json.dumps(payload["product_traits"]),
                json.dumps(payload["affected_categories"]),
                json.dumps(payload.get("countries", [])),
                json.dumps(payload.get("climate_zones", [])),
                json.dumps(payload.get("world_conditions", [])),
                json.dumps(payload["lead_time"]),
                json.dumps(confidence),
                confidence["current"],
                payload.get("historical_accuracy"),
                json.dumps(payload.get("business_value", {})),
                json.dumps({"age_days": 0, "consistency": None, "volatility": None, "revisions": 0}),
                0.0,
                payload.get("parent_mechanism"),
                json.dumps(payload["supporting_episodes"]),
                json.dumps(payload.get("control_episodes", [])),
                json.dumps(payload.get("contradicting_episodes", [])),
                json.dumps([]),
                json.dumps(payload.get("evidence_links", {})),
                Lifecycle.CANDIDATE.value,
                ValidationStatus.PENDING.value,
                now + timedelta(days=7),
                None,
                now,
            ],
        )
        self.bus.emit(
            EventType.MECHANISM_DISCOVERED,
            source="mechanisms",
            mechanism_id=mechanism_id,
            name=payload["name"],
            categories=payload["affected_categories"],
        )
        # A real write must invalidate find_by_factors()'s per-category
        # cache immediately -- unlike the read-only caches elsewhere, a
        # stale hit here could let generate_from_factors() create a
        # duplicate mechanism for the same (factor, category) if called
        # again inside the cache's TTL window.
        for category in payload["affected_categories"]:
            self._category_cache.invalidate(category)
        self.log.info("mechanism created", mechanism_id=mechanism_id, name=payload["name"])
        return mechanism_id

    # -------------------------------------------------------------- versions
    def latest(self, mechanism_id: str) -> dict[str, Any] | None:
        rows = self.db.query_dicts(
            "SELECT * FROM mechanisms WHERE mechanism_id = ? ORDER BY version DESC LIMIT 1", [mechanism_id]
        )
        return rows[0] if rows else None

    def version(self, mechanism_id: str, version: int) -> dict[str, Any] | None:
        rows = self.db.query_dicts(
            "SELECT * FROM mechanisms WHERE mechanism_id = ? AND version = ?", [mechanism_id, version]
        )
        return rows[0] if rows else None

    def new_version(self, mechanism_id: str, updates: dict[str, Any], actor: str, reason: str = "") -> int:
        current = self.latest(mechanism_id)
        if current is None:
            raise KeyError(mechanism_id)
        version = int(current["version"]) + 1
        constitution.require_append_only(int(current["version"]), version)
        from datetime import datetime

        row = dict(current)
        for key, value in updates.items():
            row[key] = (
                value
                if isinstance(value, (str, int, float, bool, datetime, type(None)))
                else json.dumps(value, default=str)
            )
        stability = json.loads(row["stability"]) if isinstance(row["stability"], str) else row["stability"]
        stability["revisions"] = stability.get("revisions", 0) + 1
        stability["age_days"] = (timeutil.utcnow() - timeutil.ensure_utc(current["created_at"])).days
        row["stability"] = json.dumps(stability)
        self.db.execute(
            f"INSERT INTO mechanisms VALUES ({', '.join('?' * MECHANISM_COLUMNS)})",
            [
                row["mechanism_id"],
                version,
                row["name"],
                row["environmental_factors"],
                row["supporting_signals"],
                row["behavioural_chain"],
                row["product_traits"],
                row["affected_categories"],
                row["countries"],
                row["climate_zones"],
                row["world_conditions"],
                row["lead_time"],
                row["confidence"],
                row["current_confidence"],
                row["historical_accuracy"],
                row["business_value"],
                row["stability"],
                row["reputation"],
                row["parent_mechanism"],
                row["supporting_episodes"],
                row["control_episodes"],
                row["contradicting_episodes"],
                row["validation_history"],
                row["evidence_links"],
                row["lifecycle"],
                row["validation_status"],
                row["next_validation_at"],
                row["last_validation"],
                row["created_at"],
            ],
        )
        self.log.info("mechanism version appended", mechanism_id=mechanism_id, version=version, actor=actor, reason=reason)
        return version

    def promote(self, mechanism_id: str, target: Lifecycle, status: ValidationStatus, actor: str, evidence: dict | None = None) -> None:
        current = self.latest(mechanism_id)
        if current is None:
            raise KeyError(mechanism_id)
        constitution.require_validated_promotion(Lifecycle(current["lifecycle"]), target, status)
        history = json.loads(current["validation_history"])
        history.append(
            {
                "at": timeutil.to_iso(timeutil.utcnow()),
                "actor": actor,
                "from": current["lifecycle"],
                "to": target.value,
                "status": status.value,
                "evidence": evidence or {},
            }
        )
        self.new_version(
            mechanism_id,
            {
                "lifecycle": target.value,
                "validation_status": status.value,
                "validation_history": history,
                "last_validation": timeutil.utcnow(),
                "next_validation_at": timeutil.utcnow() + timedelta(days=self._revalidation_days(target)),
            },
            actor=actor,
            reason=f"promotion to {target.value}",
        )
        event = EventType.MECHANISM_PROMOTED if target in (
            Lifecycle.VALIDATED,
            Lifecycle.TRUSTED,
            Lifecycle.EXPERIMENTAL,
        ) else EventType.MECHANISM_DEMOTED
        self.bus.emit(event, source="mechanisms", mechanism_id=mechanism_id, lifecycle=target.value)

    @staticmethod
    def _revalidation_days(lifecycle: Lifecycle) -> int:
        from elecous.infrastructure.config import active_config

        days = active_config().load_file("thresholds.yaml")["validation"]["revalidation_days"]
        return int(days.get(lifecycle.value, 14))

    # ------------------------------------------------------------ confidence
    def update_confidence(self, mechanism_id: str, components: dict[str, float], actor: str) -> None:
        current = self.latest(mechanism_id)
        if current is None:
            raise KeyError(mechanism_id)
        confidence = json.loads(current["confidence"])
        confidence.update({k: round(v, 4) for k, v in components.items()})
        confidence["current"] = round(
            0.3 * confidence.get("recent", 0)
            + 0.25 * confidence.get("historical", 0)
            + 0.2 * confidence.get("scientific", 0)
            + 0.15 * confidence.get("regional", 0)
            + 0.1 * confidence.get("category", confidence.get("recent", 0)),
            4,
        )
        self.new_version(
            mechanism_id,
            {"confidence": confidence, "current_confidence": confidence["current"]},
            actor=actor,
            reason="confidence update",
        )

    def update_reputation(self, mechanism_id: str, reputation: float, actor: str) -> None:
        self.new_version(mechanism_id, {"reputation": round(reputation, 4)}, actor=actor, reason="reputation update")

    # -------------------------------------------------------------- failures
    def record_failure(self, mechanism_id: str, kind: str, questions: dict[str, Any]) -> str:
        failure_id = new_id("task")
        self.db.execute(
            "INSERT INTO mechanism_failures VALUES (?, ?, ?, ?, ?, ?)",
            [failure_id, mechanism_id, timeutil.utcnow(), kind, json.dumps(questions, default=str), None],
        )
        self.log.warning("mechanism failure recorded", mechanism_id=mechanism_id, kind=kind)
        return failure_id

    # ------------------------------------------------------------ competition
    def open_competition(self, mechanism_a: str, mechanism_b: str, subject: str) -> str:
        competition_id = new_id("task")
        self.db.execute(
            "INSERT INTO mechanism_competitions VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [competition_id, mechanism_a, mechanism_b, subject, timeutil.utcnow(), None, None, None],
        )
        return competition_id

    def resolve_competition(self, competition_id: str, winner: str, evidence: dict[str, Any]) -> None:
        self.db.execute(
            "UPDATE mechanism_competitions SET resolved_at = ?, winner = ?, evidence = ? WHERE competition_id = ?",
            [timeutil.utcnow(), winner, json.dumps(evidence, default=str), competition_id],
        )

    # ------------------------------------------------------------ interactions
    def record_interaction(self, mechanism_a: str, mechanism_b: str, shared_episodes: int, strength: float) -> None:
        a, b = sorted((mechanism_a, mechanism_b))
        self.db.execute(
            "INSERT OR REPLACE INTO mechanism_interactions VALUES (?, ?, ?, ?, ?)",
            [a, b, shared_episodes, round(strength, 4), timeutil.utcnow()],
        )

    # ---------------------------------------------------------------- queries
    def list_latest(self, lifecycle: str | None = None, category: str | None = None) -> list[dict[str, Any]]:
        sql = (
            "SELECT * FROM mechanisms m WHERE version = "
            "(SELECT max(version) FROM mechanisms WHERE mechanism_id = m.mechanism_id)"
        )
        params: list[Any] = []
        if lifecycle:
            sql += " AND lifecycle = ?"
            params.append(lifecycle)
        rows = self.db.query_dicts(sql + " ORDER BY current_confidence DESC", params)
        if category:
            rows = [r for r in rows if category in json.loads(r["affected_categories"])]
        return rows

    def find_by_factors(self, factor_ids: list[str], category: str) -> dict[str, Any] | None:
        target = set(factor_ids)
        cached_rows = self._category_cache.get_or_compute(category, lambda: self.list_latest(category=category))
        for row in cached_rows:
            if set(json.loads(row["environmental_factors"])) == target:
                return row
        return None

    def due_for_validation(self) -> list[dict[str, Any]]:
        return [
            r
            for r in self.list_latest()
            if r["next_validation_at"] is not None
            and timeutil.ensure_utc(r["next_validation_at"]) <= timeutil.utcnow()
        ]

    def counts(self) -> dict[str, int]:
        return {
            r[0]: r[1]
            for r in self.db.query(
                "SELECT lifecycle, count(DISTINCT mechanism_id) FROM mechanisms m WHERE version = "
                "(SELECT max(version) FROM mechanisms WHERE mechanism_id = m.mechanism_id) GROUP BY lifecycle"
            )
        }
