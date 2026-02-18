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

## Install

### Setup Pup

```bash
# Install pup CLI
go install github.com/datadog-labs/pup@latest
export PATH="$HOME/go/bin:$PATH"

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

