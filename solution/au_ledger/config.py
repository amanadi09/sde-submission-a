"""Environment-driven configuration.

Nothing here is loaded at import time: callers explicitly construct a ``Config``
via :func:`load_config`, which makes it trivial to test with a fully isolated,
in-memory setup (no monkeypatching required for most tests).
"""
from __future__ import annotations

import base64
import os
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SCHEMAS_DIR = REPO_ROOT / "assignment-a-data" / "schemas"


class ConfigError(RuntimeError):
    """Raised when required configuration (e.g. a secret key) is missing or invalid."""


@dataclass(frozen=True)
class Config:
    database_url: str
    hmac_key: bytes
    fernet_key: bytes
    schemas_dir: Path


def _require_key(env_var: str) -> bytes:
    raw = os.environ.get(env_var)
    if not raw:
        raise ConfigError(
            f"{env_var} is not set. Generate one (e.g. "
            "`python -c \"import secrets;print(secrets.token_urlsafe(32))\"`) "
            f"and export it as {env_var} before running ingestion."
        )
    return raw.encode("utf-8")


def load_config() -> Config:
    """Build a :class:`Config` from environment variables.

    Required:
      - ``AU_HMAC_KEY``: secret used to key the deterministic phone-lookup hash.
      - ``AU_ENCRYPTION_KEY``: a urlsafe-base64 Fernet key for at-rest PII encryption.
    Optional:
      - ``DATABASE_URL``: defaults to a local SQLite file ``au_ledger.db``.
      - ``AU_SCHEMAS_DIR``: defaults to ``assignment-a-data/schemas`` in this repo.
    """
    hmac_key = _require_key("AU_HMAC_KEY")
    fernet_key_raw = _require_key("AU_ENCRYPTION_KEY")
    try:
        base64.urlsafe_b64decode(fernet_key_raw)
    except Exception as exc:  # noqa: BLE001 - re-raised as a clear ConfigError
        raise ConfigError(
            "AU_ENCRYPTION_KEY must be a urlsafe-base64 Fernet key, e.g. "
            "`python -c \"from cryptography.fernet import Fernet;print(Fernet.generate_key().decode())\"`"
        ) from exc

    database_url = os.environ.get("DATABASE_URL", "sqlite:///au_ledger.db")
    schemas_dir = Path(os.environ.get("AU_SCHEMAS_DIR", str(DEFAULT_SCHEMAS_DIR)))

    return Config(
        database_url=database_url,
        hmac_key=hmac_key,
        fernet_key=fernet_key_raw,
        schemas_dir=schemas_dir,
    )
