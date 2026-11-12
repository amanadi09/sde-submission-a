# Implementation Plan — Assignment A: Interaction Ledger & Reporting Pipeline

Status: Phases 1–8 complete. Core implementation, tests (74 passing), real-dataset end-to-end
verification (including idempotency), and documentation are all done. See
`solution/project_artifacts/agent_work_log.md` Interaction 2 for the full implementation log and
`SUBMISSION.md` for results, decisions, and what's left for "if I had another day".

## 1. Repository audit findings

- `assignment-a-data/README.md` is the authoritative spec (headers, phone/date/decimal rules,
  rejection order, event ordering rules, business rules, output contract). Treated as source of
  truth over this prompt wherever they could be read as conflicting.
- `docs/GLOSSARY.md` confirms vocabulary (lead identity = phone within a workflow, batch =
  one uploaded file, ledger = append-only).
- Two schemas exist: `motor_renewal` (fields: customer_name*, phone*, policy_number*[ref],
  policy_expiry, premium_amount, preferred_language[enum, default en]) and `policy_feedback`
  (customer_name*, phone*, order_id*[ref], product, order_date, preferred_language). `*` =
  required. `customer_name` and `phone` are the only fields marked `"pii": true` in either
  schema — PII handling only needs to special-case those two.
- `dispositions.csv` gives per-workflow disposition catalogue with `is_terminal`/`is_success`.
- `tools/generate_data.py` is stdlib-only, untouched, generates `drop_1`/`drop_2` with messy
  headers (aliased in `batch_003`), bad phones, bad dates/amounts, blank rows, malformed JSONL
  lines, duplicate events (incl. cross-drop), orphan events, pure-orphan numbers, and overrides.
  Confirms exactly the dirtiness categories named in the spec — no surprises beyond the README.
- `solution/` only has a placeholder `README.md`. Nothing to preserve/reuse there.
- Root `AI_USAGE.md` / `SUBMISSION.md` are the official blank templates — fill in, don't
  duplicate.
- `docker-compose.yml` provides an optional local Postgres (`au`/`au_local_only`/`au_assignment`)
  — reuse as-is, unmodified. SQLite is explicitly equally acceptable and is simpler to set up
  from a clean clone, so SQLite is the **default**; Postgres is supported via `DATABASE_URL` for
  anyone who starts the compose stack.
- `requirements.txt` currently only lists Assignment B's FastAPI/httpx/pytest deps. Must *add*
  Assignment A's dependencies (not remove B's), per "reuse deps where appropriate" / "don't
  modify unrelated components".
- No `.env` is committed (correctly gitignored). No existing DB, migrations, or app code exists
  for Assignment A — building from scratch.

## 2. Requirements breakdown (traceability)

| Area | Key rules | Where implemented |
|---|---|---|
| Header normalization | lower/trim/collapse `_`/whitespace, match name or alias | `schema/registry.py` |
| Phone normalization | strip non-digit, drop `91`/`0` prefix, validate 10-digit 6-9 start | `normalization/phone.py` |
| Date parsing | 4 formats, day-first, impossible dates invalid | `normalization/dates.py` |
| Decimal parsing | currency prefixes, thousands commas, ≥0, round 2dp | `normalization/decimal_.py` |
| Enum | case-insensitive match against `values`, store lowercase | `normalization/enum_.py` |
| Rejection order/reason | schema field order, first failure wins | `ingestion/leads.py` |
| Duplicate lead merge | identity = (workflow, phone); first row creates; later non-null wins; batch_id frozen | `ingestion/leads.py` |
| Defaults after merge | apply schema default to still-null fields after each write | `ingestion/leads.py` |
| Event parsing/dedupe | malformed → skip; dup event_id (cross-drop) → skip | `ingestion/events.py` |
| Linking/orphans | phone-based; override via call_id; reconciled on later drops | `ingestion/events.py` (+ `business/linking.py`) |
| Unknown disposition | re-checked at reconciliation time too | `ingestion/events.py` |
| Timestamps | UTC/offset/naive(=IST), fixed +05:30 offset (no DST in India) | `normalization/timestamps.py` |
| Business rules | call/connected/effective disposition/bucket/success | `business/rules.py` |
| PII | HMAC phone_hash (lookup) + Fernet-encrypted recoverable blobs, last-4 plaintext for masking | `security/crypto.py`, `db/models.py` |
| Idempotency | file-hash skip (counts) + DB unique constraints (events, leads, rejections, row log) | `ingestion/files.py`, `db/models.py` |
| Reports | 5 files, exact columns/sort/format | `reporting/reports.py` |

## 3. Proposed folder structure

```
solution/
├── README.md                     # setup & usage (updated)
├── pytest.ini                    # repo-root? -> actually placed at repo root (pytest needs rootdir discovery)
├── .env.example
├── solution/
│   ├── __init__.py
│   ├── cli.py                    # `ingest`, `report` subcommands
│   ├── config.py                 # env-driven config (DB url, secret keys)
│   ├── db/{base.py, models.py}
│   ├── security/crypto.py
│   ├── schema/registry.py
│   ├── normalization/{phone,dates,decimal_,enum_,timestamps}.py
│   ├── ingestion/{files,leads,events}.py
│   ├── business/rules.py
│   └── reporting/reports.py
├── tests/
│   └── test_*.py
└── project_artifacts/
    ├── implementation_plan.md
    └── agent_work_log.md
```
(`pytest.ini` must live at the repo root so `pytest` works from a clean clone without extra
flags — this is the one exception to "everything inside solution/", matching the existing
convention that `requirements.txt`/`docker-compose.yml` live at root.)

## 4. Architecture

- **Layered**: normalization (pure functions) → schema registry (declarative field specs) →
  ingestion (orchestrates parsing + validation + DB writes inside transactions) → business rules
  (pure functions over ORM-loaded data, no I/O) → reporting (queries + CSV/JSON writers).
- **Dependency direction**: ingestion depends on normalization/schema/db; business depends on
  db only; reporting depends on business + db. Normalization has zero internal dependencies →
  easy to unit test in isolation.
- **Config via environment** (`config.py`), loaded lazily (not at import time) so tests can
  monkeypatch `os.environ` per-test without reload tricks.
- **SQLAlchemy 2.0 Core+ORM** chosen for portability between SQLite (default, zero-setup) and
  Postgres (via `docker-compose.yml`), explicit transactions (`Session.begin()`), and because it
  is a reasonable, common choice for a junior-level take-home (vs. hand-rolled SQL or a heavier
  framework).

## 5. Database design

- `leads(id uuid pk, workflow_id, batch_id, external_ref, phone_hash, phone_encrypted,
  customer_name_encrypted, phone_last4, extra_fields JSON, created_at, updated_at)`
  — `UNIQUE(workflow_id, phone_hash)` enforces lead identity at the DB level.
- `lead_row_log(id pk, workflow_id, batch_id, row_number, lead_id fk, is_merge bool,
  field_warnings int, created_at)` — `UNIQUE(workflow_id, batch_id, row_number)`; append-only
  audit of every *valid* row processed, used to compute `lead_rows_total`,
  `duplicate_lead_rows_merged`, `field_warnings` without separate counters (avoids double-count
  risk on re-ingest; guarded further by file-hash skip).
- `rejections(id pk, workflow_id, batch_id, row_number, reason, created_at)` —
  `UNIQUE(workflow_id, batch_id, row_number)`; source for `rejections.csv` and
  `lead_rows_rejected`/`rejections_by_reason` (computed via `GROUP BY`).
- `events(event_id pk, event_type, workflow_id, call_id, occurred_at_utc, phone_hash nullable,
  payload JSON, lead_id fk nullable, link_status enum[linked|orphan], disposition_status
  nullable[known|unknown], created_at)` — `event_id` as primary key gives DB-level dedupe across
  drops "for free" (insert is attempted, `IntegrityError` ⇒ already seen ⇒ counted as duplicate).
  The ledger is append-only: nothing is ever updated except `lead_id`/`link_status`/
  `disposition_status`, which only transition from "unknown/orphan" to "resolved" as more data
  arrives — the event's own content is never mutated. `disposition.override` rows are never
  merged into the original `disposition.set` row.
- `ingested_files(id pk, content_hash unique, drop_name, file_path, file_type, ingested_at)` —
  whole-file idempotency: re-running `ingest` on an already-seen file (by SHA-256 of its bytes)
  is a no-op, which is what makes the line-count counters (`events_total`, `malformed_events`,
  `duplicate_events_dropped`, `unknown_disposition_events`) safe to store as cumulative counters
  without double-counting on re-ingestion.
- `ingestion_counters(key pk, value int)` — holds only the 4 event-line counters above (values
  that cannot be reconstructed after the fact, since malformed lines/duplicate lines are not
  persisted). Everything else in `data_quality.json` is computed by querying the tables above at
  report time (so it is always consistent with current DB state, e.g. `orphan_events` reflects
  the *current* unresolved count, not a historical cumulative count — required by the spec).

Relationships: `lead_row_log.lead_id → leads.id`, `events.lead_id → leads.id` (nullable until
linked). No FK from `rejections` (rejected rows never become leads).

## 6. PII protection design

- `phone_hash = HMAC-SHA256(secret, canonical_phone)` — deterministic, keyed (not a bare hash,
  so it resists dictionary/rainbow-table attacks on a 10-digit search space), used for lead
  identity and event→lead linking. Never reversed.
- `phone_encrypted` / `customer_name_encrypted` — Fernet (AES-128-CBC + HMAC) symmetric
  encryption, recoverable only with the key, covers "information that must be recovered" (e.g.
  a future ops/compliance need). Not actually decrypted anywhere in the current reports.
- `phone_last4` stored in plaintext (last 4 of 10 digits) — the minimum data needed for the
  `phone_masked` report column (`+91******1234`); matches the spec's masking format and is not
  independently identifying.
- Keys come from `AU_ENCRYPTION_KEY` / `AU_HMAC_KEY` environment variables only; no defaults or
  hardcoded secrets. Missing keys raise a clear `ConfigError` at startup rather than silently
  using an insecure default.
- Logs/exceptions never interpolate raw phone/name — only `lead.id`, `phone_hash[:8]`, or
  `phone_masked` are used in any log/error message. `rejections.csv` never includes PII columns
  (the contract already excludes them).
- **Limitation (documented, not solved)**: Fernet encryption is not currently used by any report
  or business logic — it exists to satisfy "encryption for information that must be recovered"
  defensively, but there is no in-scope feature that decrypts it. This is called out in
  `SUBMISSION.md` as a deliberate, limited-scope choice.

## 7. Implementation phases (this session)

1. Audit + this plan. ✅
2. DB foundation: config, engine/session, models, Alembic-free `create_all` bootstrap.
3. Normalization + schema registry (pure, heavily unit-tested).
4. Lead ingestion (CSV → validate → reject/create/merge → defaults).
5. Event ingestion (JSONL → dedupe → link/orphan → unknown-disposition → reconciliation pass).
6. Business rules (connected/effective disposition/bucket/success) as pure functions over
   loaded rows.
7. Reporting (5 files) + CLI wiring (`ingest`, `report`).
8. Tests (unit + integration incl. idempotency/sequential-vs-combined) + run `pytest`.
9. Docs: `SUBMISSION.md`, `AI_USAGE.md`, `solution/README.md`, work log, final audit.

## 8. Testing strategy

- Unit tests per normalization function (phone/date/decimal/enum/timestamp) with
  table-driven valid/invalid cases taken directly from the README's examples.
- Lead ingestion tests using small in-memory CSV fixtures (aliased headers, blank rows, bad
  phone, missing required field, duplicate merge with partial nulls, default application).
- Event ingestion tests: malformed line, missing `event_id`, duplicate `event_id` (same file and
  across two separate `ingest` calls), orphan → later reconciliation, unknown disposition code,
  disposition override precedence, all 3 timestamp forms.
- Business rule tests: effective disposition (override beats set; latest-by-time within each);
  bucket precedence (`yet_to_reach_out` > `completed` > `telephony_failed` > `dialed`);
  connectivity; ever-success.
- End-to-end/integration test: generate a tiny synthetic drop_1/drop_2 (hand-built fixtures, not
  the real generator, so tests stay deterministic and fast), ingest drop_1, ingest drop_2,
  compare reports against ingesting a merged/combined drop — must be byte-identical. Re-run
  `ingest drop_1` a second time — reports must be unchanged (idempotency).
- All tests run against a fresh SQLite file/in-memory DB per test (fixture), never touching any
  developer DB file; original `assignment-a-data` fixtures are never written to.

## 9. Assumptions and risks (ambiguities resolved)

| # | Ambiguity | Decision | Rationale |
|---|---|---|---|
| 1 | Reasons beyond `missing_required`/`invalid_phone` (e.g. required non-phone field invalid) | Not reachable with given schemas (only string/phone fields are required); if it ever occurred, treat as `missing_required:<field>` since the spec only names these two reasons. | Matches spec wording; schemas confirm this branch is dead code today, documented rather than speculatively engineered. |
| 2 | Where to persist non-reference optional fields (e.g. `premium_amount`) that never appear in any report | Store in a `extra_fields` JSON blob on `leads` so merge/default semantics stay correct without one rigid column per workflow. | Keeps schema generic across workflows (open/closed), avoids a wide sparse table. |
| 3 | DB choice | SQLite by default; Postgres supported via `DATABASE_URL` (compose file unmodified). | Spec says both are "equally acceptable"; SQLite needs zero setup for a clean-clone reviewer. |
| 4 | IST timezone handling | Fixed `+05:30` offset (no `zoneinfo`/`tzdata` dependency). | India has one fixed offset, no DST; avoids a Windows `tzdata` dependency footgun. |
| 5 | PII fields needing recovery | No current feature decrypts PII; Fernet is present but unused beyond at-rest protection. | Documented as a known limitation rather than building speculative decrypt tooling. |
| 6 | Counters vs. computed aggregates in `data_quality.json` | Mixed: 4 raw line-counters (events_total etc., not reconstructable) are DB counters guarded by file-hash idempotency; everything else is computed fresh from current table state at report time. | Avoids double-counting on re-ingest while keeping "final state" fields (orphans) truly current. |
| 7 | Multi-pass orphan reconciliation ordering (override depends on sibling event) | Run phone-based linking pass, then call_id-based (override) linking pass, every `ingest` invocation, over **all** currently-unlinked events (not just the current drop). | Correctness over micro-optimizing "true incremental" (explicitly a stretch goal we are skipping — documented trade-off). |

## 10. Time-aware priorities

Given the 6–8 hour box: DB schema + lead ingestion + event ingestion + business rules + reports
+ core tests are mandatory and will consume most of the time. Stretch goals (true incremental
processing, large-scale perf run, SQL-only aggregation, production notes) are addressed only as
a short written note in `SUBMISSION.md`, not implemented, unless core finishes early.
