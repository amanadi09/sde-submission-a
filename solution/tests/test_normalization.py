from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from au_ledger.normalization.dates import parse_lead_date
from au_ledger.normalization.decimal_ import parse_decimal
from au_ledger.normalization.enum_ import parse_enum
from au_ledger.normalization.phone import normalize_phone
from au_ledger.normalization.timestamps import parse_event_timestamp, to_ist_date, to_ist_isoformat


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("9876543210", "+919876543210"),
        ("+91 98765 43210", "+919876543210"),
        ("098765 43210", "+919876543210"),
        ("91-98765-43210", "+919876543210"),
        ("(+91) 9876543210", "+919876543210"),
        ("+919876543210", "+919876543210"),
    ],
)
def test_normalize_phone_valid_forms(raw, expected):
    assert normalize_phone(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "123456789",  # 9 digits
        "5876543210",  # bad leading digit
        "022-2345-6789",  # landline
        "N/A",
        "+1 415 555 0134",  # non-Indian number
    ],
)
def test_normalize_phone_invalid_forms(raw):
    assert normalize_phone(raw) is None


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("2026-12-01", date(2026, 12, 1)),
        ("01/12/2026", date(2026, 12, 1)),
        ("01-12-2026", date(2026, 12, 1)),
        ("01-Dec-2026", date(2026, 12, 1)),
        ("01-dec-2026", date(2026, 12, 1)),
    ],
)
def test_parse_lead_date_valid(raw, expected):
    assert parse_lead_date(raw) == expected


@pytest.mark.parametrize("raw", ["31/02/2026", "soon", "2026-13-01", "TBD", ""])
def test_parse_lead_date_invalid(raw):
    assert parse_lead_date(raw) is None


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("1234.5", Decimal("1234.50")),
        ("₹1,234", Decimal("1234.00")),
        ("Rs. 1234", Decimal("1234.00")),
        ("Rs1234", Decimal("1234.00")),
        ("INR 1234.567", Decimal("1234.57")),
        ("0", Decimal("0.00")),
    ],
)
def test_parse_decimal_valid(raw, expected):
    assert parse_decimal(raw) == expected


@pytest.mark.parametrize("raw", ["-100", "twelve thousand", "N/A", "-", ""])
def test_parse_decimal_invalid(raw):
    assert parse_decimal(raw) is None


def test_parse_enum_case_insensitive():
    assert parse_enum("EN", ["en", "hi"]) == "en"
    assert parse_enum(" Hi ", ["en", "hi"]) == "hi"


def test_parse_enum_invalid():
    assert parse_enum("french", ["en", "hi"]) is None


def test_parse_event_timestamp_utc():
    dt = parse_event_timestamp("2026-10-05T13:20:21Z")
    assert to_ist_isoformat(dt) == "2026-10-05T18:50:21+05:30"


def test_parse_event_timestamp_offset():
    dt = parse_event_timestamp("2026-10-05T18:50:21+05:30")
    assert to_ist_isoformat(dt) == "2026-10-05T18:50:21+05:30"


def test_parse_event_timestamp_naive_is_ist():
    dt = parse_event_timestamp("2026-10-03 10:01:00")
    assert to_ist_isoformat(dt) == "2026-10-03T10:01:00+05:30"
    assert to_ist_date(dt) == date(2026, 10, 3)
