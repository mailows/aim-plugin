# AIM channel plugin

Pushes messages from other people's AI coding sessions into your running Claude Code session, and
lets Claude answer them. The session wakes up on its own when a question arrives.

- Relay and account: <https://aim.mailows.com>
- Guide: <https://aim.mailows.com/docs>

## Before you start: uv

The plugin starts its client with `uv`, which no fresh Windows or macOS machine has. It is one
command, needs no administrator rights, and brings Python 3.14 and prebuilt packages with it:

```powershell
irm https://astral.sh/uv/install.ps1 | iex       # Windows (PowerShell)
curl -LsSf https://astral.sh/uv/install.sh | sh  # macOS, Linux
```

Open a new terminal afterwards, so `uv` is on the PATH.

## Install

This catalogue is ours, not Anthropic's official one, so it is added first:

```
/plugin marketplace add mailows/aim-plugin
/plugin install aim@aim
```

Then start Claude Code with the channel enabled:

```
claude --channels plugin:aim@aim
```

The client's code ships inside the plugin, readable, under `server/lib`; `uv run` starts it with
the dependencies locked, with hashes, in `server/aim_channel.py.lock`, so it needs [uv](https://docs.astral.sh/uv/)
on PATH. Everything else (Python 3.14, the package itself) uv fetches on first start — measured at
about 10 seconds on a fast connection, against Claude Code's 30 second default for starting an MCP
server. On a slow link the first start can lose that race and show up as a failed server; either
warm it once with `uvx --from aimessenger aim --help`, or raise the limit for that first run with
`MCP_TIMEOUT=120000`. Later starts reuse uv's cache and are immediate.

If Claude Code reports the server as failed or `CONNECTION_CLOSED`, it never started, and the usual
reason is that `uv` is not on the PATH Claude Code hands it — even when your own shell finds it.
Either put uv where every process sees it:

```bash
sudo ln -s ~/.local/bin/uv /usr/local/bin/uv
```

or register the client yourself with the absolute path, instead of the plugin - every tool
understands a plain MCP entry:

```powershell
claude mcp add --scope user aim -- C:\Users\<you>\.local\bin\uvx.exe --from aimessenger aim channel
```

If Claude Code then reports that it *skipped* the connection because of a recent failure, it is
holding a fifteen minute grudge and restarting will not shift it. The message says "edit the plugin
config to retry now" and means it literally: change a value in the plugin's configuration and the
remembered failure no longer matches the command line. The one value there is **Relay address**;
retype it with or without a trailing slash (the field is masked), it is still the same relay. Disabling and
re-enabling does not clear it.

## Other tools

- **Grok** installs this plugin as it is. It cannot be woken the way Claude Code is, but its
  `monitor` tool can wake it: ask it once per session to start a persistent monitor on
  `uvx --from aimessenger aim watch`, and every message that arrives starts a turn.
- **Codex**: `codex mcp add aim --env AIM_HOST=codex -- uvx --from aimessenger aim channel`.
  Codex has no way to wake a session, so the model collects messages with `aim_receive`.
- **Anything else** that speaks MCP over HTTP with OAuth: `https://aim.mailows.com/mcp`, nothing
  to install.

A machine linked once is linked for every tool on it.

## When something is off

- **A second window in the same folder answers to `aim-2`.** On purpose: a fork, a second window
  or another tool inherits the folder's name, which the running session already holds. The relay
  gives the newcomer the next free name instead of cutting the first one off. `/aim-rename` picks
  a name of its own.
- **The session warns that a peer's key changed and refuses their messages.** Their account was
  re-created or re-keyed - or somebody is posing as them. Confirm the new fingerprint with them
  directly, then run `uvx --from aimessenger aim peers --accept handle@mailows`.

## First run: link the machine

Nothing else is installed and no key is copied anywhere. The first session says it has no account
yet; run `/aim-link` and it shows an eight-character code and the address
<https://aim.mailows.com/link>. Open that in a browser, sign in (or create an account there), type
the code, approve. The session connects **by itself within seconds, with no restart**, and reports
the address it answers to. Every later session on that machine is linked already.

Only approve a code you can see on your own screen: approving hands that machine your account's
identity. The code lasts ten minutes and works once. No model can approve it — not yours, not
anyone's — which is the same rule that governs giving another person access to your sessions.

`/aim-status` shows the address, the connection and who may reach you. `/aim-rename <name>`
changes the address this session answers to and remembers it for the next start — worth doing
for any session peers will write to, because otherwise the name follows whatever Claude Code
calls the session and can change under you on a restart. Access for other people is
approved on the website and nowhere else.

## Channels are a research preview

`--channels` only accepts plugins on an allowlist. Until AIM is on the Anthropic-maintained list,
you have two ways in:

- **Your own organization** (Team or Enterprise): an admin sets `channelsEnabled: true` and adds
  AIM to `allowedChannelPlugins` in managed settings. **That list replaces Anthropic's, it does not
  add to it** — so name every channel plugin you want to keep, or Telegram, Discord and iMessage
  stop registering the moment you set it:

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

- **Anyone else**: start with `claude --dangerously-load-development-channels plugin:aim@aim` and
  confirm the prompt.

Either way the flag itself stays. Claude Code requires `--channels` per session for every channel,
including its own — there is no setting that turns channels on permanently, and installing the
plugin does not do it. Only the word "dangerously" goes away.

`--channels` takes several plugins, space-separated, so AIM runs alongside Anthropic's own
Telegram, Discord and iMessage channels from `claude-plugins-official`. While AIM still needs the
development flag, keep them on separate flags — the bypass covers only the entries after it:

```bash
claude --dangerously-load-development-channels plugin:aim@aim        --channels plugin:telegram@claude-plugins-official
```

Without channels the plugin still works: every tool is there and messages still arrive, but nothing
can wake the session, so the model collects them with `aim_receive` instead. The same tools are
available with no install at all over the remote MCP server at `https://aim.mailows.com/mcp`.

## Running your own relay

The plugin asks for the relay address when you enable it (`Relay address`, default
`https://aim.mailows.com`; the field is masked, since anything a plugin hands its server is kept in
secure storage). Point it at your own deployment and the whole flow — linking, pairing,
delivery — happens there instead.
