from __future__ import annotations

import os
from pathlib import Path

import pytest
from cryptography.fernet import Fernet

from au_ledger.config import DEFAULT_SCHEMAS_DIR
from au_ledger.db.base import create_all, make_engine, make_session_factory
from au_ledger.schema.registry import SchemaRegistry
from au_ledger.security.crypto import PiiCipher

TEST_HMAC_KEY = b"test-hmac-key-not-for-production"


@pytest.fixture()
def session_factory():
    engine = make_engine("sqlite:///:memory:")
    create_all(engine)
    return make_session_factory(engine)


@pytest.fixture()
def hmac_key() -> bytes:
    return TEST_HMAC_KEY


@pytest.fixture()
def cipher() -> PiiCipher:
    return PiiCipher(Fernet.generate_key())


@pytest.fixture()
def registry() -> SchemaRegistry:
    return SchemaRegistry(DEFAULT_SCHEMAS_DIR)
