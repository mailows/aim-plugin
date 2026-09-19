---
description: Show this session's AIM address, its relay connection and who may reach it
---

Call `aim_status` and, unless it reports `linked: false`, `aim_contacts` as well. Report to the
user, briefly and in their language:

- the address of this session (`me`) and whether it is connected to the relay,
- who may write to it and which of their sessions are online right now,
- anything waiting in `aim_pending`.

When `linked` is false, do not list anything else: say what `next_step` says instead, and offer to
run `/aim-link`.
