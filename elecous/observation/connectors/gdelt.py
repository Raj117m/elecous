"""GDELT media-volume connector.

The GDELT Project (https://www.gdeltproject.org) publishes free, open APIs
over global news coverage. The DOC 2.0 ``timelinevol`` mode returns the
share of monitored articles matching a query over time — a real measurement
of media attention for world phenomena (heatwaves, floods, outages...).
"""

from __future__ import annotations

from datetime import datetime, timedelta

from elecous.common import timeutil
from elecous.observation.connectors.base import CollectionResult, Connector

URL = "https://api.gdeltproject.org/api/v2/doc/doc"


class GdeltMediaVolumeConnector(Connector):
    parser_id = "gdelt_timelinevol@1"

    def collect(self, mode: str = "live", since: datetime | None = None) -> CollectionResult:
        themes = self.config.load_file("entities.yaml").get("themes", [])
        observations = []
        schema_hash = None
        raw_count = 0
        now = timeutil.utcnow()
        if mode == "backfill":
            start = since or now - timedelta(days=int(self.params.get("backfill_days", 365)))
            span = {"startdatetime": start.strftime("%Y%m%d%H%M%S"), "enddatetime": now.strftime("%Y%m%d%H%M%S")}
        else:
            span = {"timespan": self.params.get("timespan", "7d")}
        for theme in themes:
            payload = self.http.get_json(
                URL,
                params={"query": theme["query"], "mode": "timelinevol", "format": "json", **span},
            )
            schema_hash = self.structure_hash(payload)
            timeline = (payload.get("timeline") or [{}])[0].get("data", [])
            raw_count += len(timeline)
            # GDELT returns 15-minute or daily buckets depending on span;
            # aggregate to daily means and keep only complete past days.
            by_day: dict[str, list[float]] = {}
            today = now.date()
            for point in timeline:
                raw_date = point.get("date", "")
                value = point.get("value")
                if value is None or len(raw_date) < 8:
                    continue
                day = f"{raw_date[:4]}-{raw_date[4:6]}-{raw_date[6:8]}"
                if datetime.fromisoformat(day).date() >= today:
                    continue
                by_day.setdefault(day, []).append(float(value))
            for day, vals in sorted(by_day.items()):
                ts = datetime.fromisoformat(day).replace(hour=23, minute=59, tzinfo=timeutil.UTC)
                observations.append(
                    self.observation(
                        timestamp=ts,
                        entity=theme["entity"],
                        category="media",
                        observation_type="media_volume_pct",
                        value=round(sum(vals) / len(vals), 6),
                        unit="pct_of_articles",
                        source_record=self.record(URL, raw_reference=f"{theme['query']}:{day}"),
                    )
                )
        return CollectionResult(observations=observations, schema_hash=schema_hash, raw_count=raw_count)
