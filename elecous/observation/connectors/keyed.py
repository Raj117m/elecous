"""Key-gated public connectors: EIA (US energy) and FRED (US economics).

Both are official government/federal-reserve open APIs whose terms require a
free registered API key. Without a configured key the source remains
``suspended`` in the registry — ELECOUS waits for real data rather than
inventing any (Real Data Rule).
"""

from __future__ import annotations

from datetime import datetime, timedelta

from elecous.common import timeutil
from elecous.common.errors import ConnectorSuspended
from elecous.observation.connectors.base import CollectionResult, Connector

EIA_URL = "https://api.eia.gov/v2/electricity/rto/daily-region-data/data/"
FRED_URL = "https://api.stlouisfed.org/fred/series/observations"


class EiaElectricityConnector(Connector):
    parser_id = "eia_rto_daily@1"

    def collect(self, mode: str = "live", since: datetime | None = None) -> CollectionResult:
        api_key = self.config.get("credentials.eia_api_key", "")
        if not api_key:
            raise ConnectorSuspended("EIA API key not configured", context={"source": self.source_id})
        now = timeutil.utcnow()
        days = int(self.params.get("backfill_days", 365)) if mode == "backfill" else int(self.params.get("past_days", 7))
        # Same since-truncation bug class fixed in wikimedia.py/open_meteo.py:
        # backfill must always request full configured depth; `since` is a
        # live-mode incremental optimization only.
        start = (now - timedelta(days=days)).date() if mode == "backfill" else (since or now - timedelta(days=days)).date()
        regions = self.params.get("regions", ["US48"])
        payload = self.http.get_json(
            EIA_URL,
            params={
                "api_key": api_key,
                "frequency": "daily",
                "data[0]": "value",
                "facets[respondent][]": regions,
                "facets[type][]": "D",  # demand
                "start": start.isoformat(),
                "sort[0][column]": "period",
                "sort[0][direction]": "asc",
                "length": 5000,
            },
        )
        schema_hash = self.structure_hash(payload)
        rows = payload.get("response", {}).get("data", [])
        observations = []
        today = now.date()
        for row in rows:
            if row.get("value") is None:
                continue
            day = datetime.fromisoformat(row["period"]).date()
            if day >= today:
                continue
            ts = datetime(day.year, day.month, day.day, 23, 59, tzinfo=timeutil.UTC)
            observations.append(
                self.observation(
                    timestamp=ts,
                    entity=f"Grid {row.get('respondent', 'US48')}",
                    category="energy",
                    observation_type="electricity_demand_mwh",
                    value=float(row["value"]),
                    unit="megawatthours",
                    country="US",
                    region=row.get("respondent-name", ""),
                    source_record=self.record(EIA_URL, raw_reference=f"{row.get('respondent')}:{row['period']}"),
                )
            )
        return CollectionResult(observations=observations, schema_hash=schema_hash, raw_count=len(rows))


class FredSeriesConnector(Connector):
    parser_id = "fred_series@1"

    def collect(self, mode: str = "live", since: datetime | None = None) -> CollectionResult:
        api_key = self.config.get("credentials.fred_api_key", "")
        if not api_key:
            raise ConnectorSuspended("FRED API key not configured", context={"source": self.source_id})
        series = self.params.get(
            "series",
            {"UNRATE": ("unemployment_pct", "percent"), "CPIAUCSL": ("cpi_index", "index_1982_84")},
        )
        now = timeutil.utcnow()
        days = int(self.params.get("backfill_days", 1095)) if mode == "backfill" else 90
        # Same since-truncation bug class fixed in wikimedia.py/open_meteo.py:
        # backfill must always request full configured depth; `since` is a
        # live-mode incremental optimization only.
        start = (now - timedelta(days=days)).date() if mode == "backfill" else (since or now - timedelta(days=days)).date()
        observations = []
        schema_hash = None
        raw_count = 0
        for series_id, spec in series.items():
            obs_type, unit = spec if isinstance(spec, (list, tuple)) else (spec, "value")
            payload = self.http.get_json(
                FRED_URL,
                params={
                    "series_id": series_id,
                    "api_key": api_key,
                    "file_type": "json",
                    "observation_start": start.isoformat(),
                },
            )
            schema_hash = self.structure_hash(payload)
            rows = payload.get("observations", [])
            raw_count += len(rows)
            for row in rows:
                if row.get("value") in (None, "."):
                    continue
                day = datetime.fromisoformat(row["date"]).date()
                ts = datetime(day.year, day.month, day.day, 23, 59, tzinfo=timeutil.UTC)
                observations.append(
                    self.observation(
                        timestamp=ts,
                        entity="Economy US",
                        category="economics",
                        observation_type=obs_type,
                        value=float(row["value"]),
                        unit=unit,
                        country="US",
                        source_record=self.record(FRED_URL, raw_reference=f"{series_id}:{row['date']}"),
                    )
                )
        return CollectionResult(observations=observations, schema_hash=schema_hash, raw_count=raw_count)
