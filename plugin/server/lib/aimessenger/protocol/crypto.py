"""Ed25519 identities (PyNaCl): key generation, encoding, signing, verification, fingerprints."""

from __future__ import annotations

import base64
import hashlib

from nacl.exceptions import BadSignatureError
from nacl.signing import SigningKey, VerifyKey


def b64encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def b64decode(text: str) -> bytes:
    padding = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + padding)


def generate_signing_key() -> SigningKey:
    return SigningKey.generate()


def signing_key_from_seed(seed_b64: str) -> SigningKey:
    return SigningKey(b64decode(seed_b64))


def seed_b64(key: SigningKey) -> str:
    return b64encode(bytes(key))


def public_key_b64(key: SigningKey | VerifyKey) -> str:
    verify_key = key.verify_key if isinstance(key, SigningKey) else key
    return b64encode(bytes(verify_key))


def verify_key_from_b64(pubkey_b64: str) -> VerifyKey:
    raw = b64decode(pubkey_b64)
    if len(raw) != 32:
        raise ValueError("Ed25519 public key must be 32 bytes")
    return VerifyKey(raw)


def fingerprint(pubkey_b64: str) -> str:
    """Human-checkable fingerprint: base32(SHA-256(pubkey)), first 40 chars, grouped by 4."""
    digest = hashlib.sha256(b64decode(pubkey_b64)).digest()
    b32 = base64.b32encode(digest).decode("ascii").lower()[:40]
    return "-".join(b32[i : i + 4] for i in range(0, 40, 4))


def sign(key: SigningKey, payload: bytes) -> str:
    return b64encode(key.sign(payload).signature)


def verify(pubkey_b64: str, payload: bytes, sig_b64: str) -> bool:
    try:
        verify_key_from_b64(pubkey_b64).verify(payload, b64decode(sig_b64))
    except BadSignatureError, ValueError:
        return False
    return True
