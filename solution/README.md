# Assignment A — Interaction Ledger & Reporting Pipeline

Implementation of [assignment-a-data/README.md](../assignment-a-data/README.md). Source lives
in [`au_ledger/`](au_ledger/); tests live in [`tests/`](tests/).

## Setup (from a clean clone)

```bash
python3.11 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\Activate.ps1
pip install -r requirements.txt

# Required secrets (never commit these):
export AU_HMAC_KEY=$(python -c "import secrets;print(secrets.token_urlsafe(32))")
export AU_ENCRYPTION_KEY=$(python -c "from cryptography.fernet import Fernet;print(Fernet.generate_key().decode())")

# Makes `python -m au_ledger.cli` resolve from the repo root.
export PYTHONPATH=solution      # Windows (PowerShell): $env:PYTHONPATH="solution"

# Default DB is a local SQLite file (au_ledger.db in the cwd). To use Postgres instead:
# docker compose up -d
# export DATABASE_URL=postgresql+psycopg2://au:au_local_only@localhost:5432/au_assignment
```

## Generate synthetic data

```bash
python assignment-a-data/tools/generate_data.py --seed 42 --scale small --out data
```

## Run the pipeline

```bash
python -m au_ledger.cli ingest data/drop_1
python -m au_ledger.cli ingest data/drop_2
python -m au_ledger.cli report --out reports/
```

Re-running `ingest` on an already-ingested drop is a no-op (whole-file content hash check).

## Tests

```bash
pytest
```

(`pytest.ini` at the repo root sets `pythonpath = solution`. Tests use their own in-memory
SQLite DB and locally generated HMAC/Fernet keys — `AU_HMAC_KEY`/`AU_ENCRYPTION_KEY` are **not**
required to run `pytest`, only for the `ingest`/`report` CLI commands above.)

## Layout

```
au_ledger/
├── cli.py                    # `ingest`, `report` subcommands
├── config.py                 # env-driven config, no secrets hardcoded
├── pipeline.py                # per-file transactions, idempotency, reconciliation
├── schema/registry.py          # loads assignment-a-data/schemas/*.json + dispositions.csv
├── normalization/               # pure phone/date/decimal/enum/timestamp parsing
├── security/crypto.py            # HMAC phone hash + Fernet PII encryption
├── db/{base,models}.py            # SQLAlchemy engine/session + ORM schema
├── ingestion/{leads,events,files}.py  # CSV/JSONL ingestion
├── business/rules.py               # pure derived-fact rules (calls, bucket, success)
└── reporting/reports.py             # the five output files
```

See [project_artifacts/implementation_plan.md](project_artifacts/implementation_plan.md) for the
full design rationale and [project_artifacts/agent_work_log.md](project_artifacts/agent_work_log.md)
for the AI-assisted development log.
