"""AIM addresses: ``handle@relay-host`` (user) and ``handle@relay-host/session`` (endpoint)."""

from __future__ import annotations

import re
from dataclasses import dataclass

HANDLE_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{1,31}$")
SESSION_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,47}$")
HOST_RE = re.compile(
    r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)*(:\d{1,5})?$"
)
_INVALID_SESSION_CHARS = re.compile(r"[^a-z0-9._-]+")


class AddressError(ValueError):
    """Malformed AIM address."""


@dataclass(frozen=True, slots=True)
class UserAddress:
    handle: str
    host: str

    def __str__(self) -> str:
        return f"{self.handle}@{self.host}"

    def endpoint(self, session: str) -> EndpointAddress:
        return EndpointAddress(self.handle, self.host, session)


@dataclass(frozen=True, slots=True)
class EndpointAddress:
    handle: str
    host: str
    session: str

    def __str__(self) -> str:
        return f"{self.handle}@{self.host}/{self.session}"

    @property
    def user(self) -> UserAddress:
        return UserAddress(self.handle, self.host)


def validate_handle(handle: str) -> str:
    if not HANDLE_RE.match(handle):
        raise AddressError(f"invalid handle {handle!r} (allowed: [a-z0-9._-], 2-32 chars)")
    return handle


def validate_host(host: str) -> str:
    if not HOST_RE.match(host):
        raise AddressError(f"invalid relay host {host!r}")
    return host


def validate_session(session: str) -> str:
    if not SESSION_RE.match(session):
        raise AddressError(f"invalid session name {session!r} (allowed: [a-z0-9._-], 1-48 chars)")
    return session


def parse_user(text: str) -> UserAddress:
    text = text.strip().lower()
    if "/" in text or text.count("@") != 1:
        raise AddressError(f"invalid user address {text!r}, expected handle@relay-host")
    handle, host = text.split("@")
    return UserAddress(validate_handle(handle), validate_host(host))


def parse_endpoint(text: str) -> EndpointAddress:
    text = text.strip().lower()
    if text.count("@") != 1 or text.count("/") != 1:
        raise AddressError(f"invalid endpoint address {text!r}, expected handle@relay-host/session")
    user_part, session = text.split("/")
    user = parse_user(user_part)
    return EndpointAddress(user.handle, user.host, validate_session(session))


SESSION_PATTERN_RE = re.compile(r"^[a-z0-9*][a-z0-9._*-]{0,47}$")


def validate_session_pattern(pattern: str) -> str:
    """A session name that may contain `*`, as grants use to cover a family of sessions."""
    name = (pattern or "").strip().lower()
    if not SESSION_PATTERN_RE.match(name):
        raise AddressError(f"invalid session pattern {pattern!r}")
    return name


def normalize_session_name(raw: str | None, default: str = "session") -> str:
    """Turn a Claude Code session name or directory name into a valid AIM session name."""
    if not raw:
        return default
    name = _INVALID_SESSION_CHARS.sub("-", raw.strip().lower()).strip("-._")
    name = re.sub(r"-{2,}", "-", name)[:48].rstrip("-._")
    if not name or not SESSION_RE.match(name):
        return default
    return name


def sanitize_label(text: str | None, max_len: int = 80) -> str:
    """Clean a peer-supplied session label: one line, no markup characters, bounded length.

    The label is written by another user and shown to a model, so it must not be able to carry
    control characters or anything that could be mistaken for markup.
    """
    if not text:
        return ""
    cleaned = "".join(" " if ch.isspace() or not ch.isprintable() else ch for ch in str(text))
    cleaned = cleaned.replace("<", "(").replace(">", ")").replace('"', "'")
    cleaned = re.sub(r"\s{2,}", " ", cleaned).strip()
    return cleaned[:max_len].strip()
