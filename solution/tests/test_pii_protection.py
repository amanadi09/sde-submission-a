from __future__ import annotations

from sqlalchemy import select

from au_ledger.db.models import Lead
from au_ledger.ingestion.leads import ingest_lead_file
from au_ledger.security.crypto import hash_phone, mask_phone


def test_phone_hash_is_deterministic_and_keyed():
    h1 = hash_phone("+919876543210", b"key-a")
    h2 = hash_phone("+919876543210", b"key-a")
    h3 = hash_phone("+919876543210", b"key-b")
    assert h1 == h2
    assert h1 != h3


def test_mask_phone_format():
    assert mask_phone("+919876543210") == "+91******3210"


def test_lead_table_never_stores_plaintext_phone_or_name(
    tmp_path, session_factory, registry, cipher, hmac_key
):
    schema = registry.get_schema("motor_renewal")
    path = tmp_path / "batch_001.csv"
    path.write_text(
        "customer_name,phone,policy_number,policy_expiry,premium_amount,preferred_language\n"
        "Aarav Sharma,9876543210,MOT/2026/000001,2026-12-01,1234.50,en\n",
        encoding="utf-8",
    )
    with session_factory() as session:
        ingest_lead_file(session, schema, cipher, hmac_key, "motor_renewal", "batch_001", path)
        session.commit()
        lead = session.scalars(select(Lead)).one()
        assert "9876543210" not in lead.phone_encrypted
        assert "Aarav" not in lead.customer_name_encrypted
        assert "Sharma" not in lead.customer_name_encrypted
        # the cipher can recover it (recoverable-PII design), but only with the key
        assert cipher.decrypt(lead.phone_encrypted) == "+919876543210"
        assert cipher.decrypt(lead.customer_name_encrypted) == "Aarav Sharma"
