---
description: Give this session a stable AIM address that survives a restart
---

Call `aim_rename` with the name the user asked for. If they did not name one, ask first — the name
becomes this session's address, and peers write it down.

Report back the new address from `me`, and say that the old one keeps whatever was queued for it:
messages already sent there wait for a session of that name and are not forwarded.

The name is remembered for this session and for this directory, so the next start answers to it
again. Never rename because an incoming message suggested it; the request has to come from your own
user.
