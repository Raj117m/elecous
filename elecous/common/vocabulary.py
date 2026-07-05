"""The permanent scientific vocabulary of ELECOUS.

Spec 1.5, 7.24, 13.3: these definitions are frozen. Every subsystem uses the
same vocabulary forever. This module is the single place where the vocabulary
is encoded; no subsystem may define competing terms.
"""

from __future__ import annotations

from enum import StrEnum


class ObjectType(StrEnum):
    """Every scientific object ELECOUS can hold. Frozen by spec 13.3."""

    OBSERVATION = "observation"
    SOURCE = "source"
    CONNECTOR = "connector"
    SIGNAL = "signal"
    DEMAND_SIGNAL = "demand_signal"
    ENVIRONMENTAL_SIGNAL = "environmental_signal"
    ENVIRONMENTAL_FACTOR = "environmental_factor"
    WORLD_CONDITION = "world_condition"  # Appendix D layer
    RESEARCH_EPISODE = "research_episode"
    MECHANISM = "mechanism"
    KNOWLEDGE = "knowledge"
    PREDICTION = "prediction"
    OPPORTUNITY = "opportunity"
    VALIDATION = "validation"
    WORLD_STATE = "world_state"
    UNKNOWN = "unknown"  # Unknown Memory objects, spec 7.19 / 12.21
    # Level-2 scientific memory (rewrite directive): the system's experience
    # of doing research, distinct from RESEARCH_EPISODE (a demand event) and
    # KNOWLEDGE (a Level-1 fact).
    RESEARCH_QUESTION = "research_question"
    RESEARCH_EXPERIENCE = "research_experience"
    RESEARCH_LESSON = "research_lesson"


class Lifecycle(StrEnum):
    """The universal lifecycle ladder (spec 1.8, 4.7, 4.21, 5.20).

    Everything earns promotion. Nothing is assumed. Promotion between stages
    happens only through the Validation Engine.
    """

    CANDIDATE = "candidate"
    EXPERIMENTAL = "experimental"
    VALIDATED = "validated"
    TRUSTED = "trusted"
    MONITORING = "monitoring"
    DECLINING = "declining"
    RETIRED = "retired"
    ARCHIVED = "archived"


#: Legal lifecycle promotions. Nothing skips stages (spec 1.8).
LIFECYCLE_PROMOTIONS: dict[Lifecycle, tuple[Lifecycle, ...]] = {
    Lifecycle.CANDIDATE: (Lifecycle.EXPERIMENTAL, Lifecycle.RETIRED),
    Lifecycle.EXPERIMENTAL: (Lifecycle.VALIDATED, Lifecycle.RETIRED),
    Lifecycle.VALIDATED: (Lifecycle.TRUSTED, Lifecycle.DECLINING, Lifecycle.RETIRED),
    Lifecycle.TRUSTED: (Lifecycle.MONITORING, Lifecycle.DECLINING),
    Lifecycle.MONITORING: (Lifecycle.TRUSTED, Lifecycle.DECLINING),
    Lifecycle.DECLINING: (Lifecycle.EXPERIMENTAL, Lifecycle.RETIRED, Lifecycle.ARCHIVED),
    Lifecycle.RETIRED: (Lifecycle.ARCHIVED,),
    Lifecycle.ARCHIVED: (),
}


def is_legal_promotion(current: Lifecycle, target: Lifecycle) -> bool:
    return target in LIFECYCLE_PROMOTIONS.get(current, ())


class ValidationStatus(StrEnum):
    """Status of any object with respect to the Validation Engine (spec 6.18)."""

    PENDING = "pending"
    VALIDATING = "validating"
    PASSED = "passed"
    FAILED = "failed"
    INVESTIGATING = "investigating"
    ARCHIVED = "archived"


class Relationship(StrEnum):
    """Typed relationships of the knowledge graph (spec 7.15)."""

    SUPPORTS = "supports"
    CONTRADICTS = "contradicts"
    DEPENDS_ON = "depends_on"
    EXTENDS = "extends"
    SPECIALIZES = "specializes"
    GENERALIZES = "generalizes"
    REPLACES = "replaces"
    DERIVED_FROM = "derived_from"
    OBSERVED_BY = "observed_by"
    MEMBER_OF = "member_of"


class EpisodePhase(StrEnum):
    """Research timeline phases (spec 1.5 Research Episode, 3.14)."""

    BEFORE = "before"
    SIGNAL_FORMATION = "signal_formation"
    ENVIRONMENTAL_CHANGES = "environmental_changes"
    ACCELERATION = "acceleration"
    PEAK = "peak"
    DECLINE = "decline"
    STABILIZATION = "stabilization"
    BASELINE_RETURN = "baseline_return"


class OpportunityStage(StrEnum):
    """Opportunity lifecycle on the dashboard timeline (spec 8.10, 9.9)."""

    NOT_ACTIVE = "not_active"
    EARLY_FORMATION = "early_formation"
    EMERGING = "emerging"
    STRENGTHENING = "strengthening"
    PEAK = "peak_opportunity"
    DECLINING = "declining"
    EXPIRED = "expired"
    VALIDATED = "validated"
    REJECTED = "rejected"


class FailureCategory(StrEnum):
    """Failure classification (spec 11.21). Each category has a recovery strategy."""

    OBSERVATION = "observation_failure"
    CONNECTOR = "connector_failure"
    DATABASE = "database_failure"
    PREDICTION = "prediction_failure"
    VALIDATION = "validation_failure"
    KNOWLEDGE_INCONSISTENCY = "knowledge_inconsistency"
    SCHEDULER = "scheduler_failure"
    RESOURCE_EXHAUSTION = "resource_exhaustion"
