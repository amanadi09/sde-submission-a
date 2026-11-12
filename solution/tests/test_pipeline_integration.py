"""End-to-end tests: idempotent re-ingest, and sequential vs combined ingestion equivalence."""
from __future__ import annotations

import json
from pathlib import Path

from au_ledger.db.base import create_all, make_engine, make_session_factory
from au_ledger.pipeline import ingest_drop
from au_ledger.reporting.reports import generate_reports

LEAD_HEADER = "customer_name,phone,policy_number,policy_expiry,premium_amount,preferred_language\n"


def _build_drop(
    base: Path, name: str, lead_rows: list[str], event_lines: list[str], batch_id: str = "batch_001"
) -> Path:
    drop_dir = base / name
    leads_dir = drop_dir / "leads" / "motor_renewal"
    leads_dir.mkdir(parents=True, exist_ok=True)
    (leads_dir / f"{batch_id}.csv").write_text(LEAD_HEADER + "".join(lead_rows), encoding="utf-8")

    events_dir = drop_dir / "events"
    events_dir.mkdir(parents=True, exist_ok=True)
    (events_dir / "events_0001.jsonl").write_text("\n".join(event_lines) + "\n", encoding="utf-8")
    return drop_dir


def _event(event_id, event_type, call_id, occurred_at, phone=None, payload=None):
    e = {"event_id": event_id, "event_type": event_type, "workflow_id": "motor_renewal",
         "call_id": call_id, "occurred_at": occurred_at, "payload": payload or {}}
    if phone is not None:
        e["phone"] = phone
    return json.dumps(e)


def _new_env(tmp_path, registry, cipher, hmac_key):
    engine = make_engine(f"sqlite:///{tmp_path / 'test.db'}")
    create_all(engine)
    session_factory = make_session_factory(engine)
    return session_factory


def test_reingesting_same_drop_is_idempotent(tmp_path, registry, cipher, hmac_key):
    data_dir = tmp_path / "data"
    drop1 = _build_drop(
        data_dir,
        "drop_1",
        ["Aarav Sharma,9876543210,MOT/2026/000001,2026-12-01,1234.50,en\n"],
        [_event("evt_1", "call.initiated", "call_1", "2026-10-05T13:20:21Z", phone="9876543210")],
    )
    session_factory = _new_env(tmp_path, registry, cipher, hmac_key)

    ingest_drop(session_factory, registry, cipher, hmac_key, drop1)
    out1 = tmp_path / "reports1"
    with session_factory() as session:
        generate_reports(session, registry, out1)

    ingest_drop(session_factory, registry, cipher, hmac_key, drop1)  # re-ingest, same files
    out2 = tmp_path / "reports2"
    with session_factory() as session:
        generate_reports(session, registry, out2)

    for name in ("batch_summary.csv", "daily_overview.csv", "lead_report.csv",
                  "rejections.csv", "data_quality.json"):
        assert (out1 / name).read_text() == (out2 / name).read_text(), f"{name} differs after re-ingest"


def test_sequential_drops_equal_combined_ingestion(tmp_path, registry, cipher, hmac_key):
    data_dir = tmp_path / "data"
    drop1 = _build_drop(
        data_dir,
        "drop_1",
        ["Aarav Sharma,9876543210,MOT/2026/000001,2026-12-01,1234.50,en\n"],
        [_event("evt_1", "call.initiated", "call_1", "2026-10-05T13:20:21Z", phone="9999999999")],
        # orphan on purpose (its lead arrives in drop_2)
        batch_id="batch_001",
    )
    drop2 = _build_drop(
        data_dir,
        "drop_2",
        ["Priya Rao,9999999999,MOT/2026/000002,2026-12-02,2000,hi\n"],
        [_event("evt_2", "disposition.set", "call_1", "2026-10-05T13:25:00Z",
                 phone="9999999999", payload={"disposition_code": "renewed"})],
        batch_id="batch_002",
    )

    # Sequential: drop_1 then drop_2 on one DB.
    seq_engine = make_engine(f"sqlite:///{tmp_path / 'seq.db'}")
    create_all(seq_engine)
    seq_factory = make_session_factory(seq_engine)
    ingest_drop(seq_factory, registry, cipher, hmac_key, drop1)
    ingest_drop(seq_factory, registry, cipher, hmac_key, drop2)
    seq_out = tmp_path / "seq_reports"
    with seq_factory() as session:
        generate_reports(session, registry, seq_out)

    # Combined: put both drops' lead/event files into one virtual "combined" drop
    # and ingest in one shot, into a *fresh* DB.
    combined_dir = tmp_path / "data" / "combined"
    (combined_dir / "leads" / "motor_renewal").mkdir(parents=True)
    (combined_dir / "events").mkdir(parents=True)
    (combined_dir / "leads" / "motor_renewal" / "batch_a.csv").write_text(
        (drop1 / "leads" / "motor_renewal" / "batch_001.csv").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (combined_dir / "leads" / "motor_renewal" / "batch_b.csv").write_text(
        (drop2 / "leads" / "motor_renewal" / "batch_002.csv").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (combined_dir / "events" / "events_a.jsonl").write_text(
        (drop1 / "events" / "events_0001.jsonl").read_text(encoding="utf-8"), encoding="utf-8"
    )
    (combined_dir / "events" / "events_b.jsonl").write_text(
        (drop2 / "events" / "events_0001.jsonl").read_text(encoding="utf-8"), encoding="utf-8"
    )
    combined_engine = make_engine(f"sqlite:///{tmp_path / 'combined.db'}")
    create_all(combined_engine)
    combined_factory = make_session_factory(combined_engine)
    ingest_drop(combined_factory, registry, cipher, hmac_key, combined_dir)
    combined_out = tmp_path / "combined_reports"
    with combined_factory() as session:
        generate_reports(session, registry, combined_out)

    # batch_id differs by construction (batch_001 vs batch_a/batch_b), so compare
    # everything except batch_summary.csv (which is keyed by batch_id) and
    # lead_report.csv's batch_id column indirectly via the other reports.
    for name in ("daily_overview.csv", "rejections.csv", "data_quality.json"):
        assert (seq_out / name).read_text() == (combined_out / name).read_text(), (
            f"{name} differs between sequential and combined ingestion"
        )


def test_orphan_event_excluded_until_lead_arrives_in_reports(tmp_path, registry, cipher, hmac_key):
    data_dir = tmp_path / "data"
    drop1 = _build_drop(
        data_dir,
        "drop_1",
        [],  # no leads yet
        [_event("evt_1", "call.initiated", "call_1", "2026-10-05T13:20:21Z", phone="9876543210")],
    )
    engine = make_engine(f"sqlite:///{tmp_path / 'db1.db'}")
    create_all(engine)
    session_factory = make_session_factory(engine)

    ingest_drop(session_factory, registry, cipher, hmac_key, drop1)
    out1 = tmp_path / "reports1"
    with session_factory() as session:
        generate_reports(session, registry, out1)
    dq1 = json.loads((out1 / "data_quality.json").read_text())
    assert dq1["orphan_events"] == 1
    assert (out1 / "lead_report.csv").read_text().strip().splitlines() == [
        "workflow_id,external_ref,batch_id,phone_masked,bucket,total_calls,connected_calls,last_call_at,current_disposition,ever_success"
    ]

    drop2 = _build_drop(
        data_dir,
        "drop_2",
        ["Aarav Sharma,9876543210,MOT/2026/000001,2026-12-01,1234.50,en\n"],
        [],
    )
    ingest_drop(session_factory, registry, cipher, hmac_key, drop2)
    out2 = tmp_path / "reports2"
    with session_factory() as session:
        generate_reports(session, registry, out2)
    dq2 = json.loads((out2 / "data_quality.json").read_text())
    assert dq2["orphan_events"] == 0  # reconciled now that the lead has arrived
