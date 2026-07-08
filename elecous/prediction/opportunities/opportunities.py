"""Opportunity formation, clustering, scoring and lifecycle (spec 8.3, 8.7-8.11, 8.14-8.18).

Activated mechanisms become Demand Opportunities. Products are members of an
opportunity via trait activation; opportunities with overlapping product
membership merge into a cluster (8.18). Independent scores are kept
separate — never merged into one mysterious number (8.8).
"""

from __future__ import annotations

import json
from typing import Any

from elecous.common import timeutil
from elecous.common.ids import new_id
from elecous.common.vocabulary import OpportunityStage
from elecous.infrastructure.config import Config, active_config
from elecous.infrastructure.event_bus import EventBus, EventType, global_bus
from elecous.infrastructure.logging import get_logger
from elecous.prediction.schema import (
    PREDICTION_MIGRATION,
    PREDICTION_MIGRATION_0002,
    PREDICTION_MIGRATION_0003,
    PREDICTION_MIGRATION_0004,
)
from elecous.storage.database import DatabaseManager, global_databases


class OpportunityStore:
    def __init__(self, databases: DatabaseManager | None = None, bus: EventBus | None = None, config: Config | None = None):
        self.databases = databases or global_databases()
        self.databases.register_schema(PREDICTION_MIGRATION)
        self.databases.register_schema(PREDICTION_MIGRATION_0002)
        self.databases.register_schema(PREDICTION_MIGRATION_0003)
        self.databases.register_schema(PREDICTION_MIGRATION_0004)
        self.db = self.databases.get("predictions")
        self.bus = bus or global_bus()
        self.config = config or active_config()
        self.log = get_logger("prediction")

    def products_for(self, categories: list[str]) -> list[str]:
        """Static catalog roster for the given categories (config membership,
        no evidence attached). UI/browse use only — never a prediction claim."""
        topics = self.config.load_file("entities.yaml").get("topics", [])
        return [t["entity"] for t in topics if t.get("category") in categories]

    def products_with_evidence(self, categories: list[str]) -> list[str]:
        """Products an opportunity may CLAIM (roadmap R2): only catalog members
        with at least one genuine demand episode of their own in these
        categories, ordered by real evidence strength (episode count, then
        recency). The old behaviour — copying the whole category roster onto
        every opportunity with identical scores — made per-product claims
        unfalsifiable decoration (mission audit, CONFIRMED finding)."""
        roster = set(self.products_for(categories))
        if not roster:
            return []
        try:
            demand_db = self.databases.get("demand")
            placeholders = ",".join("?" for _ in categories)
            rows = demand_db.query_dicts(
                f"""
                SELECT entity, count(*) AS n, max(spike_start) AS latest
                FROM research_episodes e
                WHERE version = (SELECT max(version) FROM research_episodes WHERE episode_id = e.episode_id)
                  AND category IN ({placeholders})
                  AND is_control = FALSE AND is_negative = FALSE
                GROUP BY entity ORDER BY n DESC, latest DESC
                """,
                list(categories),
            )
        except Exception:
            # Demand domain not initialized in this context (e.g. an isolated
            # prediction-only test): no evidence available -> claim no products.
            return []
        return [r["entity"] for r in rows if r["entity"] in roster]

    def _compose(
        self,
        activated: list[dict[str, Any]],  # [{mechanism, match, lead_time}]
        scores: dict[str, float],
        confidence: dict[str, float],
        uncertainty: dict[str, Any],
        business_value: dict[str, Any],
    ) -> dict[str, Any]:
        """Everything an opportunity row derives from its activated mechanisms,
        shared by form() (new opportunity) and evolve() (new version of an
        existing one) so both paths stay scientifically identical."""
        categories = sorted({c for a in activated for c in json.loads(a["mechanism"]["affected_categories"])})
        products = self.products_with_evidence(categories)
        lead_times = [a["lead_time"] for a in activated if a["lead_time"].get("most_probable") is not None]
        combined_lead_time = (
            {
                "earliest": min(lt["earliest"] for lt in lead_times),
                "most_probable": round(sum(lt["most_probable"] for lt in lead_times) / len(lead_times), 1),
                "latest": max(lt["latest"] for lt in lead_times),
                "confidence": round(sum(lt["confidence"] for lt in lead_times) / len(lead_times), 3),
            }
            if lead_times
            else {"earliest": None, "most_probable": None, "latest": None, "confidence": 0.0}
        )
        evidence = {
            "activated_mechanisms": [
                {
                    "mechanism_id": a["mechanism"]["mechanism_id"],
                    "mechanism_version": a["mechanism"]["version"],
                    "activation": a["match"].activation,
                    "matched_signals": a["match"].matched_signals,
                    "missing_signals": a["match"].missing_signals,
                    "behavioural_chain": json.loads(a["mechanism"]["behavioural_chain"]),
                }
                for a in activated
            ],
            "environmental_factors": sorted(
                {f for a in activated for f in json.loads(a["mechanism"]["environmental_factors"])}
            ),
        }
        probability = round(
            min(0.99, sum(a["match"].activation * float(a["mechanism"]["current_confidence"]) for a in activated) / len(activated)),
            4,
        ) if activated else 0.0
        stage = self._stage_for(probability, scores.get("overall", 0.0))

        expected_roi = business_value.get("expected_roi", 0.0)
        risk = business_value.get("risk", 0.0)
        capital = business_value.get("capital", 0.0)
        urgency = business_value.get("urgency", 0.0)

        opportunity_score = round((expected_roi * probability) - (risk * capital) + urgency, 2)

        sigma_p = confidence.get("sigma_p", 0.0)

        if sigma_p > 0.5:
            decision = "Review"
        elif opportunity_score >= 60:
            decision = "Act"
        elif opportunity_score >= 30:
            decision = "Prepare"
        elif opportunity_score >= 10:
            decision = "Monitor"
        else:
            decision = "Ignore"

        return {
            "categories": categories,
            "products": products,
            "evidence": evidence,
            "combined_lead_time": combined_lead_time,
            "probability": probability,
            "stage": stage,
            "opportunity_score": opportunity_score,
            "decision": decision,
            "regions": sorted({c for a in activated for c in json.loads(a["mechanism"]["countries"])}),
        }

    def _insert_version(
        self,
        opportunity_id: str,
        version: int,
        world_state_id: str,
        composed: dict[str, Any],
        scores: dict[str, float],
        confidence: dict[str, float],
        uncertainty: dict[str, Any],
        business_value: dict[str, Any],
        created_at,
        expires_at,
    ) -> None:
        t_dec = timeutil.utcnow() if composed["decision"] != "Ignore" else None
        self.db.execute(
            """
            INSERT INTO opportunities (
                opportunity_id, version, world_state_id, categories, products,
                activated_mechanisms, environmental_factors, evidence, scores, lead_time,
                confidence, probability, uncertainty, business_value, regions,
                stage, created_at, expires_at, opportunity_score, decision,
                t_dec, contributing_forces, certainty_score
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                opportunity_id,
                version,
                world_state_id,
                json.dumps(composed["categories"]),
                json.dumps(composed["products"]),
                json.dumps(composed["evidence"]["activated_mechanisms"]),
                json.dumps(composed["evidence"]["environmental_factors"]),
                json.dumps(composed["evidence"]),
                json.dumps(scores),
                json.dumps(composed["combined_lead_time"]),
                json.dumps(confidence),
                composed["probability"],
                json.dumps(uncertainty),
                json.dumps(business_value),
                json.dumps(composed["regions"]),
                composed["stage"].value,
                created_at,
                expires_at,
                composed["opportunity_score"],
                composed["decision"],
                t_dec,
                json.dumps(scores.get("dpi_forces", {})),
                scores.get("overall", 0.0),
            ],
        )

    def form(
        self,
        world_state_id: str,
        activated: list[dict[str, Any]],  # [{mechanism, match, lead_time}]
        scores: dict[str, float],
        confidence: dict[str, float],
        uncertainty: dict[str, Any],
        business_value: dict[str, Any],
    ) -> str:
        from datetime import timedelta

        composed = self._compose(activated, scores, confidence, uncertainty, business_value)
        opportunity_id = new_id("opportunity")
        expiry_days = int(self.config.load_file("thresholds.yaml")["prediction"]["opportunity_expiry_days"])
        now = timeutil.utcnow()
        self._insert_version(
            opportunity_id, 1, world_state_id, composed, scores, confidence, uncertainty, business_value,
            now, now + timedelta(days=expiry_days),
        )
        self.bus.emit(
            EventType.OPPORTUNITY_UPDATED, source="prediction", opportunity_id=opportunity_id,
            stage=composed["stage"].value, probability=composed["probability"],
        )
        self.log.info(
            "opportunity formed", opportunity_id=opportunity_id,
            categories=composed["categories"], probability=composed["probability"],
        )
        return opportunity_id

    def evolve(
        self,
        opportunity_id: str,
        world_state_id: str,
        activated: list[dict[str, Any]],
        scores: dict[str, float],
        confidence: dict[str, float],
        uncertainty: dict[str, Any],
        business_value: dict[str, Any],
    ) -> dict[str, Any]:
        """New version of an existing opportunity as fresh evidence arrives
        (roadmap R1: evolve, never duplicate). created_at and expires_at are
        preserved from the original — an opportunity cannot dodge expiry or
        judgment by refreshing itself. Returns the deltas so the caller can
        explain WHY the opportunity changed."""
        current = self.latest(opportunity_id)
        if current is None:
            raise KeyError(opportunity_id)
        composed = self._compose(activated, scores, confidence, uncertainty, business_value)
        version = int(current["version"]) + 1
        self._insert_version(
            opportunity_id, version, world_state_id, composed, scores, confidence, uncertainty, business_value,
            current["created_at"], current["expires_at"],
        )
        deltas = {
            "version": version,
            "probability": composed["probability"],
            "probability_delta": round(composed["probability"] - float(current["probability"]), 4),
            "stage": composed["stage"].value,
            "stage_was": current["stage"],
            "decision": composed["decision"],
            "decision_was": current["decision"],
        }
        self.bus.emit(
            EventType.OPPORTUNITY_UPDATED, source="prediction", opportunity_id=opportunity_id,
            stage=composed["stage"].value, probability=composed["probability"],
            probability_delta=deltas["probability_delta"],
        )
        self.log.info(
            "opportunity evolved", opportunity_id=opportunity_id, version=version,
            probability_delta=deltas["probability_delta"],
        )
        return deltas

    @staticmethod
    def _stage_for(probability: float, overall_score: float) -> OpportunityStage:
        combined = 0.5 * probability + 0.5 * overall_score
        if combined < 0.15:
            return OpportunityStage.NOT_ACTIVE
        if combined < 0.35:
            return OpportunityStage.EARLY_FORMATION
        if combined < 0.55:
            return OpportunityStage.EMERGING
        if combined < 0.75:
            return OpportunityStage.STRENGTHENING
        return OpportunityStage.PEAK

    def latest(self, opportunity_id: str) -> dict[str, Any] | None:
        rows = self.db.query_dicts(
            "SELECT * FROM opportunities WHERE opportunity_id = ? ORDER BY version DESC LIMIT 1", [opportunity_id]
        )
        return rows[0] if rows else None

    def update_stage(self, opportunity_id: str, stage: OpportunityStage) -> None:
        current = self.latest(opportunity_id)
        if current is None:
            raise KeyError(opportunity_id)
        version = int(current["version"]) + 1
        row = dict(current)
        row["stage"] = stage.value
        columns = [
            "opportunity_id", "world_state_id", "categories", "products", "activated_mechanisms",
            "environmental_factors", "evidence", "scores", "lead_time", "confidence", "probability",
            "uncertainty", "business_value", "regions", "stage", "created_at", "expires_at",
            "opportunity_score", "decision", "t_dec", "contributing_forces", "certainty_score"
        ]
        self.db.execute(
            """
            INSERT INTO opportunities (
                opportunity_id, version, world_state_id, categories, products,
                activated_mechanisms, environmental_factors, evidence, scores, lead_time,
                confidence, probability, uncertainty, business_value, regions,
                stage, created_at, expires_at, opportunity_score, decision,
                t_dec, contributing_forces, certainty_score
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [row["opportunity_id"], version] + [row.get(c) for c in columns[1:]],
        )
        self.bus.emit(EventType.OPPORTUNITY_UPDATED, source="prediction", opportunity_id=opportunity_id, stage=stage.value)

    def active(self) -> list[dict[str, Any]]:
        return self.db.query_dicts(
            "SELECT * FROM opportunities o WHERE version = "
            "(SELECT max(version) FROM opportunities WHERE opportunity_id = o.opportunity_id) "
            "AND expires_at > now() AND stage NOT IN ('expired', 'rejected') ORDER BY probability DESC"
        )

    def cluster(self, opportunities: list[dict[str, Any]], min_overlap: float = 0.5) -> list[list[str]]:
        """Opportunity Clustering (8.18): opportunities sharing most of their
        product membership become one cluster."""
        clusters: list[list[str]] = []
        assigned: set[str] = set()
        product_sets = {o["opportunity_id"]: set(json.loads(o["products"])) for o in opportunities}
        for opp in opportunities:
            if opp["opportunity_id"] in assigned:
                continue
            group = [opp["opportunity_id"]]
            assigned.add(opp["opportunity_id"])
            for other in opportunities:
                if other["opportunity_id"] in assigned:
                    continue
                a, b = product_sets[opp["opportunity_id"]], product_sets[other["opportunity_id"]]
                if not a or not b:
                    continue
                overlap = len(a & b) / len(a | b)
                if overlap >= min_overlap:
                    group.append(other["opportunity_id"])
                    assigned.add(other["opportunity_id"])
            clusters.append(group)
        return clusters

    def detect_conflicts(self, opportunities: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Opportunity Conflict Engine (8.15): opportunities sharing a
        category whose mechanisms disagree on direction (one's factors
        contradict the other's) become a recorded conflict."""
        conflicts = []
        for i, a in enumerate(opportunities):
            for b in opportunities[i + 1 :]:
                shared = set(json.loads(a["categories"])) & set(json.loads(b["categories"]))
                if not shared:
                    continue
                factors_a = set(json.loads(a["environmental_factors"]))
                factors_b = set(json.loads(b["environmental_factors"]))
                if factors_a and factors_b and not (factors_a & factors_b) and abs(a["probability"] - b["probability"]) > 0.3:
                    conflict_id = new_id("task")
                    self.db.execute(
                        "INSERT INTO opportunity_conflicts VALUES (?, ?, ?, ?, ?, ?)",
                        [
                            conflict_id,
                            a["opportunity_id"],
                            b["opportunity_id"],
                            f"disagreement in categories={sorted(shared)}",
                            json.dumps(
                                {"favored": a["opportunity_id"] if a["probability"] > b["probability"] else b["opportunity_id"]}
                            ),
                            timeutil.utcnow(),
                        ],
                    )
                    conflicts.append({"conflict_id": conflict_id, "a": a["opportunity_id"], "b": b["opportunity_id"]})
        return conflicts
