"""Connector base (spec 2.6).

Every provider has exactly one connector responsible for authentication,
download, parsing, normalization, validation handoff, error handling, retry
scheduling and version tracking. If a provider changes, only its connector
changes.

Connectors return Observations; they never write to the database themselves.
The ObservationNetwork pipeline (connector → parser → normalizer → validator
→ database, spec 2.3) is enforced by construction: the store is the only
writer and it validates everything.
"""

from __future__ import annotations

import abc
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from elecous.common import timeutil
from elecous.common.ids import content_hash
from elecous.infrastructure.config import Config, active_config
from elecous.infrastructure.http import HttpClient
from elecous.infrastructure.logging import get_logger
from elecous.observation.model import Observation, SourceRecord
from elecous.observation.normalizers.semantic import SemanticNormalizer


@dataclass
class CollectionResult:
    observations: list[Observation]
    schema_hash: str | None  # hash of the payload structure for stability tracking
    raw_count: int  # reports seen before extraction/compression


class Connector(abc.ABC):
    """Subclasses set ``parser_id`` and implement ``collect``."""

    parser_id: str = "base@1"

    def __init__(self, source: dict[str, Any], config: Config | None = None, http: HttpClient | None = None):
        self.source = source
        self.config = config or active_config()
        self.http = http or HttpClient(self.config)
        self.log = get_logger("observation")
        self.normalizer = SemanticNormalizer(self.config)
        import json

        raw = source.get("connector_config")
        self.params: dict[str, Any] = json.loads(raw) if isinstance(raw, str) else (raw or {})

    @property
    def source_id(self) -> str:
        return self.source["source_id"]

    # ------------------------------------------------------------------ hooks
    @abc.abstractmethod
    def collect(self, mode: str = "live", since: datetime | None = None) -> CollectionResult:
        """Fetch real public data and extract observations.

        mode: "live" for the current scheduling window, "backfill" for
        historical collection. ``since`` is the newest timestamp already held
        for this source, enabling incremental collection (spec 10.19).
        """

    # ---------------------------------------------------------------- helpers
    def record(self, url: str, raw_reference: str = "") -> SourceRecord:
        return SourceRecord(
            source_id=self.source_id,
            url=url,
            access_method=self.source["access_method"],
            extracted_at=timeutil.to_iso(timeutil.utcnow()),
            extraction=self.parser_id,
            license=self.source.get("license", ""),
            raw_reference=raw_reference,
        )

    def observation(self, **kwargs: Any) -> Observation:
        obs = Observation(**kwargs)
        obs.level = self.normalizer.level_for(obs.observation_type, obs.value)
        return obs

    @staticmethod
    def structure_hash(payload: Any) -> str:
        """Hash of the *shape* of a payload (keys/types, not values) used to
        detect provider schema changes (spec 2.14 schema stability)."""

        def shape(node: Any) -> Any:
            if isinstance(node, dict):
                return {k: shape(v) for k, v in sorted(node.items())}
            if isinstance(node, list):
                return [shape(node[0])] if node else []
            return type(node).__name__

        return content_hash(shape(payload))

    def timed_collect(self, mode: str = "live", since: datetime | None = None) -> tuple[CollectionResult, float]:
        start = time.monotonic()
        result = self.collect(mode=mode, since=since)
        return result, (time.monotonic() - start) * 1000.0


def load_connector(source: dict[str, Any], config: Config | None = None) -> Connector:
    """Instantiate the connector class named in the source registry."""
    import importlib

    module_name, _, class_name = source["connector"].rpartition(".")
    module = importlib.import_module(module_name)
    cls = getattr(module, class_name)
    if not issubclass(cls, Connector):
        raise TypeError(f"{source['connector']} is not a Connector")
    return cls(source, config=config)
