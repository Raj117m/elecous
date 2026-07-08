"""Observation Sandbox (spec 2.18, 12.10).

Every new connector runs here before production. Checks: schema consistency,
data freshness, repeatability, licensing compatibility, observation quality,
performance and resource consumption. Nothing enters production directly —
promotion only happens when every check passes.

The sandbox collects *real* public data twice (repeatability) and evaluates
the resulting observations through the same validator production uses.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from elecous.common import timeutil
from elecous.infrastructure.config import Config, active_config
from elecous.infrastructure.logging import get_logger
from elecous.observation.connectors.base import load_connector
from elecous.observation.registry.registry import SourceRegistry
from elecous.observation.validators.checks import ObservationValidator


@dataclass
class SandboxReport:
    source_id: str
    started_at: str
    checks: dict[str, bool] = field(default_factory=dict)
    detail: dict[str, Any] = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        return bool(self.checks) and all(self.checks.values())


class ObservationSandbox:
    def __init__(self, registry: SourceRegistry, config: Config | None = None):
        self.registry = registry
        self.config = config or active_config()
        self.validator = ObservationValidator()
        self.log = get_logger("observation")

    def evaluate(self, source_id: str) -> SandboxReport:
        source = self.registry.get(source_id)
        if source is None:
            raise KeyError(source_id)
        report = SandboxReport(source_id=source_id, started_at=timeutil.to_iso(timeutil.utcnow()))

        # Licensing compatibility: a declared license and legal access method
        # are mandatory before any request is made. Independence constraint
        # (roadmap R8): ELECOUS must remain commercially usable at scale, so
        # licenses that restrict commercial use are rejected at discovery
        # time — a source that cannot be licensed, self-hosted, or replaced
        # is a foundation the platform must never build on. (google_trends/
        # pytrends entered production by bypassing this sandbox entirely —
        # its access_method 'unofficial_api' has never been allowed here.)
        non_commercial_markers = (
            "non-commercial", "noncommercial", "nc 4.0", "by-nc", "research only",
            "research use only", "personal use", "evaluation only",
        )
        license_text = (source["license"] or "").lower()
        commercially_usable = bool(license_text) and not any(m in license_text for m in non_commercial_markers)
        report.checks["licensing"] = commercially_usable and source["access_method"] in (
            "official_api",
            "open_data",
            "public_dataset",
            "rss",
            "public_structured",
        )
        if not report.checks["licensing"]:
            return report

        connector = load_connector(source, config=self.config)
        try:
            first, latency_first = connector.timed_collect(mode="live", since=None)
            time.sleep(1.0)
            second, latency_second = connector.timed_collect(mode="live", since=None)
        except Exception as exc:
            report.checks["collection"] = False
            report.detail["error"] = f"{type(exc).__name__}: {exc}"
            return report
        report.checks["collection"] = True

        # Schema consistency between two runs of the same window.
        report.checks["schema_consistency"] = (
            first.schema_hash is not None and first.schema_hash == second.schema_hash
        )

        # Repeatability: the same window yields the same observation set.
        keys_first = {o.dedup_key: o.value for o in first.observations}
        keys_second = {o.dedup_key: o.value for o in second.observations}
        overlap = set(keys_first) & set(keys_second)
        stable = [k for k in overlap if keys_first[k] == keys_second[k]]
        report.checks["repeatability"] = bool(overlap) and len(stable) / len(overlap) >= 0.95
        report.detail["observations_first"] = len(first.observations)
        report.detail["observations_second"] = len(second.observations)

        # Observation quality: share passing full validation.
        outcomes = [self.validator.validate(o) for o in first.observations]
        valid = sum(1 for o in outcomes if o.passed)
        quality = valid / len(outcomes) if outcomes else 0.0
        report.checks["quality"] = quality >= 0.9 and valid > 0
        report.detail["validation_pass_rate"] = round(quality, 4)

        # Freshness: at least one observation from the recent window.
        newest = max((o.timestamp for o in first.observations), default=None)
        freshness_days = (timeutil.utcnow() - newest).days if newest else None
        report.checks["freshness"] = freshness_days is not None and freshness_days <= 14
        report.detail["newest_observation_age_days"] = freshness_days

        # Performance / resource consumption.
        report.checks["performance"] = latency_first < 120_000 and latency_second < 120_000
        report.detail["latency_ms"] = [round(latency_first, 1), round(latency_second, 1)]

        self.log.info("sandbox evaluation", source_id=source_id, passed=report.passed, checks=report.checks)
        return report

    def promote_if_passing(self, source_id: str) -> bool:
        """Sandbox -> production switch (spec 2.17): only after successful
        validation does a source enter production."""
        report = self.evaluate(source_id)
        if report.passed:
            self.registry.set_status(source_id, "active", reason="sandbox checks passed")
            self.registry.db.execute(
                "UPDATE source_registry SET last_validation = ?, updated_at = ? WHERE source_id = ?",
                [timeutil.utcnow(), timeutil.utcnow(), source_id],
            )
            return True
        self.registry.set_status(source_id, "sandbox", reason="sandbox checks failed")
        return False

    def evaluate_domain(self, domain: str) -> list[SandboxReport]:
        """Upgrade to evaluate all known sandbox sources for a specific domain.
        This provides mathematical reliability assessment for the new taxonomy."""
        reports = []
        for source in self.registry.list_by_domain(domain, status="sandbox"):
            reports.append(self.evaluate(source["source_id"]))
        return reports

    def promote_domain_if_passing(self, domain: str) -> dict[str, bool]:
        """Evaluates all sandbox sources for a domain and promotes them if passing."""
        results = {}
        for source in self.registry.list_by_domain(domain, status="sandbox"):
            results[source["source_id"]] = self.promote_if_passing(source["source_id"])
        return results
