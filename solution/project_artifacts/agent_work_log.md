# Agent Work Log — Assignment A

## Status summary
- Phase 1 (Audit & design): Completed.
- Phases 2–8 (DB foundation through documentation/final review): Completed in Interaction 2.
- Test suite: 74 tests, all passing (`pytest`).
- Real-dataset end-to-end run (seed 42, `--scale small`): ingested drop_1+drop_2, generated all
  five reports, re-ingested both drops a second time, diffed output — byte-identical
  (idempotency confirmed on real data, not just test fixtures).

---

## Interaction 1 — Audit, planning, and work-log setup

**Date:** 2026-11-12

**Phase:** 1 — Audit and design

**Status:** Completed

### User Prompt
[Full master implementation prompt supplied by the user for "Agentic Universe — Assignment A:
Interaction Ledger & Reporting Pipeline", covering assignment constraints, repository rules,
required audit, core implementation requirements (lead ingestion, event ingestion, database
design, PII protection, idempotency, business logic, reporting), architecture/engineering
quality expectations, testing requirements, the 8-phase implementation workflow, mandatory
prompt/response logging requirements, documentation/submission requirements, time management
guidance, git/submission safety rules, the final verification checklist, and the required final
response format. Reproduced in full in the conversation transcript; omitted here only for
length, not paraphrased.]

### Agent Final Response
Read the assignment README, glossary, both lead schemas, the disposition catalogue, the data
generator, the existing (placeholder) `solution/README.md`, the root `AI_USAGE.md`/
`SUBMISSION.md` templates, `docker-compose.yml`, `requirements.txt`, and `.gitignore`. Confirmed
`solution/` is empty aside from a placeholder README, and that the repo's `.gitignore` already
excludes `data/`, `data_large/`, `reports/`, `*.db`, `.env`. Wrote
`solution/project_artifacts/implementation_plan.md` covering repository audit findings, a
requirements-to-module traceability table, proposed folder structure, architecture, database
design (tables, keys, constraints, idempotency story), PII protection design, the 8
implementation phases, testing strategy, a table of ambiguity resolutions with rationale, and
time-aware priorities. Created this work log. Proceeding to Phase 2 (database foundation) next
without further confirmation, per instructions to only pause for genuine blockers.

### Files Changed
- [solution/project_artifacts/implementation_plan.md](../project_artifacts/implementation_plan.md) — created; full implementation plan.
- [solution/project_artifacts/agent_work_log.md](../project_artifacts/agent_work_log.md) — created; this log.

### Commands and Tests
- None run yet (audit/planning phase only).

### Decisions and Assumptions
- SQLite as the default DB (Postgres supported via `DATABASE_URL`), fixed `+05:30` IST offset,
  `extra_fields` JSON blob for non-reference optional lead fields, mixed counter/computed
  strategy for `data_quality.json`, phone-then-call_id two-pass reconciliation on every
  `ingest` run. Full rationale in `implementation_plan.md` §9.

### Pending Issues
- None for this phase. Implementation (Phases 2–8) not yet started.

---

## Interaction 2 — Full implementation, testing, and documentation (Phases 2–8)

**Date:** 2026-11-12

**Phase:** 2–8 — Database foundation through documentation/final review

**Status:** Completed

### User Prompt
Continuation of the same master implementation prompt from Interaction 1 (no new user message
was sent between interactions; the agent proceeded directly into implementation per the
instruction "proceed with implementation. Do not unnecessarily stop and ask me questions"). No
separate prompt text exists for this interaction beyond Interaction 1's.

### Agent Final Response
Implemented the full pipeline under `solution/au_ledger/`:
- `config.py`, `security/crypto.py` (HMAC phone hash + Fernet PII encryption, both keyed from
  required env vars, never hardcoded).
- `normalization/{phone,dates,decimal_,enum_,timestamps}.py` — pure parsing functions for every
  field type and the three timestamp forms (UTC/offset/naive-as-IST).
- `schema/registry.py` — loads `assignment-a-data/schemas/*.json` and `dispositions.csv`,
  implements the header-normalization/alias-matching rule.
- `db/{base,models}.py` — SQLAlchemy models for `leads`, `lead_row_log`, `rejections`, `events`
  (append-only ledger, `event_id` PK for DB-level dedupe), `ingested_files` (whole-file
  idempotency), `ingestion_counters`.
- `ingestion/{files,leads,events}.py` — CSV lead ingestion (header mapping, validation in schema
  order, rejection reasons, duplicate merge with frozen `batch_id`, defaults-after-merge) and
  JSONL event ingestion (malformed/duplicate/orphan/unknown-disposition handling, two-pass
  phone-then-call_id reconciliation).
- `business/rules.py` — pure functions for call grouping, effective disposition (override beats
  set, latest-by-time), lead bucket, `ever_success`, `current_disposition`, daily success-leads.
- `reporting/reports.py` — all five output files with exact column order/sort/format.
- `pipeline.py`, `cli.py` — per-file transactional orchestration (`ingest`, `report` subcommands).
- `solution/tests/` — 74 tests across normalization, schema registry, lead ingestion, event
  ingestion, business rules, PII protection, and full pipeline integration (idempotent re-ingest,
  sequential-vs-combined equivalence, orphan-then-reconciled reporting).
- Updated root `requirements.txt` (added SQLAlchemy, cryptography, psycopg2-binary without
  removing Assignment B's existing deps), added root `pytest.ini` (`pythonpath = solution`), and
  `solution/.env.example`.

During the first full test run, two issues surfaced and were fixed (see `AI_USAGE.md` for full
detail): (1) a real bug where catching a duplicate `event_id`'s `IntegrityError` rolled back the
entire session instead of just that insert — fixed using `session.begin_nested()` savepoints
plus an explicit pre-check; (2) a test-fixture filename collision (`batch_001.csv` reused across
two drops), fixed in the test, not the application code. After fixes, all 74 tests passed.

Then ran the real generator (`assignment-a-data/tools/generate_data.py --seed 42 --scale
small`, unmodified) and the CLI end-to-end: `ingest data/drop_1`, `ingest data/drop_2`, `report
--out reports/`. Manually inspected all five output files for contract compliance, then
re-ran both `ingest` commands a second time and diffed every report file against the first
run — byte-identical, confirming idempotency on real (not just hand-built test) data. Cleaned up
all local run artifacts (`data/`, `reports/`, `*.db` are already gitignored).

Finally, filled in root `SUBMISSION.md` (design, database schema, decisions/assumptions table,
failure modes, testing summary, real-dataset results, "if I had another day") and `AI_USAGE.md`
(honest disclosure that this entire session's code was AI-authored under human direction, the
two bugs found and fixed, and how the output was verified), updated `solution/README.md` with
exact clean-clone setup/run/test commands, and marked
`solution/project_artifacts/implementation_plan.md` as complete.

### Files Changed
- [solution/au_ledger/__init__.py](../au_ledger/__init__.py), [config.py](../au_ledger/config.py), [cli.py](../au_ledger/cli.py), [pipeline.py](../au_ledger/pipeline.py) — created.
- [solution/au_ledger/security/crypto.py](../au_ledger/security/crypto.py) — created.
- [solution/au_ledger/normalization/phone.py](../au_ledger/normalization/phone.py), [dates.py](../au_ledger/normalization/dates.py), [decimal_.py](../au_ledger/normalization/decimal_.py), [enum_.py](../au_ledger/normalization/enum_.py), [timestamps.py](../au_ledger/normalization/timestamps.py) — created.
- [solution/au_ledger/schema/registry.py](../au_ledger/schema/registry.py) — created.
- [solution/au_ledger/db/base.py](../au_ledger/db/base.py), [models.py](../au_ledger/db/models.py) — created.
- [solution/au_ledger/ingestion/files.py](../au_ledger/ingestion/files.py), [leads.py](../au_ledger/ingestion/leads.py), [events.py](../au_ledger/ingestion/events.py) — created.
- [solution/au_ledger/business/rules.py](../au_ledger/business/rules.py) — created.
- [solution/au_ledger/reporting/reports.py](../au_ledger/reporting/reports.py) — created.
- [solution/tests/conftest.py](../tests/conftest.py) and `solution/tests/test_*.py` (7 test modules, 74 tests) — created.
- [requirements.txt](../../requirements.txt) — appended Assignment A dependencies.
- [pytest.ini](../../pytest.ini) — created at repo root.
- [solution/.env.example](../.env.example) — created.
- [solution/README.md](../README.md) — rewritten with setup/run/test instructions and layout.
- [SUBMISSION.md](../../SUBMISSION.md), [AI_USAGE.md](../../AI_USAGE.md) — filled in.
- [solution/project_artifacts/implementation_plan.md](../project_artifacts/implementation_plan.md) — status updated to complete.

### Commands and Tests
- `python -m venv .venv` then `pip install -r requirements.txt` — succeeded.
- `pytest -q` (via `.venv\Scripts\python.exe -m pytest -q`) with `AU_HMAC_KEY`/`AU_ENCRYPTION_KEY`
  set — first run: **2 failed, 72 passed**. After fixes: **74 passed**. Re-verified with no
  environment variables set at all (confirming the test suite needs none): **74 passed**.
- `python assignment-a-data/tools/generate_data.py --seed 42 --scale small --out data` —
  generated `drop_1` (4,247 event lines) and `drop_2` (4,498 event lines).
- `python -m au_ledger.cli ingest data/drop_1`, `ingest data/drop_2`, `report --out reports/` —
  succeeded; `data_quality.json` showed `lead_rows_total: 2057`, `lead_rows_rejected: 108`,
  `duplicate_lead_rows_merged: 57`, `field_warnings: 122`, `events_total: 8745`,
  `malformed_events: 8`, `duplicate_events_dropped: 237`, `orphan_events: 462`,
  `unknown_disposition_events: 18`.
- Re-ran `ingest data/drop_1`, `ingest data/drop_2`, `report --out reports_run2/` a second time
  and diffed every one of the five output files against the first run with
  `Compare-Object`/content equality — no differences (idempotent on real data).

### Decisions and Assumptions
- All decisions from Interaction 1's table (`implementation_plan.md` §9) were implemented as
  written, with no changes.
- Additional implementation-time decision: event deduplication uses a `session.get()` pre-check
  plus a `session.begin_nested()` savepoint per event insert (rather than a single
  `session.flush()` + session-wide rollback on `IntegrityError`), so one duplicate line can
  never discard other valid, already-pending inserts in the same file's transaction.
- `lead_rows_total` in `data_quality.json` is computed as `valid_lead_rows + rejected_rows` from
  the two append-only audit tables rather than tracked as its own counter, to avoid any risk of
  it drifting from what's actually stored.

### Pending Issues
- Stretch goals (true incremental reconciliation, `--scale large` performance benchmarking,
  SQL-only aggregation, production-operations note) were not implemented — documented as
  explicit, deliberate trade-offs in `SUBMISSION.md` under "If I had another day", per the
  instruction to prioritize a complete core over a rushed stretch goal.
- Postgres (`DATABASE_URL` pointing at the repo's `docker-compose.yml` stack) was not exercised
  end-to-end in this session; only SQLite was used for both tests and the real-dataset run.
