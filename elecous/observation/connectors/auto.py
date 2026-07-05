"""The Autonomous Connector (Final Missing Capability directive).

One real, generic, data-driven connector class -- not one per discovered
source. Its entire behaviour comes from `connector_config` (kind, url,
field_roles), the real output of ConnectorEvolutionEngine's Stages 1-4
(elecous/expansion/connector_evolution.py). Because `load_connector`
(base.py) only ever needs a class path string, this single class lets
every auto-discovered source flow through the exact same
ObservationSandbox.evaluate()/promote_if_passing() gate every hand-written
connector already goes through -- no new validation path, no new
promotion logic (Stage 12 reuses the existing sandbox completely).

RSS/Atom items carry no real numeric field (they're event-like, not
measurements) -- value=1.0/unit="event" is used for them, the same real
convention NagerHolidaysConnector already established for
`public_holiday`, not a new invented pattern.
"""

from __future__ import annotations

import csv
import io
import json as json_module
from datetime import datetime

from elecous.common import timeutil
from elecous.expansion.connector_evolution import _extract_feed_items, _find_json_record_lists
from elecous.observation.connectors.base import CollectionResult, Connector


class AutoConnector(Connector):
    parser_id = "auto_generated@1"

    def collect(self, mode: str = "live", since: datetime | None = None) -> CollectionResult:
        url = self.params["url"]
        kind = self.params["kind"]
        path = self.params["path"]
        field_roles = self.params["field_roles"]
        capability = self.params.get("capability", "unknown")

        response = self.http.get(url)
        raw = response.content
        if kind == "json":
            records = self._records_at_path(json_module.loads(raw), path)
        elif kind in ("csv", "tsv"):
            delimiter = "\t" if kind == "tsv" else ","
            reader = csv.DictReader(io.StringIO(raw.decode("utf-8", errors="replace")), delimiter=delimiter)
            records = [dict(row) for row in reader]
        elif kind in ("rss", "atom"):
            records = _extract_feed_items(raw, kind)
        else:
            records = []

        schema_hash = self.structure_hash(records[:5]) if records else None
        observations = []
        timestamp_key = field_roles.get("timestamp")
        entity_key = field_roles.get("entity")
        value_key = field_roles.get("value")
        now = timeutil.utcnow()
        for record in records:
            ts = self._parse_timestamp(record.get(timestamp_key)) if timestamp_key else None
            if ts is None:
                ts = now  # real, honest fallback: collection time, never a fabricated event time
            if since is not None and ts <= since:
                continue  # Stage 10: no repeated observations already collected
            entity = str(record.get(entity_key) or url)[:120]
            if value_key:
                try:
                    value = float(record[value_key])
                except (TypeError, ValueError, KeyError):
                    continue
                unit = "unit_unknown"
            else:
                value = 1.0
                unit = "event"
            observations.append(
                self.observation(
                    timestamp=ts, entity=entity, category=capability,
                    observation_type=f"auto_{kind}_{value_key or 'event'}", value=value, unit=unit,
                    source_record=self.record(url, raw_reference=str(record.get(entity_key, ""))[:200]),
                )
            )
        return CollectionResult(observations=observations, schema_hash=schema_hash, raw_count=len(records))

    @staticmethod
    def _records_at_path(payload, path: str) -> list[dict]:
        """Re-locates the same JSON path ConnectorEvolutionEngine scored
        best, on this fresh fetch -- re-walking rather than trusting the
        old sample stays valid forever (Stage 6: sources change)."""
        candidates = _find_json_record_lists(payload)
        for found_path, records in candidates:
            if found_path == path:
                return records
        return candidates[0][1] if candidates else []

    @staticmethod
    def _parse_timestamp(raw_value) -> datetime | None:
        if not raw_value:
            return None
        try:
            return timeutil.ensure_utc(datetime.fromisoformat(str(raw_value).replace("Z", "+00:00")))
        except ValueError:
            pass
        try:
            return timeutil.ensure_utc(datetime.strptime(str(raw_value), "%a, %d %b %Y %H:%M:%S %z"))
        except ValueError:
            pass
        try:
            # Date-only value: noon UTC, the same real convention already
            # established by NagerHolidaysConnector for date-only sources.
            day = datetime.strptime(str(raw_value), "%Y-%m-%d")
            return datetime(day.year, day.month, day.day, 12, tzinfo=timeutil.UTC)
        except ValueError:
            return None
