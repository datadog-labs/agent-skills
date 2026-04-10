# Datadog Skills for AI Agents

Datadog skills for Claude Code, Codex CLI, Gemini CLI, Cursor, Windsurf, OpenCode, and other AI agents.

## Skills

| Skill | Description |
|-------|-------------|
| **dd-pup** | Primary CLI - commands, auth, PATH setup |
| **dd-monitors** | Create, manage, mute monitors |
| **dd-logs** | Search logs |
| **dd-apm** | Traces, services, performance |
| **dd-docs** | Search Datadog documentation |
| **dd-llmo** | LLM Observability: experiments, eval RCA, evaluator generation |

## Install

### Setup Pup

```bash
# Homebrew (macOS/Linux) — recommended
brew tap datadog-labs/pack
brew install datadog-labs/pack/pup

# Or build from source
git clone https://github.com/datadog-labs/pup.git && cd pup
cargo build --release
cp target/release/pup ~/.local/bin
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

The `dd-llmo` directory contains three skills for working with LLM Observability data:

| Skill | Purpose |
|-------|---------|
| `experiment-analyzer` | Analyze and compare offline LLM experiments |
| `eval-trace-rca` | Root-cause production failures using eval judge signal |
| `eval-bootstrap` | Generate evaluator code from traces, optionally seeded by RCA output |

**Eval pipeline flow:**

```
eval-trace-rca → eval-bootstrap
 (diagnose why)   (build evals)
```

Run `eval-trace-rca` to understand why an app is failing by analyzing eval judge verdicts across production traces. Then run `eval-bootstrap` to generate evaluator code that captures those failure patterns. Pass the RCA output directly to `eval-bootstrap` to seed it with the discovered failure taxonomy.

#### Install

```bash
# Claude Code — copy any or all skills
cp -r dd-llmo/experiment-analyzer ~/.claude/skills
cp -r dd-llmo/eval-trace-rca ~/.claude/skills
cp -r dd-llmo/eval-bootstrap ~/.claude/skills
```

#### MCP Requirements

All three skills require the LLMO toolset:

```bash
claude mcp add --scope user --transport http "datadog-llmo-mcp" 'https://mcp.datadoghq.com/api/unstable/mcp-server/mcp?toolsets=llmobs'
```

`experiment-analyzer` optionally uses the core toolset for notebook export:

```bash
claude mcp add --scope user --transport http "datadog-mcp-core" 'https://mcp.datadoghq.com/api/unstable/mcp-server/mcp?toolsets=core'
```

#### Usage

```
# Analyze experiments
experiment-analyzer <experiment_id>                         # single experiment
experiment-analyzer <baseline_id> <candidate_id>            # compare two experiments
experiment-analyzer <id(s)> <question>                      # ask a specific question
experiment-analyzer <id(s)> [question] --output notebook    # export to Datadog notebook

# Root-cause why an app is failing
What's wrong with <ml_app> based on its evals over the last 24h
Analyze eval failures for <eval_name> over the last week

# Generate evaluator code from production traces
/eval-bootstrap <ml_app>                                    # cold start
/eval-bootstrap <ml_app> [paste eval-trace-rca output here] # seeded from RCA
/eval-bootstrap <ml_app> --data-only                        # emit JSON spec instead of Python SDK code
```

## Quick Reference

| Task | Command |
|------|---------|
| Search error logs | `pup logs search --query "status:error" --from 1h` |
| List monitors | `pup monitors list` |
| Schedule monitor downtime | `pup downtime create --file downtime.json` |
| Find slow traces | `pup traces search --query "service:api @duration:>500ms" --from 1h` |
| Query metrics | `pup metrics query --query "avg:system.cpu.user{*}"` |
| List services for an env (required) | `pup apm services list --env <env> --from 1h --to now` |
| Check auth | `pup auth status` |
| Refresh token | `pup auth refresh` |

More commands for `pup` are found in the [official pup docs](https://github.com/datadog-labs/pup/blob/main/docs/COMMANDS.md).

## Auth

```bash
# Check auth first (includes token time remaining)
pup auth status

# If commands fail with 401/403, try refresh first
pup auth refresh

# If refresh fails or no session exists, do full OAuth login
pup auth login

# Non-default site/org
pup auth login --site datadoghq.eu --org <org>
```

If the browser opens the wrong profile/window, use the one-time URL printed by `pup auth login` and open it manually in the correct session.

## More Skills

Additional skills available soon.

```bash
# List all available
npx skills add datadog-labs/agent-skills --list --full-depth
```

## License

MIT
