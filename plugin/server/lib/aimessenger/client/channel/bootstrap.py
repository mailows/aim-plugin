"""The channel before anybody linked this machine.

A plugin install gives a session the AIM tools and nothing else: no key, no handle, no relay. The
old client refused to start at all, which showed up in Claude Code as a failed MCP server and told
the person nothing. Instead the server starts, says what is missing, and gets a code that finishes
the job in a browser. When a person approves it, the real channel takes over in the same process,
so nothing has to be restarted.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable
from typing import Any

from aimessenger.protocol.timeutil import now_iso

from .. import linking, names
from ..claude_sessions import resolve_session
from ..config import ClientConfig, aim_home
from ..identity import load_identity
from ..relay_client import RelayHTTPError

log = logging.getLogger("aim.channel.setup")

POLL_S = 2.0
RETRY_S = 30.0


def _link_file():
    return aim_home() / "link.json"


def saved_link() -> dict[str, Any] | None:
    """A code already shown to this person, so every session on the machine shows the same one."""
    try:
        link = json.loads(_link_file().read_text(encoding="utf-8"))
    except OSError, ValueError:
        return None
    return link if str(link.get("expires_at", "")) > now_iso() else None


def save_link(link: dict[str, Any]) -> None:
    try:
        _link_file().write_text(json.dumps(link), encoding="utf-8")
    except OSError as exc:  # pragma: no cover - a cache that cannot be written is not fatal
        log.warning("could not remember the link request: %s", exc)


def forget_link() -> None:
    _link_file().unlink(missing_ok=True)


def setup_text(link: dict[str, Any] | None, error: str = "") -> str:
    if link is None:
        return (
            "AIM is installed but this machine is not linked to an account yet. Call aim_link to "
            "get a code." + (f" (last attempt: {error})" if error else "")
        )
    return (
        f"AIM is not linked to an account yet. Tell your user, in their language, to open "
        f"{link['verify_url']} , sign in there (or create an account), and type this code: "
        f"{link['code']}\nThe code is valid until {link['expires_at']}. Only a person can do this, "
        f"you cannot. This session connects by itself within seconds of their approval."
    )


class Bootstrap:
    """Serves the tools and runs the link flow until the real channel exists."""

    def __init__(
        self,
        store: Any,
        session_name: str,
        make_channel: Callable[..., Any],
        yield_address: bool = False,
    ) -> None:
        self.yield_address = yield_address
        self.store = store
        self.session_name = session_name
        self.make_channel = make_channel
        self.channel: Any = None
        self.mcp_session: Any = None
        self.pending_events: list[tuple[str, dict[str, str]]] = []
        self.link: dict[str, Any] | None = None
        self.error = ""
        self.announced = False
        self.starting = asyncio.Lock()

    # ---- the MCP side, mirrored from ChannelServer so one caller can talk to either ----

    def capture(self, ctx: Any) -> None:
        if self.channel is not None:
            self.channel.capture(ctx)
            return
        if self.mcp_session is None:
            self.mcp_session = ctx.session
            if self.pending_events:
                asyncio.get_running_loop().create_task(self._flush())

    async def _flush(self) -> None:
        events, self.pending_events = self.pending_events, []
        for content, meta in events:
            await self.inject(content, meta)

    async def inject(self, content: str, meta: dict[str, str]) -> None:
        if self.channel is not None:
            await self.channel.inject(content, meta)
            return
        if self.mcp_session is None:
            self.pending_events.append((content, meta))
            return
        from .server import CHANNEL_METHOD, RawNotification

        await self.mcp_session.send_notification(
            RawNotification(method=CHANNEL_METHOD, params={"content": content, "meta": meta})
        )

    # ---- tools while unlinked ----------------------------------------------------------

    def status(self) -> dict[str, Any]:
        link = self.link or {}
        return {
            "linked": False,
            "connected": False,
            "session": self.session_name,
            "relay": linking.default_relay_url(),
            "code": link.get("code"),
            "verify_url": link.get("verify_url"),
            "expires_at": link.get("expires_at"),
            "last_error": self.error,
            "next_step": setup_text(self.link, self.error),
        }

    async def tool(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        if name == "aim_link":
            await self.start_link()
            return self.status()
        if name == "aim_status":
            return self.status()
        if name == "aim_rename":
            # Worth allowing before there is an account: the name is what peers will write down,
            # and it costs nothing to have it ready for the moment this machine is linked.
            info = resolve_session()
            self.session_name = names.remember(str(args["name"]), info.session_id, info.cwd)
            self.yield_address = False  # the person's choice, this session's to hold
            return {"session": self.session_name, "remembered": True, **self.status()}
        return {
            "error": "not_linked",
            "message": setup_text(self.link, self.error),
            "code": (self.link or {}).get("code"),
            "verify_url": (self.link or {}).get("verify_url"),
        }

    # ---- linking -----------------------------------------------------------------------

    async def start_link(self) -> dict[str, Any] | None:
        """One pending code per machine. The lock matters: the poll loop and an aim_link call race
        at startup, and two codes mean the person approves the one nobody is watching."""
        async with self.starting:
            if self.link and str(self.link.get("expires_at", "")) > now_iso():
                return self.link
            cached = saved_link()
            if cached:
                self.link = cached
                return cached
            try:
                self.link = await asyncio.to_thread(
                    linking.begin, "", linking.machine_label(self.session_name)
                )
            except RelayHTTPError as exc:
                self.error = str(exc)
                log.warning("could not ask for a link code: %s", exc)
                return None
            self.error, self.announced = "", False
            save_link(self.link)
            log.info("link code %s waiting at %s", self.link["code"], self.link["verify_url"])
            return self.link

    async def run(self) -> None:
        """Poll until a person approves, then become the real channel. Runs as long as it takes."""
        while self.channel is None:
            try:
                await self._round()
            except Exception as exc:  # noqa: BLE001 - one bad round must not kill the server
                self.error = str(exc)
                log.warning("linking round failed: %s", exc)
                await asyncio.sleep(RETRY_S)

    async def _round(self) -> None:
        cfg = ClientConfig.load()
        key = load_identity()
        if cfg.configured and key is not None:  # `aim link` or another session got there first
            await self._go_live(cfg, key)
            return
        if self.link is None and await self.start_link() is None:
            await asyncio.sleep(RETRY_S)
            return
        await self._announce()
        try:
            result = await asyncio.to_thread(linking.check, self.link)
        except RelayHTTPError as exc:
            if exc.code in ("unknown_link", "link_used"):  # stale cache, or already claimed
                self._drop_link("")
                return
            raise
        status = str(result.get("status"))
        if status == "linked":
            cfg = await asyncio.to_thread(linking.adopt, result, self.link["relay_url"])
            self._drop_link("")
            await self._go_live(cfg, load_identity())
            return
        if status in ("denied", "expired"):
            self._drop_link(status)
            await self.inject(
                f"The AIM link request was {status}. Call aim_link for a new code when your user "
                f"wants to try again.",
                {"kind": "status", "trust": "system"},
            )
            await asyncio.sleep(RETRY_S)
            return
        await asyncio.sleep(float(result.get("interval") or POLL_S))

    def _drop_link(self, reason: str) -> None:
        forget_link()
        self.link, self.error, self.announced = None, reason, False

    async def _announce(self) -> None:
        if self.announced or self.link is None:
            return
        self.announced = True
        await self.inject(setup_text(self.link), {"kind": "setup", "trust": "system"})

    async def _go_live(self, cfg: ClientConfig, key: Any) -> None:
        channel = self.make_channel(
            cfg, key, self.store, self.session_name, yield_address=self.yield_address
        )
        if self.mcp_session is not None:
            channel.attach(self.mcp_session)
        self.channel = channel
        log.info("linked as %s - handing over to the channel", channel.me)
        await channel.start()
        await self.inject(
            f"AIM is linked. This session is {channel.me}. Tell your user they can be reached "
            f"at that address from other sessions and other accounts.",
            {"kind": "status", "trust": "system"},
        )
