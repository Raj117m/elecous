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
from elecous.prediction.schema import PREDICTION_MIGRATION
from elecous.storage.database import DatabaseManager, global_databases


class OpportunityStore:
    def __init__(self, databases: DatabaseManager | None = None, bus: EventBus | None = None, config: Config | None = None):
        self.databases = databases or global_databases()
        self.databases.register_schema(PREDICTION_MIGRATION)
        self.db = self.databases.get("predictions")
        self.bus = bus or global_bus()
        self.config = config or active_config()
        self.log = get_logger("prediction")

    def products_for(self, categories: list[str]) -> list[str]:
        topics = self.config.load_file("entities.yaml").get("topics", [])
        return [t["entity"] for t in topics if t.get("category") in categories]

    def form(
        self,
        world_state_id: str,
        activated: list[dict[str, Any]],  # [{mechanism, match, lead_time}]
        scores: dict[str, float],
        confidence: dict[str, float],
        uncertainty: dict[str, Any],
        business_value: dict[str, Any],
    ) -> str:
        categories = sorted({c for a in activated for c in json.loads(a["mechanism"]["affected_categories"])})
        products = self.products_for(categories)
        opportunity_id = new_id("opportunity")
        expiry_days = int(self.config.load_file("thresholds.yaml")["prediction"]["opportunity_expiry_days"])
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
        from datetime import timedelta

        self.db.execute(
            f"INSERT INTO opportunities VALUES ({', '.join('?' * 18)})",
            [
                opportunity_id,
                1,
                world_state_id,
                json.dumps(categories),
                json.dumps(products),
                json.dumps(evidence["activated_mechanisms"]),
                json.dumps(evidence["environmental_factors"]),
                json.dumps(evidence),
                json.dumps(scores),
                json.dumps(combined_lead_time),
                json.dumps(confidence),
                probability,
                json.dumps(uncertainty),
                json.dumps(business_value),
                json.dumps(sorted({c for a in activated for c in json.loads(a["mechanism"]["countries"])})),
                stage.value,
                timeutil.utcnow(),
                timeutil.utcnow() + timedelta(days=expiry_days),
            ],
        )
        self.bus.emit(
            EventType.OPPORTUNITY_UPDATED, source="prediction", opportunity_id=opportunity_id, stage=stage.value,
            probability=probability,
        )
        self.log.info("opportunity formed", opportunity_id=opportunity_id, categories=categories, probability=probability)
        return opportunity_id

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
        ]
        self.db.execute(
            f"INSERT INTO opportunities VALUES ({', '.join('?' * 18)})",
            [row["opportunity_id"], version] + [row[c] for c in columns[1:]],
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
