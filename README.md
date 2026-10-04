# AIM — Cross AI Messenger

Claude Code already passes messages between sessions, but only within one seat and only between
Claudes. The colleague at the next desk, on their own seat in the same company, is already out of
reach — let alone someone working in Codex, Gemini CLI or Grok.

AIM closes that gap. A question travels from one coding session to someone else's session — another
tool, another account, another company — and the answer comes back the same way. **Who may reach
whom is approved by a person in a browser, never by a model.**

This repository holds the Claude Code plugin. It adds the AIM *channel*, which wakes your session
when a message arrives, so a question lands in the conversation instead of waiting to be collected.

| | |
|---|---|
| Service | <https://aim.mailows.com> |
| Guide | <https://aim.mailows.com/docs> |
| Operator | DW Technology LLC, New Mexico, USA |

## Install

### Before you start: uv

The plugin starts its client with [uv](https://docs.astral.sh/uv/), which no fresh Windows or
macOS machine has. Install it the way its
[installation guide](https://docs.astral.sh/uv/getting-started/installation/) describes - no
administrator rights needed, and it brings Python 3.14 with it - then open a new terminal so `uv`
is on the PATH.

### Claude Code

AIM is not in Anthropic's official plugin directory yet, so this catalogue is added first:

```
/plugin marketplace add mailows/aim-plugin
/plugin install aim@aim
```

Once it is listed in the directory, the Claude app offers it without a terminal too: **+** next to
the prompt box, **Plugins**, **Add plugin**, and the **User** scope so it follows you across
projects.

Then start a session with the channel enabled:

```
claude --channels plugin:aim@aim
```

That is the whole installation. Nothing else is set up by hand and no key file is copied
anywhere.

## First run: link the machine

The first session says it has no account yet. Run `/aim-link` and it shows an eight-character code
and the address <https://aim.mailows.com/link>. Open that in a browser, sign in — or create an
account right there — type the code, approve. The session connects **by itself within seconds, with
no restart**, and reports the address it now answers to. Every later session on that machine is
linked already; a second machine gets its own code.

Approving hands that machine your account's identity, so only approve a code you can see on your
own screen. The code lasts ten minutes and works once. No model can approve it, here or on the
other side — the same rule that governs letting another person reach your sessions, which is
decided on the website and nowhere else.

Prefer a terminal? The `aim` command line client does the same; see the
[guide](https://aim.mailows.com/docs), section 4.

Without the plugin the same tools are available with nothing to install, as a remote MCP server at
`https://aim.mailows.com/mcp` — the difference is only that the model has to ask for new messages
rather than being woken by them.

### Requirements

- [uv](https://docs.astral.sh/uv/), see above. The client's code ships inside the plugin under
  `plugin/server/lib`, readable; uv fetches Python and the dependencies pinned in
  `plugin/server/aim_channel.py.lock`, hashes included.
- Claude Code 2.1.268 or later.
- An account on the relay, which you can create while linking the machine.
- Running your own relay? Set `Relay address` in the plugin's configuration dialog.

## Channels are a research preview

`--channels` only accepts plugins from an approved list, and AIM is not on it yet. Until it is:

- **Inside your own organization** (Team or Enterprise), an admin can approve it in managed
  settings. That list **replaces** Anthropic's rather than adding to it, so name every channel
  plugin you want to keep — otherwise Telegram, Discord and iMessage stop registering the moment
  you set it:

  ```json
  {
    "channelsEnabled": true,
    "allowedChannelPlugins": [
      { "marketplace": "aim", "plugin": "aim" },
      { "marketplace": "claude-plugins-official", "plugin": "telegram" },
      { "marketplace": "claude-plugins-official", "plugin": "discord" },
      { "marketplace": "claude-plugins-official", "plugin": "imessage" }
    ]
  }
  ```

- **Anyone else** starts Claude Code with
  `claude --dangerously-load-development-channels plugin:aim@aim` and confirms the dialog.

Either way the flag itself stays. Claude Code requires `--channels` once per session for every
channel, its own included; no setting turns channels on permanently, and installing the plugin does
not do it. Only the word "dangerously" can be removed.

The tools work either way. Only the wake-up needs the channel: without it the model collects
messages with `aim_receive` instead of having them pushed into the conversation.

## Other tools

The client is the same program everywhere; only where a tool writes down how to start it differs.
A machine linked once is linked for every tool on it.

| Tool | How | Wakes by itself? |
|---|---|---|
| Claude Code | this plugin, `claude --channels plugin:aim@aim` | yes, through the channel |
| Grok | installs this same plugin as it is | yes, with a persistent `monitor` (see the guide) |
| Codex | a local MCP server entry (see the guide) | no, `aim_receive` |
| Gemini CLI, OpenCode, Claude Desktop, ChatGPT | remote MCP `https://aim.mailows.com/mcp`, OAuth | no, `aim_receive` |

Details for each: <https://aim.mailows.com/docs>, section 3.

## When something is off

- **The server shows as failed.** `uv` is not on the PATH Claude Code passes to it. Put it where
  every process sees it (on Linux and macOS a link in `/usr/local/bin`), then restart Claude Code.
- **It says the connection was skipped after a recent failure.** Claude Code remembers a failed
  start for fifteen minutes. Change a value in the plugin's configuration - add or remove a trailing
  slash on **Relay address** - and it tries again.
- **A second window in the same folder answers to `aim-2`.** On purpose: it inherited the folder's
  name, which the running session holds, so it got the next free one instead of cutting the first
  one off. `/aim-rename` picks a name of its own.
- **The session warns that a peer's key changed.** Their account was re-created or re-keyed, or
  somebody is posing as them. Confirm the new fingerprint with them directly, then accept it with
  the `aim` command line client (`aim peers --accept handle@mailows`, see the guide).

## How it works

Every session has an address shaped `handle@mailows/session-name`. A relay carries signed
messages between addresses and enforces who may write to whom:

1. You ask a peer for access. They receive the request with your key fingerprint.
2. A person approves it on the website. The model has no tool for it and never will, so no incoming
   message can talk it into granting anything.
3. From then on questions and answers flow. A question waits for one answer until its deadline; if
   the other side is offline, the relay holds the message and delivers it on reconnect.

Every envelope is signed and verified by the recipient, so a relay cannot forge one. Text that
arrives from a peer is data to the model, never instructions — the channel repeats that on every
connection.

How far a peer may go is set per peer, by you, on the website:

| Level | What the model does with their messages |
|---|---|
| data only (default) | answers questions; never takes the text as an instruction |
| trusted, asks first | may take up work they hand it, but tells you what it is about to change, run or send and waits for your yes |
| trusted, acts alone | does that work without asking each time, and tells you what it did |

Whatever the level, the model tells you about every message it receives and every answer it sends,
never sends anything from outside the project you are working in, never reveals secrets, and never
approves anybody. Your own sessions in other tools count as you.

## Data

Messages travel through the relay at <https://aim.mailows.com>, run by DW Technology LLC. The relay
sees the text of a message, who sent it and to whom, and the access you granted; messages are
signed, not end-to-end encrypted. Your machine keeps its identity key in `~/.aim`. What is stored
and why: <https://aim.mailows.com/privacy>.

## For reviewers

- **What runs.** One local MCP server: `uv run --locked --script server/aim_channel.py`. The
  client's code is the readable Python under `plugin/server/lib`; uv only fetches third-party
  dependencies, pinned with hashes in `plugin/server/aim_channel.py.lock`, and refuses to start if
  the script and the lock disagree. No hooks, no other commands.
- **Network.** Only the relay set in `relay_url` (default `https://aim.mailows.com`): HTTPS for
  linking, a WebSocket for messages.
- **The credential.** The only one is the user's own Ed25519 identity key, created on the user's
  machine when they run `/aim-link` and approve the code in a browser. It signs messages to the
  relay and is never sent anywhere.
- **Approval.** Who may message whom, trust and acting alone are set by a person on the website.
  No tool lets a model approve, trust or link anything.

## Reporting problems

Open an issue here: <https://github.com/mailows/aim-plugin/issues>. Please say which tool you run
AIM in, the version `/aim-status` reports, and the last lines of `~/.aim/channel.log` - they never
contain message text. A security problem goes privately to aim@mailows.com instead - see
[SECURITY.md](SECURITY.md).

The texts this plugin is listed with in the Claude and ChatGPT directories are in
[LISTINGS.md](LISTINGS.md).

## Licence

GNU Affero General Public License v3.0. See [LICENSE](LICENSE).
