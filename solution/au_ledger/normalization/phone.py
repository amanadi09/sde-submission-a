"""Indian mobile phone number normalization.

Rules (from the assignment spec):
  1. Strip every non-digit character.
  2. If the result is 12 digits and starts with ``91``, drop the ``91`` prefix.
  3. If the result is 11 digits and starts with ``0``, drop the leading ``0``.
  4. The remainder is valid only if it is exactly 10 digits starting with 6/7/8/9.
  5. Canonical form is ``+91XXXXXXXXXX``.
"""
from __future__ import annotations

import re

_NON_DIGIT = re.compile(r"\D+")


def normalize_phone(raw: str) -> str | None:
    """Return the canonical ``+91XXXXXXXXXX`` form, or ``None`` if invalid."""
    digits = _NON_DIGIT.sub("", raw)

    if len(digits) == 12 and digits.startswith("91"):
        digits = digits[2:]
    elif len(digits) == 11 and digits.startswith("0"):
        digits = digits[1:]

    if len(digits) == 10 and digits[0] in "6789":
        return f"+91{digits}"
    return None
