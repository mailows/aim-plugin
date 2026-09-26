"""Deterministic JSON serialization used for signing.

Rules (must be identical in every implementation): UTF-8, keys sorted, no whitespace,
non-ASCII kept as-is, NaN/Infinity forbidden, and fields whose value is null are omitted
by the caller before canonicalization.
"""

from __future__ import annotations

import json
from typing import Any


def canonical_json(obj: Any) -> bytes:
    return json.dumps(
        obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode("utf-8")
