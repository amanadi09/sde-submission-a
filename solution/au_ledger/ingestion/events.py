"""Event JSONL ingestion into the append-only interaction ledger.

Per line, in order:

1. Malformed (not valid JSON, or no ``event_id``) -> counted, skipped, never stored.
2. Duplicate ``event_id`` (seen in this or any earlier ``ingest`` call) -> counted,
   skipped. Enforced at the database level: inserting an already-present
   ``event_id`` primary key raises an ``IntegrityError``.
3. Orphan (can't be linked to a lead yet) -> stored with ``link_status="orphan"``,
   excluded from every report until it is reconciled.
4. Unknown disposition (a ``disposition.*`` event whose code isn't in the
   catalogue for that workflow) -> counted, and the event is stored but flagged
   ``disposition_status="unknown"`` so business logic ignores it as a source of
   effective disposition.

Linking happens in two passes, run on *every* ``ingest`` call over all currently
unresolved events (not just the current drop) -- this is what makes "an orphan
after drop_1 counts once its lead arrives in drop_2" work, at the cost of
re-scanning unresolved events each time (a documented, deliberate trade-off
against "true" incremental processing; see SUBMISSION.md):

- Pass 1 (phone-based): events with a ``phone`` field link via
  ``(workflow_id, normalized phone)`` to a lead.
- Pass 2 (call_id-based): ``disposition.override`` events have no ``phone``;
  they link through the lead already resolved for any other event sharing the
  same ``call_id``.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from au_ledger.db.models import Event, Lead
from au_ledger.normalization.phone import normalize_phone
from au_ledger.normalization.timestamps import parse_event_timestamp
from au_ledger.schema.registry import SchemaRegistry
from au_ledger.security.crypto import hash_phone

DISPOSITION_EVENT_PREFIX = "disposition."


@dataclass
class EventIngestStats:
    events_total: int = 0
    malformed_events: int = 0
    duplicate_events_dropped: int = 0
    unknown_disposition_events: int = 0


def ingest_event_file(session: Session, jsonl_path: Path, hmac_key: bytes) -> EventIngestStats:
    stats = EventIngestStats()

    with jsonl_path.open(encoding="utf-8") as fh:
        for raw_line in fh:
            line = raw_line.strip()
            if line == "":
                continue
            stats.events_total += 1

            event = _parse_line(line)
            if event is None:
                stats.malformed_events += 1
                continue

            # Checked explicitly (rather than relying solely on catching the
            # primary-key IntegrityError) so one duplicate line never forces a
            # rollback of other, valid inserts already pending in this
            # transaction. The primary key still protects against a concurrent
            # writer inserting the same event_id between this check and the
            # final commit -- see the nested-transaction fallback below.
            if session.get(Event, event["event_id"]) is not None:
                stats.duplicate_events_dropped += 1
                continue

            phone_hash = None
            phone = event.get("phone")
            if phone is not None:
                normalized = normalize_phone(phone)
                if normalized is not None:
                    phone_hash = hash_phone(normalized, hmac_key)

            db_event = Event(
                event_id=event["event_id"],
                event_type=event["event_type"],
                workflow_id=event["workflow_id"],
                call_id=event["call_id"],
                occurred_at_utc=parse_event_timestamp(event["occurred_at"]),
                phone_hash=phone_hash,
                payload=event.get("payload", {}),
                lead_id=None,
                link_status="orphan",
                disposition_status=None,
            )

            try:
                with session.begin_nested():
                    session.add(db_event)
                    session.flush()
            except IntegrityError:
                stats.duplicate_events_dropped += 1
                continue

    return stats


def _parse_line(line: str) -> dict | None:
    try:
        event = json.loads(line)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(event, dict):
        return None
    if not event.get("event_id"):
        return None
    required = ("event_type", "workflow_id", "call_id", "occurred_at")
    if any(event.get(key) is None for key in required):
        return None
    return event


def reconcile_events(session: Session, registry: SchemaRegistry) -> int:
    """Re-run linking + unknown-disposition checks over all unresolved events.

    Returns the number of ``disposition.*`` events newly found to use an unknown
    code (added to the running ``unknown_disposition_events`` counter by the
    caller). Idempotent: running it twice in a row with no new data changes
    nothing on the second run.
    """
    newly_unknown = 0

    # Pass 1: phone-based linking.
    unresolved_with_phone = session.scalars(
        select(Event).where(Event.link_status == "orphan", Event.phone_hash.is_not(None))
    ).all()
    for event in unresolved_with_phone:
        lead = session.scalar(
            select(Lead).where(Lead.workflow_id == event.workflow_id, Lead.phone_hash == event.phone_hash)
        )
        if lead is not None:
            event.lead_id = lead.id
            event.link_status = "linked"
            newly_unknown += _check_disposition(event, registry)

    # Pass 2: call_id-based linking for events without a phone (overrides).
    unresolved_without_phone = session.scalars(
        select(Event).where(Event.link_status == "orphan", Event.phone_hash.is_(None))
    ).all()
    for event in unresolved_without_phone:
        sibling = session.scalar(
            select(Event).where(
                Event.call_id == event.call_id,
                Event.link_status == "linked",
                Event.lead_id.is_not(None),
            )
        )
        if sibling is not None:
            event.lead_id = sibling.lead_id
            event.link_status = "linked"
            newly_unknown += _check_disposition(event, registry)

    return newly_unknown


def _check_disposition(event: Event, registry: SchemaRegistry) -> int:
    if not event.event_type.startswith(DISPOSITION_EVENT_PREFIX):
        return 0
    code = event.payload.get("disposition_code")
    if code is None or not registry.is_known_disposition(event.workflow_id, code):
        event.disposition_status = "unknown"
        return 1
    event.disposition_status = "known"
    return 0
