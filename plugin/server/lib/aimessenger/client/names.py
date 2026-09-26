"""Session names a person chose, remembered across restarts.

A session's address is its name, so the name cannot live only in the running process: close Claude
Code, open it again, and a peer's messages would go to an address nobody answers to any more. The
name is therefore written down twice - against the Claude Code session that set it, and against the
directory it was working in - because a restart may keep the session id or may not, and the
directory is what survives either way.

A host that is not Claude Code has no session id, and used to fall back on the directory's name
- which a Claude session in the same directory had written. Both then claimed one address and the
relay, keeping one reader per address, hung up on whichever came first. Such hosts keep their own
directory memory (`by_cwd_other`), so a Codex or Grok opened next to Claude never inherits its name.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

from aimessenger.protocol.address import validate_session

from .config import aim_home

log = logging.getLogger("aim.names")

KEEP = 200  # a machine that opens hundreds of directories should not grow this forever


def names_path() -> Path:
    return aim_home() / "names.json"


def _key(cwd: str | None) -> str:
    return str(Path(cwd or os.getcwd()).resolve()).lower()


SECTIONS = ("by_session", "by_cwd", "by_cwd_other")


def _load() -> dict[str, dict[str, str]]:
    try:
        data = json.loads(names_path().read_text(encoding="utf-8"))
    except OSError, ValueError:
        return {section: {} for section in SECTIONS}
    return {section: dict(data.get(section) or {}) for section in SECTIONS}


def _cwd_section(session_id: str | None) -> str:
    """Claude sessions share one directory memory; every other host gets the other one."""
    return "by_cwd" if session_id else "by_cwd_other"


def remembered(session_id: str | None, cwd: str | None) -> str | None:
    """The name this session should answer to, or None to let the usual guesswork decide."""
    return remembered_where(session_id, cwd)[0]


def remembered_where(session_id: str | None, cwd: str | None) -> tuple[str | None, str]:
    """The remembered name and whose it is: "session" if this very session chose it, "directory"
    if it was only left behind in the folder by some session - maybe one still running."""
    data = _load()
    if session_id and (name := data["by_session"].get(session_id)):
        return name, "session"
    if name := data[_cwd_section(session_id)].get(_key(cwd)):
        return name, "directory"
    return None, ""


def remember_for_session(name: str, session_id: str | None) -> None:
    """Keep a name for this session only, leaving the directory's memory as it was.

    For the name the relay hands a second session in the same folder: it must survive this
    session's restart, and it must not become what the next session opened here is called.
    """
    if not session_id:
        return
    data = _load()
    data["by_session"][session_id] = validate_session(name)
    data["by_session"] = _trim(data["by_session"])
    _save(data)


def remember(name: str, session_id: str | None, cwd: str | None) -> str:
    """Write the name down for this session and for its directory. Returns the stored name.

    A name somebody typed is validated, not sanitised: turning "my session!" quietly into
    "my-session" hands them an address that is not the one they just read out loud.
    """
    name = validate_session(name.strip().lower())
    data = _load()
    if session_id:
        data["by_session"][session_id] = name
        data["by_session"] = _trim(data["by_session"])
    section = _cwd_section(session_id)
    data[section][_key(cwd)] = name
    data[section] = _trim(data[section])
    _save(data)
    return name


def forget(session_id: str | None, cwd: str | None) -> None:
    data = _load()
    if session_id:
        data["by_session"].pop(session_id, None)
    data[_cwd_section(session_id)].pop(_key(cwd), None)
    _save(data)


def _save(data: dict[str, Any]) -> None:
    try:
        names_path().write_text(json.dumps(data, indent=2), encoding="utf-8")
    except OSError as exc:  # pragma: no cover - a name we cannot write still holds this session
        log.warning("could not write remembered names: %s", exc)


def _trim(mapping: dict[str, str]) -> dict[str, str]:
    return dict(list(mapping.items())[-KEEP:])
