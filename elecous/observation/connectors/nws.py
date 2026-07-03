"""US National Weather Service alerts connector.

api.weather.gov is US-government public-domain data, keyless, requiring only
an identifying User-Agent (which the HTTP client always sends). Active
weather alerts become weather-hazard observations with severity levels.
"""

from __future__ import annotations

from datetime import datetime

from elecous.common import timeutil
from elecous.observation.connectors.base import CollectionResult, Connector

URL = "https://api.weather.gov/alerts/active"

SEVERITY_LEVEL = {"minor": 1, "moderate": 2, "severe": 3, "extreme": 4}


class NwsAlertsConnector(Connector):
    parser_id = "nws_alerts@1"

    def collect(self, mode: str = "live", since: datetime | None = None) -> CollectionResult:
        payload = self.http.get_json(URL, params={"status": "actual", "message_type": "alert"})
        schema_hash = self.structure_hash(payload)
        features = payload.get("features", [])
        observations = []
        now = timeutil.utcnow()
        for feature in features:
            props = feature.get("properties", {})
            severity = SEVERITY_LEVEL.get((props.get("severity") or "").lower())
            event = props.get("event")
            onset = props.get("onset") or props.get("effective") or props.get("sent")
            if severity is None or not event or not onset:
                continue
            ts = timeutil.from_iso(onset)
            if ts > now:
                # Alert for a future onset: the *issuance* is the present-day
                # world change we observed.
                ts = timeutil.from_iso(props.get("sent")) if props.get("sent") else now
            area = (props.get("areaDesc") or "")[:120]
            observations.append(
                self.observation(
                    timestamp=ts,
                    entity=event,
                    category="weather_hazard",
                    observation_type="weather_alert_severity",
                    value=float(severity),
                    unit="severity_level",
                    country="US",
                    region=area,
                    source_record=self.record(URL, raw_reference=props.get("id", "")),
                )
            )
        return CollectionResult(observations=observations, schema_hash=schema_hash, raw_count=len(features))
