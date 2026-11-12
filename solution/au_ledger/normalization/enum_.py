"""Lead enum-field parsing: case-insensitive match against allowed values."""
from __future__ import annotations


def parse_enum(raw: str, allowed_values: list[str]) -> str | None:
    """Return the lower-cased matching value, or ``None`` if no case-insensitive match."""
    text = raw.strip().lower()
    for value in allowed_values:
        if text == value.lower():
            return value.lower()
    return None
