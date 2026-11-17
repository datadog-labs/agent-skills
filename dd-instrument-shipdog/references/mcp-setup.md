# Connect the Shipdog MCP tools

The Datadog plugins and the server default load only `core`, so a plugin-only user can't place a logpoint. Turn on all of Shipdog's toolsets: `core,apm,error-tracking,live-debugger`. Below, that list is `<TOOLSETS>`.

Endpoint: `https://mcp.<DD_SITE>/v1/mcp`. Government sites (`ddog-gov.com`) have no MCP server; use the no-MCP fallback in SKILL.md.

Find what's connected: a Datadog plugin (Claude Code `/plugin`, or the client's plugin settings; ask if unsure), a hand-added server (`mcp.<site>/v1/mcp` in `~/.claude.json`, `~/.cursor/mcp.json`, `~/.copilot/mcp-config.json`, `~/.codex/config.toml`), or nothing. Edit an existing server rather than add one, and never add a server that repeats toolsets the plugin already loads.

| Client | With the Datadog plugin | Without |
|---|---|---|
| Claude Code | user runs `/ddtoolsets`, adds the missing Shipdog toolsets, then `/reload-plugins` | `claude mcp add --scope user --transport http datadog "https://mcp.<DD_SITE>/v1/mcp?toolsets=<TOOLSETS>"` |
| Cursor | user runs `/ddtoolsets`, adds the missing Shipdog toolsets | `datadog` entry in `~/.cursor/mcp.json` with that URL |
| Copilot | user runs `/ddtoolsets`, adds the missing Shipdog toolsets, restarts | `copilot mcp add --transport http datadog "<that URL>"` |
| Codex | plugin has no toolset option: add the server below | the server below with `<TOOLSETS>` |
| Other | the plugin's toolset setting, else a server with only the missing toolsets | the URL with `?toolsets=<TOOLSETS>` |

Claude Code and Cursor plugins also read `DD_MCP_TOOLSETS=<TOOLSETS>,visualizations` at startup.

```toml
# ~/.codex/config.toml
[mcp_servers.datadog-live-debugger]
url = "https://mcp.<DD_SITE>/v1/mcp"
http_headers = { "X-Datadog-MCP-Toolsets" = "live-debugger" }
```

The user must reload the client and sign in; you can't. Tell them what to run (`/reload-plugins` or `/mcp`, restart Cursor or Codex, `codex mcp login datadog-live-debugger`) and continue once the tools appear. Don't wait for them: keep editing, and if the tools aren't there by verification, use the **No MCP tools** fallback in SKILL.md and list these steps as a follow-up.

Configured and reloaded but `discover_datadog_logpoint` still missing: the org may not have the Live Debugger MCP tools yet (Preview). Verify in the UI and suggest contacting Datadog support.
