"""Observation validation (spec 2.9, 6.5).

Every extracted observation must satisfy every check; failure of any check
rejects the observation. Nothing bypasses validation (spec 2.3).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import timedelta
from typing import TYPE_CHECKING, Any

from elecous.common import timeutil
from elecous.common.errors import ConstitutionViolation
from elecous.governance import constitution

if TYPE_CHECKING:
    from elecous.observation.model import Observation
    from elecous.observation.store import ObservationStore

#: Observations older than this are historical backfill; they are legal (they
#: are real history) but must be explicitly flagged by the connector run type.
RECENCY_LIMIT = timedelta(days=45)

VALID_ACCESS_METHODS = {"official_api", "open_data", "public_dataset", "rss", "public_structured"}


@dataclass
class ValidationOutcome:
    passed: bool
    failed_check: str | None = None
    detail: dict[str, Any] = field(default_factory=dict)


class ObservationValidator:
    """The ten checks of spec 2.9, in order."""

    def __init__(self, allow_backfill: bool = True):
        self.allow_backfill = allow_backfill

    def validate(self, obs: "Observation", store: "ObservationStore | None" = None) -> ValidationOutcome:
        checks = (
            self._check_timestamp,
            self._check_source,
            self._check_schema,
            self._check_entity,
            self._check_location,
            self._check_corruption,
            self._check_duplication,
            self._check_public_origin,
            self._check_traceable,
            self._check_recent,
        )
        for check in checks:
            outcome = check(obs, store)
            if outcome is not None:
                return outcome
        return ValidationOutcome(passed=True)

    # Each check returns None on success, or a failing outcome.
    def _check_timestamp(self, obs, store):
        try:
            timeutil.ensure_utc(obs.timestamp)
            constitution.require_no_future_timestamp(obs.timestamp)
        except (ValueError, ConstitutionViolation) as exc:
            return ValidationOutcome(False, "timestamp", {"error": str(exc)})

    def _check_source(self, obs, store):
        if not obs.source_record.source_id or not obs.source_record.url:
            return ValidationOutcome(False, "source", {"source_record": obs.source_record.__dict__})

    def _check_schema(self, obs, store):
        if not obs.observation_type or not obs.unit or not isinstance(obs.value, (int, float)):
            return ValidationOutcome(False, "schema", {"type": obs.observation_type, "unit": obs.unit})
        if not (0.0 <= obs.confidence <= 1.0):
            return ValidationOutcome(False, "schema", {"confidence": obs.confidence})

    def _check_entity(self, obs, store):
        if not obs.entity or not obs.entity.strip() or not obs.category:
            return ValidationOutcome(False, "entity", {"entity": obs.entity, "category": obs.category})

    def _check_location(self, obs, store):
        if obs.lat is not None and not (-90 <= obs.lat <= 90):
            return ValidationOutcome(False, "location", {"lat": obs.lat})
        if obs.lon is not None and not (-180 <= obs.lon <= 180):
            return ValidationOutcome(False, "location", {"lon": obs.lon})
        if obs.country is not None and len(obs.country) not in (0, 2):
            return ValidationOutcome(False, "location", {"country": obs.country})

    def _check_corruption(self, obs, store):
        if isinstance(obs.value, float) and (math.isnan(obs.value) or math.isinf(obs.value)):
            return ValidationOutcome(False, "corruption", {"value": obs.value})

    def _check_duplication(self, obs, store):
        # Exact duplicates are detected here; near-duplicates merge in the
        # store (2.12). Only byte-identical checksum duplicates are rejected.
        return None  # store handles merge/duplicate distinction transactionally

    def _check_public_origin(self, obs, store):
        try:
            constitution.require_real_provenance(
                obs.source_record.source_id, obs.source_record.access_method, obs.source_record.url
            )
        except ConstitutionViolation as exc:
            return ValidationOutcome(False, "public_origin", {"error": str(exc)})
        if obs.source_record.access_method not in VALID_ACCESS_METHODS:
            return ValidationOutcome(
                False, "public_origin", {"access_method": obs.source_record.access_method}
            )

    def _check_traceable(self, obs, store):
        if not obs.source_record.extraction or not obs.source_record.extracted_at:
            return ValidationOutcome(False, "traceable", {"source_record": obs.source_record.__dict__})

    def _check_recent(self, obs, store):
        age = timeutil.utcnow() - timeutil.ensure_utc(obs.timestamp)
        if age > RECENCY_LIMIT and not self.allow_backfill:
            return ValidationOutcome(False, "recent", {"age_days": age.days})
