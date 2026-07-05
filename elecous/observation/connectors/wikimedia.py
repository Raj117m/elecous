"""Wikimedia Pageviews connector.

The Wikimedia REST API (https://wikimedia.org/api/rest_v1/) publishes daily
per-article pageview counts under an open license. Pageviews of product and
activity articles are real, public measurements of collective attention —
ELECOUS's primary demand-side observation capability (search interest /
discussion proxies, spec 3.3) using only legally accessible data.

Bot traffic is excluded (`agent=user`) so observations measure human
attention.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from urllib.parse import quote

from elecous.common import timeutil
from elecous.common.errors import ConnectorError
from elecous.observation.connectors.base import CollectionResult, Connector
from elecous.research.progress import ProgressReporter

BASE = "https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article"


class WikimediaPageviewsConnector(Connector):
    parser_id = "wikimedia_pageviews@1"

    def collect(self, mode: str = "live", since: datetime | None = None) -> CollectionResult:
        topics = self.config.load_file("entities.yaml").get("topics", [])
        project = self.params.get("project", "en.wikipedia")
        today = timeutil.utcnow().date()
        end = today - timedelta(days=1)  # yesterday is the last complete day
        if mode == "backfill":
            # Backfill means "get full historical depth," always — `since`
            # reflects the *source's* most recent observation across every
            # entity sharing this source_id, not this specific entity's
            # history. Using it here silently truncated newly-added
            # entities (which have no prior observations at all) down to
            # whatever tiny window was left since the source's last run for
            # OTHER entities, discovered when a 288-topic backfill produced
            # only 271 total day-records instead of the expected tens of
            # thousands. `since` is a live-mode incremental optimization
            # only; backfill mode always requests its full configured depth
            # and lets the store's dedup handle any overlap.
            start = timeutil.days_ago(int(self.params.get("backfill_days", 730))).date()
        else:
            start = (since.date() + timedelta(days=1)) if since else end - timedelta(days=int(self.params.get("past_days", 7)))
        if start > end:
            return CollectionResult(observations=[], schema_hash=None, raw_count=0)

        observations = []
        schema_hash = None
        raw_count = 0
        # Real, measured cost (Stage 2.5, Requirement 1): this loop sweeps
        # every catalog topic (~288) with a politeness wait between
        # requests -- minutes of wall-clock time with no other visible
        # output. Report real progress instead of a silent gap.
        progress = ProgressReporter(len(topics), f"wikimedia:{mode}")
        for topic in topics:
            progress.tick()
            article = quote(topic["article"].replace(" ", "_"), safe="")
            url = f"{BASE}/{project}/all-access/user/{article}/daily/{start:%Y%m%d}00/{end:%Y%m%d}00"
            try:
                payload = self.http.get_json(url)
            except ConnectorError as exc:
                # The pageviews API answers 404 when an article has no data
                # points in the requested window; that is absence of data,
                # not connector failure.
                if exc.context.get("status") == 404:
                    self.log.info("no pageview data in window", article=topic["article"])
                    continue
                raise
            if payload is None:
                continue
            schema_hash = self.structure_hash(payload)
            items = payload.get("items", [])
            raw_count += len(items)
            for item in items:
                # timestamp format YYYYMMDDHH
                day = datetime.strptime(item["timestamp"][:8], "%Y%m%d").replace(
                    hour=23, minute=59, tzinfo=timeutil.UTC
                )
                observations.append(
                    self.observation(
                        timestamp=day,
                        entity=topic["entity"],
                        category="attention",
                        observation_type="pageviews_daily",
                        value=float(item["views"]),
                        unit="views_per_day",
                        region=project,
                        source_record=self.record(url, raw_reference=f"{topic['article']}:{item['timestamp']}"),
                    )
                )
        return CollectionResult(observations=observations, schema_hash=schema_hash, raw_count=raw_count)
