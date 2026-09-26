"""Resolve the AIM session name for the Claude Code session that spawned us.

Order: AIM_SESSION_NAME env / config -> a name the person set with aim_rename (remembered for
this session and for this directory) -> name in ~/.claude/sessions/<pid>.json matching
CLAUDE_CODE_SESSION_ID -> basename of the working directory.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

from aimessenger.protocol.address import normalize_session_name

from . import names


@dataclass(slots=True)
class ClaudeSessionInfo:
    session_id: str | None
    name: str | None
    cwd: str | None
    source: str

    @property
    def may_yield(self) -> bool:
        """Whether this name is only borrowed, so a live holder of the address keeps it.

        A name somebody chose for this very session - explicitly, with aim_rename, or in Claude
        Code itself - is this session's own, and a restart takes the address back as it should.
        One inherited from the folder, or made up from it, may belong to another session that is
        still running there: a fork, a second window, another tool. Taking over would cut it off.
        """
        return self.source in ("directory", "cwd")


def claude_sessions_dir() -> Path:
    return Path(os.environ.get("CLAUDE_CONFIG_DIR") or (Path.home() / ".claude")) / "sessions"


def lookup_registry(session_id: str) -> tuple[str | None, str | None]:
    for f in claude_sessions_dir().glob("*.json"):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except OSError, ValueError:
            continue
        if data.get("sessionId") == session_id:
            return data.get("name"), data.get("cwd")
    return None, None


def resolve_session(explicit: str | None = None, cwd: str | None = None) -> ClaudeSessionInfo:
    session_id = os.environ.get("CLAUDE_CODE_SESSION_ID")
    if explicit:
        return ClaudeSessionInfo(session_id, normalize_session_name(explicit), cwd, "explicit")
    reg_name, reg_cwd = lookup_registry(session_id) if session_id else (None, None)
    here = cwd or reg_cwd or os.getcwd()
    # A name the person gave this session with aim_rename outranks whatever Claude Code calls it:
    # the address is the thing peers wrote down, and it has to survive a restart unchanged.
    chosen, whose = names.remembered_where(session_id, here)
    if chosen:
        source = "remembered" if whose == "session" else "directory"
        return ClaudeSessionInfo(session_id, chosen, here, source)
    if reg_name:
        return ClaudeSessionInfo(session_id, normalize_session_name(reg_name), here, "registry")
    base = Path(here).name
    if not session_id:
        # Not Claude Code. The bare directory name is exactly what a Claude session opened in
        # the same place calls itself, and two clients on one address evict each other.
        # AIM_HOST names the tool, when whoever configured it said which one it is.
        base = f"{base}-{os.environ.get('AIM_HOST') or 'mcp'}"
    return ClaudeSessionInfo(session_id, normalize_session_name(base), here, "cwd")
