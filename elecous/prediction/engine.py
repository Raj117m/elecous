"""Prediction Engine facade (spec ch. 8).

Continuously compares the live World State against every Trusted (and
Validated/Monitoring) mechanism, forms explainable Demand Opportunities with
quantified uncertainty, scores and ranks them, and records every prediction
as a tracked, replayable experiment. It never invents evidence and never
promotes mechanisms — it only *applies* validated knowledge (8.1-8.2).
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
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
        from elecous.demand.ecosystem import ProductEcosystem
        from elecous.validation.calibration.calibration import CalibrationEngine
        # Calibration is applied at prediction time and recomputed at
        # resolution time — the loop the roadmap's D4 audit found dead.
        self.calibration = CalibrationEngine(self.databases, self.config)
        # Per-product force ecosystems learn from every judged product claim.
        self.ecosystem = ProductEcosystem(self.databases, self.config)

    # ------------------------------------------------------------ predict
    def predict(self, as_of: datetime | None = None) -> list[str]:
        """One full prediction cycle against the current (or historical, for
        replay) World State. Returns created opportunity ids."""
        state = self.world.build(as_of=as_of) if as_of else (self.world.latest_state() or self.world.build())
        # Predict on CHANGE, not on clock (roadmap R5): an unchanged world
        # state cannot produce new evidence, so re-running would only churn
        # identical opportunity versions every scheduler tick. Historical
        # replay (as_of) always runs — it is a different question.
        if as_of is None:
            if state["state_id"] == getattr(self, "_last_predicted_state_id", None):
                return []
            self._last_predicted_state_id = state["state_id"]
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
        pending = self._pending_by_categories()
        category_groups = list(by_category.values())
        for activated in category_groups:
            scores = self.scoring.opportunity_scores(activated, historical_success)
            cats = sorted({c for a in activated for c in json.loads(a["mechanism"]["affected_categories"])})
            # DPI v1 removed from the live path 2026-07-08: measured on 400
            # real episodes it ranked the about-to-spike entity at the 50.8th
            # percentile — chance level — while a trivial recency baseline hit
            # 67.9 (scripts/validate_dpi.py, docs/audit/dpi_validation_*.json).
            # The module stays for an evidence-grounded v2; nothing that
            # measures at random gets to burn compute in every cycle.

            confidence = self._prediction_confidence(activated, state)
            conflicts = sum(
                1
                for other in category_groups
                if other is not activated
                and set(json.loads(activated[0]["mechanism"]["environmental_factors"]))
                and not (set(json.loads(activated[0]["mechanism"]["environmental_factors"]))
                & {f for e in other for f in json.loads(e["mechanism"]["environmental_factors"])})
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

            # Evolve, never duplicate (roadmap R1): if a pending prediction
            # already covers this exact category set and its judgment window
            # is still open, update it with the fresh evidence instead of
            # re-minting a copy every scheduler tick.
            existing = pending.get(frozenset(cats))
            if existing is not None:
                self._evolve_prediction(existing, state["state_id"], activated, confidence, uncertainty, scores, business_value)
                continue

            # Emission gate (roadmap R2): fewer, more reliable. New claims
            # below the calibrated-confidence bar are not emitted at all —
            # measured cause: recall 1.0 / precision below baseline came from
            # emitting every match. Evolution above is exempt: updating an
            # existing claim with weaker evidence is honest information.
            min_emit = float(self.config.load_file("thresholds.yaml")["prediction"].get("min_emit_confidence", 0.0))
            if confidence["overall"] < min_emit:
                self.log.info(
                    "prediction suppressed below emission gate",
                    categories=cats, confidence=confidence["overall"], gate=min_emit,
                )
                continue

            opportunity_id = self.opportunities.form(
                state["state_id"], activated, scores, confidence, uncertainty, business_value
            )
            self._record_prediction(state["state_id"], opportunity_id, activated, confidence)
            created.append(opportunity_id)

        self.opportunities.detect_conflicts([self.opportunities.latest(o) for o in created])
        self.opportunities.expire_superseded()
        return created

    def _pending_by_categories(self, tolerance_days: float = 3.0) -> dict[frozenset, dict[str, Any]]:
        """Pending predictions whose judgment window is still open, keyed by
        their opportunity's exact category set. Windows already past their
        deadline are left for resolve_due — evolving them would let a
        prediction dodge falsification."""
        now = timeutil.utcnow()
        out: dict[frozenset, dict[str, Any]] = {}
        for row in self.db.query_dicts("SELECT * FROM predictions WHERE outcome IS NULL ORDER BY created_at"):
            deadline = timeutil.ensure_utc(row["created_at"]) + timedelta(
                days=float(row["lead_time_days"] or 14.0) + tolerance_days
            )
            if now >= deadline:
                continue
            opportunity = self.opportunities.latest(row["opportunity_id"])
            if opportunity is None:
                continue
            out[frozenset(json.loads(opportunity["categories"]))] = row
        return out

    def _evolve_prediction(
        self,
        existing: dict[str, Any],
        world_state_id: str,
        activated: list[dict[str, Any]],
        confidence: dict[str, float],
        uncertainty: dict[str, Any],
        scores: dict[str, float],
        business_value: dict[str, Any],
    ) -> None:
        deltas = self.opportunities.evolve(
            existing["opportunity_id"], world_state_id, activated, scores, confidence, uncertainty, business_value
        )
        lead_times = [a["lead_time"]["most_probable"] for a in activated if a["lead_time"].get("most_probable")]
        new_lead = sum(lead_times) / len(lead_times) if lead_times else None
        old_lead = existing["lead_time_days"]
        # Timing may tighten but never extend: the judgment deadline set at
        # creation is the falsifiability anchor (roadmap R1 design rule).
        if new_lead is not None and old_lead is not None:
            new_lead = min(float(old_lead), float(new_lead))
        elif new_lead is None:
            new_lead = old_lead
        evidence = {
            "matched_signals": [a["match"].matched_signals for a in activated],
            "behavioural_chains": [json.loads(a["mechanism"]["behavioural_chain"]) for a in activated],
        }
        self.db.execute(
            "UPDATE predictions SET confidence = ?, lead_time_days = ?, evidence = ?, "
            "revisions = COALESCE(revisions, 0) + 1, updated_at = ? WHERE prediction_id = ?",
            [confidence["overall"], new_lead, json.dumps(evidence), timeutil.utcnow(), existing["prediction_id"]],
        )
        self.log.info(
            "prediction evolved",
            prediction_id=existing["prediction_id"],
            confidence=confidence["overall"],
            confidence_delta=round(confidence["overall"] - float(existing["confidence"]), 4),
            probability_delta=deltas["probability_delta"],
        )

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
        raw_overall = round(0.3 * mech_conf + 0.3 * world_match + 0.25 * prospective + 0.15 * factor_conf, 4)
        # Stated confidence passes through the observed-reality curve built
        # from resolved outcomes (identity until >=5 samples exist per bin).
        overall = self.calibration.calibrate("prediction", raw_overall)
        
        # Calculate sigma_p (Confidence Confidence)
        if len(activated) > 1:
            variance = sum((float(a["mechanism"]["current_confidence"]) - mech_conf)**2 for a in activated) / (len(activated) - 1)
            sigma_p = round(variance**0.5, 4)
        else:
            sigma_p = 0.1 # Default baseline uncertainty if only 1 mechanism

        return {
            "mechanism_confidence": round(mech_conf, 4),
            "environmental_factor_confidence": round(factor_conf, 4),
            "world_match": round(world_match, 4),
            "prospective_validation_history": round(prospective, 4),
            "sigma_p": sigma_p,
            "raw_overall": raw_overall,
            "overall": overall,
        }

    def _business_value(self, activated: list[dict[str, Any]]) -> dict[str, Any]:
        values = []
        for a in activated:
            bv = a["mechanism"].get("business_value")
            if isinstance(bv, str) and bv:
                values.append(json.loads(bv))
        attention = [v.get("attention_volume_median") for v in values if v.get("attention_volume_median")]
        median_vol = sum(attention) / len(attention) if attention else 100.0
        
        expected_roi = round(median_vol / 5.0, 2)
        risk = 0.15
        capital = 2.0
        
        # Urgency is higher for shorter lead times
        lead_time_val = 7.0
        if activated and activated[0].get("lead_time") and activated[0]["lead_time"].get("most_probable") is not None:
            lead_time_val = float(activated[0]["lead_time"]["most_probable"])
        urgency = round(50.0 / max(1.0, lead_time_val), 2)

        return {
            "attention_volume_median": round(median_vol, 1),
            "historical_opportunities": sum(v.get("historical_opportunities", 0) for v in values),
            "commercial_importance": "high" if median_vol > 1000 else "moderate" if median_vol > 100 else "unknown",
            "expected_roi": expected_roi,
            "risk": risk,
            "capital": capital,
            "urgency": urgency,
        }

    def _record_prediction(
        self, world_state_id: str, opportunity_id: str, activated: list[dict[str, Any]], confidence: dict[str, float]
    ) -> str:
        prediction_id = new_id("prediction")
        lead_times = [a["lead_time"]["most_probable"] for a in activated if a["lead_time"].get("most_probable")]
        lead = sum(lead_times) / len(lead_times) if lead_times else None
        opportunity = self.opportunities.latest(opportunity_id)
        # Every prediction states its own falsification criterion (roadmap R2):
        # what exactly must happen, for what, by when — so no claim can
        # retreat into vagueness when judged.
        claim = {
            "categories": json.loads(opportunity["categories"]) if opportunity else [],
            "products": json.loads(opportunity["products"]) if opportunity else [],
            "window_days": (lead or 14.0) + 3.0,
            "criterion": (
                "correct iff every claimed category records >=1 genuine demand episode "
                "within window_days of creation; each claimed product judged by its own episodes"
            ),
        }
        # Named columns: the table has grown via additive migrations (t_pred,
        # t_spike, revisions, updated_at) — a positional INSERT here silently
        # broke prediction creation in production on 2026-07-08 when migration
        # 0002 landed without updating this statement.
        self.db.execute(
            "INSERT INTO predictions (prediction_id, world_state_id, opportunity_id, mechanisms_used, "
            "confidence, lead_time_days, evidence, outcome, validation_status, created_at, revisions) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                prediction_id,
                world_state_id,
                opportunity_id,
                json.dumps([{"id": a["mechanism"]["mechanism_id"], "version": a["mechanism"]["version"]} for a in activated]),
                confidence["overall"],
                lead,
                json.dumps(
                    {
                        "matched_signals": [a["match"].matched_signals for a in activated],
                        "behavioural_chains": [json.loads(a["mechanism"]["behavioural_chain"]) for a in activated],
                        "claim": claim,
                    }
                ),
                None,
                "pending",
                timeutil.utcnow(),
                0,
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

    # ------------------------------------------------------------ resolution
    def resolve_due(self, tolerance_days: float = 3.0) -> list[dict[str, Any]]:
        calibration = self.calibration
        now = timeutil.utcnow()
        resolved = []
        for row in self.db.query_dicts("SELECT * FROM predictions WHERE outcome IS NULL"):
            created = timeutil.ensure_utc(row["created_at"])
            horizon = float(row["lead_time_days"] or 14.0) + tolerance_days
            deadline = created + timedelta(days=horizon)
            if now < deadline:
                continue  # window still open -- too early to judge
                
            opportunity = self.opportunities.latest(row["opportunity_id"])
            categories = json.loads(opportunity["categories"]) if opportunity else []
            hit_categories = []
            actual_spike_times = []
            actual_magnitudes = []
            
            for category in categories:
                episodes = [
                    e for e in self.prospective.episodes.list_latest(category=category, include_controls=False)
                    if not e["is_negative"] and created <= timeutil.ensure_utc(e["spike_start"]) <= deadline
                ]
                if episodes:
                    hit_categories.append(category)
                    actual_spike_times.append(timeutil.ensure_utc(episodes[0]["spike_start"]))
                    actual_magnitudes.append(float(episodes[0].get("volume_multiplier", 1.0)))

            if not categories:
                outcome = "insufficient_evidence"
            elif len(hit_categories) == len(categories):
                outcome = "correct"
            elif hit_categories:
                outcome = "partially_correct"
            else:
                outcome = "incorrect"
                
            # Compute advanced metrics (Constitution Phase 4)
            P = float(row["confidence"])
            O = 1.0 if outcome == "correct" else (0.5 if outcome == "partially_correct" else 0.0)
            brier_score = round((P - O)**2, 4)
            
            timing_error = None
            if actual_spike_times and row["lead_time_days"]:
                predicted_time = created + timedelta(days=float(row["lead_time_days"]))
                avg_actual_time = actual_spike_times[0] # Simplification
                timing_error = round(abs((avg_actual_time - predicted_time).total_seconds() / 86400.0), 2)
                
            magnitude_error = None
            if actual_magnitudes:
                avg_magnitude = sum(actual_magnitudes) / len(actual_magnitudes)
                # Assume predicted magnitude was 1.0 for now unless we have a specific prediction
                magnitude_error = round(abs(avg_magnitude - 1.0), 2)

            detail = {
                "outcome": outcome,
                "resolved_at": timeutil.to_iso(now),
                "hit_categories": hit_categories,
                "categories": categories,
                "metrics": {
                    "brier_score": brier_score,
                    "timing_error_days": timing_error,
                    "magnitude_error": magnitude_error,
                    "causal_accuracy": O, # Simplified for now
                    "roi_actual": None # Pending financial integration
                }
            }

            # Product-level judgment (roadmap R2): every claimed product is
            # judged by its OWN episodes — per-product claims stopped being
            # unfalsifiable decoration when membership became evidence-based.
            claimed_products = json.loads(opportunity["products"]) if opportunity else []
            if claimed_products:
                detail["product_hits"] = {
                    p: any(
                        not e["is_negative"] and created <= timeutil.ensure_utc(e["spike_start"]) <= deadline
                        for e in self.prospective.episodes.list_latest(entity=p, include_controls=False)
                    )
                    for p in claimed_products
                }
                # Per-product force ecosystems (correction #4): every judged
                # product claim strengthens or weakens that product's links to
                # the force phenomena that were the claim's evidence.
                phenomena = sorted({
                    s.split(":")[0]
                    for am in json.loads(opportunity["evidence"]).get("activated_mechanisms", [])
                    for s in am.get("matched_signals", [])
                })
                if phenomena:
                    for product, hit in detail["product_hits"].items():
                        self.ecosystem.record_outcome(product, phenomena, hit)
                        self.ecosystem.refresh_associated_forces(product)

            # ------- close the learning loop (roadmap R1) -------
            # Each outcome flows back into the mechanisms that made the claim:
            # their prospective track record (read by every future prediction's
            # confidence), their 'recent' confidence channel, and — on failure —
            # a structured attribution naming exactly what went wrong.
            mechanism_outcomes = []
            if outcome != "insufficient_evidence":
                mechanism_outcomes = self._feed_mechanisms(
                    row, opportunity, categories, hit_categories, outcome, created, deadline, now
                )
                if outcome in ("incorrect", "partially_correct"):
                    detail["failure_attribution"] = self._attribute_failure(
                        opportunity, categories, hit_categories, mechanism_outcomes
                    )

            self.db.execute(
                "UPDATE predictions SET outcome = ?, validation_status = ? WHERE prediction_id = ?",
                [json.dumps(detail), outcome, row["prediction_id"]],
            )
            if outcome != "insufficient_evidence":
                calibration.record("prediction", P, outcome == "correct")
            self.bus.emit(
                EventType.PREDICTION_RESOLVED, source="prediction",
                prediction_id=row["prediction_id"], outcome=outcome, brier_score=brier_score,
            )
            resolved.append({"prediction_id": row["prediction_id"], **detail})

        if resolved:
            # Outcomes exist -> the confidence curve must reflect them now,
            # not whenever someone remembers to recompute (dead-loop fix, D4).
            calibration.recompute("prediction")
            calibration.recompute("mechanism")
            self.log.info("predictions resolved against reality", count=len(resolved))
        return resolved

    def _feed_mechanisms(
        self, row, opportunity, categories, hit_categories, outcome, created, deadline, now
    ) -> list[dict[str, Any]]:
        """Write each contributing mechanism's real outcome into the
        prospective track record and its 'recent' confidence channel."""
        activations = {}
        if opportunity:
            for am in json.loads(opportunity["evidence"]).get("activated_mechanisms", []):
                activations[am["mechanism_id"]] = am.get("activation", 0.0)
        results = []
        mechanisms_by_id = {m["mechanism_id"]: m for m in self.mechanisms.list_latest()}
        for used in json.loads(row["mechanisms_used"]):
            mech = mechanisms_by_id.get(used["id"])
            if mech is None:
                continue  # retired since prediction was made; nothing to update
            mech_cats = set(json.loads(mech["affected_categories"])) & set(categories)
            success = bool(mech_cats & set(hit_categories)) if mech_cats else outcome == "correct"
            self.prospective.db.execute(
                "INSERT INTO prospective_predictions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    new_id("validation"),
                    used["id"],
                    int(used["version"]),
                    sorted(mech_cats)[0] if mech_cats else (categories[0] if categories else "unknown"),
                    created,
                    round(float(activations.get(used["id"], 0.0)), 4),
                    deadline,
                    now,
                    "success" if success else "failure",
                    None,
                ],
            )
            old_recent = json.loads(mech["confidence"]).get("recent", float(mech["current_confidence"]))
            # Damped update: one outcome moves 'recent' 30% toward reality,
            # so a single fluke never whipsaws a mechanism's confidence.
            new_recent = round(0.7 * float(old_recent) + 0.3 * (1.0 if success else 0.0), 4)
            self.mechanisms.update_confidence(used["id"], {"recent": new_recent}, actor="prediction_resolution")
            self.calibration.record("mechanism", float(mech["current_confidence"]), success)
            if not success:
                self.mechanisms.record_failure(
                    used["id"],
                    "prediction_failure",
                    {
                        "prediction_id": row["prediction_id"],
                        "missed_categories": sorted(set(categories) - set(hit_categories)),
                        "activation_at_prediction": activations.get(used["id"]),
                    },
                )
            results.append({"mechanism_id": used["id"], "outcome": "success" if success else "failure"})
        return results

    @staticmethod
    def _attribute_failure(opportunity, categories, hit_categories, mechanism_outcomes) -> dict[str, Any]:
        """Structured answer to 'what exactly went wrong' (roadmap R1.5):
        which relationships failed, what evidence was present, and what the
        mechanisms themselves said was missing when the claim was made."""
        present: set[str] = set()
        missing: set[str] = set()
        if opportunity:
            for am in json.loads(opportunity["evidence"]).get("activated_mechanisms", []):
                present.update(am.get("matched_signals", []))
                missing.update(am.get("missing_signals", []))
        return {
            "missed_categories": sorted(set(categories) - set(hit_categories)),
            "failed_relationships": [m for m in mechanism_outcomes if m["outcome"] == "failure"],
            "signals_present_at_prediction": sorted(present),
            "signals_missing_at_prediction": sorted(missing),
        }
