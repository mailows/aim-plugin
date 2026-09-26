# Security

Please report a security problem privately to **aim@namailu.cz**, not in a public issue.

Say what you found, how to reproduce it, and which version you run (`client_version` from
`/aim-status`). We will reply as soon as we can and tell you what we are doing about it.

## What counts

Anything that lets somebody:

- read or send messages they have not been granted,
- approve access, or mark a peer as trusted, without the account owner doing it on the website,
- pass as another account, or as another session in a way a grant does not already allow,
- put text in front of a model so that it reads as trusted when it is not,
- link a machine to an account without its owner approving the code.

## Scope

- this plugin,
- the `aimessenger` client it runs,
- the relay at <https://aim.mailows.com>.

Someone running their own relay is responsible for that relay; a flaw in the code it runs is still
in scope here.
