"""Local Ed25519 identity stored in ~/.aim/identity.key (JSON, owner-only permissions)."""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
from pathlib import Path

from nacl.signing import SigningKey

from aimessenger.protocol.crypto import (
    fingerprint,
    generate_signing_key,
    public_key_b64,
    seed_b64,
    signing_key_from_seed,
)

from .config import aim_home


def identity_path() -> Path:
    return aim_home() / "identity.key"


def _restrict_permissions(path: Path) -> None:
    if sys.platform == "win32":
        user = os.environ.get("USERNAME")
        if user:
            subprocess.run(
                ["icacls", str(path), "/inheritance:r", "/grant:r", f"{user}:F"],
                check=False,
                capture_output=True,
            )
    else:
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)


def load_identity() -> SigningKey | None:
    p = identity_path()
    if not p.exists():
        return None
    data = json.loads(p.read_text(encoding="utf-8"))
    if data.get("alg") != "ed25519":
        raise ValueError(f"unsupported identity algorithm {data.get('alg')!r}")
    return signing_key_from_seed(data["seed"])


def create_identity(overwrite: bool = False) -> SigningKey:
    p = identity_path()
    if p.exists() and not overwrite:
        raise FileExistsError(f"{p} already exists (use --force to replace it)")
    key = generate_signing_key()
    p.write_text(
        json.dumps(
            {"v": 1, "alg": "ed25519", "seed": seed_b64(key), "pubkey": public_key_b64(key)}
        ),
        encoding="utf-8",
    )
    _restrict_permissions(p)
    return key


def write_identity(seed: str) -> SigningKey:
    """Store a seed the relay handed us when a person linked this machine."""
    key = signing_key_from_seed(seed)
    identity_path().write_text(
        json.dumps({"v": 1, "alg": "ed25519", "seed": seed, "pubkey": public_key_b64(key)}),
        encoding="utf-8",
    )
    _restrict_permissions(identity_path())
    return key


def require_identity() -> SigningKey:
    key = load_identity()
    if key is None:
        raise SystemExit("no identity found - run `aim init` first")
    return key


def describe(key: SigningKey) -> dict[str, str]:
    pub = public_key_b64(key)
    return {"pubkey": pub, "fingerprint": fingerprint(pub)}
