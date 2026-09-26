"""WebSocket text frames: ``{"type": ..., "request_id": ..., ...}`` (pattern from kecacek/protocol.py).

Requests carry a ``request_id``; the matching response repeats it and adds ``status``
("ok" | "error") plus ``data``. Pushes from the relay carry no ``request_id``.
"""

from __future__ import annotations

import json
from typing import Any

from . import MAX_FRAME_BYTES


class FrameError(ValueError):
    """Malformed or oversized frame."""


def version_gte(version: str, minimum: str) -> bool:
    def _parse(v: str) -> tuple[int, ...] | None:
        try:
            return tuple(int(x) for x in v.split("."))
        except ValueError, AttributeError:
            return None

    a, b = _parse(version), _parse(minimum)
    return a is not None and b is not None and a >= b


def encode_frame(frame_type: str, request_id: str | None = None, **fields: Any) -> str:
    frame: dict[str, Any] = {"type": frame_type, **fields}
    if request_id:
        frame["request_id"] = request_id
    text = json.dumps(frame, ensure_ascii=False, separators=(",", ":"))
    if len(text.encode("utf-8")) > MAX_FRAME_BYTES:
        raise FrameError(f"frame exceeds {MAX_FRAME_BYTES} bytes")
    return text


def encode_response(
    frame_type: str,
    status: str,
    request_id: str | None = None,
    data: dict[str, Any] | None = None,
    **fields: Any,
) -> str:
    extra = {"data": data} if data is not None else {}
    return encode_frame(frame_type, request_id, status=status, **extra, **fields)


def decode_frame(text: str | bytes) -> dict[str, Any]:
    raw = text if isinstance(text, bytes) else text.encode("utf-8")
    if len(raw) > MAX_FRAME_BYTES:
        raise FrameError(f"frame exceeds {MAX_FRAME_BYTES} bytes")
    try:
        frame = json.loads(raw)
    except (ValueError, UnicodeDecodeError) as exc:
        raise FrameError(f"invalid frame: {exc}") from exc
    if not isinstance(frame, dict) or not isinstance(frame.get("type"), str):
        raise FrameError("frame must be an object with a string 'type'")
    return frame


def encode_dict(frame: dict[str, Any]) -> str:
    """Serialize a ready-made frame dict (must contain 'type')."""
    if not isinstance(frame.get("type"), str):
        raise FrameError("frame must contain a string 'type'")
    text = json.dumps(frame, ensure_ascii=False, separators=(",", ":"))
    if len(text.encode("utf-8")) > MAX_FRAME_BYTES:
        raise FrameError(f"frame exceeds {MAX_FRAME_BYTES} bytes")
    return text
