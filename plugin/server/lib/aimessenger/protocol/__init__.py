"""AIM protocol v1: addressing, canonical JSON, Ed25519 signatures, envelopes, grants, framing."""

PROTOCOL_VERSION = 1
CLIENT_VERSION = "0.1.10"
MIN_CLIENT_VERSION = "0.1.0"

# Size limits (bytes)
MAX_TEXT_BYTES = 64 * 1024
MAX_ATTACHMENTS_BYTES = 256 * 1024
MAX_ENVELOPE_BYTES = 512 * 1024
MAX_FRAME_BYTES = 1024 * 1024

# Replay protection window for envelope timestamps (seconds)
TS_SKEW_S = 300

# Default retention for messages waiting for an offline endpoint (seconds)
DEFAULT_TTL_S = 30 * 24 * 3600
