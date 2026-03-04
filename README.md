# Datadog Skills for AI Agents

5 essential Datadog skills for Claude Code, Codex CLI, Gemini CLI, Cursor, Windsurf, OpenCode, and other AI agents.

## Skills

| Skill | Description |
|-------|-------------|
| **dd-pup** | Primary CLI - commands, auth, PATH setup |
| **dd-monitors** | Create, manage, mute monitors |
| **dd-logs** | Search logs |
| **dd-apm** | Traces, services, performance |
| **dd-docs** | Search Datadog documentation |
| **dd-llmo** | LLM Observability traces, experiments, evals |

## Install

### Setup Pup

```bash
# Homebrew (macOS/Linux) — recommended
brew tap datadog-labs/pack
brew install datadog-labs/pack/pup

# Or build from source
git clone https://github.com/datadog-labs/pup.git && cd pup
cargo build --release
cp target/release/pup /usr/local/bin/pup
```

Pre-built binaries are also available from the [latest release](https://github.com/datadog-labs/pup/releases/latest).

```bash
# Authenticate
pup auth login
```

### Add Skill(s) 

For JUST `dd-pup`:

```bash
npx skills add datadog-labs/agent-skills \
  --skill dd-pup \
  --full-depth -y
```

```bash
npx skills add datadog-labs/agent-skills \
  --skill dd-pup \
  --skill dd-monitors \
  --skill dd-logs \
  --skill dd-apm \
  --skill dd-docs \
  --full-depth -y
```

### LLM Observability (LLMO)

For LLMO skills, copy the relevant skill directories from `dd-llmo` to their local home. 
E.g.: for Claude Code `cp -r dd-llmo/experiment-analyzer ~/.claude/skills`

These have a dependency on the LLMO toolset in the Datadog MCP server. The easiest way to add it is:

```bash
claude mcp add --scope user --transport http "datadog-llmo-mcp" 'https://mcp.datadoghq.com/api/unstable/mcp-server/mcp?toolsets=llmobs'
```

This will add it as an independent MCP server from the core datadog MCP tools. 

If you'd like the ability to export results to a Datadog notebook, also install the core MCP tools. 

```bash
claude mcp add --scope user --transport http "datadog-mcp-core" 'https://mcp.datadoghq.com/api/unstable/mcp-server/mcp?toolsets=core'
```

## Quick Reference

| Task | Command |
|------|---------|
| Search error logs | `pup logs search --query "status:error" --duration 1h` |
| List monitors | `pup monitors list` |
| Mute a monitor | `pup monitors mute --id 123 --duration 1h` |
| Find slow traces | `pup apm traces list --service api --min-duration 500ms` |
| Query metrics | `pup metrics query --query "avg:system.cpu.user{*}"` |
| List services | `pup apm services list` |
| Check auth | `pup auth status` |
| Refresh token | `pup auth refresh` |

More commands for `pup` are found in the [official pup docs](https://github.com/datadog-labs/pup/blob/main/docs/COMMANDS.md). 

## Auth

```bash
pup auth login      # OAuth2 browser flow
pup auth status     # Check token
pup auth refresh    # Refresh expired token
```

Tokens expire (~1 hour). Run `pup auth refresh` if you get 401/403 errors.

## More Skills

Additional skills available soon.

```bash
# List all available
npx skills add datadog-labs/agent-skills --list --full-depth
```

## License

MIT

