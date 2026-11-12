"""Whole-file idempotency: skip a file whose exact bytes were already ingested."""
from __future__ import annotations

import hashlib
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from au_ledger.db.models import IngestedFile


def content_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def already_ingested(session: Session, digest: str) -> bool:
    return session.scalar(select(IngestedFile).where(IngestedFile.content_hash == digest)) is not None


def record_ingested(
    session: Session, digest: str, drop_name: str, file_path: str, file_type: str
) -> None:
    session.add(
        IngestedFile(
            content_hash=digest, drop_name=drop_name, file_path=file_path, file_type=file_type
        )
    )
