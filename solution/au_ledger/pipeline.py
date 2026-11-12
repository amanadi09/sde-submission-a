"""Top-level orchestration: ``ingest <drop_dir>`` and ``report --out <dir>``.

Each file is ingested in its own transaction (one commit per file), guarded by
the whole-file content hash so re-running ``ingest`` on an already-processed
drop is a true no-op. After all lead files in a drop are ingested, event files
are ingested, and then a reconciliation pass links any orphan events (from this
drop or earlier ones) to leads that have since arrived and re-checks disposition
codes -- all inside its own transaction, so a crash never leaves a half-linked
ledger.
"""
from __future__ import annotations

import logging
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from au_ledger.db.models import IngestionCounter
from au_ledger.ingestion.events import ingest_event_file, reconcile_events
from au_ledger.ingestion.files import already_ingested, content_hash, record_ingested
from au_ledger.ingestion.leads import ingest_lead_file
from au_ledger.schema.registry import SchemaRegistry
from au_ledger.security.crypto import PiiCipher

logger = logging.getLogger(__name__)

COUNTER_KEYS = (
    "events_total",
    "malformed_events",
    "duplicate_events_dropped",
    "unknown_disposition_events",
)


def _increment_counter(session: Session, key: str, amount: int) -> None:
    if amount == 0:
        return
    counter = session.get(IngestionCounter, key)
    if counter is None:
        counter = IngestionCounter(key=key, value=0)
        session.add(counter)
    counter.value += amount


def ingest_drop(
    session_factory: sessionmaker[Session],
    registry: SchemaRegistry,
    cipher: PiiCipher,
    hmac_key: bytes,
    drop_dir: Path,
) -> None:
    drop_name = drop_dir.name
    leads_dir = drop_dir / "leads"
    events_dir = drop_dir / "events"

    if leads_dir.is_dir():
        for workflow_dir in sorted(p for p in leads_dir.iterdir() if p.is_dir()):
            workflow_id = workflow_dir.name
            for csv_path in sorted(workflow_dir.glob("*.csv")):
                _ingest_one_lead_file(
                    session_factory, registry, cipher, hmac_key, drop_name, workflow_id, csv_path
                )

    if events_dir.is_dir():
        for jsonl_path in sorted(events_dir.glob("*.jsonl")):
            _ingest_one_event_file(session_factory, hmac_key, drop_name, jsonl_path)

    with session_factory() as session:
        try:
            unknown_delta = reconcile_events(session, registry)
            _increment_counter(session, "unknown_disposition_events", unknown_delta)
            session.commit()
        except Exception:
            session.rollback()
            raise


def _ingest_one_lead_file(
    session_factory: sessionmaker[Session],
    registry: SchemaRegistry,
    cipher: PiiCipher,
    hmac_key: bytes,
    drop_name: str,
    workflow_id: str,
    csv_path: Path,
) -> None:
    digest = content_hash(csv_path)
    with session_factory() as session:
        if already_ingested(session, digest):
            logger.info("skip (already ingested): %s", csv_path)
            return
        try:
            schema = registry.get_schema(workflow_id)
            batch_id = csv_path.stem
            ingest_lead_file(session, schema, cipher, hmac_key, workflow_id, batch_id, csv_path)
            record_ingested(session, digest, drop_name, str(csv_path), "leads")
            session.commit()
        except Exception:
            session.rollback()
            raise


def _ingest_one_event_file(
    session_factory: sessionmaker[Session],
    hmac_key: bytes,
    drop_name: str,
    jsonl_path: Path,
) -> None:
    digest = content_hash(jsonl_path)
    with session_factory() as session:
        if already_ingested(session, digest):
            logger.info("skip (already ingested): %s", jsonl_path)
            return
        try:
            stats = ingest_event_file(session, jsonl_path, hmac_key)
            _increment_counter(session, "events_total", stats.events_total)
            _increment_counter(session, "malformed_events", stats.malformed_events)
            _increment_counter(session, "duplicate_events_dropped", stats.duplicate_events_dropped)
            record_ingested(session, digest, drop_name, str(jsonl_path), "events")
            session.commit()
        except Exception:
            session.rollback()
            raise
