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

In the Claude app, without a terminal: click **+** next to the prompt box, choose **Plugins**, then
**Add plugin**, and pick AIM. Use the **User** scope so it follows you across projects.

In Claude Code:

```
/plugin marketplace add mailows/aim-plugin
/plugin install aim@aim
```

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

Prefer a terminal? `uvx --from aimessenger aim link` does the same.

Without the plugin the same tools are available with nothing to install, as a remote MCP server at
`https://aim.mailows.com/mcp` — the difference is only that the model has to ask for new messages
rather than being woken by them.

### Requirements

- [uv](https://docs.astral.sh/uv/) on your PATH. The plugin runs `uvx --from aimessenger aim
  channel`, and uv fetches Python and the package itself.
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

## Reporting problems

Issues are disabled here: this repository is a distribution artifact, not where the work happens.
Reach us through <https://aim.mailows.com>.

## Licence

GNU Affero General Public License v3.0. See [LICENSE](LICENSE).
