"""Event bus (spec 10.18).

Every subsystem communicates through events; there is no direct coupling
between engines. Events fan out synchronously in-process (cheap, ordered,
easy to reason about on a single machine) and are persisted to the dashboard
database for auditability and replay. The bus is transport-agnostic: scaling
later means swapping the dispatcher, not the architecture (spec 10.26).
"""

from __future__ import annotations

import threading
from collections import defaultdict
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Callable

from elecous.common import timeutil
from elecous.common.ids import new_id


class EventType(StrEnum):
    OBSERVATION_ADDED = "observation_added"
    OBSERVATION_REJECTED = "observation_rejected"
    SOURCE_HEALTH_CHANGED = "source_health_changed"
    CONNECTOR_FAILED = "connector_failed"
    CONNECTOR_REPAIRED = "connector_repaired"
    SIGNAL_DISCOVERED = "signal_discovered"
    SIGNAL_PROMOTED = "signal_promoted"
    DEMAND_SPIKE_CANDIDATE = "demand_spike_candidate"
    EPISODE_CREATED = "episode_created"
    EPISODE_VALIDATED = "episode_validated"
    FACTOR_DISCOVERED = "factor_discovered"
    FACTOR_PROMOTED = "factor_promoted"
    WORLD_CONDITION_CHANGED = "world_condition_changed"
    MECHANISM_DISCOVERED = "mechanism_discovered"
    MECHANISM_PROMOTED = "mechanism_promoted"
    MECHANISM_DEMOTED = "mechanism_demoted"
    VALIDATION_COMPLETED = "validation_completed"
    KNOWLEDGE_UPDATED = "knowledge_updated"
    KNOWLEDGE_REVISED = "knowledge_revised"
    WORLD_STATE_UPDATED = "world_state_updated"
    PREDICTION_CREATED = "prediction_created"
    PREDICTION_RESOLVED = "prediction_resolved"
    OPPORTUNITY_UPDATED = "opportunity_updated"
    DASHBOARD_REFRESH = "dashboard_refresh"
    HEALTH_REPORT = "health_report"
    REPAIR_COMPLETED = "repair_completed"
    EXPANSION_CANDIDATE = "expansion_candidate"
    OBSERVATION_REQUEST = "observation_request"  # gap engine -> expansion (4.23)
    UNKNOWN_RECORDED = "unknown_recorded"
    NOTIFICATION = "notification"


@dataclass
class Event:
    event_type: EventType
    payload: dict[str, Any]
    source: str  # emitting subsystem
    event_id: str = field(default_factory=lambda: new_id("event"))
    timestamp: str = field(default_factory=lambda: timeutil.to_iso(timeutil.utcnow()))


Handler = Callable[[Event], None]


class EventBus:
    def __init__(self, persist: Callable[[Event], None] | None = None):
        self._subscribers: dict[EventType, list[Handler]] = defaultdict(list)
        self._all_subscribers: list[Handler] = []
        self._lock = threading.RLock()
        self._persist = persist
        self._failures: list[dict[str, Any]] = []

    def subscribe(self, event_type: EventType, handler: Handler) -> None:
        with self._lock:
            self._subscribers[event_type].append(handler)

    def subscribe_all(self, handler: Handler) -> None:
        with self._lock:
            self._all_subscribers.append(handler)

    def set_persistence(self, persist: Callable[[Event], None]) -> None:
        self._persist = persist

    def publish(self, event: Event) -> None:
        if self._persist is not None:
            try:
                self._persist(event)
            except Exception as exc:  # persistence failure must not stop science
                self._failures.append({"stage": "persist", "event": event.event_id, "error": repr(exc)})
        with self._lock:
            handlers = list(self._subscribers.get(event.event_type, ())) + list(self._all_subscribers)
        for handler in handlers:
            try:
                handler(event)
            except Exception as exc:
                # A failing subscriber never blocks other subscribers
                # (spec 10.2: every stage can fail without crashing the rest).
                self._failures.append(
                    {"stage": "dispatch", "event": event.event_id, "handler": repr(handler), "error": repr(exc)}
                )

    def emit(self, event_type: EventType, source: str, **payload: Any) -> Event:
        event = Event(event_type=event_type, payload=payload, source=source)
        self.publish(event)
        return event

    def drain_failures(self) -> list[dict[str, Any]]:
        failures, self._failures = self._failures, []
        return failures


_bus_lock = threading.Lock()
_bus: EventBus | None = None


def global_bus() -> EventBus:
    global _bus
    with _bus_lock:
        if _bus is None:
            _bus = EventBus()
        return _bus


def reset_bus() -> None:
    global _bus
    with _bus_lock:
        _bus = None
