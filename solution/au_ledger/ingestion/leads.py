"""Lead CSV ingestion: header mapping, validation, rejection, and dedupe/merge.

Row processing follows the spec exactly:

1. Blank rows (every cell empty/whitespace) are skipped and never counted.
2. Row numbers count non-blank data rows within the file, starting at 1.
3. Fields are walked in schema order; the first failure (a missing required
   field, or a present-but-invalid phone) is the row's single rejection reason.
   Rejected rows never become leads.
4. An optional field that is present but invalid is stored as null and counted
   as one field warning (only for rows that are not rejected).
5. Lead identity is ``(workflow_id, canonical phone)``. The first valid row for
   an identity creates the lead and freezes its ``batch_id`` permanently; every
   later valid row for the same identity is a merge: it overwrites only the
   fields where its own value is non-null (last write wins).
6. Defaults apply after merging, to fields that are still null.
"""
from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field as dc_field
from decimal import Decimal
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from au_ledger.db.models import Lead, LeadRowLog, Rejection
from au_ledger.normalization.dates import parse_lead_date
from au_ledger.normalization.decimal_ import parse_decimal
from au_ledger.normalization.enum_ import parse_enum
from au_ledger.normalization.phone import normalize_phone
from au_ledger.schema.registry import FieldSpec, WorkflowSchema
from au_ledger.security.crypto import PiiCipher, hash_phone


@dataclass
class LeadIngestStats:
    rows_seen: int = 0
    rows_rejected: int = 0
    rows_merged: int = 0
    field_warnings: int = 0


def _serialize(value) -> object:
    if isinstance(value, Decimal):
        return str(value)
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


def _parse_field_value(field_spec: FieldSpec, raw: str) -> tuple[object | None, bool]:
    """Parse one field's raw string per its declared type.

    Returns ``(parsed_value_or_None, is_invalid)``. ``is_invalid`` is True only
    when the value was present but failed to parse (used for the optional-field
    warning count -- a missing value is never "invalid").
    """
    if field_spec.type == "string":
        return raw, False
    if field_spec.type == "phone":
        normalized = normalize_phone(raw)
        return normalized, normalized is None
    if field_spec.type == "date":
        parsed = parse_lead_date(raw)
        return parsed, parsed is None
    if field_spec.type == "decimal":
        parsed = parse_decimal(raw)
        return parsed, parsed is None
    if field_spec.type == "enum":
        parsed = parse_enum(raw, list(field_spec.values))
        return parsed, parsed is None
    raise ValueError(f"unknown field type: {field_spec.type}")


def _read_rows(csv_path: Path, schema: WorkflowSchema):
    """Yield ``(row_number, {field_name: raw_value})`` for non-blank rows, in file order."""
    with csv_path.open(newline="", encoding="utf-8-sig") as fh:
        reader = csv.reader(fh)
        try:
            header = next(reader)
        except StopIteration:
            return

        column_fields: list[str | None] = [schema.resolve_header(h) for h in header]

        row_number = 0
        for raw_row in reader:
            if all(cell.strip() == "" for cell in raw_row):
                continue
            row_number += 1
            values: dict[str, str] = {}
            for idx, cell in enumerate(raw_row):
                if idx >= len(column_fields):
                    break
                field_name = column_fields[idx]
                if field_name is None:
                    continue
                values[field_name] = cell.strip()
            yield row_number, values


def _validate_row(
    schema: WorkflowSchema, raw_values: dict[str, str]
) -> tuple[str | None, dict[str, object | None], int]:
    """Validate one row's fields in schema order.

    Returns ``(rejection_reason_or_None, parsed_values_by_field_name, field_warnings)``.
    If a rejection reason is returned, ``parsed_values``/``field_warnings`` are incomplete
    and must be ignored.
    """
    parsed: dict[str, object | None] = {}
    warnings = 0

    for field_spec in schema.fields:
        raw = raw_values.get(field_spec.name, "")
        missing = raw == ""

        if field_spec.required and missing:
            return f"missing_required:{field_spec.name}", {}, 0

        if field_spec.type == "phone" and not missing:
            value, invalid = _parse_field_value(field_spec, raw)
            if invalid and field_spec.required:
                return "invalid_phone", {}, 0
            parsed[field_spec.name] = value
            if invalid:
                warnings += 1
            continue

        if missing:
            parsed[field_spec.name] = None
            continue

        value, invalid = _parse_field_value(field_spec, raw)
        parsed[field_spec.name] = value
        if invalid and not field_spec.required:
            warnings += 1

    return None, parsed, warnings


def ingest_lead_file(
    session: Session,
    schema: WorkflowSchema,
    cipher: PiiCipher,
    hmac_key: bytes,
    workflow_id: str,
    batch_id: str,
    csv_path: Path,
) -> LeadIngestStats:
    stats = LeadIngestStats()
    reference_field = schema.reference_field.name
    pii_names = {f.name for f in schema.pii_fields}
    extra_names = [
        f.name for f in schema.fields if f.name not in pii_names and f.name != reference_field
    ]

    for row_number, raw_values in _read_rows(csv_path, schema):
        stats.rows_seen += 1
        reason, parsed, warnings = _validate_row(schema, raw_values)

        if reason is not None:
            session.add(
                Rejection(workflow_id=workflow_id, batch_id=batch_id, row_number=row_number, reason=reason)
            )
            stats.rows_rejected += 1
            continue

        stats.field_warnings += warnings
        phone = parsed["phone"]
        phone_hash = hash_phone(phone, hmac_key)

        existing = session.scalar(
            select(Lead).where(Lead.workflow_id == workflow_id, Lead.phone_hash == phone_hash)
        )

        if existing is None:
            extra_fields = {
                name: _serialize(parsed.get(name)) for name in extra_names if parsed.get(name) is not None
            }
            _apply_defaults(schema, extra_fields)
            lead = Lead(
                workflow_id=workflow_id,
                batch_id=batch_id,
                external_ref=parsed[reference_field],
                phone_hash=phone_hash,
                phone_encrypted=cipher.encrypt(phone),
                phone_last4=phone[-4:],
                customer_name_encrypted=cipher.encrypt(parsed["customer_name"]),
                extra_fields=extra_fields,
            )
            session.add(lead)
            session.flush()
            session.add(
                LeadRowLog(
                    workflow_id=workflow_id,
                    batch_id=batch_id,
                    row_number=row_number,
                    lead_id=lead.id,
                    is_merge=False,
                    field_warnings=warnings,
                )
            )
        else:
            if parsed[reference_field] is not None:
                existing.external_ref = parsed[reference_field]
            if parsed.get("customer_name") is not None:
                existing.customer_name_encrypted = cipher.encrypt(parsed["customer_name"])

            extra_fields = dict(existing.extra_fields)
            for name in extra_names:
                value = parsed.get(name)
                if value is not None:
                    extra_fields[name] = _serialize(value)
            _apply_defaults(schema, extra_fields)
            existing.extra_fields = extra_fields

            session.add(
                LeadRowLog(
                    workflow_id=workflow_id,
                    batch_id=batch_id,
                    row_number=row_number,
                    lead_id=existing.id,
                    is_merge=True,
                    field_warnings=warnings,
                )
            )
            stats.rows_merged += 1

    return stats


def _apply_defaults(schema: WorkflowSchema, extra_fields: dict) -> None:
    for field_spec in schema.fields:
        if field_spec.default is not None and extra_fields.get(field_spec.name) is None:
            extra_fields[field_spec.name] = field_spec.default
