"""Scientific object base (spec 7.6, 11.16).

Every database object in ELECOUS includes: unique ID, version, created time,
updated time, source, confidence, validation status, lifecycle and an audit
trail. Objects are append-only: a change produces a new version row, never an
overwrite (spec 7.7, Law 3).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from elecous.common import timeutil
from elecous.common.ids import content_hash, new_id
from elecous.common.vocabulary import Lifecycle, ObjectType, ValidationStatus


@dataclass
class AuditEntry:
    timestamp: str
    actor: str  # subsystem name, never a human (spec 7.25: no manual editing)
    action: str
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass
class ScientificObject:
    """Base for every versioned scientific record."""

    object_type: ObjectType
    object_id: str = ""
    version: int = 1
    created_at: str = ""
    updated_at: str = ""
    source: str = ""  # producing subsystem or source id
    confidence: float = 0.0
    validation_status: ValidationStatus = ValidationStatus.PENDING
    lifecycle: Lifecycle = Lifecycle.CANDIDATE
    audit_trail: list[AuditEntry] = field(default_factory=list)

    def __post_init__(self) -> None:
        now = timeutil.to_iso(timeutil.utcnow())
        if not self.object_id:
            self.object_id = new_id(self.object_type.value)
        if not self.created_at:
            self.created_at = now
        if not self.updated_at:
            self.updated_at = now

    def audit(self, actor: str, action: str, **detail: Any) -> None:
        self.audit_trail.append(
            AuditEntry(timestamp=timeutil.to_iso(timeutil.utcnow()), actor=actor, action=action, detail=detail)
        )
        self.updated_at = timeutil.to_iso(timeutil.utcnow())

    def next_version(self, actor: str, reason: str) -> "ScientificObject":
        """Create the successor version. The current version is never mutated
        after this point (spec 5.19, 7.7)."""
        import copy

        successor = copy.deepcopy(self)
        successor.version = self.version + 1
        successor.updated_at = timeutil.to_iso(timeutil.utcnow())
        successor.audit(actor, "new_version", reason=reason, previous_version=self.version)
        return successor


def audit_trail_json(trail: list[AuditEntry]) -> str:
    return json.dumps([entry.__dict__ for entry in trail], default=str)


def fingerprint(obj: ScientificObject, payload: dict[str, Any]) -> str:
    """Stable content fingerprint for integrity checking (spec 7.29)."""
    return content_hash({"id": obj.object_id, "version": obj.version, "payload": payload})


def parse_ts(value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        return timeutil.ensure_utc(value)
    return timeutil.from_iso(value)
