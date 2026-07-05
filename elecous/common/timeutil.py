"""Time utilities. Appendix A Principle 8: Time Is A Dimension.

Every timestamp inside ELECOUS is timezone-aware UTC. Naive datetimes are a
constitution violation because they break reproducibility (Law 3).
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

ISO = "%Y-%m-%dT%H:%M:%S.%f%z"


def utcnow() -> datetime:
    return datetime.now(UTC)


def ensure_utc(ts: datetime) -> datetime:
    """Reject naive datetimes; normalize aware ones to UTC."""
    if ts.tzinfo is None:
        raise ValueError("naive datetime is forbidden (reproducibility, Law 3)")
    return ts.astimezone(UTC)


def to_iso(ts: datetime) -> str:
    return ensure_utc(ts).isoformat()


def from_iso(text: str) -> datetime:
    ts = datetime.fromisoformat(text)
    if ts.tzinfo is None:
        # Dates from public sources without zone info are interpreted as UTC
        # and flagged by the observation validator, never silently localized.
        ts = ts.replace(tzinfo=UTC)
    return ts.astimezone(UTC)


def day_of(ts: datetime) -> date:
    return ensure_utc(ts).date()


def days_ago(n: int, now: datetime | None = None) -> datetime:
    return (now or utcnow()) - timedelta(days=n)


def date_range(start: date, end: date) -> list[date]:
    """Inclusive list of days from start to end."""
    if end < start:
        raise ValueError("end before start")
    return [start + timedelta(days=i) for i in range((end - start).days + 1)]


def season_of(month: int) -> str:
    """Meteorological (northern-hemisphere-convention) season for a
    calendar month. Consolidated here (rewrite directive cleanup) from two
    independent redefinitions in environment/engine.py and
    scripts/evidence_report.py."""
    return ["winter", "winter", "spring", "spring", "spring", "summer",
            "summer", "summer", "autumn", "autumn", "autumn", "winter"][month - 1]
