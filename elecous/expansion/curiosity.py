"""Scientific Curiosity Engine (spec 12.20, Appendix E).

Instead of waiting, ELECOUS asks "what don't I understand?" every cycle.
Unknown patterns become Research Goals recorded in the Unknown Repository
(Knowledge Memory's unknown_memory table, spec 7.19/12.21). This turns
ELECOUS from a passive monitor into an active researcher: it surfaces
mechanisms whose confidence is declining without explanation, factors
contradicted by negative episodes, and observation capabilities still
missing after repeated gap detection.
"""

from __future__ import annotations

import json
from typing import Any

from elecous.infrastructure.logging import get_logger


class CuriosityEngine:
    def __init__(self, knowledge, mechanisms, factors, gap_engine):
        self.knowledge = knowledge  # KnowledgeEngine
        self.mechanisms = mechanisms  # MechanismStore
        self.factors = factors  # FactorStore
        self.gap_engine = gap_engine  # ObservationGapEngine
        self.log = get_logger("expansion")

    def one_hourly_question(self) -> dict[str, Any]:
        """Appendix E: 'If I had to make one scientifically useful discovery
        today, what would it be?' Ranks all currently open unknowns plus
        newly surfaced ones and returns the single top priority."""
        self.surface_unknowns()
        agenda = self.knowledge.research_agenda()
        top = agenda[0] if agenda else None
        return {"agenda_size": len(agenda), "top_priority": top}

    def run_exploration(self) -> dict[str, Any]:
        """Supervisor dynamic trigger to run curiosity exploration."""
        return self.one_hourly_question()

    def surface_unknowns(self) -> int:
        surfaced = 0
        surfaced += self._surface_blind_spots()  # Prioritize Constitution Phase 5: Known Unknowns
        surfaced += self._surface_declining_mechanisms()
        surfaced += self._surface_contradicted_factors()
        surfaced += self._surface_persistent_gaps()
        surfaced += self._surface_recent_failed_validations()
        return surfaced

    def _surface_blind_spots(self) -> int:
        """Phase 5: Expose Known Unknowns (Blind Spots)."""
        demand_db = self.knowledge.databases.get("demand")
        rows = demand_db.query_dicts(
            "SELECT blind_spot_id, entity, signal_type, coverage_metrics FROM blind_spots "
            "WHERE status = 'pending' ORDER BY generated_at DESC LIMIT 50"
        )
        count = 0
        for row in rows:
            existing = self.knowledge.memory.db.query_one(
                "SELECT 1 FROM unknown_memory WHERE kind = 'blind_spot' AND context LIKE ? AND status = 'open'",
                [f'%{row["blind_spot_id"]}%'],
            )
            if existing:
                continue
            self.knowledge.record_unknown(
                "blind_spot",
                f"Blind spot on entity '{row['entity']}' for '{row['signal_type']}'. Coverage deficient.",
                {"blind_spot_id": row["blind_spot_id"], "metrics": row["coverage_metrics"]},
                research_priority=0.9,  # Highest priority to resolve known unknowns
            )
            count += 1
        return count

    def _surface_recent_failed_validations(self) -> int:
        """Active question generation: a failed validation is itself an
        unexplained result worth investigating, not just a dead end."""
        validation_db = self.knowledge.databases.get("validation")
        rows = validation_db.query_dicts(
            "SELECT validation_id, object_type, object_id, method FROM validations "
            "WHERE outcome = 'failed' ORDER BY created_at DESC LIMIT 20"
        )
        count = 0
        for row in rows:
            existing = self.knowledge.memory.db.query_one(
                "SELECT 1 FROM unknown_memory WHERE kind = 'failed_validation' AND context LIKE ? AND status = 'open'",
                [f'%{row["validation_id"]}%'],
            )
            if existing:
                continue
            self.knowledge.record_unknown(
                "failed_validation",
                f"Why did {row['method']} fail for {row['object_type']} {row['object_id']}?",
                {"validation_id": row["validation_id"], "object_type": row["object_type"], "object_id": row["object_id"]},
                research_priority=0.55,
            )
            count += 1
        return count

    def _surface_declining_mechanisms(self) -> int:
        count = 0
        for mechanism in self.mechanisms.list_latest(lifecycle="declining"):
            existing = self.knowledge.memory.db.query_one(
                "SELECT 1 FROM unknown_memory WHERE kind = 'unknown_mechanism' AND context LIKE ? AND status = 'open'",
                [f'%{mechanism["mechanism_id"]}%'],
            )
            if existing:
                continue
            self.knowledge.record_unknown(
                "unknown_mechanism",
                f"Why is mechanism '{mechanism['name']}' declining?",
                {"mechanism_id": mechanism["mechanism_id"], "confidence": mechanism["current_confidence"]},
                research_priority=0.7,
            )
            count += 1
        return count

    def _surface_contradicted_factors(self) -> int:
        count = 0
        for factor in self.factors.list_latest():
            contradictory = json.loads(factor["contradictory_episodes"])
            supporting = json.loads(factor["supporting_episodes"])
            if not contradictory or not supporting:
                continue
            contradiction_ratio = len(contradictory) / (len(contradictory) + len(supporting))
            if contradiction_ratio < 0.3:
                continue
            existing = self.knowledge.memory.db.query_one(
                "SELECT 1 FROM unknown_memory WHERE kind = 'unexplained_opportunity' AND context LIKE ? AND status = 'open'",
                [f'%{factor["factor_id"]}%'],
            )
            if existing:
                continue
            self.knowledge.record_unknown(
                "unexplained_opportunity",
                f"Factor '{factor['name']}' is contradicted in {len(contradictory)} of "
                f"{len(contradictory) + len(supporting)} episodes — what's different about them?",
                {"factor_id": factor["factor_id"], "contradiction_ratio": round(contradiction_ratio, 3)},
                research_priority=round(0.5 + 0.4 * contradiction_ratio, 3),
            )
            count += 1
        return count

    def _surface_persistent_gaps(self) -> int:
        count = 0
        for request in self.gap_engine.open_requests():
            existing = self.knowledge.memory.db.query_one(
                "SELECT 1 FROM unknown_memory WHERE kind = 'missing_observations' AND context LIKE ? AND status = 'open'",
                [f'%{request["request_id"]}%'],
            )
            if existing:
                continue
            self.knowledge.record_unknown(
                "missing_observations",
                f"No healthy source observes '{request['capability']}' ({request['reason']})",
                {"request_id": request["request_id"], "capability": request["capability"]},
                research_priority=0.6,
            )
            count += 1
        return count
