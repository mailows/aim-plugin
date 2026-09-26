"""Local SQLite store shared by all sessions on this machine: messages, grants, pair requests, peers."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from aimessenger.protocol.models import Envelope, Grant
from aimessenger.protocol.timeutil import now_iso

from .config import aim_home

SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
    id          TEXT PRIMARY KEY,
    thread      TEXT,
    kind        TEXT NOT NULL,
    direction   TEXT NOT NULL,           -- in | out
    from_ep     TEXT NOT NULL,
    to_ep       TEXT NOT NULL,
    reply_to    TEXT,
    deadline    TEXT,
    json        TEXT NOT NULL,
    state       TEXT NOT NULL,           -- out: sent|queued|delivered|answered|timeout ; in: received|injected|answered
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS messages_thread ON messages (thread, created_at);
CREATE INDEX IF NOT EXISTS messages_state ON messages (direction, kind, state);
CREATE TABLE IF NOT EXISTS grants (
    grant_id    TEXT PRIMARY KEY,
    grantor     TEXT NOT NULL,
    grantee     TEXT NOT NULL,
    json        TEXT NOT NULL,
    revoked_at  TEXT
);
CREATE TABLE IF NOT EXISTS pair_requests (
    id          TEXT PRIMARY KEY,
    requester   TEXT NOT NULL,
    target      TEXT NOT NULL,
    json        TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'pending',
    fingerprint TEXT,
    updated_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS aliases (
    alias       TEXT PRIMARY KEY,
    endpoint    TEXT NOT NULL,
    created_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS known_endpoints (
    endpoint    TEXT PRIMARY KEY,
    peer        TEXT NOT NULL,
    session     TEXT NOT NULL,
    label       TEXT NOT NULL DEFAULT '',
    direction   TEXT NOT NULL DEFAULT 'outbound',
    online      INTEGER NOT NULL DEFAULT 0,
    last_seen   TEXT,
    updated_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS known_endpoints_session ON known_endpoints (session);
CREATE TABLE IF NOT EXISTS peers (
    user        TEXT PRIMARY KEY,
    pubkey      TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    verified    INTEGER NOT NULL DEFAULT 0,
    first_seen  TEXT NOT NULL
);
"""


class Store:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or (aim_home() / "store.sqlite")
        self.conn = sqlite3.connect(self.path, timeout=10, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA busy_timeout=5000")
        self.conn.executescript(SCHEMA)
        self._migrate()
        self.conn.commit()

    def _migrate(self) -> None:
        """Add `collected_at`: when the model actually took a message in.

        Until now "arrived" and "the model has it" were one fact, kept in memory. A pull-only
        host - Codex, Grok, anything without Claude Code's channels - lost every message that
        arrived before a restart, because the relay had been acked and the in-memory list was gone.

        Existing incoming rows are marked collected on the way in. Nothing ever recorded that they
        were read, and treating the whole history as unread would pour it into the first
        `aim_receive` after the upgrade.
        """
        have = {r["name"] for r in self.conn.execute("PRAGMA table_info(messages)")}
        if "collected_at" in have:
            return
        self.conn.execute("ALTER TABLE messages ADD COLUMN collected_at TEXT")
        self.conn.execute("UPDATE messages SET collected_at = updated_at WHERE direction = 'in'")
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS messages_uncollected ON messages (to_ep, collected_at)"
        )

    def close(self) -> None:
        self.conn.close()

    # ---- messages -----------------------------------------------------------

    def save_message(self, env: Envelope, direction: str, state: str) -> None:
        now = now_iso()
        self.conn.execute(
            "INSERT OR REPLACE INTO messages (id, thread, kind, direction, from_ep, to_ep, reply_to, deadline,"
            " json, state, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,"
            " COALESCE((SELECT created_at FROM messages WHERE id = ?), ?), ?)",
            (
                env.id,
                env.thread,
                env.kind,
                direction,
                env.from_,
                env.to,
                env.reply_to,
                env.deadline,
                json.dumps(env.to_wire()),
                state,
                env.id,
                now,
                now,
            ),
        )
        self.conn.commit()

    def set_state(self, message_id: str, state: str) -> None:
        self.conn.execute(
            "UPDATE messages SET state = ?, updated_at = ? WHERE id = ?",
            (state, now_iso(), message_id),
        )
        self.conn.commit()

    def note_delivery(self, message_id: str) -> None:
        """A receipt says our ask reached the peer. It must not undo a later fact.

        The receipt and the answer race: the peer's channel sends the receipt the moment it injects
        the question, the answer follows when the model gets to it, and on a slow link they can
        arrive in either order. Writing "delivered" over "answered" made the question pending
        again, and half a minute later the deadline watcher announced a timeout for a question that
        had been answered all along.
        """
        self.conn.execute(
            "UPDATE messages SET state = 'delivered', updated_at = ?"
            " WHERE id = ? AND state NOT IN ('answered', 'timeout', 'expired')",
            (now_iso(), message_id),
        )
        self.conn.commit()

    def uncollected(self, my_endpoint: str, limit: int = 200) -> list[tuple[Envelope, str]]:
        """Messages that reached this endpoint and that its model has not taken in yet.

        The store is the queue, so this survives a restart. A receipt that only confirms
        delivery, not that a model saw anything, is not worth waking anyone for and is skipped.
        """
        rows = self.conn.execute(
            "SELECT json, state FROM messages WHERE direction = 'in' AND to_ep = ?"
            " AND collected_at IS NULL AND state != 'expired' ORDER BY created_at LIMIT ?",
            (my_endpoint, limit),
        ).fetchall()
        out: list[tuple[Envelope, str]] = []
        silent: list[str] = []
        for r in rows:
            env = Envelope.model_validate_json(r["json"])
            if env.kind == "ack" and (env.body.code or "") != "injected":
                silent.append(env.id)  # settled here, or they pile up in front of real mail
                continue
            out.append((env, r["state"]))
        self.mark_collected(silent)
        return out

    def mark_collected(self, message_ids: list[str]) -> None:
        if not message_ids:
            return
        now = now_iso()
        self.conn.executemany(
            "UPDATE messages SET collected_at = ?, updated_at = ? WHERE id = ?",
            [(now, now, mid) for mid in message_ids],
        )
        self.conn.commit()

    def get_message(self, message_id: str) -> Envelope | None:
        row = self.conn.execute("SELECT json FROM messages WHERE id = ?", (message_id,)).fetchone()
        return Envelope.model_validate_json(row["json"]) if row else None

    def message_state(self, message_id: str) -> tuple[str, str] | None:
        row = self.conn.execute(
            "SELECT direction, state FROM messages WHERE id = ?", (message_id,)
        ).fetchone()
        return (row["direction"], row["state"]) if row else None

    def has_message(self, message_id: str) -> bool:
        return (
            self.conn.execute("SELECT 1 FROM messages WHERE id = ?", (message_id,)).fetchone()
            is not None
        )

    def thread(self, thread_id: str) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT direction, state, json FROM messages WHERE thread = ? ORDER BY created_at",
            (thread_id,),
        ).fetchall()
        return [
            {"direction": r["direction"], "state": r["state"], **json.loads(r["json"])}
            for r in rows
        ]

    def pending_incoming_asks(self, my_endpoint: str | None = None) -> list[dict[str, Any]]:
        # an expired question cannot be answered any more, so it is not pending either
        sql = "SELECT direction, state, json FROM messages WHERE direction = 'in' AND kind = 'ask' AND state NOT IN ('answered', 'expired')"
        args: list[Any] = []
        if my_endpoint:
            sql += " AND to_ep = ?"
            args.append(my_endpoint)
        rows = self.conn.execute(sql + " ORDER BY created_at", args).fetchall()
        return [
            {"direction": r["direction"], "state": r["state"], **json.loads(r["json"])}
            for r in rows
        ]

    def pending_outgoing_asks(self, my_endpoint: str | None = None) -> list[dict[str, Any]]:
        sql = "SELECT direction, state, json FROM messages WHERE direction = 'out' AND kind = 'ask' AND state NOT IN ('answered','timeout')"
        args: list[Any] = []
        if my_endpoint:
            sql += " AND from_ep = ?"
            args.append(my_endpoint)
        rows = self.conn.execute(sql + " ORDER BY created_at", args).fetchall()
        return [
            {"direction": r["direction"], "state": r["state"], **json.loads(r["json"])}
            for r in rows
        ]

    def answers_for(self, ask_id: str) -> list[Envelope]:
        rows = self.conn.execute(
            "SELECT json FROM messages WHERE reply_to = ? AND kind IN ('answer','error') ORDER BY created_at",
            (ask_id,),
        ).fetchall()
        return [Envelope.model_validate_json(r["json"]) for r in rows]

    def recent(self, limit: int = 20, endpoint: str | None = None) -> list[dict[str, Any]]:
        sql = "SELECT direction, state, json FROM messages"
        args: list[Any] = []
        if endpoint:
            sql += " WHERE from_ep = ? OR to_ep = ?"
            args += [endpoint, endpoint]
        rows = self.conn.execute(
            sql + " ORDER BY created_at DESC LIMIT ?", [*args, limit]
        ).fetchall()
        return [
            {"direction": r["direction"], "state": r["state"], **json.loads(r["json"])}
            for r in rows
        ]

    # ---- grants -------------------------------------------------------------

    def save_grant(self, grant: Grant) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO grants (grant_id, grantor, grantee, json, revoked_at) VALUES (?,?,?,?,NULL)",
            (grant.grant_id, grant.grantor, grant.grantee, json.dumps(grant.to_wire())),
        )
        self.conn.commit()

    def revoke_grant(self, grant_id: str) -> None:
        self.conn.execute(
            "UPDATE grants SET revoked_at = ? WHERE grant_id = ?", (now_iso(), grant_id)
        )
        self.conn.commit()

    def replace_grants(self, grants: list[Grant]) -> None:
        """Mirror the relay's view: everything not in `grants` is marked revoked."""
        ids = {g.grant_id for g in grants}
        for g in grants:
            self.save_grant(g)
        rows = self.conn.execute("SELECT grant_id FROM grants WHERE revoked_at IS NULL").fetchall()
        for r in rows:
            if r["grant_id"] not in ids:
                self.revoke_grant(r["grant_id"])

    def active_grants(self) -> list[Grant]:
        rows = self.conn.execute("SELECT json FROM grants WHERE revoked_at IS NULL").fetchall()
        return [Grant.model_validate_json(r["json"]) for r in rows]

    # ---- pair requests -------------------------------------------------------

    def save_pair_request(
        self, data: dict[str, Any], status: str = "pending", fp: str | None = None
    ) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO pair_requests (id, requester, target, json, status, fingerprint, updated_at)"
            " VALUES (?,?,?,?,?,?,?)",
            (
                data["id"],
                data["requester"],
                data["target"],
                json.dumps(data),
                status,
                fp,
                now_iso(),
            ),
        )
        self.conn.commit()

    def set_pair_status(self, request_id: str, status: str) -> None:
        self.conn.execute(
            "UPDATE pair_requests SET status = ?, updated_at = ? WHERE id = ?",
            (status, now_iso(), request_id),
        )
        self.conn.commit()

    def pair_requests(self, status: str | None = "pending") -> list[dict[str, Any]]:
        sql, args = "SELECT json, status, fingerprint FROM pair_requests", []
        if status:
            sql, args = sql + " WHERE status = ?", [status]
        rows = self.conn.execute(sql + " ORDER BY updated_at", args).fetchall()
        return [
            {**json.loads(r["json"]), "status": r["status"], "fingerprint": r["fingerprint"]}
            for r in rows
        ]

    # ---- peers ------------------------------------------------------------------

    def get_peer_pubkey(self, user: str) -> str | None:
        row = self.conn.execute("SELECT pubkey FROM peers WHERE user = ?", (user,)).fetchone()
        return row["pubkey"] if row else None

    def save_peer(self, user: str, pubkey: str, fp: str) -> bool:
        """Trust-on-first-use. Returns False when a *different* key was already pinned."""
        row = self.conn.execute("SELECT pubkey FROM peers WHERE user = ?", (user,)).fetchone()
        if row and row["pubkey"] != pubkey:
            return False
        if not row:
            self.conn.execute(
                "INSERT INTO peers (user, pubkey, fingerprint, first_seen) VALUES (?,?,?,?)",
                (user, pubkey, fp, now_iso()),
            )
            self.conn.commit()
        return True

    def mark_verified(self, user: str) -> None:
        self.conn.execute("UPDATE peers SET verified = 1 WHERE user = ?", (user,))
        self.conn.commit()

    def replace_peer(self, user: str, pubkey: str, fp: str) -> str | None:
        """Pin a new key for a peer whose key changed. Returns the fingerprint it replaced.

        The only way out once a peer re-keys: trust on first use refuses the new key by design,
        and there was nothing to accept it with. The new pin starts unverified again.
        """
        row = self.conn.execute("SELECT fingerprint FROM peers WHERE user = ?", (user,)).fetchone()
        self.conn.execute(
            "INSERT INTO peers (user, pubkey, fingerprint, verified, first_seen) VALUES (?,?,?,0,?)"
            " ON CONFLICT(user) DO UPDATE SET pubkey = excluded.pubkey,"
            " fingerprint = excluded.fingerprint, verified = 0, first_seen = excluded.first_seen",
            (user, pubkey, fp, now_iso()),
        )
        self.conn.commit()
        return row["fingerprint"] if row else None

    def peers(self) -> list[dict[str, Any]]:
        return [dict(r) for r in self.conn.execute("SELECT * FROM peers ORDER BY user").fetchall()]

    # ---- aliases and the endpoint directory --------------------------------------

    def set_alias(self, alias: str, endpoint: str) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO aliases (alias, endpoint, created_at) VALUES (?,?,?)",
            (alias.lower(), endpoint, now_iso()),
        )
        self.conn.commit()

    def remove_alias(self, alias: str) -> bool:
        cur = self.conn.execute("DELETE FROM aliases WHERE alias = ?", (alias.lower(),))
        self.conn.commit()
        return cur.rowcount > 0

    def aliases(self) -> dict[str, str]:
        rows = self.conn.execute("SELECT alias, endpoint FROM aliases ORDER BY alias").fetchall()
        return {r["alias"]: r["endpoint"] for r in rows}

    def alias_of(self, endpoint: str) -> str | None:
        row = self.conn.execute(
            "SELECT alias FROM aliases WHERE endpoint = ? ORDER BY created_at LIMIT 1",
            (endpoint,),
        ).fetchone()
        return row["alias"] if row else None

    def save_endpoints(self, rows: list[dict[str, Any]]) -> None:
        """Mirror the directory the relay reports for the peers we may talk to."""
        now = now_iso()
        for r in rows:
            self.conn.execute(
                "INSERT INTO known_endpoints (endpoint, peer, session, label, direction,"
                " online, last_seen, updated_at) VALUES (?,?,?,?,?,?,?,?)"
                " ON CONFLICT(endpoint) DO UPDATE SET label = excluded.label,"
                " direction = excluded.direction, online = excluded.online,"
                " last_seen = excluded.last_seen, updated_at = excluded.updated_at",
                (
                    r["endpoint"],
                    r["peer"],
                    r["session"],
                    r.get("label") or "",
                    r.get("direction") or "outbound",
                    1 if r.get("online") else 0,
                    r.get("last_seen"),
                    now,
                ),
            )
        self.conn.commit()

    def known_endpoints(self, direction: str | None = None) -> list[dict[str, Any]]:
        sql = "SELECT * FROM known_endpoints"
        args: list[Any] = []
        if direction:
            sql += " WHERE direction = ?"
            args.append(direction)
        rows = self.conn.execute(sql + " ORDER BY peer, session", args).fetchall()
        return [dict(r) for r in rows]
