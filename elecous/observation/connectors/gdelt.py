"""GDELT media-volume connector.

The GDELT Project (https://www.gdeltproject.org) publishes free, open APIs
over global news coverage. The DOC 2.0 ``timelinevol`` mode returns the
share of monitored articles matching a query over time — a real measurement
of media attention for world phenomena (heatwaves, floods, outages...).
"""

from __future__ import annotations

from datetime import datetime, timedelta

from elecous.common import timeutil
from elecous.common.errors import ConnectorError
from elecous.observation.connectors.base import CollectionResult, Connector

URL = "https://api.gdeltproject.org/api/v2/doc/doc"


class GdeltMediaVolumeConnector(Connector):
    parser_id = "gdelt_timelinevol@1"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # GDELT asks for at most one request every ~5 seconds per IP.
        self.http.per_host_interval = max(self.http.per_host_interval, 6.0)

    def collect(self, mode: str = "live", since: datetime | None = None) -> CollectionResult:
        entities_config = self.config.load_file("entities.yaml")
        themes = list(entities_config.get("themes", []))
        # Product topics are also observed in media volume: news mentions of
        # a product category are an independent demand signal (spec 3.3
        # "Discussion Volume") from a different provider than pageviews.
        if self.params.get("include_topics", True):
            for topic in entities_config.get("topics", []):
                themes.append({"entity": topic["entity"], "query": f'"{topic["entity"].lower()}"'})
        observations = []
        schema_hash = None
        raw_count = 0
        now = timeutil.utcnow()
        if mode == "backfill":
            # See wikimedia.py's identical fix: `since` is a live-mode
            # incremental optimization and must never gate backfill mode,
            # or newly added entities silently get almost no real history
            # because `since` reflects the source's most recent observation
            # across every OTHER entity already collected.
            start = now - timedelta(days=int(self.params.get("backfill_days", 365)))
            span = {"startdatetime": start.strftime("%Y%m%d%H%M%S"), "enddatetime": now.strftime("%Y%m%d%H%M%S")}
        else:
            span = {"timespan": self.params.get("timespan", "7d")}
        failures = 0
        for theme in themes:
            try:
                payload = self.http.get_json(
                    URL,
                    params={"query": theme["query"], "mode": "timelinevol", "format": "json", **span},
                )
            except ConnectorError:
                # Partial collection beats total failure; the missing theme
                # is retried on the next scheduled run.
                failures += 1
                if failures >= len(themes):
                    raise
                continue
            if payload is None or not isinstance(payload, dict):
                failures += 1
                continue
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
