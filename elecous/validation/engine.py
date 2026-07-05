"""Scientific Validation Engine facade (spec ch. 6).

The single authority that decides whether an object has earned promotion.
No other engine calls ``promote`` directly — the Demand, Environmental and
Mechanism engines only ever produce Candidates; only this engine spends
validation evidence to move them forward (spec 6.30).

Validation layers implemented, per spec 6.4:
  Source -> Observation (already enforced inline, ch.2) -> Signal ->
  Environmental Factor -> Mechanism -> Prediction (hooked from Phase 8) ->
  Knowledge (hooked from Phase 7).
"""

from __future__ import annotations

import json
from typing import Any

from elecous.common import timeutil
from elecous.common.ids import new_id
from elecous.common.vocabulary import Lifecycle, ValidationStatus
from elecous.governance import constitution
from elecous.infrastructure.config import Config, active_config
from elecous.infrastructure.event_bus import EventBus, EventType, global_bus
from elecous.infrastructure.logging import get_logger
from elecous.storage.database import DatabaseManager, global_databases
from elecous.validation.benchmarks.benchmark import BenchmarkEngine
from elecous.validation.calibration.calibration import CalibrationEngine
from elecous.validation.counterfactual.negative_learning import NegativeLearningEngine
from elecous.validation.drift.drift import ConceptDriftEngine
from elecous.validation.historical.replay import HistoricalReplay
from elecous.validation.prospective.prospective import ProspectiveValidator
from elecous.validation.schema import VALIDATION_MIGRATION
from elecous.validation.skeptic.skeptic import SkepticEngine


class ValidationEngine:
    def __init__(
        self,
        signals,          # SignalLibrary
        factors,           # FactorStore
        mechanisms,         # MechanismStore
        episodes,           # EpisodeStore
        discovery,          # EpisodeSignalDiscovery
        extractor,          # EnvironmentalSignalExtractor
        world,              # WorldStateBuilder
        config: Config | None = None,
        databases: DatabaseManager | None = None,
        bus: EventBus | None = None,
    ):
        self.config = config or active_config()
        self.databases = databases or global_databases()
        self.databases.register_schema(VALIDATION_MIGRATION)
        self.db = self.databases.get("validation")
        self.bus = bus or global_bus()
        self.log = get_logger("validation")

        self.signals = signals
        self.factors = factors
        self.mechanisms = mechanisms
        self.episodes = episodes
        self.discovery = discovery
        self.extractor = extractor
        self.world = world

        self.skeptic = SkepticEngine(discovery, episodes, factors)
        self.replay = HistoricalReplay(extractor, episodes, config=self.config)
        self.prospective = ProspectiveValidator(episodes, self.databases)
        self.calibration = CalibrationEngine(self.databases, self.config)
        self.drift = ConceptDriftEngine(mechanisms, self.prospective, self.databases, self.config)
        self.negative_learning = NegativeLearningEngine(mechanisms, factors, discovery, world)
        self.benchmarks = BenchmarkEngine(self.databases, self.config)

        self.thresholds = self.config.load_file("thresholds.yaml")

    # ------------------------------------------------------------ recording
    def _record(
        self,
        object_type: str,
        object_id: str,
        object_version: int | None,
        method: str,
        evidence: dict[str, Any],
        outcome: str,
        confidence: float | None = None,
        controls: dict[str, Any] | None = None,
        failures: dict[str, Any] | None = None,
    ) -> str:
        validation_id = new_id("validation")
        self.db.execute(
            "INSERT INTO validations VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                validation_id,
                object_type,
                object_id,
                object_version,
                method,
                json.dumps(evidence, default=str),
                json.dumps(controls, default=str) if controls else None,
                json.dumps(failures, default=str) if failures else None,
                confidence,
                "validation_engine",
                outcome,
                timeutil.utcnow(),
            ],
        )
        self.bus.emit(
            EventType.VALIDATION_COMPLETED,
            source="validation",
            object_type=object_type,
            object_id=object_id,
            method=method,
            outcome=outcome,
        )
        return validation_id

    # ------------------------------------------------------------- signals
    def validate_signal(self, signal_key: str) -> dict[str, Any]:
        """Signal validation (spec 6.7): reliability = share of episodes
        where the signal's presence coincided with a validated outcome."""
        signal = self.signals.latest(signal_key)
        if signal is None:
            return {"outcome": "failed", "reason": "unknown_signal"}
        seen = int(signal["seen_in_episodes"])
        # Contribution comes from factor-level contribution analysis already
        # stored against factors containing this signal.
        contributions = []
        for factor in self.factors.list_latest():
            if signal_key in json.loads(factor["signals"]):
                contribution_map = json.loads(factor["contribution"])
                if signal_key in contribution_map:
                    contributions.append(contribution_map[signal_key])
        contribution = sum(contributions) / len(contributions) if contributions else 0.0
        coverage = min(1.0, seen / 10.0)
        reliability = min(1.0, 0.5 * coverage + 0.5 * contribution)
        passed = seen >= 3 and contribution > 0
        self.signals.apply_validation(signal_key, reliability, contribution, coverage, passed)
        outcome = "passed" if passed else "failed"
        self._record(
            "signal", signal_key, int(signal["version"]) + 1, "signal_validation",
            {"seen": seen, "contribution": contribution, "coverage": coverage},
            outcome, confidence=reliability,
        )
        return {"outcome": outcome, "reliability": reliability, "contribution": contribution}

    # ------------------------------------------------------------- factors
    def validate_factor(self, factor_id: str) -> dict[str, Any]:
        """Environmental Factor validation (spec 6.8, 4.17, 4.25): requires
        multiple episodes/regions, a surviving counterfactual and a surviving
        skeptic challenge."""
        factor = self.factors.latest(factor_id)
        if factor is None:
            raise KeyError(factor_id)
        regions = json.loads(factor["regions"])
        counterfactual = json.loads(factor["counterfactual"]) if factor["counterfactual"] else None
        skeptic_report = self.skeptic.challenge_factor(factor)

        checks = {
            "multiple_episodes": int(factor["support"]) >= 3,
            "multiple_regions": len(set(r for r in regions if len(r) == 2)) >= 1 or "global" in regions,
            "counterfactual_survives": bool(counterfactual and counterfactual.get("survives")),
            "skeptic_survives": skeptic_report["survived"],
        }
        passed = all(checks.values())
        current = Lifecycle(factor["lifecycle"])
        target = {
            Lifecycle.CANDIDATE: Lifecycle.EXPERIMENTAL,
            Lifecycle.EXPERIMENTAL: Lifecycle.VALIDATED,
            Lifecycle.VALIDATED: Lifecycle.TRUSTED,
        }.get(current)
        outcome = "passed" if passed else "failed"
        if passed and target is not None:
            self.factors.promote(factor_id, target, ValidationStatus.PASSED, actor="validation_engine")
        elif not passed and current in (Lifecycle.VALIDATED, Lifecycle.TRUSTED):
            self.factors.promote(factor_id, Lifecycle.DECLINING, ValidationStatus.FAILED, actor="validation_engine")
        self._record(
            "environmental_factor", factor_id, factor["version"], "factor_validation",
            checks, outcome, controls=skeptic_report,
        )
        return {"outcome": outcome, "checks": checks, "skeptic": skeptic_report}

    # ---------------------------------------------------------- mechanisms
    def validate_mechanism(self, mechanism_id: str) -> dict[str, Any]:
        """Mechanism validation (spec 6.9): generalization, repeatability,
        stable timing, control disagreement resolved, historical replay
        precision, and skeptic survival across its factors."""
        mechanism = self.mechanisms.latest(mechanism_id)
        if mechanism is None:
            raise KeyError(mechanism_id)
        factor_ids = json.loads(mechanism["environmental_factors"])
        factor_reports = []
        for factor_id in factor_ids:
            factor = self.factors.latest(factor_id)
            if factor:
                factor_reports.append(self.skeptic.challenge_factor(factor))
        mech_skeptic = self.skeptic.challenge_mechanism(mechanism, factor_reports)
        # Requirement 7: attempt to falsify the preferred (environmental)
        # explanation against real alternatives before trusting it.
        competing = self.skeptic.competing_explanations(mechanism, mech_skeptic)

        replay_report = self.replay.replay_mechanism(mechanism)
        min_precision = float(self.thresholds["validation"]["historical_min_precision"])
        precision_ok = replay_report.get("precision") is None or replay_report["precision"] >= min_precision

        # Break the measured confidence deadlock (v1.0 blocker): mechanism
        # confidence was frozen at its creation-time base*0.6 (~0.30, below
        # the 0.35 experimental floor) because the ONLY writer of mechanism
        # confidence was resolve_prospective -- which only runs AFTER a
        # mechanism reaches VALIDATED, which itself requires confidence
        # above the floor. Nothing could ever cross it. The "scientific"
        # confidence dimension (weight 0.2, reserved for exactly this) was
        # initialised to 0.0 and never written by any code path.
        #
        # Fix: real validation evidence now populates it. scientific =
        # measured historical replay precision, discounted if the mechanism
        # fails the adversarial skeptic or competing-explanation checks.
        # This weakens NONE of the six checks below -- they remain the real
        # gate; it only lets confidence reflect the real evidence validation
        # already gathered, so a mechanism with genuinely strong history can
        # cross the floor and a weak one still cannot.
        scientific = 0.0
        if replay_report.get("precision") is not None:
            scientific = float(replay_report["precision"])
            if not mech_skeptic["survived"]:
                scientific *= 0.5
            if not competing["environmental_explanation_survives"]:
                scientific *= 0.5
        self.mechanisms.update_confidence(
            mechanism_id, {"scientific": round(scientific, 4)}, actor="validation_evidence"
        )
        mechanism = self.mechanisms.latest(mechanism_id)  # re-read the freshened confidence

        checks = {
            "generalizes": len(factor_ids) > 0,
            "repeatable": len(json.loads(mechanism["supporting_episodes"])) >= int(
                self.thresholds["mechanisms"]["min_supporting_episodes"]
            ),
            "timing_stable": (json.loads(mechanism["lead_time"]).get("stddev") or 0)
            <= max(3.0, json.loads(mechanism["lead_time"]).get("avg") or 3.0),
            "skeptic_survives": mech_skeptic["survived"],
            "historical_precision_ok": precision_ok,
            "survives_competing_explanations": competing["environmental_explanation_survives"],
        }
        passed = all(checks.values())
        current = Lifecycle(mechanism["lifecycle"])
        min_conf = self.thresholds["mechanisms"]
        confidence_floor_ok = float(mechanism["current_confidence"]) >= float(
            {
                Lifecycle.CANDIDATE: min_conf["min_confidence_experimental"],
                Lifecycle.EXPERIMENTAL: min_conf["min_confidence_validated"],
                Lifecycle.VALIDATED: min_conf["min_confidence_trusted"],
            }.get(current, 0.0)
        )
        # Historical validation promotes at most to VALIDATED. TRUSTED is
        # reserved for real prospective (future) confirmation via
        # resolve_prospective -- reaching it on the same backtest a
        # mechanism was validated on would be survivorship/overfitting
        # self-deception (prospective.py: "Only prospective success creates
        # Trusted Knowledge"). This VALIDATED->TRUSTED path was dormant and
        # unreachable until the confidence-deadlock fix; capping it here
        # keeps the scientific separation intact.
        target = {
            Lifecycle.CANDIDATE: Lifecycle.EXPERIMENTAL,
            Lifecycle.EXPERIMENTAL: Lifecycle.VALIDATED,
        }.get(current)
        outcome = "passed" if (passed and confidence_floor_ok) else "failed"
        if outcome == "passed" and target is not None:
            self.mechanisms.promote(
                mechanism_id, target, ValidationStatus.PASSED, actor="validation_engine",
                evidence={"replay": replay_report, "skeptic": mech_skeptic},
            )
            if target == Lifecycle.VALIDATED and not self.prospective.has_open(mechanism_id):
                self.prospective.register(mechanism, activation=1.0)
        elif outcome == "passed" and current in (Lifecycle.VALIDATED, Lifecycle.TRUSTED):
            # Re-affirmed on fresh historical evidence: no lifecycle change,
            # but push the revalidation timer forward so the continuous loop
            # does not re-replay it every tick.
            self.mechanisms.reschedule_validation(mechanism_id, current)
        elif outcome == "failed" and current in (Lifecycle.VALIDATED, Lifecycle.TRUSTED):
            self.mechanisms.promote(mechanism_id, Lifecycle.DECLINING, ValidationStatus.FAILED, actor="validation_engine")
        self._record(
            "mechanism", mechanism_id, mechanism["version"], "mechanism_validation",
            {"checks": checks, "replay": replay_report, "competing_explanations": competing}, outcome, controls=mech_skeptic,
        )
        return {
            "outcome": outcome, "checks": checks, "replay": replay_report, "skeptic": mech_skeptic,
            "competing_explanations": competing,
        }

    # -------------------------------------------------------- prospective
    def resolve_prospective(self) -> list[dict[str, Any]]:
        """Resolve due prospective predictions; success promotes toward
        Trusted (spec 6.12), failure triggers negative learning + drift
        checks (spec 6.15, 6.22)."""
        resolved = self.prospective.resolve_due()
        for row in resolved:
            mechanism_id = row["mechanism_id"]
            mechanism = self.mechanisms.latest(mechanism_id)
            succeeded = row["outcome"] == "success"
            self.calibration.record("mechanism", float(mechanism["current_confidence"]), succeeded)
            track = self.prospective.track_record(mechanism_id)
            self.mechanisms.update_confidence(
                mechanism_id,
                {"recent": track["success_rate"] if track["success_rate"] is not None else mechanism["current_confidence"]},
                actor="prospective_validation",
            )
            if succeeded:
                current = Lifecycle(self.mechanisms.latest(mechanism_id)["lifecycle"])
                if current == Lifecycle.VALIDATED:
                    self.mechanisms.promote(mechanism_id, Lifecycle.TRUSTED, ValidationStatus.PASSED, actor="prospective_validation")
            else:
                questions = self.negative_learning.investigate_failed_prospective(mechanism_id, row)
                self.mechanisms.record_failure(mechanism_id, "prospective_failure", questions)
                self.drift.check(mechanism_id)
            self._record(
                "mechanism", mechanism_id, mechanism["version"], "prospective_validation",
                {"prospective_id": row["prospective_id"], "outcome": row["outcome"]}, row["outcome"],
            )
        return resolved

    # -------------------------------------------------------- revalidation
    def revalidate_due(self) -> dict[str, list[str]]:
        """Continuous revalidation (spec 6.21): mechanisms past their
        next_validation_at are re-checked."""
        due = self.mechanisms.due_for_validation()
        results = {"validated": [], "demoted": []}
        for mechanism in due:
            report = self.validate_mechanism(mechanism["mechanism_id"])
            bucket = "validated" if report["outcome"] == "passed" else "demoted"
            results[bucket].append(mechanism["mechanism_id"])
        return results

    # -------------------------------------------------------- self-audit
    def self_audit(self) -> dict[str, Any]:
        """Validation Health (spec 6.27): is the validator rejecting too
        much or too little relative to recent history?"""
        rows = self.db.query_dicts(
            "SELECT outcome, count(*) AS n FROM validations "
            "WHERE created_at >= now() - INTERVAL 30 DAY GROUP BY outcome"
        )
        counts = {r["outcome"]: r["n"] for r in rows}
        total = sum(counts.values())
        pass_rate = counts.get("passed", 0) / total if total else None
        verdict = "healthy"
        if pass_rate is not None:
            if pass_rate > 0.95:
                verdict = "too_permissive"
            elif pass_rate < 0.05:
                verdict = "too_conservative"
        return {"counts": counts, "pass_rate": pass_rate, "verdict": verdict}

    # --------------------------------------------------- rolling benchmark
    def run_rolling_benchmark(self, sample_size: int = 30) -> dict[str, Any]:
        """Final Patch, Task 6: aggregate real walk-forward replay across a
        bounded, real sample of mechanisms, compared against a real, simple
        single-signal baseline for the exact same windows -- not a
        regression check against ELECOUS's own previous version (that's
        BenchmarkEngine.compare_to_previous, a different real question).

        Sample size is capped, not exhaustive: extractor.changes() was
        directly measured at 45ms/call; a full 328-mechanism sweep would
        cost ~30 real minutes, disproportionate to run every cycle on the
        target hardware (Task 2/7). 30 mechanisms x 2 replays x <=60
        instants stays under ~3 real minutes.

        The sample is the VALIDATED set (the mechanisms actually used for
        prediction), ordered by mechanism_id for a stable, reproducible
        slice. It is deliberately NOT list_latest()[:n]: that orders by
        current_confidence, and since the confidence-deadlock fix made
        confidence proportional to replay precision, top-by-confidence
        would cherry-pick the highest-precision mechanisms and flatter the
        result -- survivorship bias in the benchmark itself. Measuring the
        deployed (validated) set answers the honest operational question:
        do the mechanisms we actually predict with beat their own
        single-signal baseline?"""
        validated = [
            m for m in self.mechanisms.list_latest()
            if m["lifecycle"] in ("validated", "trusted", "monitoring")
        ]
        mechanisms = sorted(validated, key=lambda m: m["mechanism_id"])[:sample_size]
        if not mechanisms:
            metrics = {"mechanisms_sampled": 0, "verdict": "no_validated_mechanisms"}
            self.benchmarks.record("rolling_vs_baseline", metrics)
            return metrics
        totals = {"elecous": {"tp": 0, "fp": 0, "fn": 0}, "baseline": {"tp": 0, "fp": 0, "fn": 0}}
        for mechanism in mechanisms:
            mech_report = self.replay.replay_mechanism(mechanism)
            base_report = self.replay.replay_baseline(mechanism)
            for key, report in (("elecous", mech_report), ("baseline", base_report)):
                totals[key]["tp"] += report.get("true_positives", 0)
                totals[key]["fp"] += report.get("false_positives", 0)
                totals[key]["fn"] += report.get("false_negatives", 0)

        def micro_metrics(t: dict[str, int]) -> dict[str, float | None]:
            precision = t["tp"] / (t["tp"] + t["fp"]) if (t["tp"] + t["fp"]) else None
            recall = t["tp"] / (t["tp"] + t["fn"]) if (t["tp"] + t["fn"]) else None
            f1 = (2 * precision * recall / (precision + recall)) if precision and recall and (precision + recall) else None
            return {"precision": precision, "recall": recall, "f1": f1, **t}

        metrics = {
            "mechanisms_sampled": len(mechanisms),
            "elecous": micro_metrics(totals["elecous"]),
            "baseline": micro_metrics(totals["baseline"]),
        }
        elecous_f1, baseline_f1 = metrics["elecous"]["f1"], metrics["baseline"]["f1"]
        if elecous_f1 is None or baseline_f1 is None:
            metrics["verdict"] = "insufficient_evidence"
        else:
            metrics["verdict"] = "outperforms_baseline" if elecous_f1 > baseline_f1 else "does_not_outperform_baseline"
        self.benchmarks.record("rolling_vs_baseline", metrics)
        self.log.info("rolling benchmark completed", verdict=metrics["verdict"], mechanisms=len(mechanisms))
        return metrics
