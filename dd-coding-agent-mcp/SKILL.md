---
name: dd-coding-agent-mcp
description: Connect the coding agent you are running in (Claude Code, Codex, Gemini CLI, OpenCode, Cursor, or GitHub Copilot CLI) to the Datadog MCP server with the right toolsets, then confirm the tools are there. Takes the toolsets to turn on from the calling skill or the user; with none given it turns on everything generally available, and falls back to the core toolset if that fails. Use when the user wants Datadog tools in their coding agent, when another skill needs Datadog MCP tools that aren't in your tool list, or when the Datadog MCP server is connected but missing tools. Not for building MCP servers.
metadata:
  version: "0.2.0"
  author: datadog-labs
  repository: https://github.com/datadog-labs/agent-skills
  tags: datadog,mcp,onboarding,coding-agent
  status: beta
---

# Connect a coding agent to the Datadog MCP server

The Datadog MCP server gives a coding agent Datadog tools: Live Debugger (`live-debugger`), logs, metrics, monitors, and incidents (`core`), and toolsets for APM traces, Error Tracking, LLM Observability, RUM, Database Monitoring, dashboards, and CI (`software-delivery`). The server and the Datadog plugins load only `core` unless asked for more. This skill installs the client's Datadog plugin or writes its MCP config so the right toolsets load, then checks the result.

## Rules

- Never write an API key or application key value into a file. Reference environment variables (see `references/clients.md`).
- Edit an existing Datadog server entry rather than adding a second one, and never add a server whose toolsets a Datadog plugin already loads.
- Keep the user's other MCP servers and settings. Merge into config files; never overwrite them.
- Signing in and reloading the client are the user's steps. Ask for them once, keep going, and never block the calling skill on them.

## 1. Toolsets

Use the first that applies:

1. **Given in context** (the calling skill or the user names them, such as ShipDog's `core,apm,error-tracking,live-debugger`): use exactly that list. Add `core` if it's missing.
2. **None given:** use `all`.
3. **`all` rejected** (the server returns an error for it, or the org can't use it): fall back to `core` and say so in the report.

`all` covers generally available toolsets only. Preview toolsets load only when named, so a caller that needs one lists it explicitly.

## 2. Inspect

- **Site:** `DD_SITE` from the environment or an env file, else ask. The endpoint is `https://mcp.<site>/v1/mcp` (for example `mcp.datadoghq.com`, `mcp.datadoghq.eu`, `mcp.us5.datadoghq.com`). Government sites (`ddog-gov.com`) have no MCP server: stop and report that.
- **Client:** you know which agent you are. Read its section in `references/clients.md` before writing anything: those commands and formats are tested, and a CLI's `--help` doesn't say which header format Datadog needs.
- **What's connected:** list the client's MCP servers (`<cli> mcp list`, or read its config file). Note any server whose URL is `mcp.<site>`, any Datadog plugin, and the toolsets each one loads (the `toolsets` URL parameter or the `X-Datadog-MCP-Toolsets` header).
  - Already connected with every requested toolset: skip to step 5.
  - Connected with fewer: add the missing toolsets to that entry, or to the plugin's toolset setting.
  - Nothing connected: install the client's Datadog plugin if it has one, else add a server named `datadog`.

## 3. Auth

Pick one:

- **Sign-in (default).** Write the server with no credentials. The user signs in through the browser with the client's sign-in command (`references/clients.md`). Use this whenever a person is at the keyboard.
- **Keys (headless).** Only when `DD_API_KEY` and `DD_APP_KEY` are already set where the agent runs (CI, a remote machine with no browser), or the user asks for keys. Send them as `DD-API-KEY` and `DD-APPLICATION-KEY` headers that reference those variables, in the client's syntax. Never a bearer token: Datadog rejects one key alone (in Codex that means `env_http_headers`, not `bearer_token_env_var`). Ask once before writing a key-based entry if a person is present.

## 4. Configure

Install the plugin and set its site and toolsets, or write the entry with the URL `https://mcp.<site>/v1/mcp?toolsets=<list>`, as shown in `references/clients.md`. Then ask once, using the host's native question or selector if it has one:

> Datadog MCP is configured with the `<list>` toolsets. To load it, <reload step for this client>, then <sign-in step> if prompted.

Headless, with no one to ask: print that line, plus any sign-in URL the client printed, in the report. Don't wait either way.

## 5. Check

Run the client's list command (`<cli> mcp list`) and confirm the Datadog entry (`datadog`, or the plugin's, such as `plugin:datadog:mcp`) is there and connected. "Needs authentication" or "Not logged in" means the sign-in step is still the user's to do. A listed entry alone doesn't prove it works.

On the keys path, also confirm the server accepts the entry. Send the same URL and headers the entry uses; HTTP `200` means the keys and URL work, and `401` or `403` means they don't:

```sh
curl -s -o /dev/null -w '%{http_code}\n' -X POST "<URL>" \
  -H "DD-API-KEY: $DD_API_KEY" -H "DD-APPLICATION-KEY: $DD_APP_KEY" \
  -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"setup-check","version":"1"}}}'
```

When the tools load in your session (after a reload, or from the start), confirm what was asked for:

| Requested | Passes when |
|---|---|
| `core` | `search_datadog_logs` is in your tool list |
| Specific toolsets | each requested toolset contributes at least one tool to your tool list |
| `all` | the tool list matches what the server lists for `toolsets=all`, not one hand-picked tool |

If the server is connected but a requested toolset's tools never appear, the org may not have that toolset yet (some are in Preview). Report it and suggest contacting Datadog support.

## 6. Report

One line first: `Datadog MCP configured for <client> with <list>; connected and verified.` (only when step 5 showed the requested tools, or a `200` on the keys path) or `... configured; waiting on you to <reload / sign in>.` or `Not configured: <reason>.` Then the file or command changed, and the exact steps left for the user. Never include a key value.

A calling skill continues either way: missing tools are a follow-up, never a stop.
