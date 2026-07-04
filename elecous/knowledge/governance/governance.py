"""Knowledge Governance (spec 7.25).

Permanent rules: no manual editing, no deleting history, no hidden
relationships, no synthetic knowledge, everything requires evidence and
remains traceable. These guards wrap the graph/memory write paths that are
reachable from outside the validated pipelines (e.g. dashboard or CLI tools)
so a human cannot inject unearned knowledge through a side door.
"""

from __future__ import annotations

from typing import Any

from elecous.common.errors import ConstitutionViolation


def require_traceable_node(object_type: str, object_ref: str, evidence_ids: list[str]) -> None:
    if not object_ref:
        raise ConstitutionViolation(f"{object_type} node without object reference")
    if object_type in ("environmental_factor", "mechanism") and not evidence_ids:
        raise ConstitutionViolation(f"{object_type} node without evidence links (Law 4)")


def forbid_manual_edit(actor: str) -> None:
    if actor in ("user", "human", "manual"):
        raise ConstitutionViolation("manual editing of knowledge is forbidden (spec 7.25)")


def require_relationship_declared(relationship: str, known: set[str]) -> None:
    if relationship not in known:
        raise ConstitutionViolation(f"undeclared relationship type: {relationship} (no hidden relationships)")
