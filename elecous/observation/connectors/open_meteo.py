"""Open-Meteo connectors (weather + air quality).

Open-Meteo (https://open-meteo.com) provides free, keyless access to weather
model and measurement data for non-commercial use (CC BY 4.0 attribution).
Two endpoints are used:

* forecast API with ``past_days`` for live collection of recently *measured*
  days,
* archive API (ERA5 reanalysis) for historical backfill.

Only complete past days (strictly before today UTC) become observations —
a partially measured day is not a finished world change.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from elecous.common import timeutil
from elecous.observation.connectors.base import CollectionResult, Connector

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
AQ_URL = "https://air-quality-api.open-meteo.com/v1/air-quality"

DAILY_FIELDS = {
    "temperature_2m_max": ("temperature_max_c", "celsius"),
    "temperature_2m_min": ("temperature_min_c", "celsius"),
    "precipitation_sum": ("precipitation_sum_mm", "mm"),
    "windspeed_10m_max": ("windspeed_max_kmh", "kmh"),
}


class OpenMeteoWeatherConnector(Connector):
    parser_id = "open_meteo_daily@1"

    def collect(self, mode: str = "live", since: datetime | None = None) -> CollectionResult:
        locations = self.config.load_file("entities.yaml").get("locations", [])
        observations = []
        schema_hash = None
        raw_count = 0
        today = timeutil.utcnow().date()
        for loc in locations:
            if mode == "backfill":
                # Backfill always requests its full configured depth — the
                # same real truncation bug already found and fixed in
                # wikimedia.py: `since` is the source's newest observation
                # (~today), so `since or days_ago(...)` short-circuited to
                # ~today and a deeper re-backfill fetched nothing. `since`
                # is a live-mode incremental optimization only; the store's
                # dedup handles any overlap.
                start = timeutil.days_ago(int(self.params.get("backfill_days", 730))).date()
                end = today - timedelta(days=3)  # archive availability lags a few days
                if start >= end:
                    continue
                payload = self.http.get_json(
                    ARCHIVE_URL,
                    params={
                        "latitude": loc["lat"],
                        "longitude": loc["lon"],
                        "start_date": start.isoformat(),
                        "end_date": end.isoformat(),
                        "daily": ",".join(DAILY_FIELDS),
                        "timezone": "UTC",
                    },
                )
                url = ARCHIVE_URL
            else:
                payload = self.http.get_json(
                    FORECAST_URL,
                    params={
                        "latitude": loc["lat"],
                        "longitude": loc["lon"],
                        "past_days": int(self.params.get("past_days", 7)),
                        "forecast_days": 1,
                        "daily": ",".join(DAILY_FIELDS),
                        "timezone": "UTC",
                    },
                )
                url = FORECAST_URL
            schema_hash = self.structure_hash(payload)
            daily = payload.get("daily", {})
            days = daily.get("time", [])
            raw_count += len(days)
            for i, day in enumerate(days):
                day_date = datetime.fromisoformat(day).date()
                if day_date >= today:
                    continue  # only complete, measured days
                # A daily aggregate describes the whole day; stamp end of day.
                ts = datetime.fromisoformat(day).replace(hour=23, minute=59, tzinfo=timeutil.UTC)
                for field, (obs_type, unit) in DAILY_FIELDS.items():
                    values = daily.get(field) or []
                    if i >= len(values) or values[i] is None:
                        continue
                    observations.append(
                        self.observation(
                            timestamp=ts,
                            entity=loc["name"],
                            category="weather",
                            observation_type=obs_type,
                            value=float(values[i]),
                            unit=unit,
                            country=loc["country"],
                            region=loc.get("climate"),
                            lat=loc["lat"],
                            lon=loc["lon"],
                            source_record=self.record(url, raw_reference=f"{loc['name']}:{day}:{field}"),
                        )
                    )
        return CollectionResult(observations=observations, schema_hash=schema_hash, raw_count=raw_count)


class OpenMeteoAirQualityConnector(Connector):
    parser_id = "open_meteo_air_quality@1"

    def collect(self, mode: str = "live", since: datetime | None = None) -> CollectionResult:
        locations = self.config.load_file("entities.yaml").get("locations", [])
        observations = []
        schema_hash = None
        raw_count = 0
        today = timeutil.utcnow().date()
        for loc in locations:
            params = {
                "latitude": loc["lat"],
                "longitude": loc["lon"],
                "hourly": "pm2_5",
                "timezone": "UTC",
                "past_days": int(self.params.get("past_days", 5)),
                "forecast_days": 1,
            }
            payload = self.http.get_json(AQ_URL, params=params)
            schema_hash = self.structure_hash(payload)
            hourly = payload.get("hourly", {})
            times = hourly.get("time", [])
            values = hourly.get("pm2_5") or []
            raw_count += len(times)
            # Aggregate real hourly measurements into daily means; only
            # complete past days qualify.
            by_day: dict[str, list[float]] = {}
            for t, v in zip(times, values):
                if v is None:
                    continue
                day = t[:10]
                if datetime.fromisoformat(day).date() >= today:
                    continue
                by_day.setdefault(day, []).append(float(v))
            for day, vals in sorted(by_day.items()):
                if len(vals) < 18:  # incomplete day (<75% hourly coverage)
                    continue
                ts = datetime.fromisoformat(day).replace(hour=23, minute=59, tzinfo=timeutil.UTC)
                observations.append(
                    self.observation(
                        timestamp=ts,
                        entity=loc["name"],
                        category="air_quality",
                        observation_type="pm2_5_ugm3",
                        value=round(sum(vals) / len(vals), 2),
                        unit="ugm3",
                        country=loc["country"],
                        region=loc.get("climate"),
                        lat=loc["lat"],
                        lon=loc["lon"],
                        source_record=self.record(AQ_URL, raw_reference=f"{loc['name']}:{day}:pm2_5"),
                    )
                )
        return CollectionResult(observations=observations, schema_hash=schema_hash, raw_count=raw_count)
