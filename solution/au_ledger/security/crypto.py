"""PII protection primitives.

Two mechanisms, deliberately kept separate:

- ``phone_hash``: a keyed HMAC-SHA256 over the canonical phone number. Deterministic
  and one-way, used as the DB-level lookup key for lead identity and event-to-lead
  linking. It can never be reversed back to a phone number.
- ``PiiCipher``: Fernet (AES-128-CBC + HMAC) symmetric encryption for fields that may
  need to be recovered later (e.g. compliance/ops access). Requires the secret key to
  decrypt; nothing in the current reporting pipeline decrypts these fields.

Keys are never hardcoded; see :mod:`au_ledger.config`.
"""
from __future__ import annotations

import hashlib
import hmac

from cryptography.fernet import Fernet


def hash_phone(canonical_phone: str, key: bytes) -> str:
    """Deterministic, keyed hash of a canonical ``+91XXXXXXXXXX`` phone number.

    Keyed HMAC (rather than a bare SHA-256) resists offline brute-forcing of the
    10-digit Indian mobile number space by anyone who gains read access to the
    database but not the key.
    """
    return hmac.new(key, canonical_phone.encode("utf-8"), hashlib.sha256).hexdigest()


class PiiCipher:
    """Thin wrapper around :class:`cryptography.fernet.Fernet` for recoverable PII."""

    def __init__(self, key: bytes) -> None:
        self._fernet = Fernet(key)

    def encrypt(self, plaintext: str) -> str:
        return self._fernet.encrypt(plaintext.encode("utf-8")).decode("ascii")

    def decrypt(self, token: str) -> str:
        return self._fernet.decrypt(token.encode("ascii")).decode("utf-8")


def mask_phone(canonical_phone: str) -> str:
    """``+91XXXXXXXXXX`` -> ``+91******XXXX`` for safe display in reports."""
    last4 = canonical_phone[-4:]
    return f"+91******{last4}"
