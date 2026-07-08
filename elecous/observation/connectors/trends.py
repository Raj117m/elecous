"""Google Trends connector.

Google Trends provides search interest volume for topics. This acts as a proxy 
for social volume and public interest (spec 3.3). 

This connector uses the unofficial pytrends library to fetch data. Due to rate 
limiting, backfill is limited to recent data unless cautiously batched.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta
import pandas as pd

from elecous.common import timeutil
from elecous.common.errors import ConnectorError
from elecous.observation.connectors.base import CollectionResult, Connector
from elecous.research.progress import ProgressReporter

try:
    from pytrends.request import TrendReq
except ImportError:
    TrendReq = None


class GoogleTrendsConnector(Connector):
    parser_id = "google_trends@1"

    def collect(self, mode: str = "live", since: datetime | None = None) -> CollectionResult:
        if TrendReq is None:
            self.log.error("pytrends is not installed")
            return CollectionResult(observations=[], schema_hash=None, raw_count=0)

        topics = self.config.load_file("entities.yaml").get("topics", [])
        today = timeutil.utcnow().date()
        end = today - timedelta(days=1)
        
        if mode == "backfill":
            # For backfill, we restrict the window to avoid aggressive rate-limits
            backfill_days = int(self.params.get("backfill_days", 30))
            start = timeutil.days_ago(backfill_days).date()
        else:
            past_days = int(self.params.get("past_days", 7))
            start = (since.date() + timedelta(days=1)) if since else end - timedelta(days=past_days)
            
        if start > end:
            return CollectionResult(observations=[], schema_hash=None, raw_count=0)

        observations = []
        schema_hash = None
        raw_count = 0
        
        # Format for pytrends timeframe: "YYYY-MM-DD YYYY-MM-DD"
        timeframe = f"{start:%Y-%m-%d} {end:%Y-%m-%d}"
        
        pytrend = TrendReq(hl='en-US', tz=0, retries=2, backoff_factor=1.0)
        
        progress = ProgressReporter(len(topics), f"google_trends:{mode}")
        for topic in topics:
            progress.tick()
            entity = topic["entity"]
            
            try:
                pytrend.build_payload(kw_list=[entity], timeframe=timeframe)
                df = pytrend.interest_over_time()
            except Exception as exc:
                if "429" in str(exc):
                    self.log.warning("Google Trends rate limited. Stopping collection.", entity=entity)
                    break
                self.log.warning("Failed to fetch Google Trends", entity=entity, error=str(exc))
                time.sleep(2)  # Back off
                continue
                
            if df is None or df.empty:
                self.log.info("no search trend data in window", entity=entity)
                time.sleep(1) # Be polite
                continue
                
            schema_hash = self.structure_hash(["date", "interest", "isPartial"])
            raw_count += len(df)
            
            for timestamp_pd, row in df.iterrows():
                if isinstance(timestamp_pd, pd.Timestamp):
                    day = timestamp_pd.to_pydatetime().replace(hour=23, minute=59, tzinfo=timeutil.UTC)
                else:
                    continue
                    
                val = float(row[entity])
                observations.append(
                    self.observation(
                        timestamp=day,
                        entity=entity,
                        category="attention",
                        observation_type="search_interest",
                        value=val,
                        unit="relative_interest",
                        region="global",
                        source_record=self.record("https://trends.google.com/", raw_reference=f"{entity}:{timeframe}"),
                    )
                )
                
            # Polite wait between queries
            time.sleep(2.0)
            
        return CollectionResult(observations=observations, schema_hash=schema_hash, raw_count=raw_count)
