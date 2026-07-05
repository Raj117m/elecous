"""Environmental Intelligence Engine facade (spec ch. 4).

Pipeline per research cycle:

episodes → per-episode signal discovery → pattern mining vs ordinary
windows → interaction discovery → factor formation (with timing,
contribution, geography) → counterfactual scoring → similarity maintenance.

This engine discovers factors; it never explains demand (4.27) and never
predicts. Factor promotion beyond Candidate happens only through the
Validation Engine.
"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime, timedelta
from typing import Any

from elecous.common import timeutil
from elecous.common.vocabulary import Lifecycle
from elecous.demand.episodes.episodes import EpisodeStore
from elecous.environment.factor_discovery.factors import FactorStore
from elecous.environment.interaction.interaction import InteractionDiscovery
from elecous.environment.ontology.ontology import Ontology
from elecous.environment.pattern_engine.mining import PatternMiner
from elecous.environment.signal_discovery.discovery import EpisodeSignalDiscovery
from elecous.environment.concept_discovery import ConceptDiscoveryEngine
from elecous.environment.signal_discovery.extractor import EnvironmentalSignalExtractor
from elecous.environment.state_discovery import EnvironmentalStateDiscovery
from elecous.environment.world_state import WorldStateBuilder
from elecous.infrastructure.cache import TTLCache
from elecous.infrastructure.config import Config, active_config
from elecous.infrastructure.event_bus import EventBus, EventType, global_bus
from elecous.infrastructure.logging import get_logger
from elecous.observation.store import ObservationStore
from elecous.signals.library import SignalLibrary
from elecous.storage.database import DatabaseManager, global_databases


class EnvironmentalEngine:
    def __init__(
        self,
        store: ObservationStore,
        episodes: EpisodeStore,
        config: Config | None = None,
        databases: DatabaseManager | None = None,
        bus: EventBus | None = None,
    ):
        self.config = config or active_config()
        self.databases = databases or global_databases()
        self.bus = bus or global_bus()
        self.log = get_logger("environment")
        self.store = store
        self.episodes = episodes
        self.extractor = EnvironmentalSignalExtractor(store, self.config)
        self.library = SignalLibrary(self.databases, self.bus)
        self.discovery = EpisodeSignalDiscovery(self.extractor, self.library, self.databases, self.config)
        self.miner = PatternMiner(self.config)
        self.factors = FactorStore(self.databases, self.bus)
        self.interactions = InteractionDiscovery(self.databases)
        self.states = EnvironmentalStateDiscovery(self.databases)
        self.concepts = ConceptDiscoveryEngine(self.databases, self.config)
        self.world = WorldStateBuilder(self.extractor, self.databases, self.bus)
        # Real bottleneck found profiling research_cycle() (Final
        # Implementation Phase, Task 2): counterfactual() is called once
        # per mined pattern (100+ per category) and re-ran the same
        # per-category episode/ordinary-window queries every time. Cached
        # by category, same TTLCache already used for this exact reason
        # elsewhere (EnvironmentalSignalExtractor, SkepticEngine) -- the
        # query's real scope (all non-negative episodes for the category,
        # deliberately broader than research_cycle()'s confidence-floor-
        # filtered set) is unchanged, only its repetition is removed.
        self._counterfactual_cache = TTLCache(ttl_seconds=30.0, max_entries=64)
        self.ontology = Ontology(self.config)
        self.thresholds = self.config.load_file("thresholds.yaml")

    # ------------------------------------------------------------ discovery
    def research_cycle(self) -> dict[str, Any]:
        """One full discovery pass over all research-grade episodes."""
        floor = float(self.thresholds["demand"]["research_confidence_floor"])
        episodes = [
            e
            for e in self.episodes.list_latest(include_controls=False)
            if not e["is_negative"] and float(e["overall_confidence"]) >= floor
        ]
        by_category: dict[str, list[dict[str, Any]]] = {}
        for episode in episodes:
            by_category.setdefault(episode["category"], []).append(episode)

        summary = {"episodes": len(episodes), "factors": [], "interactions": 0, "gated_categories": {}}
        for category, group in by_category.items():
            gate = self._validation_gate(category, group)
            if not gate["open"]:
                summary["gated_categories"][category] = gate["unmet_requirements"]
                self.log.info(
                    "mechanism discovery gated: insufficient evidence",
                    category=category, unmet=gate["unmet_requirements"],
                )
                continue
            for episode in group:
                self.discovery.discover_for_episode(episode)
            self._ensure_ordinary_windows(category, group)
            episode_ids = [e["episode_id"] for e in group]
            # Mining operates on phenomena, not per-country scoped signals
            # (spec 4.3: a factor is a combination of phenomena; spec 4.20:
            # geography is factor metadata). One real weather system hitting
            # many tracked countries must count as one item, not one item
            # per country, or the search space explodes combinatorially
            # into near-duplicate per-country patterns with no additional
            # scientific content.
            phenomenon_sets = self.discovery.episode_phenomenon_sets(episode_ids)
            ordinary_phenomena = self.discovery.ordinary_phenomenon_sets(category)
            patterns = self.miner.mine(category, phenomenon_sets, ordinary_phenomena)
            lead_times = self.discovery.episode_lead_times(episode_ids)
            for pattern in patterns:
                # Translate the phenomenon-level pattern back to the
                # concrete scoped signals that actually satisfied it, for
                # traceable storage (regions, contribution, lead time).
                scoped_map = self.discovery.scoped_keys_for_phenomena(episode_ids, pattern.signals)
                concrete_signals = tuple(sorted({k for keys in scoped_map.values() for k in keys}))
                concrete_pattern = replace(pattern, signals=concrete_signals)
                contribution = self._contribution(concrete_signals, pattern.supporting_episodes)
                regions = sorted({k.rsplit(":", 1)[1] for k in concrete_signals})
                factor_id = self.factors.form_from_pattern(concrete_pattern, lead_times, contribution, regions)
                counterfactual = self.counterfactual(factor_id, category)
                summary["factors"].append({"factor_id": factor_id, "counterfactual": counterfactual})
            summary["interactions"] += len(
                self.interactions.discover(category, phenomenon_sets, ordinary_phenomena)
            )
        self.factors.merge_similar()
        self.factors.split_overbroad()
        # Discover states, not signals (rewrite directive, Stage 4,
        # Requirement 2): cluster all real, currently-live factors by real
        # phenomenon overlap into named states -- runs over every category's
        # factors together so a state discovered under one category can be
        # recognized as shared by another (Requirement 4/5).
        discovered_states = self.states.discover(self.factors.list_latest())
        summary["states"] = [{"label": s["label"], "categories": s["categories"]} for s in discovered_states]
        # Mechanism hypotheses (rewrite directive, Stage 5): for every real
        # discovered state, test category-blind generalization, detect
        # real contradictions, and recompute confidence -- retiring states
        # that no longer earn one.
        hypotheses = self.states.generalize_all(self.factors, self.episodes)
        summary["mechanism_hypotheses"] = [
            {"label": h["label"], "confidence": h["confidence"], "status": h["status"]} for h in hypotheses
        ]
        return summary

    def _validation_gate(self, category: str, group: list[dict[str, Any]]) -> dict[str, Any]:
        """Evidence-sufficiency gate (evidence-bottleneck directive, Phase
        7): do not run pattern mining for a category until it clears real
        minimum entity/episode/seasonal-diversity floors. Mirrors the
        Skeptic Engine's sampling_bias/coincidence checks but runs *before*
        mining spends any computation, and reports exactly what is missing
        rather than silently producing candidates that cannot survive
        validation regardless of pattern quality."""
        entities = {e["entity"] for e in group}
        seasons = {timeutil.season_of(e["spike_start"].month) for e in group}
        min_entities = int(self.thresholds["environment"]["gate_min_entities"])
        min_episodes = int(self.thresholds["environment"]["gate_min_episodes"])
        min_seasons = int(self.thresholds["environment"]["gate_min_seasons"])
        unmet = []
        if len(entities) < min_entities:
            unmet.append(f"entities {len(entities)}/{min_entities}")
        if len(group) < min_episodes:
            unmet.append(f"episodes {len(group)}/{min_episodes}")
        if len(seasons) < min_seasons:
            unmet.append(f"seasons {len(seasons)}/{min_seasons}")
        return {"open": not unmet, "unmet_requirements": unmet}

    def _ensure_ordinary_windows(self, category: str, group: list[dict[str, Any]]) -> None:
        """Sample real ordinary periods for the category: weekly instants in
        observed history that do not overlap any episode window (spec 4.9
        'ordinary periods'). Incremental: only missing weeks are sampled."""
        existing = self.databases.get("environment").query_one(
            "SELECT count(*) FROM ordinary_windows WHERE category = ?", [category]
        )[0]
        # Pattern mining's lift statistic needs a real base-rate sample, not
        # a handful of points — see MIN_ORDINARY_SAMPLES in the pattern
        # miner. Below that floor, a single zero-occurrence itemset
        # manufactures an unreliable, arbitrarily large "lift".
        from elecous.environment.pattern_engine.mining import MIN_ORDINARY_SAMPLES

        target = max(MIN_ORDINARY_SAMPLES, 2 * len(group))
        if existing >= target:
            return
        bounds = self.store.db.query_one("SELECT min(timestamp), max(timestamp) FROM observations")
        if not bounds or bounds[0] is None:
            return
        start = timeutil.ensure_utc(bounds[0]) + timedelta(days=45)  # room for baselines
        end = timeutil.ensure_utc(bounds[1])
        spike_windows = [
            (
                timeutil.ensure_utc(e["spike_start"]) - timedelta(days=int(e["window_days"])),
                timeutil.ensure_utc(e["spike_start"]) + timedelta(days=int(e["window_days"])),
            )
            for e in group
        ]
        instant = start
        added = existing
        while instant <= end and added < target:
            if not any(a <= instant <= b for a, b in spike_windows):
                self.discovery.record_ordinary_window(category, instant)
                added += 1
            instant += timedelta(days=7)

    def _contribution(self, signals: tuple[str, ...], episode_ids: tuple[str, ...]) -> dict[str, float]:
        """Contribution Analysis (spec 4.15): share of total |z| carried by
        each signal across supporting episodes. Measured, never weighted by
        hand."""
        placeholders = ",".join("?" * len(episode_ids))
        keys_placeholder = ",".join("?" * len(signals))
        rows = self.databases.get("environment").query(
            f"SELECT signal_key, avg(abs(z)) FROM episode_signals "
            f"WHERE episode_id IN ({placeholders}) AND signal_key IN ({keys_placeholder}) GROUP BY signal_key",
            [*episode_ids, *signals],
        )
        magnitudes = {r[0]: float(r[1]) for r in rows}
        total = sum(magnitudes.values()) or 1.0
        return {k: round(v / total, 4) for k, v in magnitudes.items()}

    # -------------------------------------------------------- counterfactual
    def counterfactual(self, factor_id: str, category: str) -> dict[str, Any]:
        """Counterfactual Testing (spec 4.17): would demand have spiked
        anyway? Compare spike frequency with the factor present vs absent,
        using ordinary windows as the non-spike world sample.

        Membership must be checked at the same phenomenon-level granularity
        the pattern miner used to establish support (spec 4.3). The stored
        ``factor["signals"]`` are concrete per-country observations (kept
        for traceability/regions), which is a *union* across every
        supporting episode — no single episode necessarily contains that
        full union, so checking concrete-signal subset-membership against
        one episode at a time would almost always fail even for a factor
        with genuine support, undercounting spikes_with to near zero.
        """
        factor = self.factors.latest(factor_id)
        if factor is None:
            return {}
        phenomena = {self.discovery.phenomenon_of(k) for k in json.loads(factor["signals"])}
        episode_count, episode_sets, ordinary = self._counterfactual_cache.get_or_compute(
            category, lambda: self._counterfactual_population(category)
        )
        spikes_with = sum(1 for _, s in episode_sets.items() if phenomena <= s)
        spikes_without = episode_count - spikes_with
        ordinary_with = sum(1 for s in ordinary if phenomena <= s)
        ordinary_without = len(ordinary) - ordinary_with
        p_spike_given_factor = spikes_with / max(spikes_with + ordinary_with, 1)
        p_spike_given_absent = spikes_without / max(spikes_without + ordinary_without, 1)
        result = {
            "p_spike_given_factor": round(p_spike_given_factor, 4),
            "p_spike_given_absent": round(p_spike_given_absent, 4),
            "risk_ratio": round(p_spike_given_factor / max(p_spike_given_absent, 1e-4), 3),
            "samples": {
                "spikes_with": spikes_with,
                "spikes_without": spikes_without,
                "ordinary_with": ordinary_with,
                "ordinary_without": ordinary_without,
            },
            "computed_at": timeutil.to_iso(timeutil.utcnow()),
            "survives": p_spike_given_factor > p_spike_given_absent,
        }
        self.factors.new_version(factor_id, {"counterfactual": json.dumps(result)}, actor="counterfactual_engine")
        return result

    def _counterfactual_population(self, category: str) -> tuple[int, dict[str, set[str]], list[set[str]]]:
        """The real, per-category population counterfactual() tests against
        -- deliberately the full set of non-negative episodes for the
        category (not research_cycle()'s narrower confidence-floor-filtered
        set), computed once and cached per category instead of once per
        mined pattern."""
        all_episodes = [
            e
            for e in self.episodes.list_latest(category=category, include_controls=False)
            if not e["is_negative"]
        ]
        episode_ids = [e["episode_id"] for e in all_episodes]
        episode_sets = self.discovery.episode_phenomenon_sets(episode_ids)
        ordinary = self.discovery.ordinary_phenomenon_sets(category)
        return len(all_episodes), episode_sets, ordinary

    # ------------------------------------------------------ negative episodes
    def detect_negative_episodes(self, detector, catalog: list[dict[str, Any]]) -> int:
        """Negative Episodes (spec 4.18): the factor's signals are active in
        the current world, yet category entities show no spike. Recorded as
        contradictory evidence and as negative episode research objects."""
        state = self.world.latest_state() or self.world.build()
        active = {s["signal_key"] for s in state["signals"]}
        created = 0
        for factor in self.factors.list_latest():
            signals = set(json.loads(factor["signals"]))
            if not signals <= active:
                continue
            for category in json.loads(factor["categories"]):
                entities = [t for t in catalog if t.get("category") == category]
                for topic in entities:
                    evidence = detector.evaluate(topic["entity"], timeutil.utcnow(), 14)
                    if not evidence.signals or evidence.stage == "abnormal_acceleration":
                        continue
                    timeline = detector.research_timeline(evidence)
                    episode_id = self.episodes.create_episode(
                        evidence,
                        entity_meta={"category": category, "traits": topic.get("traits", [])},
                        timeline=timeline,
                        controls=[],
                        confidence={"overall": 0.5},
                        is_negative=True,
                    )
                    self.factors.record_contradiction(factor["factor_id"], episode_id)
                    created += 1
        return created

    # ------------------------------------------------------------ gap engine
    def report_gap(self, category: str, reason: str) -> None:
        """Environmental Gap Engine (spec 4.23): instead of forcing
        explanations, name the missing phenomenon as an Observation Request."""
        self.bus.emit(
            EventType.OBSERVATION_REQUEST,
            source="environment",
            capability=f"category:{category}",
            reason=reason,
        )

    # ------------------------------------------------------ discovery quality
    def score_discovery_quality(self, factor_id: str) -> dict[str, float]:
        """Discovery Quality (spec 4.24). Weak discoveries decay away via
        current_confidence."""
        factor = self.factors.latest(factor_id)
        if factor is None:
            return {}
        signals = json.loads(factor["signals"])
        categories = json.loads(factor["categories"])
        regions = json.loads(factor["regions"])
        counterfactual = json.loads(factor["counterfactual"]) if factor["counterfactual"] else {}
        all_factors = self.factors.list_latest()
        overlaps = [
            len(set(json.loads(f["signals"])) & set(signals)) / len(set(json.loads(f["signals"])) | set(signals))
            for f in all_factors
            if f["factor_id"] != factor_id
        ]
        novelty = 1.0 - max(overlaps, default=0.0)
        quality = {
            "novelty": round(novelty, 3),
            "repeatability": round(min(1.0, int(factor["support"]) / 10.0), 3),
            "predictive_contribution": round(min(1.0, (counterfactual.get("risk_ratio", 1.0) - 1.0) / 4.0), 3),
            "geographic_coverage": round(min(1.0, len(regions) / 5.0), 3),
            "category_coverage": round(min(1.0, len(categories) / 3.0), 3),
            "information_gain": round(novelty * min(1.0, float(factor["lift"]) / 4.0), 3),
            "scientific_stability": round(min(1.0, int(factor["version"]) / 5.0), 3),
        }
        self.factors.new_version(factor_id, {"discovery_quality": json.dumps(quality)}, actor="discovery_quality")
        return quality
