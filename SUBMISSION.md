# Submission

## Candidate
- **Name:** Aman Aditya
- **Assignment chosen:** A (Data)
- **Time spent (honest estimate):** ~7h (AI-assisted; see `AI_USAGE.md`)

## How to run
```bash
python3.11 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\Activate.ps1
pip install -r requirements.txt

export AU_HMAC_KEY=$(python -c "import secrets;print(secrets.token_urlsafe(32))")
export AU_ENCRYPTION_KEY=$(python -c "from cryptography.fernet import Fernet;print(Fernet.generate_key().decode())")
export PYTHONPATH=solution         # Windows (PowerShell): $env:PYTHONPATH="solution"

python assignment-a-data/tools/generate_data.py --seed 42 --scale small --out data

python -m au_ledger.cli ingest data/drop_1
python -m au_ledger.cli ingest data/drop_2
python -m au_ledger.cli report --out reports/

pytest   # does not require the env vars above; uses in-memory fixtures
```
SQLite (`au_ledger.db` in the cwd) is the default DB. To use the repo-root Postgres instead:
`docker compose up -d` then `export DATABASE_URL=postgresql+psycopg2://au:au_local_only@localhost:5432/au_assignment`.

## Design

Layered, dependency flowing one direction: **normalization** (pure functions, zero
dependencies) → **schema registry** (loads `assignment-a-data/schemas/*.json` +
`dispositions.csv`, implements the header-matching rule) → **ingestion** (CSV/JSONL parsing,
validation, DB writes, one transaction per file) → **business rules** (pure functions over
already-loaded ORM data — no I/O, so they're trivially unit-testable) → **reporting** (queries +
exact-format CSV/JSON writers). See [solution/project_artifacts/implementation_plan.md](solution/project_artifacts/implementation_plan.md)
for the full breakdown, and [solution/README.md](solution/README.md) for the file layout.

**Database schema** (SQLAlchemy, SQLite by default, Postgres via `DATABASE_URL`):
- `leads` — identity `(workflow_id, phone_hash)` enforced by a `UNIQUE` constraint, not just
  application logic. PII (`phone`, `customer_name`) is never stored in plaintext (see below).
  Non-reference/non-PII optional fields live in a JSON `extra_fields` column, so adding a field
  to a workflow schema doesn't require a migration.
- `lead_row_log` — append-only audit of every *valid* row processed
  (`UNIQUE(workflow_id, batch_id, row_number)`), used to derive `lead_rows_total`,
  `duplicate_lead_rows_merged`, and `field_warnings` without a separate mutable counter that
  could drift from reality.
- `rejections` — one row per rejected CSV row, same uniqueness guard. Source of `rejections.csv`.
- `events` — the append-only ledger. `event_id` is the primary key, so re-inserting an
  already-seen event raises an `IntegrityError` — deduplication is a database guarantee, not a
  Python `set` that resets between process runs. `link_status`/`lead_id`/`disposition_status`
  are the only columns ever updated after insert (as reconciliation resolves more events); the
  event's own `payload`/`occurred_at` never change, and a `disposition.override` is always a new
  row, never an edit of the `disposition.set` row it corrects.
- `ingested_files` — SHA-256 of each file's bytes; makes whole-file re-ingestion a no-op.
- `ingestion_counters` — the 4 raw event-line counters that can't be reconstructed after the
  fact (malformed/duplicate lines are never persisted). Every other `data_quality.json` field is
  computed fresh from current table state at report time, so it always reflects "everything
  ingested so far" (required for `orphan_events`, which must reflect final state).

## Decisions & assumptions

| Decision | Why | Alternative considered |
|---|---|---|
| SQLite as the default DB; Postgres via `DATABASE_URL` | Spec says both are "equally acceptable"; SQLite needs zero setup for a clean-clone reviewer | Defaulting to the provided `docker-compose.yml` Postgres — rejected, adds a required external dependency for a core workflow |
| Fixed `+05:30` offset for IST instead of `zoneinfo` | India has one fixed offset, no DST; avoids a Windows `tzdata` footgun | `zoneinfo("Asia/Kolkata")` — technically more "correct" but an unnecessary dependency for a value that never changes |
| Non-reference optional lead fields stored in a JSON `extra_fields` column | Keeps the `Lead` table schema-agnostic across workflows (open/closed principle) without a wide sparse table | One column per field per workflow — rejected, doesn't generalize past the two given schemas |
| Two-pass, full-rescan reconciliation on every `ingest` call (phone-based, then call_id-based for overrides) | Correctness first; guarantees "an orphan from drop_1 is resolved once its lead arrives in drop_2" regardless of event ordering | True incremental reconciliation (only scan newly-added events/leads) — a documented stretch goal, skipped for time; see "If I had another day" |
| `event_id` as the events table primary key for dedupe | Lets the database (not application code) guarantee "duplicate event_id, even across drops, is dropped exactly once" | A separate `seen_event_ids` lookup table/set — redundant once `event_id` is already the PK |
| `lead_rows_total` computed as `valid_rows + rejected_rows` from two append-only tables, rather than a separate counter | Avoids a counter that could double-count on a partial re-ingest; always consistent with what's actually stored | A dedicated `ingestion_counters` entry — used anyway for the 4 line-level counts that truly can't be recomputed (malformed/duplicate event lines) |
| PII: HMAC-SHA256 keyed phone hash for lookup + Fernet encryption for recoverable fields + plaintext last-4 digits for masking | Satisfies "match by phone" and "must not be stored in plaintext" simultaneously, with a clear, minimal recoverable surface (last 4 digits only) | Reversible encryption only (no hash) — rejected, `WHERE phone = ...`-style lookups would require decrypting every row |
| A required field that's also an invalid non-phone type (not reachable with the given schemas, since the only required fields are `string`/`phone`) | Treated as `missing_required:<field>` if it ever occurred, since the spec only names `missing_required`/`invalid_phone` as reasons | Inventing a new reason string — rejected, not specified anywhere in the README |

## Failure modes considered

- **Bad input:** malformed JSON lines, missing `event_id`, invalid phones, missing required
  fields, impossible dates, non-numeric currency, unknown disposition codes, blank rows, renamed
  and reordered CSV headers, extra unmapped columns — all handled per the exact spec rules
  (tested in `solution/tests/test_normalization.py`, `test_lead_ingestion.py`,
  `test_event_ingestion.py`).
- **Re-runs:** re-ingesting an identical file is a complete no-op (content-hash check before any
  row is touched). Verified against the real generated dataset (re-ingesting both drops produced
  byte-identical reports) and in `test_pipeline_integration.py::test_reingesting_same_drop_is_idempotent`.
- **Crashes mid-ingestion:** each file is ingested inside one `session.commit()`/`rollback()`
  transaction scope (`pipeline.py`); a crash partway through a file rolls that file back
  entirely — it is never marked as ingested, so the next run retries it cleanly. A crash between
  files leaves already-committed files correctly recorded and untouched by the retry.
- **Events before leads:** orphan events are stored (not discarded) with `link_status="orphan"`
  and excluded from every report; a reconciliation pass re-links them once their lead exists,
  covered by `test_orphan_event_reconciled_once_lead_arrives` and the real-dataset run (orphan
  count dropped from 462 after drop_1+drop_2 together to the same value whether ingested
  sequentially or idempotently re-run).
- **Not handled / known gap:** reconciliation re-scans *all* currently-unresolved events on every
  `ingest` call rather than only newly-arrived ones — correct, but not performance-optimal at the
  `--scale large` size (not benchmarked; see below).

## Testing

74 tests, `pytest` (see "How to run"). Covers:
- Normalization: phone (6 valid-form, 5 invalid-form cases), date (5 valid formats incl.
  impossible-date rejection), decimal (currency prefixes, thousands separators, negative
  rejection), enum, all three timestamp forms incl. naive-as-IST.
- Lead ingestion: aliased/reordered headers, blank-row skipping, missing-required rejection with
  correct row number/reason, invalid-phone rejection, optional-field warnings (not rejections),
  duplicate merge (last-write-wins, frozen `batch_id`), defaults applied after merge, the
  `lead_row_log` audit trail itself.
- Event ingestion: malformed lines, missing `event_id`, duplicate `event_id` within one file and
  **across two separate ingest calls**, phone-based linking, orphan → later reconciliation,
  unknown-disposition flagging, `disposition.override` linking via `call_id` with no phone,
  reconciliation idempotency (running it twice changes nothing the second time).
- Business rules: call-without-`initiated` exclusion, connected flag, effective disposition
  (override beats set, latest-by-time within each), unknown disposition excluded from effective,
  all four bucket branches, `ever_success`, `current_disposition`.
- PII: HMAC determinism/keying, phone masking format, and a direct assertion that the raw phone
  digits and name substrings never appear in the stored `Lead` row.
- Integration: idempotent re-ingest, sequential-vs-combined-ingestion equivalence (daily_overview/
  rejections/data_quality match exactly even though batch IDs differ), orphan-then-reconciled
  report behaviour across two `ingest` calls.
- Also manually run end-to-end against the real `--scale small` generator output (not committed):
  ingested drop_1 + drop_2, generated reports, re-ingested both drops again, diffed all five
  output files byte-for-byte against the first run — identical.
- **Not tested:** the `--scale large` dataset (performance), Postgres as the backing DB (only
  SQLite was exercised; the SQLAlchemy layer should be portable but this wasn't verified).

## Results

Real `--scale small` run (seed 42): 2,057 valid lead rows ingested, 108 rejected, 57 merged
duplicates, 122 field warnings; 8,745 event lines, 8 malformed, 237 duplicates dropped, 462
orphans remaining (phones that never got a matching lead upload, by generator design), 18
unknown-disposition events. Nothing surprising beyond what the generator's README section ("What
makes this data messy") already promised. Timings were not a concern at this scale (a few
seconds end-to-end); `--scale large` was not run.

## If I had another day

1. Make reconciliation truly incremental (only re-scan events/leads touched since the last
   successful `ingest`, likely via a `resolved_up_to` watermark) instead of a full orphan rescan
   every call — the current approach is correct but not scale-friendly.
2. Benchmark and, if needed, optimize for `--scale large` (~140k leads / ~600k events);
   likely candidates are batching inserts and moving the daily/batch aggregation into SQL.
3. Exercise the Postgres path in CI, not just SQLite.
4. Add a short production-operations note (scheduling, failure/backfill strategy for 100k
   leads/day) as the assignment's stretch goal suggests.
