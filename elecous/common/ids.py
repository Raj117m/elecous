"""Identity and hashing.

Spec 11.16: no anonymous records. Every database object carries a unique ID.
Spec 2.12: every observation receives semantic, temporal, geographic and
entity hashes so duplicates merge automatically.

IDs are time-sortable (ULID-style): 10 hex chars of millisecond timestamp +
16 hex chars of randomness, prefixed by object type for auditability
(e.g. ``mech_018f3a...``). Content hashes are SHA-256.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from datetime import datetime
from typing import Any

from elecous.common.timeutil import ensure_utc

_PREFIXES = {
    "observation": "obs",
    "source": "src",
    "connector": "con",
    "signal": "sig",
    "demand_signal": "dsg",
    "environmental_signal": "esg",
    "environmental_factor": "fac",
    "world_condition": "wcn",
    "research_episode": "epi",
    "mechanism": "mech",
    "knowledge": "kno",
    "prediction": "pred",
    "opportunity": "opp",
    "validation": "val",
    "world_state": "wst",
    "unknown": "unk",
    "event": "evt",
    "task": "tsk",
    "research_question": "rsq",
    "research_experience": "rex",
    "research_lesson": "rsl",
}


def new_id(object_type: str) -> str:
    prefix = _PREFIXES.get(object_type, object_type[:4])
    ts_ms = int(time.time() * 1000)
    rand = os.urandom(8).hex()
    return f"{prefix}_{ts_ms:012x}{rand}"


def sha256_hex(data: bytes | str) -> str:
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def canonical_json(payload: Any) -> str:
    """Deterministic JSON encoding used for all content hashing."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)


def content_hash(payload: Any) -> str:
    return sha256_hex(canonical_json(payload))


def semantic_hash(entity: str, observation_type: str, unit: str | None) -> str:
    """What is being said (spec 2.12)."""
    return content_hash({"entity": entity.lower().strip(), "type": observation_type, "unit": unit})


def temporal_hash(ts: datetime, resolution: str = "hour") -> str:
    """When it was said, bucketed so near-simultaneous duplicates collide."""
    ts = ensure_utc(ts)
    fmt = {"day": "%Y-%m-%d", "hour": "%Y-%m-%dT%H", "minute": "%Y-%m-%dT%H:%M"}[resolution]
    return sha256_hex(ts.strftime(fmt))


def geographic_hash(country: str | None, region: str | None, lat: float | None, lon: float | None) -> str:
    """Where it was observed; coordinates quantized to a ~1km grid.
    Floor-based quantization avoids float-rounding boundary flips."""
    import math

    return content_hash(
        {
            "country": (country or "").upper(),
            "region": (region or "").lower(),
            "lat": math.floor(lat * 100) if lat is not None else None,
            "lon": math.floor(lon * 100) if lon is not None else None,
        }
    )


def entity_hash(entity: str, category: str | None) -> str:
    return content_hash({"entity": entity.lower().strip(), "category": (category or "").lower()})


def dedup_key(sem: str, tem: str, geo: str, ent: str) -> str:
    """Merge key for automatic deduplication (spec 2.12)."""
    return sha256_hex("|".join((sem, tem, geo, ent)))
