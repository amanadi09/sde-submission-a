from __future__ import annotations

from pathlib import Path

from sqlalchemy import select

from au_ledger.db.models import Lead, LeadRowLog, Rejection
from au_ledger.ingestion.leads import ingest_lead_file


def _write(tmp_path: Path, name: str, content: str) -> Path:
    path = tmp_path / name
    path.write_text(content, encoding="utf-8", newline="")
    return path


def test_basic_valid_rows_create_leads(tmp_path, session_factory, registry, cipher, hmac_key):
    csv_content = (
        "customer_name,phone,policy_number,policy_expiry,premium_amount,preferred_language\n"
        "Aarav Sharma,9876543210,MOT/2026/000001,2026-12-01,1234.50,en\n"
        "Diya Nair,9876543211,MOT/2026/000002,01/12/2026,Rs. 2000,hi\n"
    )
    path = _write(tmp_path, "batch_001.csv", csv_content)
    schema = registry.get_schema("motor_renewal")

    with session_factory() as session:
        stats = ingest_lead_file(
            session, schema, cipher, hmac_key, "motor_renewal", "batch_001", path
        )
        session.commit()
        assert stats.rows_seen == 2
        assert stats.rows_rejected == 0
        assert stats.rows_merged == 0

        leads = session.scalars(select(Lead)).all()
        assert len(leads) == 2
        refs = {lead.external_ref for lead in leads}
        assert refs == {"MOT/2026/000001", "MOT/2026/000002"}


def test_aliased_and_reordered_headers(tmp_path, session_factory, registry, cipher, hmac_key):
    csv_content = (
        "Policy No,Mobile No,Insured Name,Premium,Expiry Date,Language,agent_notes\n"
        "MOT/2026/000003,9876543212,Rohan Gupta,1500,15-Dec-2026,EN,VIP\n"
    )
    path = _write(tmp_path, "batch_003.csv", csv_content)
    schema = registry.get_schema("motor_renewal")

    with session_factory() as session:
        stats = ingest_lead_file(
            session, schema, cipher, hmac_key, "motor_renewal", "batch_003", path
        )
        session.commit()
        assert stats.rows_seen == 1
        assert stats.rows_rejected == 0
        lead = session.scalars(select(Lead)).one()
        assert lead.external_ref == "MOT/2026/000003"
        assert lead.extra_fields["preferred_language"] == "en"


def test_blank_rows_skipped_and_not_counted(tmp_path, session_factory, registry, cipher, hmac_key):
    csv_content = (
        "customer_name,phone,policy_number,policy_expiry,premium_amount,preferred_language\n"
        ",,,,,\n"
        "Aarav Sharma,9876543210,MOT/2026/000001,2026-12-01,1234.50,en\n"
    )
    path = _write(tmp_path, "batch_001.csv", csv_content)
    schema = registry.get_schema("motor_renewal")

    with session_factory() as session:
        stats = ingest_lead_file(
            session, schema, cipher, hmac_key, "motor_renewal", "batch_001", path
        )
        session.commit()
        assert stats.rows_seen == 1  # the blank row is not counted


def test_missing_required_field_rejected_with_row_number(
    tmp_path, session_factory, registry, cipher, hmac_key
):
    csv_content = (
        "customer_name,phone,policy_number,policy_expiry,premium_amount,preferred_language\n"
        ",9876543210,MOT/2026/000001,2026-12-01,1234.50,en\n"
        "Diya Nair,9876543211,MOT/2026/000002,2026-12-01,1234.50,en\n"
    )
    path = _write(tmp_path, "batch_001.csv", csv_content)
    schema = registry.get_schema("motor_renewal")

    with session_factory() as session:
        ingest_lead_file(session, schema, cipher, hmac_key, "motor_renewal", "batch_001", path)
        session.commit()
        rejection = session.scalars(select(Rejection)).one()
        assert rejection.row_number == 1
        assert rejection.reason == "missing_required:customer_name"


def test_invalid_phone_rejected(tmp_path, session_factory, registry, cipher, hmac_key):
    csv_content = (
        "customer_name,phone,policy_number,policy_expiry,premium_amount,preferred_language\n"
        "Aarav Sharma,12345,MOT/2026/000001,2026-12-01,1234.50,en\n"
    )
    path = _write(tmp_path, "batch_001.csv", csv_content)
    schema = registry.get_schema("motor_renewal")

    with session_factory() as session:
        ingest_lead_file(session, schema, cipher, hmac_key, "motor_renewal", "batch_001", path)
        session.commit()
        rejection = session.scalars(select(Rejection)).one()
        assert rejection.reason == "invalid_phone"


def test_optional_invalid_field_is_warning_not_rejection(
    tmp_path, session_factory, registry, cipher, hmac_key
):
    csv_content = (
        "customer_name,phone,policy_number,policy_expiry,premium_amount,preferred_language\n"
        "Aarav Sharma,9876543210,MOT/2026/000001,not-a-date,1234.50,en\n"
    )
    path = _write(tmp_path, "batch_001.csv", csv_content)
    schema = registry.get_schema("motor_renewal")

    with session_factory() as session:
        stats = ingest_lead_file(
            session, schema, cipher, hmac_key, "motor_renewal", "batch_001", path
        )
        session.commit()
        assert stats.rows_rejected == 0
        assert stats.field_warnings == 1
        lead = session.scalars(select(Lead)).one()
        assert "policy_expiry" not in lead.extra_fields


def test_duplicate_rows_merge_last_write_wins_and_batch_id_frozen(
    tmp_path, session_factory, registry, cipher, hmac_key
):
    schema = registry.get_schema("motor_renewal")
    first = _write(
        tmp_path,
        "batch_001.csv",
        "customer_name,phone,policy_number,policy_expiry,premium_amount,preferred_language\n"
        "Aarav Sharma,9876543210,MOT/2026/000001,2026-12-01,1234.50,en\n",
    )
    second = _write(
        tmp_path,
        "batch_002.csv",
        "customer_name,phone,policy_number,policy_expiry,premium_amount,preferred_language\n"
        "Aarav S,9876543210,MOT/2026/000001-UPDATED,,2000.00,hi\n",
    )

    with session_factory() as session:
        ingest_lead_file(session, schema, cipher, hmac_key, "motor_renewal", "batch_001", first)
        stats2 = ingest_lead_file(
            session, schema, cipher, hmac_key, "motor_renewal", "batch_002", second
        )
        session.commit()
        assert stats2.rows_merged == 1

        lead = session.scalars(select(Lead)).one()
        assert lead.batch_id == "batch_001"  # frozen to first batch
        assert lead.external_ref == "MOT/2026/000001-UPDATED"  # overwritten (non-null)
        assert lead.extra_fields["premium_amount"] == "2000.00"  # overwritten
        assert lead.extra_fields["preferred_language"] == "hi"  # overwritten


def test_defaults_applied_after_merge_to_still_null_fields(
    tmp_path, session_factory, registry, cipher, hmac_key
):
    schema = registry.get_schema("motor_renewal")
    path = _write(
        tmp_path,
        "batch_001.csv",
        "customer_name,phone,policy_number,policy_expiry,premium_amount,preferred_language\n"
        "Aarav Sharma,9876543210,MOT/2026/000001,2026-12-01,1234.50,\n",
    )

    with session_factory() as session:
        ingest_lead_file(session, schema, cipher, hmac_key, "motor_renewal", "batch_001", path)
        session.commit()
        lead = session.scalars(select(Lead)).one()
        assert lead.extra_fields["preferred_language"] == "en"  # default applied


def test_lead_row_log_recorded_for_idempotency_counting(
    tmp_path, session_factory, registry, cipher, hmac_key
):
    schema = registry.get_schema("motor_renewal")
    path = _write(
        tmp_path,
        "batch_001.csv",
        "customer_name,phone,policy_number,policy_expiry,premium_amount,preferred_language\n"
        "Aarav Sharma,9876543210,MOT/2026/000001,2026-12-01,1234.50,en\n",
    )
    with session_factory() as session:
        ingest_lead_file(session, schema, cipher, hmac_key, "motor_renewal", "batch_001", path)
        session.commit()
        log_rows = session.scalars(select(LeadRowLog)).all()
        assert len(log_rows) == 1
        assert log_rows[0].row_number == 1
        assert log_rows[0].is_merge is False
