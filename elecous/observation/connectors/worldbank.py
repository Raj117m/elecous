"""World Bank open data connector.

https://api.worldbank.org/v2 is keyless open data (CC BY 4.0). Annual macro
indicators (GDP growth, inflation, unemployment) provide the economic
context layer of the World State. Values are annual: the observation is
stamped at the end of the reported year (real historical fact, backfill).
"""

from __future__ import annotations

from datetime import datetime

from elecous.common import timeutil
from elecous.observation.connectors.base import CollectionResult, Connector

BASE = "https://api.worldbank.org/v2"

INDICATORS = {
    "NY.GDP.MKTP.KD.ZG": ("gdp_growth_pct", "percent"),
    "FP.CPI.TOTL.ZG": ("inflation_pct", "percent"),
    "SL.UEM.TOTL.ZS": ("unemployment_pct", "percent"),
}


class WorldBankConnector(Connector):
    parser_id = "worldbank_v2@1"

    def collect(self, mode: str = "live", since: datetime | None = None) -> CollectionResult:
        countries = self.config.load_file("entities.yaml").get("countries", [])
        country_expr = ";".join(countries)
        this_year = timeutil.utcnow().year
        year_range = f"{this_year - 6}:{this_year - 1}" if mode == "backfill" else f"{this_year - 2}:{this_year - 1}"
        observations = []
        schema_hash = None
        raw_count = 0
        for indicator, (obs_type, unit) in INDICATORS.items():
            url = f"{BASE}/country/{country_expr}/indicator/{indicator}"
            payload = self.http.get_json(url, params={"format": "json", "per_page": 500, "date": year_range})
            if not isinstance(payload, list) or len(payload) < 2 or payload[1] is None:
                continue
            schema_hash = self.structure_hash(payload)
            rows = payload[1]
            raw_count += len(rows)
            for row in rows:
                if row.get("value") is None:
                    continue
                year = int(row["date"])
                iso2 = row.get("country", {}).get("id") or row.get("countryiso3code", "")[:2]
                ts = datetime(year, 12, 31, 23, 59, tzinfo=timeutil.UTC)
                observations.append(
                    self.observation(
                        timestamp=ts,
                        entity=f"Economy {iso2}",
                        category="economics",
                        observation_type=obs_type,
                        value=float(row["value"]),
                        unit=unit,
                        country=iso2 if len(iso2) == 2 else None,
                        source_record=self.record(url, raw_reference=f"{indicator}:{iso2}:{year}"),
                    )
                )
        return CollectionResult(observations=observations, schema_hash=schema_hash, raw_count=raw_count)
