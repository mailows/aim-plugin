"""Request signing shared by relay and client.

HTTP: headers ``X-AIM-Handle``, ``X-AIM-Ts`` (ISO-8601 UTC) and ``X-AIM-Sig`` = Ed25519 over
``"{METHOD}\\n{path}\\n{ts}\\n{sha256(body).hexdigest()}"``.
WebSocket: after ``hello`` the relay sends a random ``nonce``; the client signs
``ws_auth_payload(handle, session, nonce)``.
"""

from __future__ import annotations

import hashlib

from nacl.signing import SigningKey

from . import TS_SKEW_S
from .crypto import sign, verify
from .timeutil import at_or_now, now_iso, parse_iso

HEADER_HANDLE = "x-aim-handle"
HEADER_TS = "x-aim-ts"
HEADER_SIG = "x-aim-sig"


def signing_string(method: str, path: str, ts: str, body: bytes) -> bytes:
    digest = hashlib.sha256(body or b"").hexdigest()
    return f"{method.upper()}\n{path}\n{ts}\n{digest}".encode()


def sign_request(
    key: SigningKey, handle: str, method: str, path: str, body: bytes = b""
) -> dict[str, str]:
    ts = now_iso()
    return {
        HEADER_HANDLE: handle,
        HEADER_TS: ts,
        HEADER_SIG: sign(key, signing_string(method, path, ts, body)),
    }


def ts_within_skew(ts: str, at_iso: str | None = None, skew_s: int = TS_SKEW_S) -> bool:
    try:
        delta = abs((at_or_now(at_iso) - parse_iso(ts)).total_seconds())
    except ValueError:
        return False
    return delta <= skew_s


def verify_request(
    pubkey_b64: str,
    method: str,
    path: str,
    ts: str,
    sig: str,
    body: bytes = b"",
    at_iso: str | None = None,
) -> bool:
    if not ts_within_skew(ts, at_iso):
        return False
    return verify(pubkey_b64, signing_string(method, path, ts, body), sig)


def ws_auth_payload(handle: str, session: str, nonce: str) -> bytes:
    return f"aim-ws-auth\n{handle}\n{session}\n{nonce}".encode()


def signed_path(path: str, query: str = "") -> str:
    """The path component covered by the signature: path plus raw query string when present."""
    return f"{path}?{query}" if query else path
