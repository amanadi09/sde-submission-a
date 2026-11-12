"""Command-line entrypoint.

Usage:
    python -m au_ledger.cli ingest data/drop_1
    python -m au_ledger.cli ingest data/drop_2
    python -m au_ledger.cli report --out reports/
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from au_ledger.config import ConfigError, load_config
from au_ledger.db.base import create_all, make_engine, make_session_factory
from au_ledger.pipeline import ingest_drop
from au_ledger.reporting.reports import generate_reports
from au_ledger.schema.registry import SchemaRegistry
from au_ledger.security.crypto import PiiCipher


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="au_ledger")
    subparsers = parser.add_subparsers(dest="command", required=True)

    ingest_parser = subparsers.add_parser("ingest", help="Ingest one drop directory.")
    ingest_parser.add_argument("drop_dir", type=Path)

    report_parser = subparsers.add_parser("report", help="Generate the five report files.")
    report_parser.add_argument("--out", type=Path, required=True)

    return parser


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        config = load_config()
    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2

    engine = make_engine(config.database_url)
    create_all(engine)
    session_factory = make_session_factory(engine)
    registry = SchemaRegistry(config.schemas_dir)
    cipher = PiiCipher(config.fernet_key)

    if args.command == "ingest":
        ingest_drop(session_factory, registry, cipher, config.hmac_key, args.drop_dir)
        print(f"Ingested {args.drop_dir}")
    elif args.command == "report":
        with session_factory() as session:
            generate_reports(session, registry, args.out)
        print(f"Reports written to {args.out}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
