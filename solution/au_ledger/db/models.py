"""ORM models for the interaction ledger.

Design summary (see ``project_artifacts/implementation_plan.md`` for full rationale):

- ``Lead`` identity is ``(workflow_id, phone_hash)`` -- enforced by a unique
  constraint, not just application logic, so a race or a bug can't create two
  leads for the same person.
- ``Event`` uses ``event_id`` as its primary key. Re-inserting an already-seen
  ``event_id`` raises an ``IntegrityError``, which is exactly the signal the
  ingestion code uses to count it as a duplicate -- deduplication is enforced by
  the database, not a Python ``set`` that could be wrong across process runs.
- The ledger is append-only: once inserted, event rows are only ever updated to
  move ``link_status``/``lead_id``/``disposition_status`` from "unresolved" to
  "resolved" as later drops arrive. The event's own ``payload``/``occurred_at``
  are never mutated, and corrections are new rows (``disposition.override``),
  never edits of the original ``disposition.set`` row.
- ``IngestedFile`` gives whole-file idempotency (by content hash): re-running
  ``ingest`` on a drop that was already processed is a true no-op.
- ``IngestionCounter`` persists the four line-level counters that cannot be
  reconstructed later (malformed/duplicate lines are never stored). Every other
  ``data_quality.json`` field is computed fresh from current table state.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from au_ledger.db.base import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _uuid4() -> str:
    return str(uuid.uuid4())


class Lead(Base):
    __tablename__ = "leads"
    __table_args__ = (UniqueConstraint("workflow_id", "phone_hash", name="uq_lead_identity"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid4)
    workflow_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    batch_id: Mapped[str] = mapped_column(String(128), nullable=False)
    external_ref: Mapped[str] = mapped_column(String(256), nullable=False)
    phone_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    phone_encrypted: Mapped[str] = mapped_column(String(512), nullable=False)
    phone_last4: Mapped[str] = mapped_column(String(4), nullable=False)
    customer_name_encrypted: Mapped[str] = mapped_column(String(1024), nullable=False)
    extra_fields: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )

    events: Mapped[list["Event"]] = relationship(back_populates="lead")


class LeadRowLog(Base):
    """Append-only record of every *valid* lead CSV row processed.

    Used to compute ``lead_rows_total``, ``duplicate_lead_rows_merged`` and
    ``field_warnings`` without separate mutable counters, and to make re-ingestion
    of an already-processed row a detectable no-op via the unique constraint.
    """

    __tablename__ = "lead_row_log"
    __table_args__ = (
        UniqueConstraint("workflow_id", "batch_id", "row_number", name="uq_lead_row"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    workflow_id: Mapped[str] = mapped_column(String(64), nullable=False)
    batch_id: Mapped[str] = mapped_column(String(128), nullable=False)
    row_number: Mapped[int] = mapped_column(Integer, nullable=False)
    lead_id: Mapped[str] = mapped_column(String(36), ForeignKey("leads.id"), nullable=False)
    is_merge: Mapped[bool] = mapped_column(Boolean, nullable=False)
    field_warnings: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class Rejection(Base):
    __tablename__ = "rejections"
    __table_args__ = (
        UniqueConstraint("workflow_id", "batch_id", "row_number", name="uq_rejection_row"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    workflow_id: Mapped[str] = mapped_column(String(64), nullable=False)
    batch_id: Mapped[str] = mapped_column(String(128), nullable=False)
    row_number: Mapped[int] = mapped_column(Integer, nullable=False)
    reason: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class Event(Base):
    """One row of the append-only interaction ledger."""

    __tablename__ = "events"

    event_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    workflow_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    call_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    occurred_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    phone_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    lead_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("leads.id"), nullable=True, index=True
    )
    link_status: Mapped[str] = mapped_column(String(16), nullable=False, default="orphan")
    disposition_status: Mapped[str | None] = mapped_column(String(16), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    lead: Mapped["Lead | None"] = relationship(back_populates="events")


class IngestedFile(Base):
    """Whole-file idempotency guard: a file whose content hash is already here is skipped."""

    __tablename__ = "ingested_files"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    drop_name: Mapped[str] = mapped_column(String(128), nullable=False)
    file_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    file_type: Mapped[str] = mapped_column(String(16), nullable=False)  # "leads" | "events"
    ingested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class IngestionCounter(Base):
    """Cumulative counters for facts that cannot be recomputed from stored rows."""

    __tablename__ = "ingestion_counters"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
