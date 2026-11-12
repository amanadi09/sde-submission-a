"""Pure business logic over the interaction ledger.

Deliberately has no database or I/O dependency: every function takes plain,
already-loaded data and returns plain data. This keeps the rules themselves easy
to unit test and to reason about independently of how events are stored or
queried, and matches the "derived facts" section of the assignment README.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from au_ledger.normalization.timestamps import to_ist_date

# Event types relevant to business-rule computation.
CALL_INITIATED = "call.initiated"
CALL_CONNECTED = "call.connected"
CALL_TELEPHONY_FAILED = "call.telephony_failed"
DISPOSITION_SET = "disposition.set"
DISPOSITION_OVERRIDE = "disposition.override"


@dataclass(frozen=True)
class LedgerEvent:
    """A minimal, ORM-independent view of one linked, non-malformed event."""

    event_type: str
    call_id: str
    occurred_at_utc: datetime
    payload: dict
    is_known_disposition: bool = True


@dataclass(frozen=True)
class CallFacts:
    call_id: str
    initiated_at_utc: datetime
    connected: bool
    telephony_failed: bool
    effective_disposition: str | None


def compute_calls(events: list[LedgerEvent]) -> list[CallFacts]:
    """Group linked events by call_id and compute per-call facts.

    A call only exists in the result if it has a ``call.initiated`` event -- per
    spec, calls without one are not counted at all, even if other event types for
    that ``call_id`` exist.
    """
    by_call: dict[str, list[LedgerEvent]] = {}
    for event in events:
        by_call.setdefault(event.call_id, []).append(event)

    calls: list[CallFacts] = []
    for call_id, call_events in by_call.items():
        initiated = [e for e in call_events if e.event_type == CALL_INITIATED]
        if not initiated:
            continue
        initiated_at = min(e.occurred_at_utc for e in initiated)

        connected = any(e.event_type == CALL_CONNECTED for e in call_events)
        telephony_failed = any(e.event_type == CALL_TELEPHONY_FAILED for e in call_events)

        overrides = [
            e for e in call_events if e.event_type == DISPOSITION_OVERRIDE and e.is_known_disposition
        ]
        sets_ = [
            e for e in call_events if e.event_type == DISPOSITION_SET and e.is_known_disposition
        ]
        effective_disposition = _latest_code(overrides) or _latest_code(sets_)

        calls.append(
            CallFacts(
                call_id=call_id,
                initiated_at_utc=initiated_at,
                connected=connected,
                telephony_failed=telephony_failed,
                effective_disposition=effective_disposition,
            )
        )
    return sorted(calls, key=lambda c: c.initiated_at_utc)


def _latest_code(events: list[LedgerEvent]) -> str | None:
    if not events:
        return None
    latest = max(events, key=lambda e: e.occurred_at_utc)
    return latest.payload.get("disposition_code")


def compute_bucket(
    calls: list[CallFacts],
    disposition_flags: dict[str, tuple[bool, bool]],
) -> str:
    """First matching rule wins: yet_to_reach_out > completed > telephony_failed > dialed."""
    if not calls:
        return "yet_to_reach_out"

    for call in calls:
        if call.effective_disposition is not None:
            is_terminal, _ = disposition_flags[call.effective_disposition]
            if is_terminal:
                return "completed"

    if all(call.telephony_failed for call in calls):
        return "in_progress_telephony_failed"

    return "in_progress_dialed"


def ever_success(calls: list[CallFacts], disposition_flags: dict[str, tuple[bool, bool]]) -> bool:
    for call in calls:
        if call.effective_disposition is not None:
            _, is_success = disposition_flags[call.effective_disposition]
            if is_success:
                return True
    return False


def current_disposition(calls: list[CallFacts]) -> str | None:
    """Effective disposition of the most recent call (by initiated time) that has one."""
    for call in sorted(calls, key=lambda c: c.initiated_at_utc, reverse=True):
        if call.effective_disposition is not None:
            return call.effective_disposition
    return None


def success_leads_on_date(
    calls_by_lead: dict[str, list[CallFacts]],
    ist_date,
    disposition_flags: dict[str, tuple[bool, bool]],
) -> int:
    """Count distinct leads with >=1 call on ``ist_date`` whose effective disposition is a success."""
    count = 0
    for calls in calls_by_lead.values():
        for call in calls:
            if to_ist_date(call.initiated_at_utc) != ist_date:
                continue
            if call.effective_disposition is None:
                continue
            _, is_success = disposition_flags[call.effective_disposition]
            if is_success:
                count += 1
                break
    return count
