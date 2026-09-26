"""Turn a short target ("build", "bob/build", an alias) into a full endpoint address.

The first contact always needs the full `handle@relay/session`, because at that point there is
nothing to resolve against. Once a grant exists the relay tells us which endpoints the peer has,
and short names become unambiguous in almost every real case. Ambiguity is reported, never guessed.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from aimessenger.protocol.address import AddressError, parse_endpoint, parse_user

from .store import Store

SENDABLE_DIRECTIONS = ("outbound", "self")
"""Grant directions whose endpoints I may address."""


class TargetError(ValueError):
    """The target could not be resolved to exactly one endpoint."""


@dataclass(frozen=True, slots=True)
class Candidate:
    endpoint: str
    label: str = ""
    online: bool = False
    source: str = "directory"

    def describe(self) -> str:
        bits = [self.endpoint]
        if self.label:
            bits.append(f"({self.label})")
        bits.append("online" if self.online else "offline")
        return " ".join(bits)


def _handle_of(endpoint: str) -> str:
    return endpoint.split("@", 1)[0]


def _session_of(endpoint: str) -> str:
    return endpoint.rsplit("/", 1)[1]


def _candidates(store: Store) -> list[Candidate]:
    """Endpoints a short name may stand for: only those this session is allowed to write to.

    Somebody who may write to us but whom we may not write back to used to be a candidate as
    well, so a bare session name could resolve to them and the relay would refuse the message
    with no_grant a moment later - the same name aim_contacts had just declined to list.
    """
    out = [
        Candidate(r["endpoint"], r.get("label") or "", bool(r.get("online")))
        for r in store.known_endpoints()
        if (r.get("direction") or "outbound") in SENDABLE_DIRECTIONS
    ]
    known = {c.endpoint for c in out}
    for alias, endpoint in store.aliases().items():
        if endpoint not in known:
            out.append(Candidate(endpoint, f"alias {alias}", False, "alias"))
    return out


def _match(text: str, store: Store) -> list[Candidate]:
    text = text.strip().lower()
    alias_target = store.aliases().get(text)
    if alias_target:
        known = {c.endpoint: c for c in _candidates(store)}
        return [known.get(alias_target, Candidate(alias_target, f"alias {text}", False, "alias"))]

    cands = _candidates(store)
    if "/" in text:  # handle/session shorthand
        handle, session = text.split("/", 1)
        return [
            c
            for c in cands
            if _handle_of(c.endpoint) == handle and _session_of(c.endpoint) == session
        ]
    by_session = [c for c in cands if _session_of(c.endpoint) == text]
    if by_session:
        return by_session
    return [c for c in cands if _handle_of(c.endpoint) == text]


def resolve_target(text: str, store: Store, refresh: Callable[[], None] | None = None) -> str:
    """Return a full endpoint address for `text`, or raise TargetError listing the candidates."""
    text = (text or "").strip()
    if not text:
        raise TargetError("no target given")
    try:
        return str(parse_endpoint(text))
    except AddressError:
        pass

    user = None
    try:
        user = parse_user(text)
    except AddressError:
        pass
    if user is not None:
        sessions = [c for c in _candidates(store) if c.endpoint.split("/", 1)[0] == str(user)]
        if refresh is not None and not sessions:
            refresh()
            sessions = [c for c in _candidates(store) if c.endpoint.split("/", 1)[0] == str(user)]
        if len(sessions) == 1:
            return sessions[0].endpoint
        if not sessions:
            raise TargetError(
                f"{user} has no known session; use the full address handle@relay/session"
            )
        raise TargetError(
            f"{user} has several sessions, pick one:\n  "
            + "\n  ".join(c.describe() for c in sessions)
        )

    matches = _match(text, store)
    if not matches and refresh is not None:
        refresh()
        matches = _match(text, store)
    if len(matches) == 1:
        return matches[0].endpoint
    if not matches:
        raise TargetError(
            f"unknown target {text!r}; use the full address handle@relay/session, "
            "or run `aim contacts` to see who you can reach"
        )
    raise TargetError(
        f"{text!r} matches several endpoints, be more specific:\n  "
        + "\n  ".join(c.describe() for c in matches)
    )


def contacts_to_rows(contacts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Flatten the relay's contacts payload into rows for the local endpoint directory."""
    rows: list[dict[str, Any]] = []
    for c in contacts:
        for ep in c.get("endpoints") or []:
            rows.append(
                {
                    "endpoint": ep["endpoint"],
                    "peer": c["peer"],
                    "session": ep["session"],
                    "label": ep.get("label") or "",
                    "direction": c.get("direction") or "outbound",
                    "online": ep.get("online"),
                    "last_seen": ep.get("last_seen"),
                }
            )
    return rows
