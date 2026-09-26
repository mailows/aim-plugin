"""Linking this machine to an account from inside a session, with no key and no invite code.

The client asks the relay for a short code, a person types that code on the website while signed
in, and the relay then hands this machine the account's identity. Nobody copies a key file around
and nobody needs a terminal: installing the plugin is enough.
"""

from __future__ import annotations

import os
import socket
import time
from pathlib import Path
from typing import Any

from .config import ClientConfig, relay_url_for
from .identity import write_identity
from .relay_client import RelayHTTP, RelayHTTPError

PUBLIC_RELAY_URL = "https://aim.mailows.com"
LINK_POLL_S = 2.0
LINK_WAIT_S = 600.0


def default_relay_url() -> str:
    """Where to link, unless the plugin or the operator points somewhere else.

    The plugin passes this through Claude Code's plugin configuration. A client too old to know
    that syntax hands us the placeholder itself, which is not an address and must not be used.
    """
    configured = (os.environ.get("AIM_RELAY_URL") or "").strip()
    if not configured or "${" in configured:
        return PUBLIC_RELAY_URL
    return configured.rstrip("/")


def machine_label(session: str = "") -> str:
    """What the person approving sees. Enough to recognise their own machine, nothing more."""
    host = os.environ.get("COMPUTERNAME") or socket.gethostname() or "?"
    where = Path.cwd().name
    parts = [f"Claude on {host}"]
    if session:
        parts.append(f"session {session}")
    if where:
        parts.append(where)
    return " - ".join(parts)


def begin(relay_url: str = "", label: str = "") -> dict[str, Any]:
    """Ask for a code. Returns link_id, secret, code, verify_url, expires_at."""
    url = (relay_url or default_relay_url()).rstrip("/")
    link = RelayHTTP(url, "", None).link_start(label or machine_label())
    link["relay_url"] = url
    return link


def check(link: dict[str, Any]) -> dict[str, Any]:
    """One poll. status is pending, denied, expired or linked."""
    return RelayHTTP(link["relay_url"], "", None).link_poll(link["link_id"], link["secret"])


def adopt(result: dict[str, Any], relay_url: str) -> ClientConfig:
    """Write the identity and the configuration this machine will use from now on."""
    write_identity(result["seed"])
    cfg = ClientConfig.load()
    cfg.handle = result["handle"]
    cfg.relay_host = result.get("relay_host") or result["user"].split("@", 1)[1]
    cfg.relay_url = result.get("relay_url") or relay_url
    if not cfg.relay_url:
        cfg.relay_url = relay_url_for(cfg.relay_host)
    cfg.save()
    return cfg


def wait(link: dict[str, Any], deadline_s: float = LINK_WAIT_S) -> dict[str, Any]:
    """Block until a person decides, blowing up on anything that is not an approval."""
    until = time.monotonic() + deadline_s
    while time.monotonic() < until:
        result = check(link)
        if result["status"] == "linked":
            return result
        if result["status"] in ("denied", "expired"):
            raise RelayHTTPError(result["status"], f"the link request was {result['status']}", 403)
        time.sleep(float(result.get("interval") or LINK_POLL_S))
    raise RelayHTTPError("timeout", "nobody approved the link in time", 408)
