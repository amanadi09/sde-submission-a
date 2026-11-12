"""Generates the five required report files from current database state.

Each report is built by: loading the relevant rows via SQLAlchemy, delegating any
derived-fact computation to :mod:`au_ledger.business.rules` (kept pure/testable),
then writing CSV/JSON with the exact column names, order, and formatting from the
assignment's output contract.
"""
from __future__ import annotations

import csv
import json
from collections import defaultdict
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from au_ledger.business.rules import (
    CallFacts,
    LedgerEvent,
    compute_bucket,
    compute_calls,
    current_disposition,
    ever_success,
    success_leads_on_date,
)
from au_ledger.db.models import Event, IngestionCounter, Lead, LeadRowLog, Rejection
from au_ledger.normalization.timestamps import to_ist_date, to_ist_isoformat
from au_ledger.schema.registry import SchemaRegistry

BUCKETS = ("yet_to_reach_out", "in_progress_telephony_failed", "in_progress_dialed", "completed")


def _linked_events_by_lead(session: Session) -> dict[str, list[LedgerEvent]]:
    rows = session.scalars(select(Event).where(Event.link_status == "linked")).all()
    by_lead: dict[str, list[LedgerEvent]] = defaultdict(list)
    for row in rows:
        by_lead[row.lead_id].append(
            LedgerEvent(
                event_type=row.event_type,
                call_id=row.call_id,
                occurred_at_utc=row.occurred_at_utc,
                payload=row.payload,
                is_known_disposition=(row.disposition_status != "unknown"),
            )
        )
    return by_lead


def generate_reports(session: Session, registry: SchemaRegistry, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)

    leads = session.scalars(select(Lead)).all()
    events_by_lead = _linked_events_by_lead(session)
    calls_by_lead: dict[str, list[CallFacts]] = {
        lead.id: compute_calls(events_by_lead.get(lead.id, [])) for lead in leads
    }

    disposition_flags_by_wf = {
        wf: registry.all_disposition_flags(wf) for wf in registry.workflow_ids()
    }

    _write_batch_summary(leads, calls_by_lead, disposition_flags_by_wf, out_dir)
    _write_daily_overview(leads, calls_by_lead, disposition_flags_by_wf, out_dir)
    _write_lead_report(leads, calls_by_lead, disposition_flags_by_wf, out_dir)
    _write_rejections(session, out_dir)
    _write_data_quality(session, out_dir)


def _write_batch_summary(
    leads: list[Lead],
    calls_by_lead: dict[str, list[CallFacts]],
    disposition_flags_by_wf: dict,
    out_dir: Path,
) -> None:
    counts: dict[tuple[str, str], dict[str, int]] = defaultdict(
        lambda: {b: 0 for b in BUCKETS} | {"total_leads": 0}
    )
    for lead in leads:
        bucket = compute_bucket(calls_by_lead[lead.id], disposition_flags_by_wf[lead.workflow_id])
        key = (lead.workflow_id, lead.batch_id)
        counts[key]["total_leads"] += 1
        counts[key][bucket] += 1

    rows = [
        {
            "workflow_id": wf,
            "batch_id": batch_id,
            "total_leads": c["total_leads"],
            "yet_to_reach_out": c["yet_to_reach_out"],
            "in_progress_telephony_failed": c["in_progress_telephony_failed"],
            "in_progress_dialed": c["in_progress_dialed"],
            "completed": c["completed"],
        }
        for (wf, batch_id), c in counts.items()
    ]
    rows.sort(key=lambda r: (r["workflow_id"], r["batch_id"]))
    _write_csv(
        out_dir / "batch_summary.csv",
        rows,
        [
            "workflow_id",
            "batch_id",
            "total_leads",
            "yet_to_reach_out",
            "in_progress_telephony_failed",
            "in_progress_dialed",
            "completed",
        ],
    )


def _write_daily_overview(
    leads: list[Lead],
    calls_by_lead: dict[str, list[CallFacts]],
    disposition_flags_by_wf: dict,
    out_dir: Path,
) -> None:
    lead_workflow = {lead.id: lead.workflow_id for lead in leads}
    per_day: dict[tuple[str, object], dict[str, int]] = defaultdict(
        lambda: {"total_calls": 0, "connected_calls": 0}
    )
    calls_by_lead_by_wf: dict[str, dict[str, list[CallFacts]]] = defaultdict(dict)

    for lead_id, calls in calls_by_lead.items():
        wf = lead_workflow[lead_id]
        calls_by_lead_by_wf[wf][lead_id] = calls
        for call in calls:
            day = to_ist_date(call.initiated_at_utc)
            key = (wf, day)
            per_day[key]["total_calls"] += 1
            if call.connected:
                per_day[key]["connected_calls"] += 1

    rows = []
    for (wf, day), c in per_day.items():
        success_leads = success_leads_on_date(
            calls_by_lead_by_wf[wf], day, disposition_flags_by_wf[wf]
        )
        rate = c["connected_calls"] / c["total_calls"] if c["total_calls"] else 0.0
        rows.append(
            {
                "workflow_id": wf,
                "date": day.isoformat(),
                "total_calls": c["total_calls"],
                "connected_calls": c["connected_calls"],
                "connectivity_rate": f"{rate:.4f}",
                "success_leads": success_leads,
            }
        )
    rows.sort(key=lambda r: (r["workflow_id"], r["date"]))
    _write_csv(
        out_dir / "daily_overview.csv",
        rows,
        ["workflow_id", "date", "total_calls", "connected_calls", "connectivity_rate", "success_leads"],
    )


def _write_lead_report(
    leads: list[Lead],
    calls_by_lead: dict[str, list[CallFacts]],
    disposition_flags_by_wf: dict,
    out_dir: Path,
) -> None:
    rows = []
    for lead in leads:
        calls = calls_by_lead[lead.id]
        flags = disposition_flags_by_wf[lead.workflow_id]
        total_calls = len(calls)
        connected_calls = sum(1 for c in calls if c.connected)
        last_call_at = (
            to_ist_isoformat(max(c.initiated_at_utc for c in calls)) if calls else ""
        )
        rows.append(
            {
                "workflow_id": lead.workflow_id,
                "external_ref": lead.external_ref,
                "batch_id": lead.batch_id,
                "phone_masked": f"+91******{lead.phone_last4}",
                "bucket": compute_bucket(calls, flags),
                "total_calls": total_calls,
                "connected_calls": connected_calls,
                "last_call_at": last_call_at,
                "current_disposition": current_disposition(calls) or "",
                "ever_success": "true" if ever_success(calls, flags) else "false",
            }
        )
    rows.sort(key=lambda r: (r["workflow_id"], r["external_ref"]))
    _write_csv(
        out_dir / "lead_report.csv",
        rows,
        [
            "workflow_id",
            "external_ref",
            "batch_id",
            "phone_masked",
            "bucket",
            "total_calls",
            "connected_calls",
            "last_call_at",
            "current_disposition",
            "ever_success",
        ],
    )


def _write_rejections(session: Session, out_dir: Path) -> None:
    rejections = session.scalars(select(Rejection)).all()
    rows = [
        {
            "workflow_id": r.workflow_id,
            "batch_id": r.batch_id,
            "row_number": r.row_number,
            "reason": r.reason,
        }
        for r in rejections
    ]
    rows.sort(key=lambda r: (r["workflow_id"], r["batch_id"], r["row_number"]))
    _write_csv(out_dir / "rejections.csv", rows, ["workflow_id", "batch_id", "row_number", "reason"])


def _write_data_quality(session: Session, out_dir: Path) -> None:
    lead_rows = session.scalars(select(LeadRowLog)).all()
    valid_lead_rows = len(lead_rows)
    duplicate_lead_rows_merged = sum(1 for r in lead_rows if r.is_merge)
    field_warnings = sum(r.field_warnings for r in lead_rows)

    rejections = session.scalars(select(Rejection)).all()
    lead_rows_rejected = len(rejections)
    rejections_by_reason: dict[str, int] = defaultdict(int)
    for r in rejections:
        rejections_by_reason[r.reason] += 1

    # lead_rows_total counts every non-blank data row encountered during lead
    # ingestion (valid + rejected), reconstructed from the two append-only tables
    # rather than a separate mutable counter.
    lead_rows_total = valid_lead_rows + lead_rows_rejected

    counters = {c.key: c.value for c in session.scalars(select(IngestionCounter)).all()}
    orphan_events = len(
        session.scalars(select(Event).where(Event.link_status == "orphan")).all()
    )

    payload = {
        "lead_rows_total": lead_rows_total,
        "lead_rows_rejected": lead_rows_rejected,
        "rejections_by_reason": dict(sorted(rejections_by_reason.items())),
        "duplicate_lead_rows_merged": duplicate_lead_rows_merged,
        "field_warnings": field_warnings,
        "events_total": counters.get("events_total", 0),
        "malformed_events": counters.get("malformed_events", 0),
        "duplicate_events_dropped": counters.get("duplicate_events_dropped", 0),
        "orphan_events": orphan_events,
        "unknown_disposition_events": counters.get("unknown_disposition_events", 0),
    }
    (out_dir / "data_quality.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _write_csv(path: Path, rows: list[dict], header: list[str]) -> None:
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=header)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
