"""Client configuration: ~/.aim (override with AIM_HOME), config.json."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path

from aimessenger.protocol.address import AddressError


def aim_home() -> Path:
    home = Path(os.environ.get("AIM_HOME") or (Path.home() / ".aim"))
    home.mkdir(parents=True, exist_ok=True)
    return home


@dataclass(slots=True)
class ClientConfig:
    relay_host: str = ""  # address host part, e.g. aim.mailows.com
    relay_url: str = ""  # https://aim.mailows.com
    handle: str = ""
    session_name: str = ""  # optional fixed session name for `aim channel`
    ack_injected: bool = True  # send an `ack` envelope when an ask reached the model
    session_labels: dict[str, str] = field(default_factory=dict)  # session name -> short label
    extra: dict[str, str] = field(default_factory=dict)

    @property
    def user(self) -> str:
        return f"{self.handle}@{self.relay_host}"

    @property
    def ws_url(self) -> str:
        base = self.relay_url.rstrip("/")
        if base.startswith("https://"):
            return "wss://" + base[len("https://") :] + "/v1/ws"
        if base.startswith("http://"):
            return "ws://" + base[len("http://") :] + "/v1/ws"
        return base + "/v1/ws"

    def label_for(self, session: str) -> str:
        """Short description peers see next to this session's name."""
        return self.session_labels.get(session, "")

    @property
    def configured(self) -> bool:
        return bool(self.relay_host and self.relay_url and self.handle)

    @classmethod
    def path(cls) -> Path:
        return aim_home() / "config.json"

    @classmethod
    def load(cls) -> ClientConfig:
        p = cls.path()
        data = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
        cfg = cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})
        # also on a machine that has no config yet: the session name is how it will be addressed
        if env := os.environ.get("AIM_SESSION_NAME"):
            cfg.session_name = env
        return cfg

    def save(self) -> None:
        self.path().write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")


def relay_url_for(host: str) -> str:
    """Default API URL for a relay host: https unless it is localhost/127.* (dev).

    A host without a dot is a name, not an address -- `mailows` says who the relay is, not where
    it answers -- so the caller has to pass the URL instead of us inventing https://mailows.
    """
    bare = host.split(":")[0]
    if "." not in bare and bare not in ("localhost",):
        raise AddressError(f"relay {host!r} is a name, not a hostname: pass --url as well")
    scheme = "http" if bare in ("localhost", "127.0.0.1") else "https"
    return f"{scheme}://{host}"
