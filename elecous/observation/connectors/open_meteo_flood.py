"""Open-Meteo Flood API connector — a genuinely new observation capability
(river discharge / flood risk) not present in the initial source registry.

This connector exists specifically so the Expansion Engine (spec 12.8-12.10)
has a real, legally-accessible, keyless public source to discover, build,
sandbox-test and promote end-to-end, demonstrating the full pipeline rather
than only describing it.
"""

from __future__ import annotations

from datetime import datetime

from elecous.common import timeutil
from elecous.observation.connectors.base import CollectionResult, Connector

URL = "https://flood-api.open-meteo.com/v1/flood"


class OpenMeteoFloodConnector(Connector):
    parser_id = "open_meteo_flood@1"

    def collect(self, mode: str = "live", since: datetime | None = None) -> CollectionResult:
        locations = self.config.load_file("entities.yaml").get("locations", [])
        observations = []
        schema_hash = None
        raw_count = 0
        today = timeutil.utcnow().date()
        for loc in locations:
            payload = self.http.get_json(
                URL,
                params={
                    "latitude": loc["lat"],
                    "longitude": loc["lon"],
                    "daily": "river_discharge",
                    "past_days": int(self.params.get("past_days", 5)),
                    "forecast_days": 1,
                },
            )
            if payload is None:
                continue
            schema_hash = self.structure_hash(payload)
            daily = payload.get("daily", {})
            days = daily.get("time", [])
            values = daily.get("river_discharge") or []
            raw_count += len(days)
            for day, value in zip(days, values):
                if value is None:
                    continue
                day_date = datetime.fromisoformat(day).date()
                if day_date >= today:
                    continue
                ts = datetime.fromisoformat(day).replace(hour=23, minute=59, tzinfo=timeutil.UTC)
                observations.append(
                    self.observation(
                        timestamp=ts,
                        entity=loc["name"],
                        category="hydrology",
                        observation_type="river_discharge_m3s",
                        value=float(value),
                        unit="m3_per_s",
                        country=loc["country"],
                        region=loc.get("climate"),
                        lat=loc["lat"],
                        lon=loc["lon"],
                        source_record=self.record(URL, raw_reference=f"{loc['name']}:{day}"),
                    )
                )
        return CollectionResult(observations=observations, schema_hash=schema_hash, raw_count=raw_count)
