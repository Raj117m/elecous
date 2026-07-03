"""Error taxonomy (spec 11.20-11.21).

Every error is classified so recovery strategies can be selected per category.
Errors never silently disappear: every ElecousError carries the failure
category consumed by the self-healing pipeline (spec 12.4).
"""

from __future__ import annotations

from elecous.common.vocabulary import FailureCategory


class ElecousError(Exception):
    category: FailureCategory = FailureCategory.OBSERVATION

    def __init__(self, message: str, *, context: dict | None = None):
        super().__init__(message)
        self.context = context or {}


class ConstitutionViolation(ElecousError):
    """Raised when code attempts something the Scientific Constitution forbids
    (synthetic data, history modification, validation bypass...). Never caught
    and suppressed; a violation always stops the offending operation."""

    category = FailureCategory.VALIDATION


class ObservationError(ElecousError):
    category = FailureCategory.OBSERVATION


class ConnectorError(ElecousError):
    category = FailureCategory.CONNECTOR


class ConnectorSuspended(ConnectorError):
    """Source access conditions changed; connector suspended pending
    re-evaluation (spec 2.22)."""


class DatabaseError(ElecousError):
    category = FailureCategory.DATABASE


class PredictionError(ElecousError):
    category = FailureCategory.PREDICTION


class ValidationError(ElecousError):
    category = FailureCategory.VALIDATION


class KnowledgeInconsistency(ElecousError):
    category = FailureCategory.KNOWLEDGE_INCONSISTENCY


class SchedulerError(ElecousError):
    category = FailureCategory.SCHEDULER


class ResourceExhaustion(ElecousError):
    category = FailureCategory.RESOURCE_EXHAUSTION
