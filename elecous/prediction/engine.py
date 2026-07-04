"""Prediction Engine facade (spec ch. 8).

Continuously compares the live World State against every Trusted (and
Validated/Monitoring) mechanism, forms explainable Demand Opportunities with
quantified uncertainty, scores and ranks them, and records every prediction
as a tracked, replayable experiment. It never invents evidence and never
promotes mechanisms — it only *applies* validated knowledge (8.1-8.2).
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from elecous.common import timeutil
from elecous.common.ids import new_id
from elecous.common.vocabulary import Lifecycle
from elecous.governance import constitution
from elecous.infrastructure.config import Config, active_config
from elecous.infrastructure.event_bus import EventBus, EventType, global_bus
from elecous.infrastructure.logging import get_logger
from elecous.prediction.lead_time.lead_time import LeadTimeEstimator
from elecous.prediction.opportunities.opportunities import OpportunityStore
from elecous.prediction.ranking.ranking import ScoringEngine
from elecous.prediction.schema import PREDICTION_MIGRATION
from elecous.prediction.uncertainty.uncertainty import UncertaintyEngine
from elecous.prediction.world_matching.matching import MechanismMatcher
from elecous.storage.database import DatabaseManager, global_databases

#: Lifecycles eligible to drive predictions (spec 1.8: only earned trust
#: participates; Candidate/Experimental mechanisms never predict).
PREDICTIVE_LIFECYCLES = (Lifecycle.VALIDATED.value, Lifecycle.TRUSTED.value, Lifecycle.MONITORING.value)


class PredictionEngine:
    def __init__(
        self,
        mechanisms,
        world,
        prospective,
        config: Config | None = None,
        databases: DatabaseManager | None = None,
        bus: EventBus | None = None,
    ):
        self.config = config or active_config()
        self.databases = databases or global_databases()
        self.databases.register_schema(PREDICTION_MIGRATION)
        self.db = self.databases.get("predictions")
        self.bus = bus or global_bus()
        self.log = get_logger("prediction")

        self.mechanisms = mechanisms
        self.world = world
        self.prospective = prospective  # ProspectiveValidator, for historical success rates

        self.matcher = MechanismMatcher(self.config)
        self.lead_time = LeadTimeEstimator()
        self.uncertainty = UncertaintyEngine()
        self.scoring = ScoringEngine(self.config)
        self.opportunities = OpportunityStore(self.databases, self.bus, self.config)

    # ------------------------------------------------------------ predict
    def predict(self, as_of: datetime | None = None) -> list[str]:
        """One full prediction cycle against the current (or historical, for
        replay) World State. Returns created opportunity ids."""
        state = self.world.build(as_of=as_of) if as_of else (self.world.latest_state() or self.world.build())
        mechanisms = [m for m in self.mechanisms.list_latest() if m["lifecycle"] in PREDICTIVE_LIFECYCLES]
        matches = self.matcher.match(mechanisms, state)
        if not matches:
            return []

        by_mechanism = {m["mechanism_id"]: m for m in mechanisms}
        historical_success = {
            m["mechanism_id"]: (self.prospective.track_record(m["mechanism_id"]).get("success_rate") or 0.5)
            for m in mechanisms
        }

        # Group matches by category so each opportunity represents one
        # coherent demand story (spec 8.7); a mechanism can serve multiple
        # categories and appears in each relevant group.
        by_category: dict[str, list[dict[str, Any]]] = {}
        for match in matches:
            mechanism = by_mechanism[match.mechanism_id]
            lead_time = self.lead_time.estimate(mechanism, match.activation)
            entry = {"mechanism": mechanism, "match": match, "lead_time": lead_time}
            for category in json.loads(mechanism["affected_categories"]):
                by_category.setdefault(category, []).append(entry)

        created = []
        category_groups = list(by_category.values())
        for activated in category_groups:
            scores = self.scoring.opportunity_scores(activated, historical_success)
            confidence = self._prediction_confidence(activated, state)
            conflicts = sum(
                1
                for other in category_groups
                if other is not activated
                and set(json.loads(activated[0]["mechanism"]["environmental_factors"]))
                and not set(json.loads(activated[0]["mechanism"]["environmental_factors"]))
                & {f for e in other for f in json.loads(e["mechanism"]["environmental_factors"])}
            )
            uncertainty = self.uncertainty.assess(activated[0]["match"], activated[0]["mechanism"], activated[0]["lead_time"], conflicts)
            business_value = self._business_value(activated)
            payload_for_governance = {
                "mechanisms": [a["mechanism"]["mechanism_id"] for a in activated],
                "environmental_factors": [f for a in activated for f in json.loads(a["mechanism"]["environmental_factors"])],
                "evidence": [a["match"].matched_signals for a in activated],
                "confidence": confidence.get("overall", 0),
                "uncertainty": uncertainty.get("overall", 0),
            }
            constitution.require_explainability(payload_for_governance)
            opportunity_id = self.opportunities.form(
                state["state_id"], activated, scores, confidence, uncertainty, business_value
            )
            self._record_prediction(state["state_id"], opportunity_id, activated, confidence)
            created.append(opportunity_id)

        self.opportunities.detect_conflicts([self.opportunities.latest(o) for o in created])
        return created

    def _prediction_confidence(self, activated: list[dict[str, Any]], state: dict[str, Any]) -> dict[str, float]:
        """Prediction Confidence components (spec 8.16)."""
        mech_conf = sum(float(a["mechanism"]["current_confidence"]) for a in activated) / len(activated)
        factor_conf = mech_conf  # factors already baked into mechanism confidence at creation
        world_match = sum(a["match"].activation for a in activated) / len(activated)
        prospective_rates = []
        for a in activated:
            record = self.prospective.track_record(a["mechanism"]["mechanism_id"])
            if record["success_rate"] is not None:
                prospective_rates.append(record["success_rate"])
        prospective = sum(prospective_rates) / len(prospective_rates) if prospective_rates else mech_conf
        overall = round(0.3 * mech_conf + 0.3 * world_match + 0.25 * prospective + 0.15 * factor_conf, 4)
        return {
            "mechanism_confidence": round(mech_conf, 4),
            "environmental_factor_confidence": round(factor_conf, 4),
            "world_match": round(world_match, 4),
            "prospective_validation_history": round(prospective, 4),
            "overall": overall,
        }

    def _business_value(self, activated: list[dict[str, Any]]) -> dict[str, Any]:
        values = []
        for a in activated:
            bv = a["mechanism"].get("business_value")
            if isinstance(bv, str) and bv:
                values.append(json.loads(bv))
        attention = [v.get("attention_volume_median") for v in values if v.get("attention_volume_median")]
        return {
            "attention_volume_median": round(sum(attention) / len(attention), 1) if attention else None,
            "historical_opportunities": sum(v.get("historical_opportunities", 0) for v in values),
            "commercial_importance": "high" if attention and max(attention) > 1000 else "moderate" if attention else "unknown",
        }

    def _record_prediction(
        self, world_state_id: str, opportunity_id: str, activated: list[dict[str, Any]], confidence: dict[str, float]
    ) -> str:
        prediction_id = new_id("prediction")
        lead_times = [a["lead_time"]["most_probable"] for a in activated if a["lead_time"].get("most_probable")]
        self.db.execute(
            "INSERT INTO predictions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                prediction_id,
                world_state_id,
                opportunity_id,
                json.dumps([{"id": a["mechanism"]["mechanism_id"], "version": a["mechanism"]["version"]} for a in activated]),
                confidence["overall"],
                sum(lead_times) / len(lead_times) if lead_times else None,
                json.dumps(
                    {
                        "matched_signals": [a["match"].matched_signals for a in activated],
                        "behavioural_chains": [json.loads(a["mechanism"]["behavioural_chain"]) for a in activated],
                    }
                ),
                None,
                "pending",
                timeutil.utcnow(),
            ],
        )
        self.bus.emit(EventType.PREDICTION_CREATED, source="prediction", prediction_id=prediction_id, opportunity_id=opportunity_id)
        return prediction_id

    # -------------------------------------------------------------- replay
    def replay(self, as_of: datetime) -> dict[str, Any]:
        """Prediction Replay (spec 8.23): reconstruct exactly what the
        platform believed at a past instant, using only observations that
        existed by then (constitution-enforced no-future-leakage)."""
        state = self.world.state_at(as_of)
        constitution.require_no_future_leakage(
            as_of, [timeutil.from_iso(s["first_crossed_at"]) for s in state["signals"] if s.get("first_crossed_at")]
        )
        mechanisms = [m for m in self.mechanisms.list_latest() if m["lifecycle"] in PREDICTIVE_LIFECYCLES]
        matches = self.matcher.match(mechanisms, state)
        return {
            "as_of": timeutil.to_iso(as_of),
            "world_state": state,
            "matches": [
                {"mechanism_id": m.mechanism_id, "activation": m.activation, "matched_signals": m.matched_signals}
                for m in matches
            ],
        }

    # ------------------------------------------------------------- lifecycle
    def advance_stages(self) -> int:
        """Move opportunities through their timeline stage-by-stage as the
        world evolves, and expire stale ones (spec 8.10, 8.20-8.21)."""
        from elecous.common.vocabulary import OpportunityStage

        moved = 0
        state = self.world.latest_state() or self.world.build()
        active_signals = {s["signal_key"] for s in state["signals"]}
        for opp in self.opportunities.active():
            evidence = json.loads(opp["evidence"])
            still_matched = any(
                set(m["matched_signals"]) & active_signals for m in evidence["activated_mechanisms"]
            )
            if not still_matched and opp["stage"] not in (OpportunityStage.DECLINING.value, OpportunityStage.EXPIRED.value):
                self.opportunities.update_stage(opp["opportunity_id"], OpportunityStage.DECLINING)
                moved += 1
            elif timeutil.ensure_utc(opp["expires_at"]) < timeutil.utcnow():
                self.opportunities.update_stage(opp["opportunity_id"], OpportunityStage.EXPIRED)
                moved += 1
        return moved
