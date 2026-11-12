# AI Usage Disclosure

AI tools are allowed. We want to know **how** you used them, because using them well is a
skill. Honest answers are never held against you. Discovering undisclosed use is.

## Tools used
GitHub Copilot (agentic/"GitHub Copilot" chat mode in VS Code, using the `claude-sonnet-5`
model) in agent mode, with direct file read/write/terminal access to this repository.

## What I used them for
The entire implementation in this session was produced by the AI agent working autonomously
against a detailed implementation prompt: repository audit, the implementation plan
(`solution/project_artifacts/implementation_plan.md`), all production code under
`solution/au_ledger/` (config, PII crypto, header/phone/date/decimal/enum/timestamp
normalization, schema registry, database models, lead ingestion, event ingestion/reconciliation,
business rules, reporting, CLI/pipeline orchestration), the full `pytest` test suite under
`solution/tests/`, running the generator and the pipeline end-to-end against the real synthetic
dataset to sanity-check output, and drafting this disclosure plus `SUBMISSION.md` and
`solution/README.md`.

## What I wrote or decided myself
I supplied the governing requirements and constraints (the master prompt), reviewed the agent's
implementation plan and the resulting code/tests/outputs described in this disclosure, and
directed follow-up fixes when the initial test run surfaced two bugs (see below). The
ambiguity-resolution table in `SUBMISSION.md` reflects decisions the agent proposed under my
explicit instruction to document every such call rather than ask for clarification on
non-blocking ambiguities; I reviewed and accepted each one as reasonable given the spec.

## Where the AI was wrong or unhelpful
The first full `pytest` run surfaced two real bugs introduced by the agent, caught by the test
suite itself rather than manual inspection:
1. **Event deduplication rollback bug.** The initial event-ingestion code caught a duplicate
   `event_id`'s `IntegrityError` by calling `session.rollback()` on the whole session, which also
   discarded every other valid event already pending in that same transaction (confirmed by
   `test_duplicate_event_id_within_file_dropped` failing: an event that should have been kept
   vanished). Fixed by wrapping each insert in `session.begin_nested()` (a SQLAlchemy
   savepoint) plus an explicit `session.get()` pre-check, so a duplicate only discards its own
   insert.
2. **Test fixture collision, not implementation bug.** A sequential-vs-combined-ingestion
   integration test reused the filename `batch_001.csv` for both `drop_1` and `drop_2`, which
   collided with the real `UNIQUE(workflow_id, batch_id, row_number)` constraint on
   `lead_row_log` — correctly, since two leads with the same `batch_id` and `row_number` really
   would be ambiguous. This was a test-authoring mistake (not giving drop_2 a distinct
   `batch_002` filename), fixed in the test, not the application code.

## How I verified AI-generated code
- Ran the full `pytest` suite (74 tests) after every meaningful change; both failures above were
  caught this way, not asserted blindly as passing.
- Ran the real pipeline end-to-end against `assignment-a-data/tools/generate_data.py --scale
  small` (unmodified, original generator): ingested `drop_1` then `drop_2`, generated all five
  reports, inspected them manually (they match the documented column/format contract), then
  re-ran `ingest` on both drops a second time and diffed every output file against the first
  run — byte-identical, confirming idempotency on real data, not just the hand-built test
  fixtures.
- Read every generated file before accepting it as part of this disclosure process, in
  particular the PII-handling code (`security/crypto.py`) and the business-rule module
  (`business/rules.py`), since those are the two areas most likely to contain a subtle
  correctness or security bug.
