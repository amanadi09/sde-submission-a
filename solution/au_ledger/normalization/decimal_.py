"""Lead decimal/currency-field parsing.

Accepted: optional ``₹``, ``Rs``, ``Rs.`` or ``INR`` prefix (case-insensitive,
optional following space), commas as thousands separators, must be >= 0, rounded
to 2 decimal places.
"""
from __future__ import annotations

import re
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

_PREFIX = re.compile(r"^(₹|rs\.?|inr)\s*", re.IGNORECASE)


def parse_decimal(raw: str) -> Decimal | None:
    """Parse a currency-ish value, returning ``None`` if invalid."""
    text = raw.strip()
    text = _PREFIX.sub("", text).strip()
    text = text.replace(",", "")
    if not text:
        return None
    try:
        value = Decimal(text)
    except InvalidOperation:
        return None
    if value < 0:
        return None
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
