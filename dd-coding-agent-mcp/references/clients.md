# Per-client MCP setup

Prefer the client's Datadog plugin when it has one: it handles the site and sign-in (`ddsetup`) and keeps toolsets configurable (`ddtoolsets`). Add a server by hand only when the client has no plugin, the plugin can't be installed (no marketplace access, or the org blocks plugins), or a hand-added Datadog server already exists (edit that one instead).

`<URL>` is `https://mcp.<site>/v1/mcp?toolsets=<list>` for a hand-added server. Use user scope so the server works in every project. Headers are only for the keys path; leave them out for sign-in.

## Claude Code

- **Plugin:** `claude plugin marketplace add anthropics/claude-plugins-official` (skip if `claude plugin marketplace list` shows it), then `claude plugin install datadog@claude-plugins-official`.
  - Its server is `plugin:datadog:mcp`, defined in `<installPath>/.dd_claude-code_mcp.json`; `installPath` is under `datadog@claude-plugins-official` in `~/.claude/plugins/installed_plugins.json`.
  - Set the site and toolsets the way its `ddtoolsets` skill does: in that file, change only the text between `:-` and `}` in `${DD_MCP_DOMAIN:-mcp.<site>}` and `${DD_MCP_TOOLSETS:-<list>}`.
  - These are only defaults: a nonempty `DD_MCP_DOMAIN` or `DD_MCP_TOOLSETS` in Claude Code's launch environment wins, even after `/reload-plugins`. Check for them first; if set, ask the user to update or unset them there and restart Claude Code.
  - **Keys:** nothing to write. The plugin reads `DD_API_KEY`, `DD_APPLICATION_KEY`, and `DD_MCP_DOMAIN` from the environment Claude Code starts in; if only `DD_APP_KEY` is set, tell the user to also export `DD_APPLICATION_KEY`.
  - **Reload and sign in:** `/reload-plugins`, then `/ddsetup` (or `/mcp`) to sign in.
- **By hand:** `claude mcp add --scope user --transport http datadog "<URL>"`
  - **Keys:** add `-H 'DD-API-KEY: ${DD_API_KEY}' -H 'DD-APPLICATION-KEY: ${DD_APP_KEY}'` in single quotes, so the variable names, not their values, land in `~/.claude.json`.
  - **Edit:** remove and re-add (`claude mcp remove datadog --scope user`), or change `mcpServers.datadog.url` in `~/.claude.json`.
  - **Reload and sign in:** run `/mcp`, pick `datadog`, authenticate.
- **List:** `claude mcp list`

## Codex

No CLI plugin (the Codex desktop app's plugin is a US1-only preview with no toolset setting), so add the server by hand.

- **Add:** `codex mcp add datadog --url "<URL>"` (writes `[mcp_servers.datadog]` to `~/.codex/config.toml`).
- **Keys:** append to `~/.codex/config.toml`:
  ```toml
  [mcp_servers.datadog.env_http_headers]
  "DD-API-KEY" = "DD_API_KEY"
  "DD-APPLICATION-KEY" = "DD_APP_KEY"
  ```
  The values are bare variable names (`"DD_API_KEY"`, not `"${DD_API_KEY}"`); Codex reads them at startup. Don't use `--bearer-token-env-var`: Datadog needs both keys as headers, not a bearer token.
- **Sign in:** `codex mcp login datadog`, then restart Codex.
- **List:** `codex mcp list`

## Gemini CLI

No plugin, so add the server by hand.

- **Add:** merge `"mcpServers": {"datadog": {"httpUrl": "<URL>"}}` into `~/.gemini/settings.json`, keeping its other keys. Inside Gemini CLI, do it with a shell command (`jq` or `python3`): its policy blocks `gemini mcp add`, and its edit tools can't reach files outside the workspace. Outside Gemini, `gemini mcp add --scope user --transport http datadog "<URL>"` writes the same entry.
- **Keys:** add `"headers": {"DD-API-KEY": "$DD_API_KEY", "DD-APPLICATION-KEY": "$DD_APP_KEY"}` to the entry (or `-H 'DD-API-KEY: $DD_API_KEY' -H 'DD-APPLICATION-KEY: $DD_APP_KEY'` in single quotes with `gemini mcp add`).
- **Sign in:** restart Gemini CLI, then run `/mcp auth datadog`.
- **List:** `gemini mcp list`

## OpenCode

Edit `~/.config/opencode/opencode.json`, keeping its other keys. Add the plugin and the server entry its `ddsetup` tool would write; after a restart, its `ddtoolsets` tool manages the toolsets header.

```json
{
  "plugin": ["@datadog/opencode-plugin"],
  "mcp": {"datadog": {"type": "remote", "url": "https://mcp.<site>/v1/mcp", "enabled": true,
                      "headers": {"X-Datadog-MCP-Toolsets": "<list>"}}}
}
```

- **By hand:** the same `mcp.datadog` entry without the `plugin` line.
- **Keys:** add `"DD-API-KEY": "{env:DD_API_KEY}"` and `"DD-APPLICATION-KEY": "{env:DD_APP_KEY}"` to `headers`.
- **Sign in:** `opencode mcp auth datadog`, then restart OpenCode.
- **List:** `opencode mcp list`

## Cursor (editor and `agent` CLI)

- **Plugin:** the user adds the Datadog plugin from the Cursor Marketplace (Settings → Plugins → Add to Cursor); you can't install it. Then `/ddsetup` signs in and `/ddtoolsets` sets the toolsets.
- **By hand:** add `{"mcpServers": {"datadog": {"url": "<URL>"}}}` to `~/.cursor/mcp.json`.
  - **Keys:** add `"headers": {"DD-API-KEY": "${env:DD_API_KEY}", "DD-APPLICATION-KEY": "${env:DD_APP_KEY}"}`.
  - **Sign in:** Cursor settings → MCP → `datadog` → sign in, or `agent mcp login datadog` in the CLI.
- **List:** `agent mcp list`

## GitHub Copilot CLI

- **Plugin:** `copilot plugin install datadog@awesome-copilot`, then restart. `/ddsetup` signs in and `/ddtoolsets` sets the toolsets.
- **By hand:** `copilot mcp add --transport http datadog "<URL>"` (writes `~/.copilot/mcp-config.json`).
- **Keys:** not covered yet; use sign-in.

## Other clients

Use the client's Datadog plugin and its toolset setting if it has one, else an HTTP server named `datadog` at `<URL>`.
