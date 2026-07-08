"""The Observation — the smallest unit of ELECOUS (spec 1.5, 2.10).

An observation is a timestamped, measurable public-world change extracted
from a legitimate public source. The article/page/payload it came from is
discarded; only the scientific observation remains, with full provenance.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from elecous.common import timeutil
from elecous.common.ids import (
    content_hash,
    dedup_key,
    entity_hash,
    geographic_hash,
    new_id,
    semantic_hash,
    temporal_hash,
)


@dataclass
class SourceRecord:
    """Where exactly this observation came from (Rule 2: provenance)."""

    source_id: str
    url: str
    access_method: str  # official_api | open_data | rss | public_dataset
    extracted_at: str
    extraction: str  # parser identifier + version, e.g. "open_meteo_parser@1"
    license: str = ""
    raw_reference: str = ""  # provider-side identifier of the raw record

    def to_json(self) -> str:
        return json.dumps(self.__dict__, sort_keys=True)


@dataclass
class Observation:
    timestamp: datetime  # when the world change happened (event time)
    entity: str  # what the observation is about ("Berlin", "Portable fan")
    category: str  # observation capability ("weather", "attention", ...)
    observation_type: str  # normalized type ("temperature_max_c", ...)
    value: float
    unit: str
    source_record: SourceRecord
    country: str | None = None
    region: str | None = None
    lat: float | None = None
    lon: float | None = None
    level: int | None = None  # semantic level after normalization (2.13)
    confidence: float = 1.0
    signal_candidate: bool = True
    source_count: int = 1  # observation compression (2.11): merged reports
    observation_id: str = field(default_factory=lambda: new_id("observation"))
    version: int = 1
    collection_time: str = field(default_factory=lambda: timeutil.to_iso(timeutil.utcnow()))

    @property
    def t_obs(self) -> datetime:
        """Observation Time (T_obs): The precise real-world timestamp when an event occurred."""
        return self.timestamp

    @property
    def t_sig(self) -> datetime:
        """Signal Time (T_sig): The timestamp when ELECOUS ingested and mathematically normalized the observation."""
        return timeutil.from_iso(self.collection_time)

    # ------------------------------------------------------------- hashing
    @property
    def semantic_hash(self) -> str:
        return semantic_hash(self.entity, self.observation_type, self.unit)

    @property
    def temporal_hash(self) -> str:
        resolution = "day" if self.observation_type.endswith(("_daily", "_d")) or self.unit == "per_day" else "hour"
        return temporal_hash(self.timestamp, resolution=resolution)

    @property
    def geographic_hash(self) -> str:
        return geographic_hash(self.country, self.region, self.lat, self.lon)

    @property
    def entity_hash(self) -> str:
        return entity_hash(self.entity, self.category)

    @property
    def dedup_key(self) -> str:
        return dedup_key(self.semantic_hash, self.temporal_hash, self.geographic_hash, self.entity_hash)

    @property
    def checksum(self) -> str:
        return content_hash(
            {
                "timestamp": timeutil.to_iso(self.timestamp),
                "entity": self.entity,
                "type": self.observation_type,
                "value": self.value,
                "unit": self.unit,
                "source": self.source_record.source_id,
            }
        )

    def to_row(self) -> tuple:
        return (
            self.observation_id,
            self.dedup_key,
            timeutil.ensure_utc(self.timestamp),
            self.entity,
            self.category,
            self.observation_type,
            float(self.value),
            self.unit,
            self.level,
            self.country,
            self.region,
            self.lat,
            self.lon,
            self.signal_candidate,
            float(self.confidence),
            self.source_record.source_id,
            self.source_record.to_json(),
            self.source_count,
            self.checksum,
            self.version,
            timeutil.from_iso(self.collection_time),
        )

    @staticmethod
    def columns() -> list[str]:
        return [
            "observation_id",
            "dedup_key",
            "timestamp",
            "entity",
            "category",
            "observation_type",
            "value",
            "unit",
            "level",
            "country",
            "region",
            "lat",
            "lon",
            "signal_candidate",
            "confidence",
            "source_id",
            "source_record",
            "source_count",
            "checksum",
            "version",
            "collection_time",
        ]

    @classmethod
    def from_dict(cls, row: dict[str, Any]) -> "Observation":
        record = json.loads(row["source_record"])
        return cls(
            observation_id=row["observation_id"],
            timestamp=timeutil.ensure_utc(row["timestamp"]),
            entity=row["entity"],
            category=row["category"],
            observation_type=row["observation_type"],
            value=row["value"],
            unit=row["unit"],
            level=row["level"],
            country=row["country"],
            region=row["region"],
            lat=row["lat"],
            lon=row["lon"],
            signal_candidate=row["signal_candidate"],
            confidence=row["confidence"],
            source_record=SourceRecord(**record),
            source_count=row["source_count"],
            version=row["version"],
            collection_time=timeutil.to_iso(timeutil.ensure_utc(row["collection_time"])),
        )
