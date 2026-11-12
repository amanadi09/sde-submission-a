"""Event timestamp parsing and IST date derivation.

Three input forms are accepted:
  - UTC with a trailing ``Z``: ``2026-10-05T13:20:21Z``
  - An explicit offset: ``2026-10-05T18:50:21+05:30``
  - Naive (no timezone): ``2026-10-03 10:01:00`` -- interpreted as IST per the spec.

India uses a single fixed UTC+05:30 offset with no daylight-saving rules, so a
constant ``timezone`` object is used rather than pulling in ``zoneinfo``/``tzdata``.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

IST = timezone(timedelta(hours=5, minutes=30))


def parse_event_timestamp(raw: str) -> datetime:
    """Parse any of the three accepted forms into an aware UTC ``datetime``."""
    text = raw.strip()
    if text.endswith("Z"):
        dt = datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    elif "T" in text:
        dt = datetime.fromisoformat(text)
    else:
        dt = datetime.strptime(text, "%Y-%m-%d %H:%M:%S").replace(tzinfo=IST)
    return dt.astimezone(timezone.utc)


def to_ist_date(dt_utc: datetime) -> date:
    """Return the IST calendar date for an aware UTC ``datetime``."""
    return dt_utc.astimezone(IST).date()


def to_ist_isoformat(dt_utc: datetime) -> str:
    """Render an aware UTC ``datetime`` as ISO-8601 in IST, e.g. ``...T17:55:24+05:30``."""
    return dt_utc.astimezone(IST).isoformat()
