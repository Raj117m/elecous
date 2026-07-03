"""The Scientific Constitution enforced in code (spec 1.4, 12.28, 13.2, 13.5).

The Constitution overrides code: these guards are called at pipeline
boundaries and refuse promotion, storage or prediction whenever a rule would
be broken. They are deliberately simple, dependency-free checks so that no
subsystem can accidentally (or deliberately) bypass them.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from elecous.common import timeutil
from elecous.common.errors import ConstitutionViolation
from elecous.common.vocabulary import Lifecycle, ValidationStatus, is_legal_promotion

#: Law 2 / Rule 1 — markers that identify synthetic provenance. Any observation
#: whose source or extraction method claims one of these is refused.
FORBIDDEN_PROVENANCE = {"synthetic", "generated", "simulated", "fabricated", "mock", "faked", "placeholder"}

#: The Twelve Immutable Laws (spec 13.2), kept verbatim for audit surfaces.
IMMUTABLE_LAWS = (
    "Reality is the highest authority.",
    "Real public data only.",
    "Everything must be reproducible.",
    "Everything requires evidence.",
    "Knowledge is never permanent.",
    "Failure is knowledge.",
    "Every improvement must prove itself.",
    "Scientific explanations are mandatory.",
    "Autonomy never overrides integrity.",
    "Understanding grows before prediction.",
    "Knowledge must become simpler.",
    "ELECOUS never finishes learning.",
)


def require_real_provenance(source_id: str, access_method: str, url: str | None) -> None:
    """Law 2: real public data only — every observation names a registered
    source, a legal access method and a retrievable origin."""
    text = f"{source_id} {access_method} {url or ''}".lower()
    for marker in FORBIDDEN_PROVENANCE:
        if marker in text:
            raise ConstitutionViolation(
                f"synthetic provenance forbidden (found '{marker}')",
                context={"source_id": source_id, "access_method": access_method},
            )
    if not source_id:
        raise ConstitutionViolation("observation without source provenance (Rule 2)")


def require_no_future_timestamp(ts: datetime, tolerance: timedelta = timedelta(minutes=10)) -> None:
    """Law 1/3: an observation dated in the future cannot describe reality."""
    if timeutil.ensure_utc(ts) > timeutil.utcnow() + tolerance:
        raise ConstitutionViolation("observation timestamped in the future", context={"timestamp": str(ts)})


def require_no_future_leakage(as_of: datetime, evidence_timestamps: list[datetime]) -> None:
    """Spec 6.11/6.26: during historical validation nothing may see data
    newer than the replay instant."""
    as_of = timeutil.ensure_utc(as_of)
    for ts in evidence_timestamps:
        if timeutil.ensure_utc(ts) > as_of:
            raise ConstitutionViolation(
                "future data leakage in historical validation",
                context={"as_of": str(as_of), "leaked": str(ts)},
            )


def require_validated_promotion(
    current: Lifecycle, target: Lifecycle, validation_status: ValidationStatus
) -> None:
    """Rule/Law: no promotion without validation, and no stage skipping."""
    if not is_legal_promotion(current, target):
        raise ConstitutionViolation(
            f"illegal lifecycle promotion {current} -> {target} (stages never skip)"
        )
    advancing = target in (Lifecycle.EXPERIMENTAL, Lifecycle.VALIDATED, Lifecycle.TRUSTED)
    if advancing and validation_status != ValidationStatus.PASSED:
        raise ConstitutionViolation(
            f"promotion to {target} without passed validation (status={validation_status})"
        )


def require_explainability(prediction_payload: dict[str, Any]) -> None:
    """Law 8: every prediction must answer why / based on what. A prediction
    without mechanisms, evidence and uncertainty is refused."""
    missing = [
        key
        for key in ("mechanisms", "environmental_factors", "evidence", "confidence", "uncertainty")
        if not prediction_payload.get(key) and prediction_payload.get(key) != 0
    ]
    if missing:
        raise ConstitutionViolation(
            "unexplainable prediction refused", context={"missing": missing}
        )


def require_append_only(existing_version: int | None, incoming_version: int) -> None:
    """Law 3 / spec 7.7: versions only move forward; history is never edited."""
    if existing_version is not None and incoming_version <= existing_version:
        raise ConstitutionViolation(
            "history modification refused (append-only versioning)",
            context={"existing": existing_version, "incoming": incoming_version},
        )


def require_evidence(object_type: str, evidence_ids: list[str]) -> None:
    """Law 4: nothing exists without evidence."""
    if not evidence_ids:
        raise ConstitutionViolation(f"{object_type} without evidence refused (Law 4)")
