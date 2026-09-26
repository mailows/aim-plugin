"""Pydantic models of AIM v1 wire objects: Envelope, Body, Grant, PairRequest."""

from __future__ import annotations

from typing import Any, Literal

from nacl.signing import SigningKey
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from ulid import ULID

from . import DEFAULT_TTL_S, MAX_ATTACHMENTS_BYTES, MAX_TEXT_BYTES, PROTOCOL_VERSION
from .address import EndpointAddress, parse_endpoint, parse_user, validate_session_pattern
from .canonical import canonical_json
from .crypto import sign, verify
from .timeutil import at_or_now, now_iso, parse_iso

Kind = Literal["ask", "answer", "notify", "ack", "error", "pair_request", "pair_result", "presence"]
Authority = Literal["owner", "assistant"]
Right = Literal["ask", "notify"]
AnswerStatus = Literal["ok", "declined", "partial"]

# Kinds a peer may *initiate* (need a grant); the rest are replies or relay/system traffic.
INITIATING_KINDS: frozenset[str] = frozenset({"ask", "notify"})
REPLY_KINDS: frozenset[str] = frozenset({"answer", "ack", "error"})


def new_id() -> str:
    return str(ULID())


class Signed(BaseModel):
    """Base for objects signed with the sender's Ed25519 key over their canonical JSON."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    sig: str | None = None

    def signing_bytes(self) -> bytes:
        data = self.model_dump(by_alias=True, exclude_none=True, exclude={"sig"}, mode="json")
        return canonical_json(data)

    def sign_with(self, key: SigningKey) -> None:
        self.sig = sign(key, self.signing_bytes())

    def verify_with(self, pubkey_b64: str) -> bool:
        return bool(self.sig) and verify(pubkey_b64, self.signing_bytes(), self.sig)

    def to_wire(self) -> dict[str, Any]:
        return self.model_dump(by_alias=True, exclude_none=True, mode="json")


class Ref(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["git", "url", "file"]
    repo: str | None = None
    commit: str | None = None
    path: str | None = None
    url: str | None = None
    note: str | None = None


class Attachment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=255)
    mime: str = Field(default="text/plain", max_length=100)
    b64: str


class Body(BaseModel):
    model_config = ConfigDict(extra="forbid")

    format: Literal["markdown", "text"] = "markdown"
    text: str = ""
    refs: list[Ref] = Field(default_factory=list)
    attachments: list[Attachment] = Field(default_factory=list)
    status: AnswerStatus | None = None  # answer
    code: str | None = None  # error code / ack state (delivered, injected, ...)
    data: dict[str, Any] | None = None  # structured payload for ack / pair_* / presence

    @field_validator("text")
    @classmethod
    def _text_size(cls, v: str) -> str:
        if len(v.encode("utf-8")) > MAX_TEXT_BYTES:
            raise ValueError(f"text exceeds {MAX_TEXT_BYTES} bytes")
        return v

    @field_validator("attachments")
    @classmethod
    def _attachments_size(cls, v: list[Attachment]) -> list[Attachment]:
        if sum(len(a.b64) for a in v) > MAX_ATTACHMENTS_BYTES:
            raise ValueError(f"attachments exceed {MAX_ATTACHMENTS_BYTES} bytes")
        return v


class Envelope(Signed):
    v: int = PROTOCOL_VERSION
    id: str = Field(default_factory=new_id)
    ts: str = Field(default_factory=now_iso)
    from_: str = Field(alias="from")
    to: str
    kind: Kind
    thread: str | None = None
    reply_to: str | None = None
    ttl_s: int = Field(default=DEFAULT_TTL_S, ge=0, le=90 * 24 * 3600)
    deadline: str | None = None
    authority: Authority = "assistant"
    body: Body = Field(default_factory=Body)

    @field_validator("v")
    @classmethod
    def _version(cls, v: int) -> int:
        if v != PROTOCOL_VERSION:
            raise ValueError(f"unsupported protocol version {v}")
        return v

    @field_validator("from_", "to")
    @classmethod
    def _addresses(cls, v: str) -> str:
        return str(parse_endpoint(v))

    @field_validator("ts", "deadline")
    @classmethod
    def _timestamps(cls, v: str | None) -> str | None:
        if v is not None:
            parse_iso(v)
        return v

    @model_validator(mode="after")
    def _consistency(self) -> Envelope:
        # Only fill in the thread on a freshly built envelope. An incoming one is already
        # signed, and adding a field here would change its canonical form and break the signature.
        if self.kind == "ask" and self.thread is None and self.sig is None:
            self.thread = self.id
        if self.kind in REPLY_KINDS and not self.reply_to:
            raise ValueError(f"{self.kind} requires reply_to")
        if self.kind == "answer" and self.body.status is None:
            self.body.status = "ok"
        if self.kind == "error" and not self.body.code:
            raise ValueError("error requires body.code")
        return self

    @property
    def sender(self) -> EndpointAddress:
        return parse_endpoint(self.from_)

    @property
    def recipient(self) -> EndpointAddress:
        return parse_endpoint(self.to)

    def is_expired(self, at_iso: str | None = None) -> bool:
        return (at_or_now(at_iso) - parse_iso(self.ts)).total_seconds() > self.ttl_s

    def past_deadline(self, at_iso: str | None = None) -> bool:
        return bool(self.deadline) and at_or_now(at_iso) > parse_iso(self.deadline)


class Grant(Signed):
    """Signed by *grantor*: *grantee*'s sessions may initiate traffic to grantor's sessions."""

    v: int = PROTOCOL_VERSION
    grant_id: str = Field(default_factory=new_id)
    grantor: str
    grantee: str
    grantee_sessions: list[str] = Field(default_factory=lambda: ["*"], min_length=1)
    local_sessions: list[str] = Field(default_factory=lambda: ["*"], min_length=1)
    rights: list[Right] = Field(default_factory=lambda: ["ask", "notify"], min_length=1)
    created_at: str = Field(default_factory=now_iso)
    expires_at: str | None = None
    note: str | None = Field(default=None, max_length=500)
    trusted: bool | None = None
    """Whether the grantor lets this peer direct the work, not merely ask.

    Absent or false is the ordinary case: an incoming message is data, never instructions. Setting
    it is the grantor saying "I vouch for this person; their session may tell mine what to do." It
    is signed with the rest of the grant, so a relay cannot hand out trust on its own, and it is
    decided by a human on the website - never by a session and never by a model.

    None rather than False on purpose: the signature covers every field that is present, so a
    default of False would have changed the signed bytes of every grant issued before this field
    existed and broken all of them at once.
    """

    @property
    def is_trusted(self) -> bool:
        return bool(self.trusted)

    @field_validator("grantor", "grantee")
    @classmethod
    def _users(cls, v: str) -> str:
        return str(parse_user(v))

    @field_validator("created_at", "expires_at")
    @classmethod
    def _timestamps(cls, v: str | None) -> str | None:
        if v is not None:
            parse_iso(v)
        return v

    @field_validator("grantee_sessions", "local_sessions")
    @classmethod
    def _patterns(cls, v: list[str]) -> list[str]:
        """Session patterns are matched against names, so they have to look like names.

        Only the website and the CLI checked that. A grant published straight through the signed
        API could carry any string at all, and the grantee's client read those strings out into
        its session when the relay announced the grant - a way for a stranger to put text in
        front of somebody else's model without ever having been approved by them.
        """
        cleaned = [p.strip().lower() for p in v if p.strip()]
        if not cleaned:
            raise ValueError("at least one session pattern is required")
        if len(cleaned) > 32:
            raise ValueError("a grant may not name more than 32 session patterns")
        return [validate_session_pattern(p) for p in cleaned]

    def is_expired(self, at_iso: str | None = None) -> bool:
        return bool(self.expires_at) and at_or_now(at_iso) > parse_iso(self.expires_at)


class PairRequest(Signed):
    """Request from *requester* endpoint for access to *target* endpoint (approved by a human).

    The `want_*` fields are a proposal, in the vocabulary of the Grant they are asking for, so
    the person deciding sees a filled-in form rather than a blank one and answers a real
    question: is this the shape of the connection, or a smaller one? They bind nobody. The
    grantor signs whatever they actually decided, which may be narrower, and widening later is
    another request rather than an edit.

    All of them default to None, never to a value: the signature covers every field that is
    present, so a default would have changed the signed bytes of every request made before the
    field existed.
    """

    v: int = PROTOCOL_VERSION
    id: str = Field(default_factory=new_id)
    requester: str
    target: str
    note: str | None = Field(default=None, max_length=500)
    created_at: str = Field(default_factory=now_iso)
    want_local_sessions: list[str] | None = None
    """Which of the TARGET's sessions the requester would like to reach."""
    want_peer_sessions: list[str] | None = None
    """Which of the REQUESTER's own sessions would write."""
    want_rights: list[Right] | None = None
    want_trusted: bool | None = None
    """Whether the requester is asking to be allowed to direct the target's work.

    Asking is all it can ever be: trust lives in the grant the target signs, so it arrives as a
    ticked box on their form and they untick it like any other term.
    """
    want_mutual: bool | None = None
    """Whether the requester would also welcome the mirror of this grant."""
    direction: Literal["out", "in", "both"] | None = None
    """What the requester is proposing. Absent means `out`, which is all there used to be.

    `out` asks for access and nothing else. `in` asks for nothing and carries an `offer`.
    `both` does the two together, which is the ordinary case between colleagues.
    """
    offer: Grant | None = None
    """A grant the requester has already signed, letting the target reach them.

    It travels with the request rather than being published, because it is still a change to
    somebody else's contacts: they see what is offered and accept it along with the rest, or
    they decline and nothing of it survives. Signed by the requester, so the relay cannot
    invent one and the target's client can check it like any other grant.
    """

    @property
    def asks_for_access(self) -> bool:
        return (self.direction or "out") in ("out", "both")

    @field_validator("requester", "target")
    @classmethod
    def _endpoints(cls, v: str) -> str:
        return str(parse_endpoint(v))

    @model_validator(mode="after")
    def _offer_is_between_these_two(self) -> PairRequest:
        """An offer names the same pair as the request, or it is somebody else's business."""
        if self.offer is None:
            return self
        here, there = parse_endpoint(self.requester).user, parse_endpoint(self.target).user
        if self.offer.grantor != str(here) or self.offer.grantee != str(there):
            raise ValueError("the offer must be from the requester to the target")
        return self

    @field_validator("want_local_sessions", "want_peer_sessions")
    @classmethod
    def _wanted(cls, v: list[str] | None) -> list[str] | None:
        """Same rule as a grant: a proposal is read out to a person, so it has to look like names."""
        if v is None:
            return None
        cleaned = [p.strip().lower() for p in v if p.strip()]
        if len(cleaned) > 32:
            raise ValueError("a request may not name more than 32 session patterns")
        return [validate_session_pattern(p) for p in cleaned] or None

    def proposal(self, field: str) -> str:
        """What to put in the form field, as the person would type it. Empty means every session."""
        values = getattr(self, field, None)
        return ", ".join(values) if values and values != ["*"] else ""
