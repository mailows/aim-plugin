"""The aim channel: a Claude Code *channel* MCP server bridging this session to the AIM relay.

Spawned by Claude Code (stdio). Inbound envelopes are verified (signature, TOFU key pinning,
local grant check), stored, acked and pushed into the session as `notifications/claude/channel`.
Outbound traffic goes through MCP tools (aim_ask, aim_answer, ...).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from contextlib import asynccontextmanager, suppress
from logging.handlers import RotatingFileHandler
from typing import Any

import anyio
from mcp import types
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server
from pydantic import BaseModel, ValidationError

from aimessenger.protocol import CLIENT_VERSION
from aimessenger.protocol.address import AddressError, EndpointAddress, parse_endpoint
from aimessenger.protocol.crypto import fingerprint, public_key_b64
from aimessenger.protocol.grants import grant_allows, grant_covers, same_account
from aimessenger.protocol.models import INITIATING_KINDS, REPLY_KINDS, Body, Envelope, Grant, Ref
from aimessenger.protocol.timeutil import iso_in, now_iso, parse_iso

from .. import names
from ..claude_sessions import resolve_session
from ..config import ClientConfig, aim_home
from ..identity import load_identity
from ..relay_client import RelayHTTP, RelayHTTPError, RelayWS, RelayWSError
from ..resolve import (
    SENDABLE_DIRECTIONS,
    contacts_to_rows,
    resolve_target,
)
from ..store import Store
from .bootstrap import Bootstrap
from .instructions import INSTRUCTIONS
from .tools import TOOLS

CHANNEL_METHOD = "notifications/claude/channel"
UNREAD_KEEP = 200
log = logging.getLogger("aim.channel")


def safe_for_model(text: str | None) -> str:
    """Neutralise anything in foreign text that could pass for the channel framing itself.

    Claude Code wraps what we push in <channel source="aim" ...>...</channel>. A peer who writes
    "</channel>" into a message - or into a pairing note, which needs no approval at all - could
    otherwise appear to close our tag and open one of their own, and whatever followed would read
    as if the client had vouched for it. Only the two tag spellings are touched, so code and
    ordinary angle brackets in a message survive intact.
    """
    if not text:
        return ""
    return re.sub(r"<(/?)\s*channel\b", r"(\1channel", text, flags=re.IGNORECASE)


class RawNotification(BaseModel):
    method: str
    params: dict[str, Any] | None = None


def _setup_logging() -> None:
    handler = RotatingFileHandler(
        aim_home() / "channel.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8"
    )
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(os.environ.get("AIM_LOG_LEVEL", "INFO").upper())


class ChannelServer:
    def __init__(
        self,
        cfg: ClientConfig,
        key,
        store: Store,
        session_name: str,
        yield_address: bool = False,
    ) -> None:
        self.cfg, self.key, self.store = cfg, key, store
        self.session_name = session_name
        self.me = f"{cfg.user}/{session_name}"
        self.http = RelayHTTP(cfg.relay_url, cfg.handle, key)
        self.ws = RelayWS(
            cfg.ws_url,
            cfg.handle,
            session_name,
            key,
            on_push=self.on_push,
            on_state=self.on_state,
            label=cfg.label_for(session_name),
            yield_address=yield_address,
        )
        self.mcp_session: Any = None
        self.pending_events: list[tuple[str, dict[str, str]]] = []
        self.answer_events: dict[str, asyncio.Event] = {}
        self.presence: dict[str, bool] = {}
        self.connected_once = False
        # relay and account notices only; peer messages live in the store, which is the queue
        self.unread: list[dict[str, Any]] = []
        self.arrived = asyncio.Event()
        self.tasks: list[asyncio.Task[Any]] = []
        # Only Claude Code turns our notification into something its model reads. Every other
        # host drops it on the floor, and saying "injected" there was a false receipt.
        self.pushes = bool(os.environ.get("CLAUDE_CODE_SESSION_ID"))
        self._key_change_reported: set[str] = set()  # warn once per peer, not per message

    # ---- MCP side --------------------------------------------------------------

    def capture(self, ctx: Any) -> None:
        if self.mcp_session is None:
            self.mcp_session = ctx.session
            log.info("MCP session captured via %s", ctx.method)
            if self.pending_events:
                asyncio.get_running_loop().create_task(self._flush_pending())

    async def _flush_pending(self) -> None:
        events, self.pending_events = self.pending_events, []
        for content, meta in events:
            await self.inject(content, meta)

    def attach(self, session: Any) -> None:
        """Adopt the MCP session the setup phase already captured."""
        self.mcp_session = session

    async def start(self) -> None:
        """Start talking to the relay. Separate from __init__: a channel that had to be linked
        first appears minutes after the server did."""
        if self.tasks:
            return
        ws = asyncio.create_task(self.ws.run())
        ws.add_done_callback(_report_if_it_died)  # nobody awaits it; a crash must not be silent
        ws.add_done_callback(self._say_why_it_stopped)
        self.tasks = [ws, asyncio.create_task(self.deadline_watch())]

    async def stop(self) -> None:
        for task in self.tasks:
            task.cancel()
        self.tasks = []
        await self.ws.stop()

    def _say_why_it_stopped(self, task: asyncio.Task[Any]) -> None:
        """A relay task that gave up must reach the conversation, not only channel.log.

        Losing the address to another AIM process is the case that matters: the session stays open
        and looks fine, while every message for it goes somewhere the model cannot see.
        """
        if task.cancelled():
            return
        exc = task.exception()
        if exc is None:
            return
        code = getattr(exc, "code", "")
        text = (
            f"AIM stopped: {self.me} is now held by another AIM client. Two clients cannot "
            f"share one address, so this one gave it up and will not receive anything. It is "
            f"usually a second AIM server in the same tool (`claude mcp list`, keep the plugin) "
            f"or another tool - Codex, Grok - started in the same directory under the same name; "
            f"give that one its own with AIM_SESSION_NAME. Then restart this session."
            if code == "replaced"
            else f"AIM lost its connection to the relay and stopped: {exc}"
        )
        with suppress(RuntimeError):  # no loop left during shutdown
            asyncio.get_running_loop().create_task(
                self.inject(text, {"kind": "status", "trust": "system", "severity": "high"})
            )

    async def inject(self, content: str, meta: dict[str, str], remember: bool = True) -> bool:
        """Push an event towards the conversation. True only if a session actually took it.

        `remember=False` is for peer messages: they are already in the store, which is the
        queue `aim_receive` reads, and keeping a second copy here would hand them out twice.
        """
        if remember:
            self._remember(content, meta)
        if self.mcp_session is None:
            self.pending_events.append((content, meta))
            return False
        await self.mcp_session.send_notification(
            RawNotification(method=CHANNEL_METHOD, params={"content": content, "meta": meta})
        )
        return True

    # ---- relay side --------------------------------------------------------------

    async def on_state(self, connected: bool) -> None:
        if connected:
            self._adopt_relay_address()
            try:
                self.store.replace_grants(await self.ws.grants())
                self.store.save_endpoints(contacts_to_rows(await self.ws.contacts()))
            except (RelayWSError, ValidationError) as exc:
                log.warning("directory sync failed: %s", exc)
            if not self.connected_once:
                self.connected_once = True
            else:
                await self.inject(
                    f"AIM reconnected to relay as {self.me}.", {"kind": "status", "trust": "system"}
                )
        else:
            log.warning("relay disconnected")

    def _adopt_relay_address(self) -> None:
        """Take the address the relay just gave us, rather than the one we assumed at startup.

        A relay that renames itself (aim.mailows.com -> mailows) otherwise leaves every running
        channel answering to an address nobody uses: deliveries arrive for the new name, the old
        `me` does not match, and each one is dropped as somebody else's mail.
        """
        told = self.ws.endpoint
        if not told or told == self.me:
            return
        log.warning("relay calls this endpoint %s, not %s - adopting it", told, self.me)
        was, given = self.session_name, told.rsplit("/", 1)[-1]
        self.me = told
        self.cfg.relay_host = told.split("@", 1)[1].split("/", 1)[0]
        if given != was:
            self._took_the_next_free_name(was, given)

    def _took_the_next_free_name(self, wanted: str, given: str) -> None:
        """The relay kept the address for the session already holding it and gave us another.

        Kept for this session's restarts, and only for this session: the folder's memory still
        names the session that had it first, which is what the next window opened here should
        not be confused with either.
        """
        self.session_name = given
        info = resolve_session()
        names.remember_for_session(given, info.session_id)
        with suppress(RuntimeError):
            asyncio.get_running_loop().create_task(
                self.inject(
                    f"Another session is already {self.cfg.user}/{wanted}, so this one answers to "
                    f"{self.me}. Nothing was taken from the other. To pick a name of your own, "
                    f"use /aim-rename.",
                    {"kind": "status", "trust": "system"},
                )
            )

    async def on_push(self, frame: dict[str, Any]) -> None:
        ftype = frame.get("type")
        if ftype == "deliver":
            await self.handle_incoming(frame.get("envelope"))
        elif ftype == "presence":
            self.presence[str(frame.get("endpoint"))] = bool(frame.get("online"))
        elif ftype == "pair_request":
            # Nothing reaches the model. Whoever is asking has not been approved by anybody, so
            # this is the one thing a stranger can start, and it stops at the website where a
            # person decides. Relays from 0.1.7 do not send this frame at all; an older one might.
            log.info("pairing request %s ignored here; it belongs on the website", frame.get("id"))
        elif ftype == "pair_result":
            status = str(frame.get("status"))
            self.store.set_pair_status(str(frame.get("request_id")), status)
            if frame.get("grant"):
                try:
                    self.store.save_grant(Grant.model_validate(frame["grant"]))
                except ValidationError:
                    log.warning("invalid grant in pair_result")
            await self.inject(
                f"Your pairing request {frame.get('request_id')} was {status}.",
                {"kind": "pair_result", "status": status, "trust": "system"},
            )
        elif ftype == "grant_added":
            try:
                g = Grant.model_validate(frame.get("grant"))
                self.store.save_grant(g)
                await self.inject(
                    safe_for_model(
                        f"{g.grantor} granted {g.grantee} sessions {g.grantee_sessions} access "
                        f"to {g.local_sessions} with rights {g.rights}."
                    ),
                    {"kind": "grant", "grant_id": g.grant_id, "trust": "system"},
                )
            except ValidationError:
                log.warning("invalid grant_added frame")
        elif ftype == "grant_revoked":
            gid = str(frame.get("grant_id"))
            self.store.revoke_grant(gid)
            await self.inject(
                f"Grant {gid} was revoked by {frame.get('by')}.",
                {"kind": "grant", "grant_id": gid, "revoked": "true", "trust": "system"},
            )
        else:
            log.debug("ignoring push %s", ftype)

    async def _peer_pubkey(self, user: str, handle: str) -> str | None:
        if user == self.cfg.user:
            # My own account signs with the key this process holds. Pinning it like a stranger's
            # outlived the account: re-created with a new key, every message between its own
            # sessions was checked against the old one and dropped without a word.
            return public_key_b64(self.key)
        cached = self.store.get_peer_pubkey(user)
        if cached:
            return cached
        try:
            info = await anyio.to_thread.run_sync(self.http.user_info, handle)
        except RelayHTTPError as exc:
            log.warning("cannot fetch key of %s: %s", user, exc)
            return None
        pub = info["pubkey"]
        if not self.store.save_peer(user, pub, fingerprint(pub)):
            log.error("KEY CHANGE for %s - refusing message", user)
            await self.inject(
                f"WARNING: the relay now reports a different public key for {user} than the one "
                f"pinned locally. Messages from them are refused until your user confirms the new "
                f"fingerprint with {user} directly and runs `aim peers --accept {user}`.",
                {"kind": "status", "trust": "system", "severity": "high"},
            )
            return None
        return pub

    async def _report_key_change(self, user: str, handle: str, pinned: str) -> None:
        """A signature that fails against the pinned key: find out whether the key moved.

        The warning used to fire only when a key was first fetched, so a peer who had re-keyed
        since was dropped silently forever - a line in channel.log and nothing anywhere a person
        looks. The new key is not accepted here: accepting it is the person's call, after they
        have compared fingerprints with the peer some other way.
        """
        if user in self._key_change_reported:
            return
        try:
            info = await anyio.to_thread.run_sync(self.http.user_info, handle)
        except RelayHTTPError as exc:
            log.warning("cannot fetch key of %s: %s", user, exc)
            return
        current = info.get("pubkey")
        if not current or current == pinned:
            return  # the key did not move: this was a bad signature, not a re-keyed peer
        self._key_change_reported.add(user)
        log.error("KEY CHANGE for %s - refusing its messages", user)
        await self.inject(
            f"WARNING: messages from {user} are being refused. Its key was pinned as "
            f"{fingerprint(pinned)}, and the relay now reports {fingerprint(current)}. That "
            f"happens when an account is re-created or re-keyed - and also when somebody is "
            f"impersonating it. Tell your user to confirm the new fingerprint with {user} "
            f"directly, then run `aim peers --accept {user}`.",
            {"kind": "status", "trust": "system", "severity": "high"},
        )

    def _my_grants(self) -> list[Grant]:
        """Grants that let somebody write to me - and that I can prove I issued.

        The relay hands us this list, and until now we took its word for it. That was tolerable
        while every incoming message was data whatever happened; it stopped being tolerable when a
        grant could also say the peer is trusted to direct the work. So each one is checked against
        our own public key: a relay cannot invent a grant, and cannot flip the trust bit on a real
        one, because the signature covers it.
        """
        mine = public_key_b64(self.key)
        return [
            g
            for g in self.store.active_grants()
            if g.grantor == self.cfg.user and g.verify_with(mine)
        ]

    def _trusts(self, sender: EndpointAddress) -> bool:
        """Whether my user vouched for the session this came from.

        Read off the peer, not off the grant that let this particular envelope through: an
        answer to my own question travels on the thread and needs no grant, and rendering it
        as less trustworthy than an unprompted message from the same peer made no sense.
        """
        me = parse_endpoint(self.me)
        return any(g.is_trusted and grant_covers(g, sender, me) for g in self._my_grants())

    def _authorized(self, env: Envelope) -> tuple[bool, str, Grant | None]:
        sender, recipient = env.sender, env.recipient
        if same_account(sender, recipient):
            return True, "same_account", None  # my own other session, no grant involved
        grants = self._my_grants()
        if env.kind in INITIATING_KINDS:
            for g in grants:
                if grant_allows(g, sender, recipient, env.kind):
                    return True, "grant", g
            return False, "no_local_grant", None
        if env.kind in REPLY_KINDS:
            orig = self.store.get_message(env.reply_to or "")
            if orig is not None and orig.from_ == env.to and orig.to == env.from_:
                return True, "reply_in_thread", None
            for g in grants:
                if grant_covers(g, sender, recipient):
                    return True, "grant", g
            return False, "no_thread", None
        return False, "unexpected_kind", None

    async def handle_incoming(self, raw: Any) -> None:
        try:
            env = Envelope.model_validate(raw)
        except ValidationError as exc:
            log.warning("invalid envelope dropped: %s", exc)
            return
        if env.to != self.me:
            log.warning("envelope for %s delivered to %s - dropped", env.to, self.me)
            return
        if self.store.has_message(env.id):
            await self._safe_ack(env.id)
            return
        pub = await self._peer_pubkey(str(env.sender.user), env.sender.handle)
        if not pub or not env.verify_with(pub):
            log.error("signature check failed for %s from %s", env.id, env.from_)
            if pub:
                await self._report_key_change(str(env.sender.user), env.sender.handle, pub)
            return
        ok, reason, _grant = self._authorized(env)
        if not ok and env.kind in INITIATING_KINDS:
            # a grant may have been approved seconds ago: refresh once
            try:
                self.store.replace_grants(await self.ws.grants())
                ok, reason, _grant = self._authorized(env)
            except RelayWSError, ValidationError:
                pass
        if not ok:
            log.error("unauthorized %s from %s dropped (%s)", env.kind, env.from_, reason)
            await self._safe_ack(env.id)
            return
        if env.kind == "ask" and env.past_deadline():
            log.info("ask %s already past deadline - dropped", env.id)
            self.store.save_message(env, "in", "expired")
            await self._safe_ack(env.id)
            return

        self.store.save_message(env, "in", "received")
        await self._safe_ack(env.id)

        if env.kind in ("answer", "error") and env.reply_to:
            self.store.set_state(env.reply_to, "answered")
            ev = self.answer_events.get(env.reply_to)
            if ev:
                ev.set()
        if env.kind == "ack":
            self.store.note_delivery(env.reply_to or "")
            if (env.body.code or "") != "injected":
                self.store.mark_collected([env.id])  # a delivery receipt wakes nobody
                return  # only the "reached the model" receipt is worth an event

        self.arrived.set()  # a blocked aim_receive finds it in the store
        trusted = reason == "same_account" or self._trusts(env.sender)
        content, meta = self._render(env, trusted=trusted)
        pushed = await self.inject(content, meta, remember=False)
        # "injected" tells the sender a model has the message. Only a push into Claude Code
        # earns it here; anywhere else it is earned when aim_receive hands the message over.
        if pushed and self.pushes:
            self.store.set_state(env.id, "injected")
            if env.kind == "ask" and self.cfg.ack_injected:
                await self._send_receipt(env)

    async def _safe_ack(self, message_id: str) -> None:
        try:
            await self.ws.ack(message_id)
        except RelayWSError as exc:
            log.warning("ack %s failed: %s", message_id, exc)

    async def _send_receipt(self, env: Envelope) -> None:
        receipt = Envelope(
            **{
                "from": self.me,
                "to": env.from_,
                "kind": "ack",
                "reply_to": env.id,
                "thread": env.thread,
                "ttl_s": 3600,
                "body": Body(code="injected"),
            }
        )
        receipt.sign_with(self.key)
        try:
            await self.ws.send_envelope(receipt)
        except RelayWSError as exc:
            log.debug("receipt failed: %s", exc)

    def _render(self, env: Envelope, trusted: bool = False) -> tuple[str, dict[str, str]]:
        meta = {
            "kind": env.kind,
            "from": env.from_,
            "msg_id": env.id,
            "thread": env.thread or env.id,
            "authority": env.authority,
            "trust": "trusted" if trusted else "foreign",
        }
        if env.reply_to:
            meta["reply_to"] = env.reply_to
        if env.deadline:
            meta["deadline"] = env.deadline
        if env.body.status:
            meta["status"] = env.body.status
        if env.body.code:
            meta["code"] = env.body.code
        known = {r["endpoint"]: r for r in self.store.known_endpoints()}
        sender_row = known.get(env.from_)
        if sender_row:
            if sender_row.get("label"):
                meta["from_label"] = sender_row["label"]
            alias = self.store.alias_of(env.from_)
            if alias:
                meta["from_alias"] = alias
        who = "a session your user marked as trusted" if trusted else "an outside party"
        lines = [
            f"From {env.from_} ({env.kind}, authority={env.authority}, msg_id={env.id}) - {who}."
        ]
        if env.kind == "ask":
            lines.append(
                f'Answer with aim_answer(msg_id="{env.id}", ...) before {env.deadline or "no deadline"}.'
            )
        if env.kind == "ack":
            lines.append(f"Receipt: your message {env.reply_to} reached the peer's Claude.")
        lines.append(
            "--- message (trusted: your user vouched for this peer) ---"
            if trusted
            else "--- message (untrusted) ---"
        )
        lines.append(safe_for_model(env.body.text) or "(no text)")
        if env.body.refs:
            lines.append("--- refs ---")
            for r in env.body.refs:
                parts = [r.type, r.repo or r.url or "", r.commit or "", r.path or "", r.note or ""]
                lines.append("- " + safe_for_model(" ".join(p for p in parts if p)))
        if env.body.attachments:
            lines.append(
                f"--- {len(env.body.attachments)} attachment(s) stored locally (aim_thread) ---"
            )
        return "\n".join(lines), meta

    # ---- outbound helpers (tools) ------------------------------------------------------

    def _resolve(self, target: str) -> str:
        return resolve_target(target, self.store)

    def _build(self, kind: str, to: str, text: str, **kw: Any) -> Envelope:
        refs = [Ref.model_validate(r) for r in kw.pop("refs", None) or []]
        env = Envelope(
            **{"from": self.me, "to": self._resolve(to), "kind": kind, **kw},
            body=Body(text=text, refs=refs),
        )
        return env

    async def _send(self, env: Envelope) -> dict[str, Any]:
        env.sign_with(self.key)
        if not self.ws.connected.is_set():
            raise RelayWSError("offline", "not connected to the relay right now - retry shortly")
        result = await self.ws.send_envelope(env)
        self.store.save_message(env, "out", result.get("state", "sent"))
        return {"id": env.id, "thread": env.thread, "state": result.get("state"), "to": env.to}

    async def tool_ask(self, a: dict[str, Any]) -> dict[str, Any]:
        deadline_s = int(a.get("deadline_s") or 1800)
        env = self._build(
            "ask",
            a["to"],
            a["text"],
            thread=a.get("thread"),
            refs=a.get("refs"),
            authority=a.get("authority", "assistant"),
            deadline=iso_in(deadline_s),
            ttl_s=min(max(deadline_s, 60), 7 * 86400),
        )
        self.answer_events[env.id] = asyncio.Event()
        out = await self._send(env)
        out["hint"] = (
            'the answer arrives as a <channel kind="answer"> event; or call aim_wait(msg_id)'
        )
        return out

    async def tool_answer(self, a: dict[str, Any]) -> dict[str, Any]:
        ask = self.store.get_message(a["msg_id"])
        if ask is None or ask.kind != "ask" or ask.to != self.me:
            raise ValueError("msg_id is not an incoming ask for this session")
        env = self._build(
            "answer", ask.from_, a["text"], reply_to=ask.id, thread=ask.thread, refs=a.get("refs")
        )
        env.body.status = a.get("status", "ok")
        out = await self._send(env)
        self.store.set_state(ask.id, "answered")
        return out

    async def tool_notify(self, a: dict[str, Any]) -> dict[str, Any]:
        env = self._build(
            "notify",
            a["to"],
            a["text"],
            thread=a.get("thread"),
            refs=a.get("refs"),
            authority=a.get("authority", "assistant"),
        )
        return await self._send(env)

    async def tool_wait(self, a: dict[str, Any]) -> dict[str, Any]:
        msg_id = a["msg_id"]
        timeout = int(a.get("timeout_s") or 120)
        ev = self.answer_events.setdefault(msg_id, asyncio.Event())
        answers = self.store.answers_for(msg_id)
        if not answers:
            try:
                await asyncio.wait_for(ev.wait(), timeout)
            except TimeoutError:
                return {"msg_id": msg_id, "answered": False, "waited_s": timeout}
            answers = self.store.answers_for(msg_id)
        return {
            "msg_id": msg_id,
            "answered": bool(answers),
            "answers": [
                {
                    "from": x.from_,
                    "kind": x.kind,
                    "status": x.body.status,
                    "code": x.body.code,
                    "text": x.body.text,
                }
                for x in answers
            ],
        }

    async def tool_contacts(self) -> dict[str, Any]:
        contacts = await self.ws.contacts()
        rows = contacts_to_rows(contacts)
        self.store.save_endpoints(rows)
        aliases = self.store.aliases()
        by_endpoint = {ep: a for a, ep in aliases.items()}
        reachable = [
            {
                "endpoint": r["endpoint"],
                "alias": by_endpoint.get(r["endpoint"]),
                "short": r["session"],
                "label": r["label"],
                "online": bool(r["online"]),
                "last_seen": r["last_seen"],
            }
            for r in rows
            if r["direction"] in SENDABLE_DIRECTIONS
        ]
        return {
            "me": self.me,
            "may_send_to": reachable,
            "hint": "address a peer by alias, by session name, or by the full endpoint",
            "grants": contacts,
        }

    def tool_status(self) -> dict[str, Any]:
        grants = self.store.active_grants()
        return {
            "me": self.me,
            "linked": True,
            "relay": self.cfg.relay_url,
            "connected": self.ws.connected.is_set(),
            "last_error": self.ws.last_error,
            "grants_as_grantor": sum(1 for g in grants if g.grantor == self.cfg.user),
            "grants_as_grantee": sum(1 for g in grants if g.grantee == self.cfg.user),
            "client_version": CLIENT_VERSION,
        }

    def _remember(self, content: str, meta: dict[str, str]) -> None:
        """Keep every event we pushed. A session started without the channel flag is never woken,
        and its model has no other way to find out that something arrived."""
        self.unread.append({"at": now_iso(), "content": content, **meta})
        del self.unread[:-UNREAD_KEEP]
        self.arrived.set()

    async def tool_receive(self, a: dict[str, Any]) -> dict[str, Any]:
        """Everything that arrived and this model has not taken in yet, oldest first.

        Peer messages come from the store, so a restart loses nothing: the relay was acked the
        moment they landed, and an in-memory list used to be the only thing standing between
        that and "never seen". Relay and account notices stay in memory; they describe the
        connection as it was and are worth nothing after a restart.
        """
        timeout = max(0, min(600, int(a.get("timeout_s") or 0)))
        if not self.unread and not self.store.uncollected(self.me, 1) and timeout:
            self.arrived.clear()
            with suppress(TimeoutError):
                await asyncio.wait_for(self.arrived.wait(), timeout)
        notices, self.unread = self.unread, []
        waiting = self.store.uncollected(self.me)
        self.store.mark_collected([env.id for env, _ in waiting])
        messages = []
        for env, state in waiting:
            trusted = same_account(env.sender, env.recipient) or self._trusts(env.sender)
            content, meta = self._render(env, trusted=trusted)
            messages.append({"at": env.ts, "content": content, **meta})
            if self.pushes or state != "received":
                continue  # Claude Code: the push already said so, or this was answered
            self.store.set_state(env.id, "injected")
            if env.kind == "ask" and self.cfg.ack_injected:
                await self._send_receipt(env)  # now it is true: the model has it
        events = sorted(notices + messages, key=lambda e: e.get("at") or "")
        return {"events": events, "count": len(events), "me": self.me}

    async def tool_rename(self, a: dict[str, Any]) -> dict[str, Any]:
        """Answer to a different name from now on, and to the same one after the next restart."""
        info = resolve_session()
        wanted = names.remember(str(a["name"]), info.session_id, info.cwd)
        was, self.session_name = self.me, wanted
        self.ws.yield_address = False  # chosen by the person, so it is this session's to hold
        self.me = f"{self.cfg.user}/{wanted}"
        await self.ws.rename(wanted)
        log.info("renamed %s to %s", was, self.me)
        return {
            "me": self.me,
            "was": was,
            "remembered": True,
            "note": (
                "Messages already sent to the old address wait for a session of that name; they "
                "are not forwarded here."
            ),
        }

    def tool_pending(self) -> dict[str, Any]:
        return {
            "incoming_unanswered": self.store.pending_incoming_asks(self.me),
            "outgoing_waiting": self.store.pending_outgoing_asks(self.me),
        }

    async def deadline_watch(self) -> None:
        while True:
            await asyncio.sleep(30)
            try:
                for m in self.store.pending_outgoing_asks(self.me):
                    dl = m.get("deadline")
                    if dl and parse_iso(dl) < parse_iso(now_iso()):
                        self.store.set_state(m["id"], "timeout")
                        await self.inject(
                            f"Your ask {m['id']} to {m['to']} got no answer before its deadline {dl}.",
                            {
                                "kind": "timeout",
                                "msg_id": m["id"],
                                "thread": m.get("thread") or m["id"],
                                "trust": "system",
                            },
                        )
                for m in self.store.pending_incoming_asks(self.me):
                    dl = m.get("deadline")
                    if dl and parse_iso(dl) < parse_iso(now_iso()):
                        self.store.set_state(m["id"], "expired")
            except Exception:
                log.exception("deadline watch failed")


def _result(payload: Any, error: bool = False) -> types.CallToolResult:
    text = (
        payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False, indent=1)
    )
    return types.CallToolResult(content=[types.TextContent(type="text", text=text)], is_error=error)


def _report_if_it_died(task: asyncio.Task[Any]) -> None:
    """The relay task is fire-and-forget, so its last words would otherwise go nowhere."""
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        log.error("relay task stopped for good: %s: %s", type(exc).__name__, exc)
    else:
        log.warning("relay task returned; this session is offline until it restarts")


async def amain() -> None:
    _setup_logging()
    cfg = ClientConfig.load()
    key = load_identity()
    info = resolve_session(cfg.session_name or None)
    store = Store()
    session_name = info.name or "session"

    # An unlinked machine still serves the tools: it hands out a code instead of dying, and turns
    # into the real channel in this same process the moment a person approves it on the website.
    boot = Bootstrap(store, session_name, ChannelServer, yield_address=info.may_yield)
    if cfg.configured and key is not None:
        boot.channel = ChannelServer(cfg, key, store, session_name, yield_address=info.may_yield)
    log.info(
        "starting channel for %s (claude session %s via %s)",
        boot.channel.me if boot.channel else f"an unlinked machine, session {session_name}",
        info.session_id,
        info.source,
    )

    @asynccontextmanager
    async def lifespan(_server: Server[Any]):
        if boot.channel is not None:
            await boot.channel.start()
        setup_task = asyncio.create_task(boot.run()) if boot.channel is None else None
        try:
            yield boot
        finally:
            if setup_task is not None:
                setup_task.cancel()
            if boot.channel is not None:
                await boot.channel.stop()
            store.close()

    async def on_initialized(ctx: Any, _params: Any) -> None:
        boot.capture(ctx)
        _note_host(ctx)

    async def on_list_tools(ctx: Any, _params: Any) -> types.ListToolsResult:
        boot.capture(ctx)
        return types.ListToolsResult(tools=TOOLS)

    async def on_call_tool(ctx: Any, params: types.CallToolRequestParams) -> types.CallToolResult:
        boot.capture(ctx)
        a = params.arguments or {}
        channel = boot.channel  # None until somebody links this machine
        try:
            if channel is None:
                return _result(await boot.tool(params.name, a))
            match params.name:
                case "aim_status":
                    return _result(channel.tool_status())
                case "aim_contacts":
                    return _result(await channel.tool_contacts())
                case "aim_ask":
                    return _result(await channel.tool_ask(a))
                case "aim_answer":
                    return _result(await channel.tool_answer(a))
                case "aim_notify":
                    return _result(await channel.tool_notify(a))
                case "aim_wait":
                    return _result(await channel.tool_wait(a))
                case "aim_thread":
                    return _result({"thread": a["thread"], "messages": store.thread(a["thread"])})
                case "aim_pending":
                    return _result(channel.tool_pending())
                case "aim_receive":
                    return _result(await channel.tool_receive(a))
                case "aim_rename":
                    return _result(await channel.tool_rename(a))
                case "aim_link":
                    return _result({"linked": True, "me": channel.me, "message": "already linked"})
                case _:
                    return _result(f"unknown tool {params.name}", error=True)
        except (
            RelayWSError,
            RelayHTTPError,
            AddressError,
            ValidationError,
            ValueError,
            KeyError,
        ) as exc:
            log.warning("tool %s failed: %s", params.name, exc)
            return _result(f"{params.name} failed: {exc}", error=True)

    server: Server[Any] = Server(
        "aim",
        version=CLIENT_VERSION,
        instructions=INSTRUCTIONS,
        lifespan=lifespan,
        on_list_tools=on_list_tools,
        on_call_tool=on_call_tool,
    )
    server.add_notification_handler(
        "notifications/initialized", types.NotificationParams, on_initialized
    )
    options = server.create_initialization_options(experimental_capabilities={"claude/channel": {}})
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, options)
    log.info("channel stopped")


def _note_host(ctx: Any) -> None:
    """Write down which tool started us and what it says it can do.

    Push into a running conversation is not part of MCP: Claude Code does it with a capability
    of its own (`claude/channel`), and nothing else answers to it. The standard does have two
    server-initiated calls that could carry a message in - sampling and elicitation - so what a
    host declares here decides whether anything but polling is possible there. Reading it off a
    real handshake beats guessing from a binary.
    """
    session = getattr(ctx, "session", None)
    params = getattr(session, "client_params", None)
    if params is None:
        return
    info = getattr(params, "client_info", None)  # the SDK uses the snake_case field name
    caps = getattr(params, "capabilities", None)
    log.info(
        "host %s %s, capabilities: sampling=%s elicitation=%s roots=%s experimental=%s",
        getattr(info, "name", "?"),
        getattr(info, "version", "?"),
        getattr(caps, "sampling", None) is not None,
        getattr(caps, "elicitation", None) is not None,
        getattr(caps, "roots", None) is not None,
        getattr(caps, "experimental", None) or {},
    )


def main() -> None:
    anyio.run(amain, backend="asyncio")


if __name__ == "__main__":
    main()
