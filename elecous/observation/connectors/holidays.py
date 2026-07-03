"""Public holidays connector (Nager.Date).

https://date.nager.at publishes worldwide public-holiday calendars under MIT
license, keyless. Two real observations are produced:

* ``public_holiday`` — a holiday that has occurred (event time = the day),
* ``days_to_next_holiday`` — measured now: the published calendar distance to
  the next holiday. The *announcement* is present-day public information, so
  the observation is stamped at collection time, never in the future.
"""

from __future__ import annotations

from datetime import datetime

from elecous.common import timeutil
from elecous.observation.connectors.base import CollectionResult, Connector

BASE = "https://date.nager.at/api/v3"


class NagerHolidaysConnector(Connector):
    parser_id = "nager_holidays@1"

    def collect(self, mode: str = "live", since: datetime | None = None) -> CollectionResult:
        countries = self.config.load_file("entities.yaml").get("countries", [])
        observations = []
        schema_hash = None
        raw_count = 0
        now = timeutil.utcnow()
        today = now.date()
        years = [today.year - 2, today.year - 1, today.year] if mode == "backfill" else [today.year]
        for country in countries:
            future_days: list[int] = []
            for year in years:
                url = f"{BASE}/PublicHolidays/{year}/{country}"
                payload = self.http.get_json(url)
                if payload is None:  # country not covered by this provider
                    continue
                schema_hash = self.structure_hash(payload)
                raw_count += len(payload)
                for holiday in payload:
                    day = datetime.fromisoformat(holiday["date"]).date()
                    if day <= today:
                        ts = datetime(day.year, day.month, day.day, 12, tzinfo=timeutil.UTC)
                        observations.append(
                            self.observation(
                                timestamp=ts,
                                entity=f"Public holiday {country}",
                                category="calendar",
                                observation_type="public_holiday",
                                value=1.0,
                                unit="event",
                                country=country,
                                region=holiday.get("name", "")[:80],
                                source_record=self.record(url, raw_reference=f"{country}:{holiday['date']}"),
                            )
                        )
                    else:
                        future_days.append((day - today).days)
            if future_days:
                url = f"{BASE}/PublicHolidays/{today.year}/{country}"
                observations.append(
                    self.observation(
                        timestamp=now,
                        entity=f"Public holiday {country}",
                        category="calendar",
                        observation_type="days_to_next_holiday",
                        value=float(min(future_days)),
                        unit="days",
                        country=country,
                        source_record=self.record(url, raw_reference=f"{country}:next"),
                    )
                )
        return CollectionResult(observations=observations, schema_hash=schema_hash, raw_count=raw_count)
