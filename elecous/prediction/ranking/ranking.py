"""Product Ranking (spec 8.11) and Opportunity Scoring (spec 8.8).

Products inherit scores from opportunities via trait compatibility, category
relevance, geographic suitability and historical responsiveness. Opportunity
scores stay independent components, never a single opaque number.
"""

from __future__ import annotations

from typing import Any

from elecous.infrastructure.config import Config, active_config


class ScoringEngine:
    def __init__(self, config: Config | None = None):
        self.config = config or active_config()

    def opportunity_scores(
        self,
        activated: list[dict[str, Any]],
        historical_success: dict[str, float],
    ) -> dict[str, float]:
        """Independent scores (spec 8.8), each in [0,1]."""
        import json

        if not activated:
            return {"overall": 0.0}
        scientific_confidence = sum(float(a["mechanism"]["current_confidence"]) for a in activated) / len(activated)
        activation_score = sum(a["match"].activation for a in activated) / len(activated)
        mechanism_stability = sum(
            min(1.0, json.loads(a["mechanism"]["stability"]).get("age_days", 0) / 180.0) for a in activated
        ) / len(activated)
        history = sum(historical_success.get(a["mechanism"]["mechanism_id"], 0.5) for a in activated) / len(activated)
        evidence_completeness = sum(
            len(a["match"].matched_signals) / max(len(a["match"].matched_signals) + len(a["match"].missing_signals), 1)
            for a in activated
        ) / len(activated)
        overall = round(
            0.3 * scientific_confidence + 0.25 * activation_score + 0.2 * history
            + 0.15 * evidence_completeness + 0.1 * mechanism_stability,
            4,
        )
        return {
            "scientific_confidence": round(scientific_confidence, 4),
            "current_activation": round(activation_score, 4),
            "historical_success": round(history, 4),
            "evidence_completeness": round(evidence_completeness, 4),
            "mechanism_stability": round(mechanism_stability, 4),
            "overall": overall,
        }

    def rank_products(self, products: list[str], categories: list[str], scores: dict[str, float]) -> list[dict[str, Any]]:
        """Product ranking (8.11): every product in the opportunity's
        categories inherits the same evidence-based score; trait-level
        differentiation is available via configs/entities.yaml traits when a
        mechanism narrows to specific traits (product_traits on the
        mechanism), applied by the caller before this step if needed."""
        topics = {t["entity"]: t for t in self.config.load_file("entities.yaml").get("topics", [])}
        ranked = []
        for product in products:
            topic = topics.get(product, {})
            category_relevance = 1.0 if topic.get("category") in categories else 0.5
            ranked.append(
                {
                    "product": product,
                    "category": topic.get("category"),
                    "traits": topic.get("traits", []),
                    "score": round(scores.get("overall", 0.0) * category_relevance, 4),
                }
            )
        return sorted(ranked, key=lambda r: -r["score"])
