"""Grant matching and routing decisions shared by relay and client."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from fnmatch import fnmatchcase

from .address import EndpointAddress
from .models import INITIATING_KINDS, REPLY_KINDS, Envelope, Grant


def match_pattern(pattern: str, name: str) -> bool:
    return fnmatchcase(name.lower(), pattern.lower())


def match_any(patterns: Iterable[str], name: str) -> bool:
    return any(match_pattern(p, name) for p in patterns)


def grant_covers(
    grant: Grant, sender: EndpointAddress, recipient: EndpointAddress, at_iso: str | None = None
) -> bool:
    """True when the grant's parties and session patterns match, regardless of rights."""
    if grant.is_expired(at_iso):
        return False
    if str(sender.user) != grant.grantee or str(recipient.user) != grant.grantor:
        return False
    return match_any(grant.grantee_sessions, sender.session) and match_any(
        grant.local_sessions, recipient.session
    )


def grant_allows(
    grant: Grant,
    sender: EndpointAddress,
    recipient: EndpointAddress,
    kind: str,
    at_iso: str | None = None,
) -> bool:
    return kind in grant.rights and grant_covers(grant, sender, recipient, at_iso)


@dataclass(frozen=True, slots=True)
class Decision:
    allowed: bool
    reason: str
    grant_id: str | None = None


ReplyCheck = Callable[[str, EndpointAddress, EndpointAddress], bool]
"""(reply_to id, sender, recipient) -> True when message `reply_to` went recipient -> sender."""


def same_account(sender: EndpointAddress, recipient: EndpointAddress) -> bool:
    """One account's own sessions. Nobody grants themselves permission to talk to themselves."""
    return str(sender.user) == str(recipient.user)


def decide_route(
    env: Envelope,
    grants: Iterable[Grant],
    is_reply_permitted: ReplyCheck,
    at_iso: str | None = None,
) -> Decision:
    sender, recipient = env.sender, env.recipient
    if sender == recipient:
        return Decision(False, "self_addressed")
    if same_account(sender, recipient):
        return Decision(True, "same_account")
    grants = list(grants)
    if env.kind in INITIATING_KINDS:
        for g in grants:
            if grant_allows(g, sender, recipient, env.kind, at_iso):
                return Decision(True, "grant", g.grant_id)
        return Decision(False, "no_grant")
    if env.kind in REPLY_KINDS:
        if env.reply_to and is_reply_permitted(env.reply_to, sender, recipient):
            return Decision(True, "reply_in_thread")
        for g in grants:  # a standing grant also covers replies (e.g. answer to a notify)
            if grant_covers(g, sender, recipient, at_iso):
                return Decision(True, "grant", g.grant_id)
        return Decision(False, "no_thread")
    if env.kind in ("pair_request", "pair_result"):
        return Decision(True, env.kind)
    return Decision(False, "relay_only_kind")
