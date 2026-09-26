"""ISO-8601 UTC timestamps carried as strings (stable across languages for signing)."""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta

ISO_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,6})?Z$")


def now() -> datetime:
    return datetime.now(UTC)


def to_iso(dt: datetime) -> str:
    dt = dt.astimezone(UTC)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


def now_iso() -> str:
    return to_iso(now())


def parse_iso(text: str) -> datetime:
    if not ISO_RE.match(text):
        raise ValueError(f"timestamp must be ISO-8601 UTC with Z suffix, got {text!r}")
    return datetime.fromisoformat(text)


def iso_in(seconds: float, start: datetime | None = None) -> str:
    return to_iso((start or now()) + timedelta(seconds=seconds))


def at_or_now(at_iso: str | None) -> datetime:
    return parse_iso(at_iso) if at_iso else now()
