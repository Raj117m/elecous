"""Candidate Concept Discovery (rewrite directive, Stage 6).

State discovery (Stage 4) clusters factors that already share known
phenomena into named states. It says nothing about the phenomena a
mechanism carries that are NOT part of its state -- the real, recurring
residue current vocabulary cannot describe. This module finds that residue,
clusters it across real mechanisms (reusing the exact same complete-linkage
primitive Stage 4 already built, per Task 8/9 -- no parallel algorithm),
and tracks it as a provisional "CONCEPT-NNN" object with its own evidence,
contradictions, confidence history, and transferability -- never a
permanent name, never asserted without repeated real evidence.
"""

from __future__ import annotations

import json
from typing import Any

from elecous.common import timeutil
from elecous.common.ids import new_id
from elecous.environment.ontology.ontology import Ontology
from elecous.environment.schema import CONCEPT_MIGRATION, ENVIRONMENT_MIGRATION
from elecous.environment.signal_discovery.discovery import EpisodeSignalDiscovery
from elecous.environment.state_discovery import (
    STATE_SIMILARITY_THRESHOLD,
    _jaccard,
    complete_linkage_cluster,
    dominant_phenomena,
    is_coherent,
)
from elecous.infrastructure.config import Config, active_config
from elecous.storage.database import DatabaseManager, global_databases
# Same family the Skeptic Engine already declares real data cannot test --
# reused as-is for "unknowns" on a concept (Task 7/9: one real fact, not
# redeclared).
from elecous.validation.skeptic.skeptic import SkepticEngine

#: Two candidate concepts are near-duplicates worth compressing into one
#: (Task 5) at a stricter bar than state formation, since a concept is
#: already a residual, not raw evidence.
CONCEPT_MERGE_THRESHOLD = 0.7

#: Below this confidence, a candidate concept is retired (Task 2/7) --
#: same real bar Stage 5 uses for state retirement, for one consistent
#: standard across the whole reasoning layer.
MIN_CONCEPT_CONFIDENCE = 0.1


class ConceptDiscoveryEngine:
    def __init__(self, databases: DatabaseManager | None = None, config: Config | None = None):
        self.databases = databases or global_databases()
        self.databases.register_schema(ENVIRONMENT_MIGRATION)
        self.databases.register_schema(CONCEPT_MIGRATION)
        self.db = self.databases.get("environment")
        self.config = config or active_config()
        self.ontology = Ontology(self.config)

    # -------------------------------------------------------- Task 1 / 6
    def discover(self, mechanisms: list[dict[str, Any]], states: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Task 1/6: cluster real, recurring UNEXPLAINED residual evidence
        across mechanisms -- signals present in a mechanism's real evidence
        that its assigned state's dominant phenomena do not cover, or the
        mechanism's entire signal set when it has no state at all. Declining
        mechanisms (real validation failures) are included deliberately --
        Task 6 wants failures to drive concept invention, not just retire
        silently."""
        state_by_id = {s["state_id"]: s for s in states}
        residuals: dict[str, set[str]] = {}
        for mechanism in mechanisms:
            signal_keys = json.loads(mechanism["supporting_signals"])
            phenomena = {EpisodeSignalDiscovery.phenomenon_of(k) for k in signal_keys}
            state_id = json.loads(mechanism["evidence_links"]).get("state_id")
            state = state_by_id.get(state_id)
            residual = phenomena - set(state["dominant_phenomena"]) if state else phenomena
            if residual:
                residuals[mechanism["mechanism_id"]] = residual
        if not residuals:
            return []

        clusters = complete_linkage_cluster(residuals, STATE_SIMILARITY_THRESHOLD)
        by_id = {m["mechanism_id"]: m for m in mechanisms}
        concepts = []
        for member_ids in clusters:
            if len(member_ids) < 2:
                continue  # a single mechanism's residue is not yet recurring evidence
            all_phenomena: list[str] = []
            for mid in member_ids:
                all_phenomena.extend(residuals[mid])
            dominant = dominant_phenomena(all_phenomena, len(member_ids))
            if not dominant or not is_coherent(dominant):
                continue
            # A mechanism whose real lifecycle already declined/retired is
            # evidence the residual pattern is associated with failure
            # (Task 6), not evidence supporting a healthy concept -- kept
            # in both origin tracking and its own contradicting list.
            contradicting = [mid for mid in member_ids if by_id[mid]["lifecycle"] in ("declining", "retired", "archived")]
            supporting = [mid for mid in member_ids if mid not in contradicting]
            origin = {
                "reason": "recurring unexplained residual across real mechanisms",
                "triggering_mechanisms": member_ids,
                "failure_driven": bool(contradicting),
            }
            concepts.append(self._upsert_concept(dominant, supporting, contradicting, origin))
        return concepts

    # -------------------------------------------------------- Task 2/3/4/5/7
    def evolve(self, mechanisms: list[dict[str, Any]], states: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Task 2/3/4/5/7: recompute every candidate concept's real support/
        contradictions, test cross-family transfer (Task 4), compete
        against the closest existing state for the same evidence (Task 3),
        merge near-duplicate concepts (Task 5), and retire ones that no
        longer earn confidence -- confidence HISTORY is appended, never
        overwritten."""
        by_id = {m["mechanism_id"]: m for m in mechanisms}
        concepts = self.list_all(status="candidate")
        self._merge_duplicates(concepts)
        concepts = self.list_all(status="candidate")

        results = []
        for concept in concepts:
            real_supporting = [mid for mid in concept["supporting_mechanism_ids"] if mid in by_id]
            real_contradicting = [
                mid for mid in concept["supporting_mechanism_ids"]
                if mid in by_id and by_id[mid]["lifecycle"] in ("declining", "retired", "archived")
            ]
            families: set[str] = set()
            for mid in real_supporting:
                for category in json.loads(by_id[mid]["affected_categories"]):
                    families.add(self.ontology.family_of(category))
            evidence_explained = len(real_supporting)
            competing_state, competing_evidence = self._best_competing_state(concept, states, by_id)
            competition_result = None
            if competing_state is not None:
                competition_result = "concept_wins" if evidence_explained > competing_evidence else (
                    "state_wins" if evidence_explained < competing_evidence else "tied"
                )
            contradiction_ratio = len(real_contradicting) / len(real_supporting) if real_supporting else 1.0
            transfer_bonus = 0.15 * min(1.0, max(0, len(families) - 1))
            confidence = max(0.0, min(1.0, 0.5 * (1.0 - contradiction_ratio) + transfer_bonus))
            status = "candidate" if confidence >= MIN_CONCEPT_CONFIDENCE else "retired"
            retirement_reason = (
                None if status == "candidate"
                else f"confidence {confidence:.3f} fell below {MIN_CONCEPT_CONFIDENCE} "
                     f"(contradiction_ratio={contradiction_ratio:.2f})"
            )
            results.append(self._persist_evolution(
                concept["concept_id"], real_supporting, real_contradicting, sorted(families),
                evidence_explained, competing_state["state_id"] if competing_state else None,
                competition_result, confidence, status, retirement_reason,
            ))
        return results

    def _best_competing_state(
        self, concept: dict[str, Any], states: list[dict[str, Any]], by_id: dict[str, Any]
    ) -> tuple[dict[str, Any] | None, int]:
        """Task 3: which existing state explains the most of the SAME real
        mechanisms this concept explains -- competition, not assertion."""
        concept_mechanisms = set(concept["supporting_mechanism_ids"])
        best, best_overlap = None, 0
        for state in states:
            state_mechanisms = {
                mid for mid, m in by_id.items()
                if json.loads(m["evidence_links"]).get("state_id") == state["state_id"]
            }
            overlap = len(concept_mechanisms & state_mechanisms)
            if overlap > best_overlap:
                best, best_overlap = state, overlap
        return best, best_overlap

    def _merge_duplicates(self, concepts: list[dict[str, Any]]) -> None:
        """Task 5: compress near-duplicate candidate concepts into one
        rather than letting slightly-different residual clusters multiply."""
        merged_ids: set[str] = set()
        for i, a in enumerate(concepts):
            if a["concept_id"] in merged_ids:
                continue
            for b in concepts[i + 1:]:
                if b["concept_id"] in merged_ids:
                    continue
                sim = _jaccard(set(a["residual_phenomena"]), set(b["residual_phenomena"]))
                if sim >= CONCEPT_MERGE_THRESHOLD:
                    combined_support = sorted(set(a["supporting_mechanism_ids"]) | set(b["supporting_mechanism_ids"]))
                    self.db.execute(
                        "UPDATE candidate_concepts SET supporting_mechanism_ids = ?, updated_at = ? WHERE concept_id = ?",
                        [json.dumps(combined_support), timeutil.utcnow(), a["concept_id"]],
                    )
                    self.db.execute(
                        "UPDATE candidate_concepts SET status = 'merged', retirement_reason = ?, updated_at = ? "
                        "WHERE concept_id = ?",
                        [f"merged into {a['label']} (jaccard={sim:.2f})", timeutil.utcnow(), b["concept_id"]],
                    )
                    merged_ids.add(b["concept_id"])

    # ------------------------------------------------------------- storage
    def _upsert_concept(
        self, dominant: list[str], supporting: list[str], contradicting: list[str], origin: dict[str, Any],
    ) -> dict[str, Any]:
        existing = self._find_by_residual(dominant)
        now = timeutil.utcnow()
        if existing:
            merged_support = sorted(set(existing["supporting_mechanism_ids"]) | set(supporting))
            merged_contra = sorted(set(existing["contradicting_mechanism_ids"]) | set(contradicting))
            self.db.execute(
                "UPDATE candidate_concepts SET supporting_mechanism_ids = ?, contradicting_mechanism_ids = ?, "
                "updated_at = ? WHERE concept_id = ?",
                [json.dumps(merged_support), json.dumps(merged_contra), now, existing["concept_id"]],
            )
            return self.get(existing["concept_id"])
        seq = self._next_sequence_number()
        concept_id = new_id("world_condition")
        label = f"CONCEPT-{seq:03d}"
        self.db.execute(
            "INSERT INTO candidate_concepts "
            "(concept_id, label, sequence_number, residual_phenomena, supporting_mechanism_ids, "
            "contradicting_mechanism_ids, origin, confidence, confidence_history, transferability_families, "
            "evidence_explained_count, competing_state_id, competition_result, unknowns, status, "
            "retirement_reason, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                concept_id, label, seq, json.dumps(dominant), json.dumps(supporting), json.dumps(contradicting),
                json.dumps(origin), 0.0, json.dumps([{"at": timeutil.to_iso(now), "value": 0.0}]),
                json.dumps([]), 0, None, None, json.dumps(list(SkepticEngine.UNTESTABLE_EXPLANATIONS)),
                "candidate", None, now, now,
            ],
        )
        return self.get(concept_id)

    def _persist_evolution(
        self, concept_id: str, supporting: list[str], contradicting: list[str], families: list[str],
        evidence_explained: int, competing_state_id: str | None, competition_result: str | None,
        confidence: float, status: str, retirement_reason: str | None,
    ) -> dict[str, Any]:
        existing = self.get(concept_id)
        now = timeutil.utcnow()
        history = existing["confidence_history"] + [{"at": timeutil.to_iso(now), "value": confidence}]
        self.db.execute(
            "UPDATE candidate_concepts SET supporting_mechanism_ids = ?, contradicting_mechanism_ids = ?, "
            "transferability_families = ?, evidence_explained_count = ?, competing_state_id = ?, "
            "competition_result = ?, confidence = ?, confidence_history = ?, status = ?, retirement_reason = ?, "
            "updated_at = ? WHERE concept_id = ?",
            [
                json.dumps(supporting), json.dumps(contradicting), json.dumps(families), evidence_explained,
                competing_state_id, competition_result, confidence, json.dumps(history), status,
                retirement_reason, now, concept_id,
            ],
        )
        return self.get(concept_id)

    def _next_sequence_number(self) -> int:
        row = self.db.query_one("SELECT max(sequence_number) FROM candidate_concepts")
        return (row[0] or 0) + 1

    def _find_by_residual(self, dominant: list[str]) -> dict[str, Any] | None:
        for concept in self.list_all():
            if set(concept["residual_phenomena"]) == set(dominant):
                return concept
        return None

    def get(self, concept_id: str) -> dict[str, Any] | None:
        rows = self.db.query_dicts("SELECT * FROM candidate_concepts WHERE concept_id = ?", [concept_id])
        return self._decode(rows[0]) if rows else None

    def list_all(self, status: str | None = None) -> list[dict[str, Any]]:
        sql, params = "SELECT * FROM candidate_concepts", []
        if status:
            sql += " WHERE status = ?"
            params.append(status)
        return [self._decode(r) for r in self.db.query_dicts(sql + " ORDER BY sequence_number", params)]

    @staticmethod
    def _decode(row: dict[str, Any]) -> dict[str, Any]:
        row = dict(row)
        for field in (
            "residual_phenomena", "supporting_mechanism_ids", "contradicting_mechanism_ids",
            "origin", "confidence_history", "transferability_families", "unknowns",
        ):
            row[field] = json.loads(row[field]) if row.get(field) else ([] if field != "origin" else {})
        return row
