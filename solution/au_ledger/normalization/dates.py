"""Lead date-field parsing.

Accepted formats (all day-first): ``YYYY-MM-DD``, ``DD/MM/YYYY``, ``DD-MM-YYYY``,
``DD-Mon-YYYY`` (month name case-insensitive, e.g. ``01-Dec-2026``). Impossible
calendar dates (e.g. 31/02) are invalid.
"""
from __future__ import annotations

from datetime import date

_FORMATS = ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d-%b-%Y")


def parse_lead_date(raw: str) -> date | None:
    """Parse a lead date field, returning ``None`` if it matches no accepted format."""
    text = raw.strip()
    for fmt in _FORMATS:
        try:
            # %b is locale-dependent for some locales; the generator and spec both
            # use English month abbreviations, so title-case the token for %b to
            # make matching case-insensitive without depending on locale settings.
            candidate = text
            if fmt == "%d-%b-%Y":
                parts = text.split("-")
                if len(parts) == 3:
                    parts[1] = parts[1].capitalize()
                    candidate = "-".join(parts)
            from datetime import datetime

            return datetime.strptime(candidate, fmt).date()
        except ValueError:
            continue
    return None
