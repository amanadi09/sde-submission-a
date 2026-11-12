from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy import select

from au_ledger.db.models import Event, Lead
from au_ledger.ingestion.events import ingest_event_file, reconcile_events
from au_ledger.ingestion.leads import ingest_lead_file


def _write_jsonl(tmp_path: Path, name: str, lines: list[str]) -> Path:
    path = tmp_path / name
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _create_lead(session_factory, registry, cipher, hmac_key, tmp_path, phone="9876543210"):
    schema = registry.get_schema("motor_renewal")
    csv_path = tmp_path / "leads.csv"
    csv_path.write_text(
        "customer_name,phone,policy_number,policy_expiry,premium_amount,preferred_language\n"
        f"Aarav Sharma,{phone},MOT/2026/000001,2026-12-01,1234.50,en\n",
        encoding="utf-8",
    )
    with session_factory() as session:
        ingest_lead_file(session, schema, cipher, hmac_key, "motor_renewal", "batch_001", csv_path)
        session.commit()


def test_malformed_line_and_missing_event_id(tmp_path, session_factory, hmac_key):
    lines = [
        "not valid json {{{",
        json.dumps({"event_type": "call.initiated", "workflow_id": "motor_renewal",
                     "call_id": "call_1", "occurred_at": "2026-10-05T13:20:21Z"}),  # no event_id
        json.dumps({"event_id": "evt_1", "event_type": "call.initiated", "workflow_id": "motor_renewal",
                     "call_id": "call_1", "occurred_at": "2026-10-05T13:20:21Z", "phone": "9876543210"}),
    ]
    path = _write_jsonl(tmp_path, "events_0001.jsonl", lines)
    with session_factory() as session:
        stats = ingest_event_file(session, path, hmac_key)
        session.commit()
        assert stats.events_total == 3
        assert stats.malformed_events == 2
        assert session.scalars(select(Event)).one().event_id == "evt_1"


def test_duplicate_event_id_within_file_dropped(tmp_path, session_factory, hmac_key):
    event = {"event_id": "evt_1", "event_type": "call.initiated", "workflow_id": "motor_renewal",
              "call_id": "call_1", "occurred_at": "2026-10-05T13:20:21Z", "phone": "9876543210"}
    lines = [json.dumps(event), json.dumps(event)]
    path = _write_jsonl(tmp_path, "events_0001.jsonl", lines)
    with session_factory() as session:
        stats = ingest_event_file(session, path, hmac_key)
        session.commit()
        assert stats.duplicate_events_dropped == 1
        assert len(session.scalars(select(Event)).all()) == 1


def test_duplicate_event_id_across_drops_dropped(tmp_path, session_factory, hmac_key):
    event = {"event_id": "evt_1", "event_type": "call.initiated", "workflow_id": "motor_renewal",
              "call_id": "call_1", "occurred_at": "2026-10-05T13:20:21Z", "phone": "9876543210"}
    path1 = _write_jsonl(tmp_path, "drop1.jsonl", [json.dumps(event)])
    path2 = _write_jsonl(tmp_path, "drop2.jsonl", [json.dumps(event)])
    with session_factory() as session:
        ingest_event_file(session, path1, hmac_key)
        session.commit()
        stats2 = ingest_event_file(session, path2, hmac_key)
        session.commit()
        assert stats2.duplicate_events_dropped == 1
        assert len(session.scalars(select(Event)).all()) == 1


def test_event_links_to_lead_by_phone(tmp_path, session_factory, registry, cipher, hmac_key):
    _create_lead(session_factory, registry, cipher, hmac_key, tmp_path)
    event = {"event_id": "evt_1", "event_type": "call.initiated", "workflow_id": "motor_renewal",
              "call_id": "call_1", "occurred_at": "2026-10-05T13:20:21Z", "phone": "9876543210"}
    path = _write_jsonl(tmp_path, "events.jsonl", [json.dumps(event)])
    with session_factory() as session:
        ingest_event_file(session, path, hmac_key)
        reconcile_events(session, registry)
        session.commit()
        db_event = session.scalars(select(Event)).one()
        assert db_event.link_status == "linked"
        lead = session.scalars(select(Lead)).one()
        assert db_event.lead_id == lead.id


def test_orphan_event_reconciled_once_lead_arrives(
    tmp_path, session_factory, registry, cipher, hmac_key
):
    event = {"event_id": "evt_1", "event_type": "call.initiated", "workflow_id": "motor_renewal",
              "call_id": "call_1", "occurred_at": "2026-10-05T13:20:21Z", "phone": "9876543210"}
    path = _write_jsonl(tmp_path, "events.jsonl", [json.dumps(event)])

    with session_factory() as session:
        ingest_event_file(session, path, hmac_key)
        reconcile_events(session, registry)
        session.commit()
        db_event = session.scalars(select(Event)).one()
        assert db_event.link_status == "orphan"

    # Lead arrives in a later drop.
    _create_lead(session_factory, registry, cipher, hmac_key, tmp_path)

    with session_factory() as session:
        reconcile_events(session, registry)
        session.commit()
        db_event = session.scalars(select(Event)).one()
        assert db_event.link_status == "linked"


def test_unknown_disposition_flagged(tmp_path, session_factory, registry, cipher, hmac_key):
    _create_lead(session_factory, registry, cipher, hmac_key, tmp_path)
    init = {"event_id": "evt_1", "event_type": "call.initiated", "workflow_id": "motor_renewal",
             "call_id": "call_1", "occurred_at": "2026-10-05T13:20:21Z", "phone": "9876543210"}
    disp = {"event_id": "evt_2", "event_type": "disposition.set", "workflow_id": "motor_renewal",
             "call_id": "call_1", "occurred_at": "2026-10-05T13:21:21Z", "phone": "9876543210",
             "payload": {"disposition_code": "not_a_real_code"}}
    path = _write_jsonl(tmp_path, "events.jsonl", [json.dumps(init), json.dumps(disp)])
    with session_factory() as session:
        ingest_event_file(session, path, hmac_key)
        unknown_count = reconcile_events(session, registry)
        session.commit()
        assert unknown_count == 1
        disp_event = session.scalar(select(Event).where(Event.event_id == "evt_2"))
        assert disp_event.disposition_status == "unknown"


def test_disposition_override_links_via_call_id_no_phone(
    tmp_path, session_factory, registry, cipher, hmac_key
):
    _create_lead(session_factory, registry, cipher, hmac_key, tmp_path)
    init = {"event_id": "evt_1", "event_type": "call.initiated", "workflow_id": "motor_renewal",
             "call_id": "call_1", "occurred_at": "2026-10-05T13:20:21Z", "phone": "9876543210"}
    override = {"event_id": "evt_2", "event_type": "disposition.override", "workflow_id": "motor_renewal",
                 "call_id": "call_1", "occurred_at": "2026-10-06T13:20:21Z",
                 "payload": {"disposition_code": "renewed", "overridden_by": "supervisor_1",
                             "reason": "qa_review"}}
    path = _write_jsonl(tmp_path, "events.jsonl", [json.dumps(init), json.dumps(override)])
    with session_factory() as session:
        ingest_event_file(session, path, hmac_key)
        reconcile_events(session, registry)
        session.commit()
        override_event = session.scalar(select(Event).where(Event.event_id == "evt_2"))
        assert override_event.link_status == "linked"
        assert override_event.lead_id is not None


def test_reconcile_is_idempotent(tmp_path, session_factory, registry, cipher, hmac_key):
    _create_lead(session_factory, registry, cipher, hmac_key, tmp_path)
    event = {"event_id": "evt_1", "event_type": "call.initiated", "workflow_id": "motor_renewal",
              "call_id": "call_1", "occurred_at": "2026-10-05T13:20:21Z", "phone": "9876543210"}
    path = _write_jsonl(tmp_path, "events.jsonl", [json.dumps(event)])
    with session_factory() as session:
        ingest_event_file(session, path, hmac_key)
        reconcile_events(session, registry)
        session.commit()
        first_run_count = reconcile_events(session, registry)
        session.commit()
        assert first_run_count == 0
