from __future__ import annotations

from datetime import datetime, timezone

from au_ledger.business.rules import (
    LedgerEvent,
    compute_bucket,
    compute_calls,
    current_disposition,
    ever_success,
)

FLAGS = {
    "renewed": (True, True),
    "callback_requested": (False, False),
    "not_interested": (True, False),
}


def _ts(s: str) -> datetime:
    return datetime.fromisoformat(s).replace(tzinfo=timezone.utc)


def test_call_without_initiated_is_not_counted():
    events = [
        LedgerEvent("call.connected", "call_1", _ts("2026-10-05T10:00:00"), {}),
    ]
    assert compute_calls(events) == []


def test_connected_flag():
    events = [
        LedgerEvent("call.initiated", "call_1", _ts("2026-10-05T10:00:00"), {}),
        LedgerEvent("call.connected", "call_1", _ts("2026-10-05T10:00:10"), {}),
    ]
    calls = compute_calls(events)
    assert len(calls) == 1
    assert calls[0].connected is True


def test_effective_disposition_override_beats_set():
    events = [
        LedgerEvent("call.initiated", "call_1", _ts("2026-10-05T10:00:00"), {}),
        LedgerEvent("disposition.set", "call_1", _ts("2026-10-05T10:05:00"),
                     {"disposition_code": "callback_requested"}),
        LedgerEvent("disposition.override", "call_1", _ts("2026-10-06T10:00:00"),
                     {"disposition_code": "renewed"}),
    ]
    calls = compute_calls(events)
    assert calls[0].effective_disposition == "renewed"


def test_effective_disposition_latest_set_when_no_override():
    events = [
        LedgerEvent("call.initiated", "call_1", _ts("2026-10-05T10:00:00"), {}),
        LedgerEvent("disposition.set", "call_1", _ts("2026-10-05T10:05:00"),
                     {"disposition_code": "callback_requested"}),
        LedgerEvent("disposition.set", "call_1", _ts("2026-10-05T10:06:00"),
                     {"disposition_code": "renewed"}),
    ]
    calls = compute_calls(events)
    assert calls[0].effective_disposition == "renewed"


def test_unknown_disposition_excluded_from_effective():
    events = [
        LedgerEvent("call.initiated", "call_1", _ts("2026-10-05T10:00:00"), {}),
        LedgerEvent("disposition.set", "call_1", _ts("2026-10-05T10:05:00"),
                     {"disposition_code": "bogus"}, is_known_disposition=False),
    ]
    calls = compute_calls(events)
    assert calls[0].effective_disposition is None


def test_bucket_yet_to_reach_out_when_no_calls():
    assert compute_bucket([], FLAGS) == "yet_to_reach_out"


def test_bucket_completed_when_any_call_terminal():
    events = [
        LedgerEvent("call.initiated", "call_1", _ts("2026-10-05T10:00:00"), {}),
        LedgerEvent("disposition.set", "call_1", _ts("2026-10-05T10:05:00"),
                     {"disposition_code": "not_interested"}),
    ]
    calls = compute_calls(events)
    assert compute_bucket(calls, FLAGS) == "completed"


def test_bucket_telephony_failed_when_every_call_failed():
    events = [
        LedgerEvent("call.initiated", "call_1", _ts("2026-10-05T10:00:00"), {}),
        LedgerEvent("call.telephony_failed", "call_1", _ts("2026-10-05T10:00:01"), {}),
        LedgerEvent("call.initiated", "call_2", _ts("2026-10-06T10:00:00"), {}),
        LedgerEvent("call.telephony_failed", "call_2", _ts("2026-10-06T10:00:01"), {}),
    ]
    calls = compute_calls(events)
    assert compute_bucket(calls, FLAGS) == "in_progress_telephony_failed"


def test_bucket_dialed_otherwise():
    events = [
        LedgerEvent("call.initiated", "call_1", _ts("2026-10-05T10:00:00"), {}),
        LedgerEvent("call.connected", "call_1", _ts("2026-10-05T10:00:05"), {}),
    ]
    calls = compute_calls(events)
    assert compute_bucket(calls, FLAGS) == "in_progress_dialed"


def test_ever_success():
    events = [
        LedgerEvent("call.initiated", "call_1", _ts("2026-10-05T10:00:00"), {}),
        LedgerEvent("disposition.set", "call_1", _ts("2026-10-05T10:05:00"),
                     {"disposition_code": "renewed"}),
    ]
    calls = compute_calls(events)
    assert ever_success(calls, FLAGS) is True


def test_current_disposition_is_most_recent_call_with_one():
    events = [
        LedgerEvent("call.initiated", "call_1", _ts("2026-10-05T10:00:00"), {}),
        LedgerEvent("disposition.set", "call_1", _ts("2026-10-05T10:05:00"),
                     {"disposition_code": "callback_requested"}),
        LedgerEvent("call.initiated", "call_2", _ts("2026-10-06T10:00:00"), {}),
    ]
    calls = compute_calls(events)
    assert current_disposition(calls) == "callback_requested"
