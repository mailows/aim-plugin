---
description: Link this machine to an AIM account (a person approves it in a browser)
---

Call the `aim_link` tool, then tell the user - in their own language - to:

1. open the `verify_url` from the result in a browser,
2. sign in to AIM there, or create an account if they have none,
3. type the `code` from the result.

Show the code on its own line so it is easy to read. Say plainly that you cannot approve it
yourself: only the person signed in to the website can. Once they approve, this session connects on
its own within a few seconds and reports the address it got. If they say it did not work, call
`aim_status` and read `last_error` back to them.
