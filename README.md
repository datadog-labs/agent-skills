# Datadog Skills for AI Agents

Datadog skills for Claude Code, Codex CLI, Gemini CLI, Cursor, Windsurf, OpenCode, and other AI agents.

## Skills

| Skill | Description |
|-------|-------------|
| **dd-pup** | Primary CLI - commands, auth, PATH setup |
| **dd-monitors** | Create, manage, mute monitors |
| **dd-logs** | Search logs |
| **dd-apm** | Traces, services, performance, Single-Step Instrumentation |
| **dd-docs** | Search Datadog documentation |
| **dd-llmo** | LLM Observability: experiments, eval RCA, evaluator generation, session classification |
| **dd-browser-sdk** | Browser SDK: RUM, Logs, Session Replay, profiling, product analytics, error tracking, version migration |
| **dd-audit** | Audit Trail investigations: who changed what, key compromise, cost spike root cause, compliance evidence (SOC 2/PCI), AI activity auditing |
| **dd-software-delivery** | CI/CD workflow skills — unblock PR pipelines, triage flaky tests (MCP + pup) |
| **dd-software-delivery** | CI/CD workflow skills — unblock failing PR pipelines, triage flaky tests |

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

For ALL skills:

```bash
npx skills add datadog-labs/agent-skills \
  --skill dd-pup \
  --skill dd-monitors \
  --skill dd-logs \
  --skill dd-apm \
  --skill dd-docs \
  --skill dd-browser-sdk \
  --skill dd-audit \
  --skill service-remapping \
  --skill agent-install \
  --skill enable-ssi \
  --skill verify-ssi \
  --skill troubleshoot-ssi \
  --skill onboarding-summary \
  --skill upgrade-browser-sdk-v7 \
  --skill dd-audit-security-investigation \
  --skill dd-audit-key-compromise \
  --skill dd-audit-cost-spike-investigation \
  --skill dd-audit-compliance-report \
  --skill dd-audit-ai-activity \
  --skill llm-obs-experiment-analyzer \
  --skill llm-obs-experiment-py-bootstrap \
  --skill llm-obs-trace-rca \
  --skill llm-obs-eval-bootstrap \
  --skill llm-obs-eval-pipeline \
  --skill llm-obs-session-classify \
  --skill k9-ownership-byod-setup \
  --skill unblock-pr \
  --skill triage-flaky-test \
  --full-depth -y
```

### LLM Observability (LLMO)

The `dd-llmo` directory contains six skills for working with LLM Observability data:

| Skill | Purpose |
|-------|---------|
| `llm-obs-experiment-analyzer` | Analyze and compare offline LLM experiments |
| `llm-obs-experiment-py-bootstrap` | Generate self-contained Python experiment code using the `ddtrace.llmobs` SDK |
| `llm-obs-trace-rca` | Root-cause production failures using eval judge signal or runtime errors |
| `llm-obs-eval-bootstrap` | Generate evaluator code from traces, optionally seeded by RCA output. Also emits a dataset from traces in `--emit-dataset` mode. |
| `llm-obs-eval-pipeline` | Eight-phase pipeline: classify → RCA → bootstrap evaluators → create dataset → publish → generate experiment → run → analyze. Stop early with `--stop-after`. |
| `llm-obs-session-classify` | Classify whether user intent was satisfied in a session (trace + RUM signals) |

**Eval pipeline flow:**

```
llm-obs-session-classify    llm-obs-trace-rca → llm-obs-eval-bootstrap
 (classify sessions)          (diagnose why)      (build evals)
```

Run `llm-obs-trace-rca` to understand why an app is failing by analyzing eval judge verdicts or
runtime errors across production traces. Then run `llm-obs-eval-bootstrap` to generate evaluator
code that captures those failure patterns. Pass the RCA output directly to `llm-obs-eval-bootstrap`
to seed it with the discovered failure taxonomy.

Use `llm-obs-eval-pipeline` to run all three steps in sequence with checkpoints between each phase.

Use `llm-obs-session-classify` independently to evaluate whether individual assistant sessions
satisfied user intent, combining LLM Obs trace data with RUM behavioral signals.

Use `llm-obs-experiment-py-bootstrap` to generate a self-contained Python experiment client
that uses the `ddtrace.llmobs` SDK — runnable as a `.py` script or `.ipynb` notebook, with
inline records, a CSV path, or a named Datadog dataset as the input.

#### Install

```bash
# Claude Code — copy any or all skills
cp -r dd-llmo/llm-obs-experiment-analyzer ~/.claude/skills
cp -r dd-llmo/llm-obs-experiment-py-bootstrap ~/.claude/skills
cp -r dd-llmo/llm-obs-trace-rca ~/.claude/skills
cp -r dd-llmo/llm-obs-eval-bootstrap ~/.claude/skills
cp -r dd-llmo/llm-obs-eval-pipeline ~/.claude/skills
cp -r dd-llmo/llm-obs-session-classify ~/.claude/skills
```

#### MCP Requirements

All six skills require the LLMO toolset:

```bash
claude mcp add --scope user --transport http "datadog-llmo-mcp" 'https://mcp.datadoghq.com/api/unstable/mcp-server/mcp?toolsets=llmobs'
```

`experiment-analyzer` uses the core toolset for notebook export (optional). `eval-session-classify`
requires it for RUM behavioral analysis and efficient batched fetches of trace session spans:

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
Look at the errors on <ml_app> over the last 24h

# Generate evaluator code from production traces
/eval-bootstrap <ml_app>                                    # cold start
/eval-bootstrap <ml_app> [paste eval-trace-rca output here] # seeded from RCA
/eval-bootstrap <ml_app> --data-only                        # emit JSON spec instead of Python SDK code

# Generate a Python experiment client using the ddtrace.llmobs SDK
/llm-obs-experiment-py-bootstrap                                                  # 3-record inline sample
/llm-obs-experiment-py-bootstrap --dataset ./data/qa.json --format ipynb          # local JSON dataset, notebook
/llm-obs-experiment-py-bootstrap --dataset-name qa_v3 --project-name customer-qa  # existing Datadog dataset
/llm-obs-experiment-py-bootstrap --evaluator-style remote                         # server-side RemoteEvaluator stubs

# Classify a session
/eval-session-classify <session_id>

# Guided onboarding through datasets + experiments (6 narrated states)
/llm-obs-onboarding-datasets-experiments <ml_app>
/llm-obs-onboarding-datasets-experiments <ml_app> --timeframe now-30d --trace-limit 25 --format ipynb
```

### Software Delivery (dd-software-delivery)

The `dd-software-delivery` directory contains workflow skills for CI/CD visibility and test reliability:

| Skill | Purpose |
|-------|---------|
| `unblock-pr` | Investigate a failing PR CI pipeline — classify each failure as flaky, infra, or regression; fetch code coverage and PR quality/security insights; propose targeted actions |
| `triage-flaky-test` | Deep-dive on a specific flaky test — get history, blast radius, root cause category, and recommend a code fix or quarantine |

**Workflow:**

```
unblock-pr → (if flaky failure) → triage-flaky-test → quarantine or fix
```

#### Backend

Both skills auto-detect the available backend at runtime:
- **MCP mode** (preferred): uses the Datadog software-delivery MCP tools (`search_datadog_ci_pipeline_events`, `get_datadog_flaky_tests`, `retry_datadog_ci_job`, etc.). Enables PR quality/security insights and native GitHub Actions retry.
- **pup mode** (fallback): uses the `pup` CLI. PR quality/security data is not available; GitHub Actions retry falls back to `gh run rerun`.

Pass `--backend pup` to force pup mode regardless of MCP availability.

#### MCP Requirements

Connect the Datadog MCP server with the `software-delivery` toolset:

```bash
claude mcp add --scope user --transport http "datadog-mcp" \
  'https://mcp.datadoghq.com/api/unstable/mcp-server/mcp?toolsets=core,software-delivery'
```

#### Prerequisites

Requires `pup` CLI for pup mode (and as a fallback). See [Setup Pup](#setup-pup).

#### Install

```bash
# Claude Code — copy any or all skills
cp -r dd-software-delivery/unblock-pr ~/.claude/skills
cp -r dd-software-delivery/triage-flaky-test ~/.claude/skills
```

Or via `npx`:

```bash
npx skills add datadog-labs/agent-skills \
  --skill unblock-pr \
  --skill triage-flaky-test \
  --full-depth -y
```

#### Usage

```
# Investigate a failing PR
unblock-pr                                     # auto-detects branch and repo from git
unblock-pr my-feature-branch                   # explicit branch
unblock-pr my-feature-branch github.com/org/repo

# Triage a specific flaky test
triage-flaky-test TestMyFunc
triage-flaky-test com.example.MyTest github.com/org/repo
```

### Audit Trail (dd-audit)

The `dd-audit` directory contains five skills for investigating Datadog Audit Trail data:

| Skill | Purpose |
|-------|---------|
| `security-investigation` | Who changed what, user activity, login geo, deletions, permission changes |
| `key-compromise` | Investigate a potentially compromised API key — timeline, geo/IP, endpoints called |
| `cost-spike-investigation` | Correlate usage spike (Usage Metering) with config changes (Audit Trail) to find root cause |
| `compliance-report` | Generate SOC 2 / PCI DSS evidence from audit data |
| `ai-activity-audit` | Audit what the Bits AI / MCP assistant did in your org |

#### Prerequisites

These skills use the Datadog Audit REST API directly (no `pup audit` command exists yet). You need an API key + App key with `audit_logs_read` scope:

```bash
export DD_API_KEY=<your-api-key>
export DD_APP_KEY=<your-app-key>
export DD_SITE=datadoghq.com   # or us3/us5/eu/ap1/ap2
```

#### Install

```bash
# Claude Code — copy any or all skills
cp -r dd-audit/security-investigation ~/.claude/skills
cp -r dd-audit/key-compromise ~/.claude/skills
cp -r dd-audit/cost-spike-investigation ~/.claude/skills
cp -r dd-audit/compliance-report ~/.claude/skills
cp -r dd-audit/ai-activity-audit ~/.claude/skills
```

#### Usage

```
# Security investigation
Who deleted monitors in the last 24 hours?
What did user@example.com do this week?
Show login activity from unexpected locations

# Key compromise
Was API key <key_id> used from unexpected locations?
Investigate this API key: <key_id>

# Cost spike
Why did our LLM Observability usage spike on May 1?
What caused the cost increase this week?

# Compliance
Generate SOC 2 evidence for CC6.2 and CC6.3 for Q1 2026
Create a PCI DSS Requirement 10 report for the last 90 days

# AI activity
What did the Bits AI assistant do in my org this week?
Show me a governance report for AI tool calls in April
```

### Software Delivery (dd-software-delivery)

The `dd-software-delivery` directory contains workflow skills for CI/CD visibility and test reliability:

| Skill | Purpose |
|-------|---------|
| `unblock-pr` | Investigate a failing PR CI pipeline — classify each failure as flaky, infra, or regression; fetch code coverage; propose targeted actions |
| `triage-flaky-test` | Deep-dive on a specific flaky test — get history, blast radius, root cause category, and recommend a code fix or quarantine |

**Workflow:**

```
unblock-pr → (if flaky failure) → triage-flaky-test → quarantine or fix
```

Run `unblock-pr` when CI is red on a PR to attribute each failing job. If a failure is classified as **flaky**, the skill hands off to `triage-flaky-test` for deeper investigation and a targeted fix or quarantine via `pup test-optimization flaky-tests update`.

#### Prerequisites

Requires `pup` CLI installed and authenticated (`pup auth login`). See [Setup Pup](#setup-pup).

#### Install

```bash
# Claude Code — copy any or all skills
cp -r dd-software-delivery/unblock-pr ~/.claude/skills
cp -r dd-software-delivery/triage-flaky-test ~/.claude/skills
```

Or via `npx`:

```bash
npx skills add datadog-labs/agent-skills \
  --skill unblock-pr \
  --skill triage-flaky-test \
  --full-depth -y
```

#### Usage

```
# Investigate a failing PR
unblock-pr                                    # auto-detects branch and repo from git
unblock-pr my-feature-branch                  # explicit branch
unblock-pr my-feature-branch github.com/org/repo

# Triage a specific flaky test
triage-flaky-test TestMyFunc
triage-flaky-test com.example.MyTest github.com/org/repo
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
