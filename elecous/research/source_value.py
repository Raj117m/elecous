"""Source Value Ledger (rewrite directive, Level 2 source intelligence).

Stage 1 built schema-backed CRUD only. Stage 2 wires ``on_validation_completed``
as a real listener on ``EventType.VALIDATION_COMPLETED``: resolves which
``source_id``s fed the judged factor/mechanism (via the Stage 1 lineage --
``mechanisms.evidence_links["observations"]`` for mechanisms, derived via
``EpisodeSignalDiscovery.sources_for_signals`` for factors), computes a real
lead time from ``episode_signals.days_before_spike``, and credits/debits each
source. Subscribed once, additively, in ``bootstrap.py``.
"""

from __future__ import annotations

import json
from typing import Any

from elecous.common import timeutil
from elecous.infrastructure.config import Config, active_config
from elecous.infrastructure.event_bus import Event
from elecous.infrastructure.logging import get_logger
from elecous.observation.registry.source_value_schema import (
    SOURCE_VALUE_METRICS_MIGRATION,
    SOURCE_VALUE_MIGRATION,
)
from elecous.research.priority import wilson_interval, wilson_width
from elecous.storage.database import DatabaseManager, global_databases

#: Exponential smoothing factor for net_value_ema. Small (slow-moving) since
#: a single validation outcome should not swing a source's whole track
#: record -- consistent with the project's "evidence accumulates, it doesn't
#: flip a switch" validation philosophy.
EMA_ALPHA = 0.2


class SourceValueLedger:
    def __init__(
        self,
        databases: DatabaseManager | None = None,
        config: Config | None = None,
        factors=None,
        mechanisms=None,
        discovery=None,
        registry=None,
    ):
        self.databases = databases or global_databases()
        self.databases.register_schema(SOURCE_VALUE_MIGRATION)
        self.databases.register_schema(SOURCE_VALUE_METRICS_MIGRATION)
        self.db = self.databases.get("observations")
        self.config = config or active_config()
        self.log = get_logger("research")
        # Optional so existing callers (Stage 1 tests exercising record/get/
        # global_contribution in isolation) are unaffected. Required only by
        # on_validation_completed, which bootstrap.py wires with the real
        # stores once they exist in ElecousStack's construction order.
        self.factors = factors
        self.mechanisms = mechanisms
        self.discovery = discovery
        self.registry = registry

    def record(
        self,
        source_id: str,
        need_cluster: str,
        passed: bool,
        lead_time_days: float | None = None,
        redundant: bool = False,
        novel: bool = False,
        compute_cost_ms: float | None = None,
    ) -> dict[str, Any]:
        """Credit or debit a source for one validation outcome, keyed by
        (source, need_cluster). ``redundant``/``novel``/``compute_cost_ms``
        (Stage 2.5, Requirement 2) are real, per-occurrence facts the caller
        already determined from real lineage/timing data -- this method
        only accumulates them, it does not derive them. Returns the updated
        row."""
        existing = self.get(source_id, need_cluster)
        now = timeutil.utcnow()
        if existing is None:
            supports = 1 if passed else 0
            contradictions = 0 if passed else 1
            net_value_ema = 1.0 if passed else -1.0
            lead_avg = lead_time_days
            sample_size = 1
            redundancy_count = 1 if redundant else 0
            novelty_count = 1 if novel else 0
            cost_avg = compute_cost_ms
        else:
            supports = existing["supports"] + (1 if passed else 0)
            contradictions = existing["contradictions"] + (0 if passed else 1)
            observed = 1.0 if passed else -1.0
            net_value_ema = existing["net_value_ema"] + EMA_ALPHA * (observed - existing["net_value_ema"])
            sample_size = existing["sample_size"] + 1
            if lead_time_days is None:
                lead_avg = existing["lead_time_days_avg"]
            elif existing["lead_time_days_avg"] is None:
                lead_avg = lead_time_days
            else:
                lead_avg = existing["lead_time_days_avg"] + EMA_ALPHA * (
                    lead_time_days - existing["lead_time_days_avg"]
                )
            redundancy_count = (existing["redundancy_count"] or 0) + (1 if redundant else 0)
            novelty_count = (existing["novelty_count"] or 0) + (1 if novel else 0)
            if compute_cost_ms is None:
                cost_avg = existing["compute_cost_ms_avg"]
            elif existing["compute_cost_ms_avg"] is None:
                cost_avg = compute_cost_ms
            else:
                cost_avg = existing["compute_cost_ms_avg"] + EMA_ALPHA * (
                    compute_cost_ms - existing["compute_cost_ms_avg"]
                )
        self.db.execute(
            """
            INSERT INTO source_value_records
                (source_id, need_cluster, supports, contradictions, net_value_ema,
                 lead_time_days_avg, sample_size, last_event_at, updated_at,
                 redundancy_count, novelty_count, compute_cost_ms_avg, storage_bytes_est)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (source_id, need_cluster) DO UPDATE SET
                supports = excluded.supports,
                contradictions = excluded.contradictions,
                net_value_ema = excluded.net_value_ema,
                lead_time_days_avg = excluded.lead_time_days_avg,
                sample_size = excluded.sample_size,
                last_event_at = excluded.last_event_at,
                updated_at = excluded.updated_at,
                redundancy_count = excluded.redundancy_count,
                novelty_count = excluded.novelty_count,
                compute_cost_ms_avg = excluded.compute_cost_ms_avg
            """,
            [source_id, need_cluster, supports, contradictions, net_value_ema, lead_avg, sample_size, now, now,
             redundancy_count, novelty_count, cost_avg, None],
        )
        return self.get(source_id, need_cluster)

    def get(self, source_id: str, need_cluster: str) -> dict[str, Any] | None:
        rows = self.db.query_dicts(
            "SELECT * FROM source_value_records WHERE source_id = ? AND need_cluster = ?",
            [source_id, need_cluster],
        )
        return rows[0] if rows else None

    def for_source(self, source_id: str) -> list[dict[str, Any]]:
        return self.db.query_dicts(
            "SELECT * FROM source_value_records WHERE source_id = ? ORDER BY need_cluster", [source_id]
        )

    # ------------------------------------------------------------ live wiring
    def on_validation_completed(self, event: Event) -> None:
        """Credit/debit every source that fed a judged factor or mechanism.
        Real lineage only (Stage 1 plumbing) -- silently no-ops if the
        collaborators needed to resolve it were not supplied (e.g. a caller
        exercising only record()/get() in isolation)."""
        object_type = event.payload.get("object_type")
        if object_type not in ("environmental_factor", "mechanism"):
            return
        object_id = event.payload.get("object_id")
        passed = event.payload.get("outcome") == "passed"
        # Real timestamp of the event being processed, when the caller has
        # it (backfill replay does; live events default to "now" via the
        # Event dataclass). Required for a correct novelty check below --
        # without it, replaying history sees the *entire* already-complete
        # table, including rows that are chronologically in the "future"
        # relative to the event being replayed, and every real novelty
        # ever found this way (a real bug caught by inspecting the
        # backfill's output: novelty_count was 0 everywhere it should not
        # have been).
        as_of = event.payload.get("created_at") or event.timestamp

        if object_type == "mechanism":
            if self.mechanisms is None or self.discovery is None:
                return
            mechanism = self.mechanisms.latest(object_id)
            if mechanism is None:
                return
            evidence_links = json.loads(mechanism["evidence_links"])
            categories = json.loads(mechanism["affected_categories"])
            episode_ids = json.loads(mechanism["supporting_episodes"])
            signal_keys = json.loads(mechanism["supporting_signals"])
            sources = evidence_links.get("observations") or self.discovery.sources_for_signals(
                episode_ids, signal_keys
            )
            # Mechanisms created before the Stage 1 lineage fix have no
            # "observations" key at all; deriving live (same call the
            # factor branch always uses) covers real historical rows
            # without requiring every old mechanism to be regenerated.
        else:
            if self.factors is None or self.discovery is None:
                return
            factor = self.factors.latest(object_id)
            if factor is None:
                return
            signal_keys = json.loads(factor["signals"])
            episode_ids = json.loads(factor["supporting_episodes"])
            sources = self.discovery.sources_for_signals(episode_ids, signal_keys)
            categories = json.loads(factor["categories"])

        need_cluster = categories[0] if categories else "unknown"
        lead_time = self._avg_lead_time(episode_ids, signal_keys)
        # Real, per-occurrence facts (Requirement 2): redundant if this
        # object had more than one contributing source (this source's
        # evidence was not exclusive); novel if this is the first real
        # passed validation for this need_cluster across all prior history.
        redundant = len(sources) > 1
        novel = passed and self._is_first_passed_for_cluster(
            object_type, need_cluster, exclude_object_id=object_id, before=as_of
        )
        for source_id in sources:
            cost = self._recent_latency_ms(source_id)
            self.record(
                source_id, need_cluster, passed, lead_time_days=lead_time,
                redundant=redundant, novel=novel, compute_cost_ms=cost,
            )
            if self.registry is not None:
                self.registry.update_contribution(source_id, self.global_contribution(source_id))

    def _is_first_passed_for_cluster(
        self, object_type: str, need_cluster: str, exclude_object_id: str, before
    ) -> bool:
        """Real check against the permanent ``validations`` record: has any
        OTHER object with this need_cluster passed strictly before ``before``
        (the real timestamp of the event under evaluation)? Filtering by
        time is required for a correct replay of history -- without it, the
        query sees the whole, already-complete table rather than the state
        as of this event, and nothing is ever "first" on a backfill."""
        before = timeutil.ensure_utc(before) if not isinstance(before, str) else timeutil.from_iso(before)
        validation_db = self.databases.get("validation")
        rows = validation_db.query_dicts(
            "SELECT DISTINCT object_id FROM validations WHERE object_type = ? AND outcome = 'passed' AND created_at < ?",
            [object_type, before],
        )
        store = self.factors if object_type == "environmental_factor" else self.mechanisms
        category_field = "categories" if object_type == "environmental_factor" else "affected_categories"
        for row in rows:
            if row["object_id"] == exclude_object_id:
                continue
            obj = store.latest(row["object_id"]) if store else None
            if obj is None:
                continue
            if need_cluster in json.loads(obj[category_field]):
                return False
        return True

    def _recent_latency_ms(self, source_id: str) -> float | None:
        if self.registry is None:
            return None
        runs = self.registry.runs(source_id, limit=1)
        return runs[0]["latency_ms"] if runs else None

    def _avg_lead_time(self, episode_ids: list[str], signal_keys: list[str]) -> float | None:
        lead_times = self.discovery.episode_lead_times(episode_ids)
        values = [
            lead_times[eid][key]
            for eid in episode_ids
            for key in signal_keys
            if eid in lead_times and key in lead_times[eid]
        ]
        return sum(values) / len(values) if values else None

    def global_contribution(self, source_id: str) -> float:
        """Weighted-average net_value_ema across need_clusters, weighted by
        sample_size -- the value ``SourceRegistry.update_contribution`` (its
        first real caller, once wired in stage 2) should be fed with."""
        rows = self.for_source(source_id)
        total_weight = sum(r["sample_size"] for r in rows)
        if total_weight == 0:
            return 0.0
        return sum(r["net_value_ema"] * r["sample_size"] for r in rows) / total_weight

    def refresh_storage_estimate(self, source_id: str) -> float | None:
        """Real byte estimate: row count for this source in the real
        ``observations`` table times the average serialized row width of a
        real sample -- queried directly, not modeled (Requirement 2)."""
        count_row = self.db.query_one("SELECT count(*) FROM observations WHERE source_id = ?", [source_id])
        count = count_row[0] if count_row else 0
        if count == 0:
            return None
        sample = self.db.query_dicts("SELECT * FROM observations WHERE source_id = ? LIMIT 200", [source_id])
        avg_width = sum(len(json.dumps(dict(row), default=str)) for row in sample) / len(sample)
        estimate = count * avg_width
        self.db.execute(
            "UPDATE source_value_records SET storage_bytes_est = ? WHERE source_id = ?", [estimate, source_id]
        )
        return estimate

    def learned_facts(self, source_id: str) -> list[str]:
        """Real numbers, turned into real sentences (Requirement 4) -- every
        clause below reads directly from a stored row; nothing is
        templated with an invented number."""
        facts = []
        for row in self.for_source(source_id):
            lower, upper = wilson_interval(row["supports"], row["sample_size"])
            width = wilson_width(row["supports"], row["sample_size"])
            verdict = (
                "confirmed poor" if row["sample_size"] >= 5 and upper < 0.5
                else "unproven" if row["sample_size"] < 5
                else "confirmed useful"
            )
            sentence = (
                f"{source_id} / {row['need_cluster']}: {row['supports']}/{row['sample_size']} real outcomes "
                f"supported (wilson=[{lower:.2f},{upper:.2f}], width={width:.2f}) -- {verdict}"
            )
            if row["novelty_count"]:
                sentence += f"; contributed to {row['novelty_count']} first-ever validated finding(s) for this cluster"
            if row["lead_time_days_avg"] is not None:
                sentence += f"; average real lead time {row['lead_time_days_avg']:.1f} days before the spike"
            facts.append(sentence)
        return facts
