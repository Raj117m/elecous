"""Research Episodes (spec 3.12-3.19).

Once Demand Evidence crosses threshold a Research Episode begins. Its
evidence bundle — signal series, baselines, timeline, world context — is
frozen as JSON at creation and never modified; later stages append new
versions (append-only, spec 3.19 "Nothing is overwritten").
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from elecous.common import timeutil
from elecous.common.ids import new_id
from elecous.common.vocabulary import Lifecycle, ValidationStatus
from elecous.demand.schema import DEMAND_MIGRATION
from elecous.demand.spikes.detector import DemandEvidence
from elecous.governance import constitution
from elecous.infrastructure.event_bus import EventBus, EventType, global_bus
from elecous.infrastructure.logging import get_logger
from elecous.storage.database import DatabaseManager, global_databases

EPISODE_COLUMNS = 28

class EpisodeStore:
    def __init__(self, databases: DatabaseManager | None = None, bus: EventBus | None = None):
        self.databases = databases or global_databases()
        self.databases.register_schema(DEMAND_MIGRATION)
        self.db = self.databases.get("demand")
        self.bus = bus or global_bus()
        self.log = get_logger("demand")

    # ------------------------------------------------------------- creation
    def create_episode(
        self,
        evidence: DemandEvidence,
        entity_meta: dict[str, Any],
        timeline: dict[str, Any],
        controls: list[dict[str, Any]],
        confidence: dict[str, float],
        is_control: bool = False,
        is_negative: bool = False,
    ) -> str:
        frozen = {
            "signals": [
                {
                    "signal_type": s.signal_type,
                    "stage": s.stage,
                    "peak_z": s.peak_z,
                    "peak_at": timeutil.to_iso(s.peak_at) if s.peak_at else None,
                    "evidence": s.evidence,
                    "coverage": s.coverage,
                    "series": s.values,
                    "baseline": {
                        "median": s.baseline.median,
                        "mad": s.baseline.mad,
                        "mean": s.baseline.mean,
                        "std": s.baseline.std,
                        "n_points": s.baseline.n_points,
                        "reliable": s.baseline.reliable,
                    }
                    if s.baseline
                    else None,
                }
                for s in evidence.signals
            ],
            "weights": evidence.weights,
            "score": evidence.score,
            "as_of": timeutil.to_iso(evidence.as_of),
        }
        
        # Calculate volume multiplier M
        volume_multiplier = 1.0
        strongest = max(evidence.signals, key=lambda s: s.evidence, default=None)
        if strongest and strongest.baseline and strongest.values and timeline.get("acceleration") and timeline.get("peak"):
            accel_time = timeutil.from_iso(timeline["acceleration"])
            peak_time = timeutil.from_iso(timeline["peak"])
            integral_s = 0.0
            integral_b = 0.0
            for d_iso, v in strongest.values:
                d = timeutil.from_iso(d_iso)
                if accel_time <= d <= peak_time:
                    integral_s += v
                    integral_b += strongest.baseline.predict(d)
            if integral_b > 0:
                volume_multiplier = integral_s / integral_b
                
        constitution.require_evidence("research_episode", [s.signal_type for s in evidence.signals])
        episode_id = new_id("research_episode")
        overall = confidence.get("overall", 0.0)
        spike_start = timeutil.from_iso(timeline["acceleration"]) if timeline.get("acceleration") else evidence.as_of
        spike_peak = timeutil.from_iso(timeline["peak"]) if timeline.get("peak") else None
        recovery = timeutil.from_iso(timeline["decline"]) if timeline.get("decline") else None
        self.db.execute(
            f"INSERT INTO research_episodes VALUES ({', '.join('?' * EPISODE_COLUMNS)})",
            [
                episode_id,
                1,
                evidence.entity,
                entity_meta.get("category", "unknown"),
                json.dumps(entity_meta.get("traits", [])),
                entity_meta.get("country"),
                entity_meta.get("region"),
                json.dumps(
                    {
                        s.signal_type: frozen["signals"][i]["baseline"]
                        for i, s in enumerate(evidence.signals)
                    }
                ),
                evidence.window_days,
                spike_start,
                spike_peak,
                recovery,
                evidence.score,
                json.dumps(frozen),
                json.dumps(timeline),
                json.dumps({s.signal_type: s.evidence for s in evidence.signals}),
                json.dumps(confidence),
                overall,
                json.dumps(controls),
                json.dumps([]),
                json.dumps({"passed": evidence.false_spike is None, "detail": evidence.false_spike}),
                None,
                ValidationStatus.PENDING.value,
                Lifecycle.CANDIDATE.value,
                is_control,
                is_negative,
                timeutil.utcnow(),
                float(volume_multiplier),
            ],
        )
        self.bus.emit(
            EventType.EPISODE_CREATED,
            source="demand",
            episode_id=episode_id,
            entity=evidence.entity,
            category=entity_meta.get("category"),
            score=evidence.score,
            is_control=is_control,
            is_negative=is_negative,
        )
        self.log.info(
            "research episode created",
            episode_id=episode_id,
            entity=evidence.entity,
            score=evidence.score,
            control=is_control,
            negative=is_negative,
        )
        return episode_id

    # -------------------------------------------------------------- versions
    def latest(self, episode_id: str) -> dict[str, Any] | None:
        rows = self.db.query_dicts(
            "SELECT * FROM research_episodes WHERE episode_id = ? ORDER BY version DESC LIMIT 1",
            [episode_id],
        )
        return rows[0] if rows else None

    def new_version(self, episode_id: str, updates: dict[str, Any], actor: str) -> int:
        """Append a new version with selected mutable-by-promotion fields."""
        current = self.latest(episode_id)
        if current is None:
            raise KeyError(episode_id)
        version = int(current["version"]) + 1
        constitution.require_append_only(int(current["version"]), version)
        allowed = {
            "mechanisms_found",
            "outcome",
            "validation_status",
            "lifecycle",
            "control_episodes",
            "confidence",
            "overall_confidence",
        }
        illegal = set(updates) - allowed
        if illegal:
            raise ValueError(f"fields frozen at creation: {sorted(illegal)}")
        row = dict(current)
        row.update(updates)
        self.db.execute(
            f"INSERT INTO research_episodes VALUES ({', '.join('?' * EPISODE_COLUMNS)})",
            [
                row["episode_id"],
                version,
                row["entity"],
                row["category"],
                row["traits"],
                row["country"],
                row["region"],
                row["baseline"],
                row["window_days"],
                row["spike_start"],
                row["spike_peak"],
                row["recovery_end"],
                row["demand_score"],
                row["evidence"],
                row["timeline"],
                row["signal_agreement"],
                row["confidence"] if isinstance(row["confidence"], str) else json.dumps(row["confidence"]),
                row["overall_confidence"],
                row["control_episodes"] if isinstance(row["control_episodes"], str) else json.dumps(row["control_episodes"]),
                row["mechanisms_found"] if isinstance(row["mechanisms_found"], str) else json.dumps(row["mechanisms_found"]),
                row["false_spike_checks"],
                row["outcome"] if isinstance(row.get("outcome"), (str, type(None))) else json.dumps(row["outcome"]),
                row["validation_status"],
                row["lifecycle"],
                row["is_control"],
                row["is_negative"],
                timeutil.utcnow(),
                row.get("volume_multiplier", 1.0),
            ],
        )
        self.log.info("episode version appended", episode_id=episode_id, version=version, actor=actor)
        return version

    def promote(self, episode_id: str, target: Lifecycle, validation_status: ValidationStatus, actor: str) -> None:
        current = self.latest(episode_id)
        if current is None:
            raise KeyError(episode_id)
        constitution.require_validated_promotion(
            Lifecycle(current["lifecycle"]), target, validation_status
        )
        self.new_version(
            episode_id,
            {"lifecycle": target.value, "validation_status": validation_status.value},
            actor=actor,
        )
        if target in (Lifecycle.VALIDATED, Lifecycle.TRUSTED):
            self.bus.emit(EventType.EPISODE_VALIDATED, source="demand", episode_id=episode_id)

    # --------------------------------------------------------------- queries
    def list_latest(
        self,
        category: str | None = None,
        lifecycle: str | None = None,
        include_controls: bool = True,
        entity: str | None = None,
    ) -> list[dict[str, Any]]:
        sql = (
            "SELECT * FROM research_episodes e WHERE version = "
            "(SELECT max(version) FROM research_episodes WHERE episode_id = e.episode_id)"
        )
        params: list[Any] = []
        if category:
            sql += " AND category = ?"
            params.append(category)
        if lifecycle:
            sql += " AND lifecycle = ?"
            params.append(lifecycle)
        if entity:
            sql += " AND entity = ?"
            params.append(entity)
        if not include_controls:
            sql += " AND NOT is_control"
        sql += " ORDER BY spike_start DESC"
        return self.db.query_dicts(sql, params)

    def replay(self, episode_id: str) -> dict[str, Any] | None:
        """Episode Replay (spec 3.18): the frozen evidence bundle allows the
        full day-by-day history to be watched again."""
        episode = self.latest(episode_id)
        if episode is None:
            return None
        evidence = json.loads(episode["evidence"])
        frames: dict[str, dict[str, float]] = {}
        for signal in evidence["signals"]:
            for day, value in signal["series"]:
                frames.setdefault(day, {})[signal["signal_type"]] = value
        return {
            "episode_id": episode_id,
            "entity": episode["entity"],
            "timeline": json.loads(episode["timeline"]),
            "frames": [{"day": day, "signals": frames[day]} for day in sorted(frames)],
        }

    def overlapping(self, entity: str, start: datetime, end: datetime) -> list[dict[str, Any]]:
        return self.db.query_dicts(
            "SELECT * FROM research_episodes e WHERE entity = ? AND spike_start <= ? AND spike_start >= ? "
            "AND version = (SELECT max(version) FROM research_episodes WHERE episode_id = e.episode_id)",
            [entity, timeutil.ensure_utc(end), timeutil.ensure_utc(start)],
        )
