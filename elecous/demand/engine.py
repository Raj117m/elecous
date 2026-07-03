"""Demand Intelligence Engine facade (spec ch. 3).

Answers exactly one question with scientific rigor: did an authentic demand
opportunity occur? It scans eligible entities, detects abnormal acceleration,
applies false-spike rules, creates frozen Research Episodes with controls and
research confidence, learns signal weights and time windows from completed
episodes, and records failure research. It never touches environmental
reasoning (spec 3.23).
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any

from elecous.common import timeutil
from elecous.common.ids import new_id
from elecous.common.vocabulary import Lifecycle
from elecous.demand.baseline.baseline import DynamicBaseline
from elecous.demand.controls.controls import ControlSelector
from elecous.demand.eligibility.eligibility import EligibilityEngine
from elecous.demand.episodes.episodes import EpisodeStore
from elecous.demand.research.scientist import DemandScientist
from elecous.demand.schema import DEMAND_MIGRATION
from elecous.demand.spikes.detector import DemandEvidence, SpikeDetector
from elecous.infrastructure.config import Config, active_config
from elecous.infrastructure.event_bus import EventBus, EventType, global_bus
from elecous.infrastructure.logging import get_logger
from elecous.observation.store import ObservationStore
from elecous.storage.database import DatabaseManager, global_databases


class DemandEngine:
    def __init__(
        self,
        store: ObservationStore,
        config: Config | None = None,
        databases: DatabaseManager | None = None,
        bus: EventBus | None = None,
    ):
        self.config = config or active_config()
        self.databases = databases or global_databases()
        self.databases.register_schema(DEMAND_MIGRATION)
        self.db = self.databases.get("demand")
        self.bus = bus or global_bus()
        self.log = get_logger("demand")
        self.store = store
        self.baselines = DynamicBaseline(store, self.config, self.databases)
        self.eligibility = EligibilityEngine(store, self.config)
        self.detector = SpikeDetector(store, self.baselines, self.config)
        self.controls = ControlSelector(self.detector, self.config)
        self.scientist = DemandScientist()
        self.episodes = EpisodeStore(self.databases, self.bus)
        self.thresholds = self.config.load_file("thresholds.yaml")["demand"]

    # ------------------------------------------------------------------ scan
    def scan(self, as_of: datetime | None = None) -> list[str]:
        """Evaluate every configured entity; create Research Episodes for
        genuine candidate spikes. Returns created episode ids."""
        as_of = as_of or timeutil.utcnow()
        created: list[str] = []
        for topic in self.controls.entity_catalog():
            entity = topic["entity"]
            category = topic.get("category", "unknown")
            window = self.learned_window(category)
            # Eligibility per signal; at least two eligible signals required
            # since spikes need multi-signal agreement anyway (3.20).
            eligible_signals = [
                t
                for t in SpikeDetector.DEMAND_SIGNAL_TYPES
                if self.eligibility.check(entity, t, as_of).eligible
            ]
            if len(eligible_signals) < int(self.thresholds["false_spike"]["min_signals_agreeing"]):
                continue
            evidence = self.detector.evaluate(entity, as_of, window, weights=self.learned_weights(category))
            if not evidence.is_candidate_spike:
                if evidence.false_spike is not None:
                    self.log.info("false spike rejected", entity=entity, detail=evidence.false_spike)
                continue
            if self._already_researched(entity, as_of, window):
                continue
            created.append(self._open_episode(evidence, topic, as_of, window))
        return created

    def _already_researched(self, entity: str, as_of: datetime, window: int) -> bool:
        overlapping = self.episodes.overlapping(entity, as_of - timedelta(days=window * 2), as_of)
        return any(not e["is_control"] for e in overlapping)

    def _open_episode(
        self, evidence: DemandEvidence, topic: dict[str, Any], as_of: datetime, window: int
    ) -> str:
        timeline = self.detector.research_timeline(evidence)
        controls = self.controls.select_controls(
            evidence.entity, topic.get("category", "unknown"), topic.get("traits", []), as_of, window
        )
        confidence = self.scientist.assess(evidence, controls, timeline)
        episode_id = self.episodes.create_episode(
            evidence,
            entity_meta={
                "category": topic.get("category", "unknown"),
                "traits": topic.get("traits", []),
                "country": None,  # attention entities are observed globally
                "region": "en.wikipedia",
            },
            timeline=timeline,
            controls=controls,
            confidence=confidence,
        )
        self.bus.emit(
            EventType.DEMAND_SPIKE_CANDIDATE,
            source="demand",
            episode_id=episode_id,
            entity=evidence.entity,
            score=evidence.score,
            confidence=confidence.get("overall"),
        )
        return episode_id

    # -------------------------------------------------------------- learning
    def learned_window(self, category: str) -> int:
        row = self.db.query_one("SELECT window_days FROM learned_windows WHERE category = ?", [category])
        return int(row[0]) if row else int(self.thresholds["default_window_days"])

    def learned_weights(self, category: str) -> dict[str, float]:
        rows = self.db.query("SELECT signal_type, weight FROM signal_weights WHERE category = ?", [category])
        return {r[0]: r[1] for r in rows}

    def improve_from_episodes(self) -> dict[str, Any]:
        """Continuous improvement (spec 3.21): learn signal weights from each
        signal's measured contribution in validated episodes, and time
        windows from observed formation→decline durations."""
        episodes = self.episodes.list_latest(lifecycle=Lifecycle.VALIDATED.value, include_controls=False)
        episodes += self.episodes.list_latest(lifecycle=Lifecycle.TRUSTED.value, include_controls=False)
        by_category: dict[str, list[dict[str, Any]]] = {}
        for episode in episodes:
            by_category.setdefault(episode["category"], []).append(episode)
        updates: dict[str, Any] = {"weights": {}, "windows": {}}
        for category, group in by_category.items():
            contributions: dict[str, list[float]] = {}
            durations: list[int] = []
            for episode in group:
                agreement = json.loads(episode["signal_agreement"])
                for signal_type, value in agreement.items():
                    contributions.setdefault(signal_type, []).append(float(value))
                timeline = json.loads(episode["timeline"])
                if timeline.get("signal_formation") and timeline.get("decline"):
                    duration = (
                        timeutil.from_iso(timeline["decline"]) - timeutil.from_iso(timeline["signal_formation"])
                    ).days
                    if duration > 0:
                        durations.append(duration)
            if contributions:
                means = {t: sum(v) / len(v) for t, v in contributions.items()}
                total = sum(means.values()) or 1.0
                for signal_type, mean in means.items():
                    weight = mean / total
                    self.db.execute(
                        "INSERT OR REPLACE INTO signal_weights VALUES (?, ?, ?, ?, ?)",
                        [category, signal_type, weight, len(group), timeutil.utcnow()],
                    )
                updates["weights"][category] = {t: round(m / total, 3) for t, m in means.items()}
            if durations:
                learned = max(3, min(45, round(sum(durations) / len(durations)) + 2))
                self.db.execute(
                    "INSERT OR REPLACE INTO learned_windows VALUES (?, ?, ?, ?)",
                    [category, learned, len(durations), timeutil.utcnow()],
                )
                updates["windows"][category] = learned
        if updates["weights"] or updates["windows"]:
            self.log.info("demand engine improved", **updates)
        return updates

    # ------------------------------------------------------ failure research
    def record_failure(self, kind: str, entity: str | None, category: str | None, questions: dict[str, Any]) -> str:
        """Demand Failure Analysis (spec 3.22): every miss becomes research."""
        failure_id = new_id("task")
        self.db.execute(
            "INSERT INTO demand_failures VALUES (?, ?, ?, ?, ?, ?, ?)",
            [failure_id, timeutil.utcnow(), entity, category, kind, json.dumps(questions, default=str), None],
        )
        self.log.warning("demand failure recorded", kind=kind, entity=entity)
        return failure_id

    def signal_registry_refresh(self) -> None:
        """Maintain the demand-signal registry (spec 3.4) from episode
        outcomes: reliability = share of validated episodes in which the
        signal independently agreed."""
        episodes = self.episodes.list_latest(include_controls=False)
        by_key: dict[tuple[str, str], list[float]] = {}
        for episode in episodes:
            agreement = json.loads(episode["signal_agreement"])
            validated = episode["lifecycle"] in (Lifecycle.VALIDATED.value, Lifecycle.TRUSTED.value)
            for signal_type, evidence in agreement.items():
                by_key.setdefault((signal_type, episode["category"]), []).append(
                    1.0 if (validated and evidence >= 50) else 0.0
                )
        for (signal_type, category), outcomes in by_key.items():
            reliability = sum(outcomes) / len(outcomes)
            existing = self.db.query_one(
                "SELECT max(version) FROM demand_signal_registry WHERE signal_type = ? AND category = ? AND country = ''",
                [signal_type, category],
            )
            version = (existing[0] or 0) + 1
            self.db.execute(
                "INSERT INTO demand_signal_registry VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    signal_type,
                    category,
                    "",
                    reliability,
                    1.0,
                    reliability,
                    sum(outcomes) / max(len(episodes), 1),
                    len(outcomes),
                    reliability,
                    Lifecycle.EXPERIMENTAL.value if len(outcomes) < 5 else Lifecycle.VALIDATED.value,
                    version,
                    timeutil.utcnow(),
                ],
            )
