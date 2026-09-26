"""Relay clients: signed HTTP (stdlib urllib, sync) and WebSocket (websockets, async, auto-reconnect)."""

from __future__ import annotations

import asyncio
import json
import logging
import secrets
import urllib.error
import urllib.request
from collections.abc import Awaitable, Callable
from typing import Any

from nacl.signing import SigningKey
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed

from aimessenger.protocol import CLIENT_VERSION, MAX_FRAME_BYTES
from aimessenger.protocol.canonical import canonical_json
from aimessenger.protocol.crypto import public_key_b64, sign
from aimessenger.protocol.framing import decode_frame, encode_dict
from aimessenger.protocol.httpsig import sign_request, signed_path, ws_auth_payload
from aimessenger.protocol.models import Envelope, Grant, PairRequest

log = logging.getLogger("aim.client")


class RelayHTTPError(Exception):
    def __init__(self, code: str, message: str, status: int) -> None:
        super().__init__(f"{code}: {message}")
        self.code, self.message, self.status = code, message, status


class RelayHTTP:
    def __init__(
        self, base_url: str, handle: str, key: SigningKey | None, timeout: float = 20.0
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.handle = handle
        self.key = key
        self.timeout = timeout

    def _call(
        self, method: str, path: str, payload: Any = None, query: str = "", auth: bool = True
    ) -> dict[str, Any]:
        body = json.dumps(payload).encode("utf-8") if payload is not None else b""
        headers = {"content-type": "application/json", "user-agent": f"aim/{CLIENT_VERSION}"}
        if auth:
            if self.key is None:
                raise RelayHTTPError("no_identity", "identity key required", 0)
            headers.update(
                sign_request(self.key, self.handle, method, signed_path(path, query), body)
            )
        url = self.base_url + path + (f"?{query}" if query else "")
        req = urllib.request.Request(
            url, data=body if body else None, method=method, headers=headers
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return json.loads(resp.read().decode("utf-8") or "{}")
        except urllib.error.HTTPError as exc:
            try:
                err = json.loads(exc.read().decode("utf-8")).get("error", {})
            except ValueError:
                err = {}
            raise RelayHTTPError(
                err.get("code", "http_error"), err.get("message", str(exc)), exc.code
            ) from None
        except urllib.error.URLError as exc:
            raise RelayHTTPError("unreachable", f"relay unreachable: {exc.reason}", 0) from None

    # ---- public API -----------------------------------------------------------

    def health(self) -> dict[str, Any]:
        return self._call("GET", "/health", auth=False)

    def enroll(self, invite: str, key: SigningKey) -> dict[str, Any]:
        pub = public_key_b64(key)
        payload = canonical_json({"handle": self.handle, "invite": invite, "pubkey": pub})
        return self._call(
            "POST",
            "/v1/enroll",
            {"invite": invite, "handle": self.handle, "pubkey": pub, "sig": sign(key, payload)},
            auth=False,
        )

    def link_start(self, label: str) -> dict[str, Any]:
        return self._call("POST", "/v1/link/start", {"label": label}, auth=False)

    def link_poll(self, link_id: str, secret: str) -> dict[str, Any]:
        return self._call(
            "POST", "/v1/link/poll", {"link_id": link_id, "secret": secret}, auth=False
        )

    def user_info(self, handle: str) -> dict[str, Any]:
        return self._call("GET", f"/v1/users/{handle}")

    def send(self, env: Envelope) -> dict[str, Any]:
        return self._call("POST", "/v1/messages", env.to_wire())

    def inbox(self, session: str, limit: int = 100) -> list[dict[str, Any]]:
        return self._call("GET", "/v1/inbox", query=f"session={session}&limit={limit}")["messages"]

    def ack(self, message_id: str) -> bool:
        return bool(self._call("POST", f"/v1/messages/{message_id}/ack")["acked"])

    def grants(self) -> list[Grant]:
        return [Grant.model_validate(g) for g in self._call("GET", "/v1/grants")["grants"]]

    def put_grant(self, grant: Grant) -> str:
        return self._call("POST", "/v1/grants", grant.to_wire())["grant_id"]

    def revoke_grant(self, grant_id: str) -> None:
        self._call("DELETE", f"/v1/grants/{grant_id}")

    def contacts(self) -> list[dict[str, Any]]:
        return self._call("GET", "/v1/contacts")["contacts"]

    def pair_requests(self, status: str | None = "pending") -> list[dict[str, Any]]:
        return self._call("GET", "/v1/pair/requests", query=f"status={status or ''}")["requests"]

    def pair_request(self, pr: PairRequest) -> dict[str, Any]:
        return self._call("POST", "/v1/pair/requests", pr.to_wire())

    def pair_decide(self, request_id: str, approve: bool, grant: Grant | None) -> dict[str, Any]:
        return self._call(
            "POST",
            f"/v1/pair/requests/{request_id}/decide",
            {"approve": approve, "grant": grant.to_wire() if grant else None},
        )


class RelayWSError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code, self.message = code, message


PushHandler = Callable[[dict[str, Any]], Awaitable[None]]
StateHandler = Callable[[bool], Awaitable[None]]


class RelayWS:
    """Persistent authenticated WebSocket to the relay for one endpoint (handle@host/session)."""

    def __init__(
        self,
        ws_url: str,
        handle: str,
        session: str,
        key: SigningKey,
        on_push: PushHandler,
        on_state: StateHandler | None = None,
        endpoint_id: str | None = None,
        label: str = "",
        yield_address: bool = False,
    ) -> None:
        self.ws_url, self.handle, self.session, self.key = ws_url, handle, session, key
        self.label = label
        # a borrowed name: let a live holder keep the address and take the next free one instead
        self.yield_address = yield_address
        self.on_push, self.on_state = on_push, on_state
        self.endpoint_id = endpoint_id or secrets.token_hex(8)
        self._ws = None
        self._pending: dict[str, asyncio.Future[dict[str, Any]]] = {}
        self.connected = asyncio.Event()
        self.endpoint: str | None = None
        self.last_error: str | None = None
        self._stopping = False

    async def run(self) -> None:
        """Keep a connection up until stop(), whatever happens to the one we have.

        The reconnecting iterator below handles the ordinary cases, but it can also give up and
        return (or raise) - after a relay restart it went quiet for good, and because nothing awaits
        this task the session then looked alive while being permanently offline. So it gets its own
        outer loop: only a fatal answer from the relay, or stop(), ends this.
        """
        while not self._stopping:
            try:
                await self._connect_loop()
            except RelayWSError:
                raise  # the relay refuses this identity; retrying cannot fix it
            except Exception:
                log.exception("relay connection loop crashed, starting it again")
            if self._stopping:
                return
            log.warning("relay connection loop ended; reconnecting in 5s")
            self.last_error = self.last_error or "connection loop ended"
            await asyncio.sleep(5)

    async def _connect_loop(self) -> None:
        async for ws in connect(
            self.ws_url,
            ping_interval=30,
            ping_timeout=30,
            max_size=MAX_FRAME_BYTES,
            open_timeout=20,
        ):
            if self._stopping:
                break
            try:
                await self._session(ws)
            except ConnectionClosed as exc:
                log.warning("relay connection closed: %s", exc)
                self.last_error = f"connection closed: {exc}"
            except RelayWSError as exc:
                log.error("relay refused us: %s", exc)
                self.last_error = str(exc)
                if exc.code in ("unknown_user", "bad_signature", "client_too_old", "replaced"):
                    raise
            except Exception as exc:
                log.exception("relay session failed")
                self.last_error = f"{type(exc).__name__}: {exc}"
            finally:
                self._ws = None
                self.connected.clear()
                for fut in self._pending.values():
                    if not fut.done():
                        fut.set_exception(RelayWSError("disconnected", "connection lost"))
                self._pending.clear()
                if self.on_state:
                    await self.on_state(False)
            if self._stopping:
                break
            await asyncio.sleep(1)

    async def rename(self, session: str) -> None:
        """Register under a different session name. The connection loop does the reconnecting.

        A new endpoint id goes with it: this is a different endpoint, not the old one wearing a
        new label, and the relay's presence list should say so.
        """
        self.session, self.endpoint_id = session, secrets.token_hex(8)
        if self._ws is not None:
            await self._ws.close()  # _connect_loop says hello again, with the new name

    async def stop(self) -> None:
        self._stopping = True
        if self._ws is not None:
            await self._ws.close()

    async def _session(self, ws) -> None:
        await ws.send(
            encode_dict(
                {
                    "type": "hello",
                    "handle": self.handle,
                    "session": self.session,
                    "endpoint_id": self.endpoint_id,
                    "client_version": CLIENT_VERSION,
                    "label": self.label,
                    **({"yield": True} if self.yield_address else {}),
                }
            )
        )
        challenge = decode_frame(await ws.recv())
        if challenge.get("type") != "challenge":
            raise RelayWSError(challenge.get("error", {}).get("code", "protocol"), str(challenge))
        await ws.send(
            encode_dict(
                {
                    "type": "auth",
                    "sig": sign(
                        self.key, ws_auth_payload(self.handle, self.session, challenge["nonce"])
                    ),
                }
            )
        )
        auth = decode_frame(await ws.recv())
        if auth.get("status") != "ok":
            err = auth.get("error", {})
            raise RelayWSError(
                err.get("code", "auth_failed"), err.get("message", "authentication failed")
            )
        self.endpoint = auth["data"]["endpoint"]
        # The relay may have given a borrowed name the next free suffix. Reconnect under it from
        # now on, or every reconnect would ask for the taken name and get yet another suffix.
        self.session = self.endpoint.rsplit("/", 1)[-1]
        self._ws = ws
        self.connected.set()
        self.last_error = None
        log.info("connected to relay as %s", self.endpoint)
        # Pushes and state changes are handled by a separate consumer so handlers may issue
        # requests (ack, grants_list, ...) whose responses arrive on this very receive loop.
        queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
        consumer = asyncio.create_task(self._consume(queue))
        try:
            if self.on_state:
                await queue.put(None)  # None = "connected" state event
            async for raw in ws:
                frame = decode_frame(raw)
                rid = frame.get("request_id")
                if rid and rid in self._pending:
                    fut = self._pending.pop(rid)
                    if not fut.done():
                        fut.set_result(frame)
                    continue
                if frame.get("type") == "replaced":
                    # Another process took this address. Reconnecting would only take it back and
                    # start a tug of war, so this one stops and says so.
                    raise RelayWSError(
                        "replaced",
                        f"{frame.get('endpoint')} was taken over by another AIM client "
                        f"({frame.get('by')}); this connection is giving it up",
                    )
                await queue.put(frame)
        finally:
            consumer.cancel()

    async def _consume(self, queue: asyncio.Queue[dict[str, Any] | None]) -> None:
        while True:
            frame = await queue.get()
            try:
                if frame is None:
                    if self.on_state:
                        await self.on_state(True)
                else:
                    await self.on_push(frame)
            except Exception:
                log.exception("push handler failed for %s", frame and frame.get("type"))

    async def request(
        self, frame_type: str, timeout: float = 20.0, **fields: Any
    ) -> dict[str, Any]:
        if self._ws is None:
            raise RelayWSError("disconnected", "not connected to relay")
        rid = secrets.token_hex(6)
        fut: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        self._pending[rid] = fut
        await self._ws.send(encode_dict({"type": frame_type, "request_id": rid, **fields}))
        try:
            resp = await asyncio.wait_for(fut, timeout)
        except TimeoutError:
            self._pending.pop(rid, None)
            raise RelayWSError("timeout", f"{frame_type} timed out") from None
        if resp.get("status") != "ok":
            err = resp.get("error", {})
            raise RelayWSError(err.get("code", "error"), err.get("message", "request failed"))
        return resp.get("data", {})

    # convenience wrappers
    async def send_envelope(self, env: Envelope) -> dict[str, Any]:
        return await self.request("send", envelope=env.to_wire())

    async def ack(self, message_id: str) -> None:
        await self.request("acked", id=message_id)

    async def contacts(self) -> list[dict[str, Any]]:
        return (await self.request("contacts"))["contacts"]

    async def grants(self) -> list[Grant]:
        return [Grant.model_validate(g) for g in (await self.request("grants_list"))["grants"]]

    async def pair_requests(self, status: str | None = "pending") -> list[dict[str, Any]]:
        return (await self.request("pair_list", status=status))["requests"]
