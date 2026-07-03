"""USGS earthquake connector.

The USGS FDSN event service (https://earthquake.usgs.gov/fdsnws/event/1/)
is US-government public-domain data. Each qualifying earthquake becomes one
observation; the entity is the regional descriptor from the ``place`` field
so seismic activity forms a per-region series.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from elecous.common import timeutil
from elecous.observation.connectors.base import CollectionResult, Connector

URL = "https://earthquake.usgs.gov/fdsnws/event/1/query"


def region_of(place: str | None) -> str:
    if not place:
        return "Unknown region"
    return place.rsplit(",", 1)[-1].strip() if "," in place else place.strip()


class UsgsEarthquakeConnector(Connector):
    parser_id = "usgs_fdsn@1"

    def collect(self, mode: str = "live", since: datetime | None = None) -> CollectionResult:
        now = timeutil.utcnow()
        if mode == "backfill":
            start = since or now - timedelta(days=int(self.params.get("backfill_days", 365)))
        else:
            start = since or now - timedelta(days=int(self.params.get("past_days", 2)))
        payload = self.http.get_json(
            URL,
            params={
                "format": "geojson",
                "starttime": start.strftime("%Y-%m-%dT%H:%M:%S"),
                "endtime": now.strftime("%Y-%m-%dT%H:%M:%S"),
                "minmagnitude": float(self.params.get("min_magnitude", 4.5)),
                "orderby": "time-asc",
                "limit": 2000,
            },
        )
        schema_hash = self.structure_hash(payload)
        features = payload.get("features", [])
        observations = []
        for feature in features:
            props = feature.get("properties", {})
            geometry = feature.get("geometry", {}) or {}
            coords = geometry.get("coordinates") or [None, None]
            magnitude = props.get("mag")
            when = props.get("time")
            if magnitude is None or when is None:
                continue
            ts = datetime.fromtimestamp(when / 1000.0, tz=timeutil.UTC)
            observations.append(
                self.observation(
                    timestamp=ts,
                    entity=region_of(props.get("place")),
                    category="geophysical",
                    observation_type="earthquake_magnitude",
                    value=float(magnitude),
                    unit="magnitude",
                    lat=coords[1],
                    lon=coords[0],
                    source_record=self.record(URL, raw_reference=feature.get("id", "")),
                )
            )
        return CollectionResult(observations=observations, schema_hash=schema_hash, raw_count=len(features))
