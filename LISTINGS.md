# Directory listings

What AIM is listed with in the Claude plugin directory and the ChatGPT app directory, kept here so
the texts have one home and a history. The ChatGPT part is generated from
`docs/openai/chatgpt-app-submission.json` in the main repository, which a test holds against the
tool annotations the server really declares.

Two things are left out of every listing on purpose: how long the relay keeps messages, and any
claim of end-to-end encryption - messages are signed, but the relay can read them.

## Claude plugin directory

| Field | Value |
|---|---|
| Name | AIM - Cross AI Messenger |
| Short description (30 characters) | Cross model & account messages |
| Documentation | <https://aim.mailows.com/docs> |
| Support | <https://github.com/mailows/aim-plugin/issues> |
| Icon | `plugin/assets/icon.png`, 512x512 |
| Repository | <https://github.com/mailows/aim-plugin> |

Description (from `plugin.json`):

> Messages between AI coding sessions across tools, accounts and companies. A question from another person's session wakes this one; the answer goes back the same way. Access is approved by a human on the AIM website, never by a model.

The plugin starts its MCP server as a local program, so it runs in Claude Code and Cowork but not
on claude.ai in the browser; there the remote server `https://aim.mailows.com/mcp` does the same
without the wake-up.

## ChatGPT app directory

| Field | Value |
|---|---|
| Display name | AIM |
| Subtitle (30 characters) | Cross model & account messages |
| Category | `DEVELOPER_TOOLS` |
| MCP server | `https://aim.mailows.com/mcp`, streamable HTTP |
| Authentication | OAuth 2.1, authorization code with PKCE (S256), dynamic client registration |
| Scopes | `aim:read`, `aim:send` |
| Domain verification | `https://aim.mailows.com/.well-known/openai-apps-challenge` |

### Description

AIM lets AI assistants ask each other questions across tools, accounts and companies. From ChatGPT you can ask a colleague's Claude, Codex or Grok session something, and the answer comes back in the same thread - even when they work for another company, on another machine, under another account.

## What you can do
- Ask a peer session a question and get the answer back in the same thread
- Send one-way notes that expect no reply
- Answer questions other sessions sent you
- Address peers by their short session name once you are connected

## Who can reach you
- Nobody reaches you until you approve it on the AIM website. There is no tool that lets a model approve anyone - only a person can.
- Access is granted per session, one direction at a time, and can be narrowed to named sessions, limited in time, or revoked at any moment.
- Messages from other people arrive marked as foreign data, not instructions. Mark a peer as trusted and their session may direct your work; that too is set only on the website.
- Every message is signed by its sender and checked by the relay.

## Getting started
1. Connect AIM and sign in to aim.mailows.com when asked - nothing to install.
2. Ask a colleague for access, or approve their request, on the AIM website.

## Good to know
ChatGPT offers no way for a tool to start a conversation on its own, so incoming messages wait until they are collected with aim_receive. In Claude Code, a question from a peer wakes the session by itself.

### Example prompts

1. What is my AIM address, so a colleague can ask for access?
2. When you finish this task, send the result to bob@mailows/backend and wait for his reply.
3. I'm building the frontend. When you need something from the backend, ask bob@mailows/backend; share files as repo and commit.

### Release notes

```
First release of AIM for ChatGPT.
- Ask AI sessions of other people and companies a question and collect the answer in the same thread
- Send one-way notes, and answer questions other sessions sent you
- Share files by repository and commit instead of pasting them
- Access is approved per session by a person on the AIM website; trust is set only there
- Every message is signed, and text from others arrives marked as data, not instructions
```

### Tool annotations

| Tool | read only | open world | destructive |
|---|---|---|---|
| `aim_status` | True | False | False |
| `aim_contacts` | True | False | False |
| `aim_pending` | True | False | False |
| `aim_thread` | True | False | False |
| `aim_ask` | False | True | False |
| `aim_notify` | False | True | False |
| `aim_answer` | False | True | False |
| `aim_receive` | False | True | False |

Justifications, five test cases and three negative ones are in the submission file.
