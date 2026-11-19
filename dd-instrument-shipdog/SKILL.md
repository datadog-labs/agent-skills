---
name: dd-instrument-shipdog
description: Datadog Shipdog backend setup. Gets one service's traces and logs into Datadog, turns on Live Debugger (logpoints that capture variable values from running code), and proves it with a real logpoint. Supports Python and Node.js today, sending straight to Datadog with no Agent (agentless mode) or through an existing working Agent. Use when the user wants to add logpoints to their app, see variable values from a running or production service without print statements or redeploys, set up or fix Live Debugger ("service isn't available", logpoints never capture), send traces and logs to Datadog without an Agent, or set up or onboard to Shipdog. Not for installing the Datadog Agent on hosts, frontend RUM, or adding log statements to code.
metadata:
  version: "0.5.0"
  author: datadog-labs
  repository: https://github.com/datadog-labs/agent-skills
  tags: datadog,shipdog,live-debugger,apm,logs,agentless,python,nodejs
  alwaysApply: "false"
---

# Datadog Shipdog backend setup

Shipdog gives a team traces, logs, and Live Debugger for their backend. This skill sets up one service for it. Today that means Python or Node.js in agentless mode: the tracer sends straight to Datadog with no Agent ([Node.js](https://docs.datadoghq.com/tracing/trace_collection/library_config/nodejs/#agentless-mode), [Python](https://docs.datadoghq.com/tracing/trace_collection/library_config/python/#agentless-mode)).

Live Debugger is the only Dynamic Instrumentation feature in scope. Its tracer switch is `DD_DYNAMIC_INSTRUMENTATION_ENABLED` (step 2); don't set up other Dynamic Instrumentation probes or suggest them in the report.

## Rules

- Read the entrypoint, manifests, and deploy files before editing.
- Live Debugger isn't available on the US1-FED or US2-FED sites. If `DD_SITE` is `ddog-gov.com` or `us2.ddog-gov.com`, stop before editing anything: reply `Not set up: Shipdog isn't available on <site>.`
- Never write the API key value into a committed file, and never print it.
- One tracer init. If the project already loads `ddtrace`/`dd-trace`, keep that load and fix its version and settings, so the tracer's settings come from one place.
- Add dependencies to the manifest the deploy installs from, not only the local environment. Keep `node_modules/` and virtualenvs in `.gitignore`.
- No unrelated edits: the user reviews the diff, and unrelated changes hide the setup. Suggest other products in the report instead, such as RUM (`dd-instrument-rum`) for the frontend.
- Done means verified (step 4). If something blocks verification, report the blocker.
- Requires the `dd-git-tagging` skill. If it isn't installed, stop before editing anything: reply `Not set up: dd-instrument-shipdog needs the dd-git-tagging skill installed alongside it.` Don't set Git metadata any other way.

## 0. Source code integration first

Live Debugger places logpoints by file and line, so Datadog must see the service's source. [Source code integration](https://docs.datadoghq.com/source_code/) has two halves: connecting the Git provider to Datadog (`dd-github-onboarding` for GitHub, now), and tagging the service's telemetry with the commit (`dd-git-tagging`, in step 2). For a GitHub repo, run `dd-github-onboarding` if it's available; it checks whether GitHub is already connected. Without it, or for another provider, tell the user to connect it under **Integrations** in Datadog and continue. Say in the report which happened.

## 1. Inspect

- **Runtime:** `requirements.txt`/`pyproject.toml`/`Pipfile` → Python, read `references/python.md`. `package.json` with a server entrypoint → Node.js, read `references/nodejs.md`. Anything else (Java, Go, Ruby, .NET, PHP, Bun, Deno, browser code): say this skill covers only Python and Node.js, and stop. Several services: ask which one.
- **Launch commands:** every place the service starts (`scripts.start`, `Procfile`, Dockerfile `CMD`, compose, Kubernetes, a gunicorn/uvicorn command). Each one gets the tracer.
- **Agent:** decide this yourself; don't ask. Grep the project for `DD_AGENT_HOST`, `DD_TRACE_AGENT_URL`, `DD_TRACE_AGENT_PORT`, a `datadog/agent` image, or a Datadog Helm chart, and run `curl -s -m 2 http://localhost:8126/info`.
  - No Agent anywhere (the normal Shipdog case): go agentless.
  - Project config sends to an Agent: `curl` its `/info` at the configured host and port. If it answers and its `endpoints` include `/v0.7/config` and a `/debugger/` path, keep the Agent: set everything in step 2 except `DD_AGENTLESS_ENABLED`, log submission, and `DD_API_KEY` (the Agent holds the key), then verify as usual. Missing logs here mean the Agent's log collection is off; report that as a follow-up. If the Agent doesn't answer or lacks those endpoints, Agent repair is out of scope: report what you found and stop.
  - An Agent answers locally but the project doesn't use it: a local Agent says nothing about where the service deploys. Go agentless and mention it in the report.
- **Logging library:** Node.js `winston`/`pino`/`bunyan` or only `console.log`; Python `logging`/`structlog`, `loguru`, or only `print`.
- **Existing values** of the variables in step 2. Reuse them.
- **MCP tools:** if `discover_datadog_logpoint` isn't in your tool list, run the `dd-coding-agent-mcp` skill now with Shipdog's toolsets (below). If that skill isn't installed, or there's no local coding agent whose MCP config you can change, don't set up MCP yourself: finish the setup, verify with **No MCP tools**, and add a warning to the report's follow-ups (`Warning: the dd-coding-agent-mcp skill is missing, so the Datadog MCP tools weren't connected. Install it from https://github.com/datadog-labs/agent-skills and rerun.`, or say no local coding agent could be configured). The user's reload and sign-in can happen while you edit; don't wait on them. If the tools still aren't there by step 4, verify with **No MCP tools** below and list the steps that skill reported as a follow-up. Missing tools never stop the setup.

  | Shipdog product | Toolset |
  |---|---|
  | Logs search and verification | `core` |
  | Traces | `apm` |
  | Errors from the service | `error-tracking` |
  | Live Debugger logpoints and snapshots | `live-debugger` |

  Ask for all four: `core,apm,error-tracking,live-debugger`.

## 2. Configure

Set these where the service's runtime environment is defined (Dockerfile `ENV`, compose, Kubernetes, platform config). With no deploy config, put them in the launch command itself; the language file shows how.

| Variable | Value |
|---|---|
| `DD_AGENTLESS_ENABLED` | `true`: send straight to Datadog, no Agent |
| `DD_DYNAMIC_INSTRUMENTATION_ENABLED` | `true`: Live Debugger on from startup (the tracer reads this one setting for it) |
| Log submission | Node.js `DD_AGENTLESS_LOG_SUBMISSION_ENABLED=true`; Python: the OpenTelemetry log settings in `python.md` |
| `DD_SITE` | the org's site, such as `datadoghq.com` or `datadoghq.eu`; see **API key and site** |
| `DD_SERVICE` | package or project name, lowercase with hyphens |
| `DD_ENV` | `dev` unless one exists (see redaction below) |
| `DD_VERSION` | the app's version field, else the short commit SHA set at build time |
| `DD_API_KEY` | secret; see **API key and site** |

**Git metadata** ties telemetry and logpoints to source. Run the `dd-git-tagging` skill with `DD_SERVICE` and `DD_ENV`. Until the service runs, its check reports `no_spans`, so do its inspect and configure steps now, in the same files as the variables above, and its confirm step during step 4.

**API key and site:** this skill doesn't create accounts or keys. Reuse `DD_API_KEY` and `DD_SITE` from the environment, an env file, or deploy secrets. If either is missing, run the `dd-account-setup` skill first (the onboarding orchestrator already does), or ask the user for them. For local runs, keep the key in the first existing of `.env.local`, `.env.development.local`, `.env.development`, `.env` (create `.env` if none), and make sure that file is gitignored. The launch examples in the language files load `.env`; if you used another file, point the launch command at that one. Keep only secrets there; everything else goes in committed config so a fresh clone starts. For deploys, tell the user to add the key to their platform's secret store and reference it by name.

**Redaction:** Shipdog orgs show variable values by default. Other orgs redact all but numbers and booleans in environments not named like pre-production (`dev`, `staging`, `test`, `qa`), and the MCP tools refuse logpoints there. Never rename a real production env to dodge it; tell the user someone with `live_debugger_redaction_write` can switch it to Targeted mode on the Live Debugger settings page.

Then follow the language file.

## 3. Run

Start the service with the edited launch command and leave it running. Hit an endpoint every few seconds through step 4: Live Debugger sees a service only after the tracer reports it, up to a minute after the first request.

## 4. Verify

**Logpoint** (Datadog MCP; without the tools, skip to **No MCP tools**):

1. `create_debugger_session` (`session_name: "Shipdog setup check"`). Keep `session_id`.
2. `discover_datadog_logpoint` with `service_name` = `DD_SERVICE`. If the service is missing or not enabled, send requests, wait 30 seconds, retry up to three times.
3. `create_datadog_logpoint` with `service_name` and `environment` as discover returned them, `file_path` relative to the repo root, the `session_id`, a `purpose`, and a `message_template` interpolating one variable in scope. Python: `logpoint_type: method` with `method_name` when discover lists `method_logpoints`. Node.js: `line` with an executable `line_number` inside the handler. A "not reported active yet" warning is normal. On an error, call `list_datadog_session_logpoints` before retrying: the logpoint may exist anyway.
4. Keep sending requests. Poll `list_datadog_session_logpoints` until the status is `active` and `last_fired_at` is set (up to two minutes).
5. Run the returned `logs_query` through `search_datadog_logs` (last 15 minutes, `extra_fields: ["event_id"]`, without which ids are omitted). Pass an event id (starts with `AwAA`) to `get_datadog_debugger_snapshot` and confirm variable values. No log search tool: give the user the `logs_url` and treat `last_fired_at` as the pass.
6. `disable_datadog_logpoints` with the `session_id`.

**Logs:** make the app log a line, then `search_datadog_logs` for `service:<DD_SERVICE> -source:dd_debugger` (snapshots are logs too). A line should appear within a couple of minutes. None, and the app only uses `console.log`/`print`: expected, report it as a follow-up.

**Git metadata:** run `dd-git-tagging`'s confirm step. The skill must be installed (see **Rules**), but spans that are still untagged don't fail the setup, since logpoints still work by file path; report it as a follow-up.

| Error says | Do |
|---|---|
| MCP write is not enabled | An org admin turns it on under **Organization Settings → MCP Server**. Stop and report. |
| Remote Configuration is not currently available | An org admin enables it under **Organization Settings → Remote Configuration**. Stop and report. |
| missing permission (`live_debugger_write`, `mcp_write`) | Report it. Stop. |
| strict redaction mode | See **Redaction**. Stop and report. |
| service not found / no instances | Recheck that the variables reach the process, the key, and the site. Send more requests. |

**No MCP tools:** you can't place a logpoint yourself; there's no public API for it. Count the setup as a soft success and don't wait on the user. Put these verification steps in the report:

Open Live Debugger in Datadog, start a Debug Session on the service and env, add a logpoint on the handler, send a request, and look for a snapshot with variable values.

You can still confirm that traces and logs arrive through the public API, with `DD_API_KEY` and an application key in `DD_APP_KEY`; never print either. `dd-account-setup` can leave `DD_APP_KEY` unset after an OAuth sign-in; if it's missing, skip these checks. Use [Search logs](https://docs.datadoghq.com/api/latest/logs/#search-logs-post) with `service:<DD_SERVICE> -source:dd_debugger` for app logs, and [Search spans](https://docs.datadoghq.com/api/latest/spans/#search-spans) (`/api/v2/spans/events/search`, body wrapped as `{"data":{"type":"search_request","attributes":{...}}}`) for traces:

```sh
printf 'DD-API-KEY: %s\nDD-APPLICATION-KEY: %s\n' "$DD_API_KEY" "$DD_APP_KEY" | \
curl -s -X POST "https://api.$DD_SITE/api/v2/logs/events/search" -H @- -H "Content-Type: application/json" \
  -d '{"filter":{"query":"service:<DD_SERVICE> -source:dd_debugger","from":"now-15m"},"page":{"limit":5}}'
```

Lead the report with `Set up, not verified in Datadog: the Live Debugger MCP tools aren't connected.`, then what the API confirmed and the steps above.

## 5. Report

Write for someone new to Datadog, in plain language. Never include the key.

1. One-line result first: `Verified: ledger-api (env dev) sends logs to Datadog; Live Debugger captured a snapshot showing items, discount, subtotal.` On failure: `Not verified: <step> failed with "<error>". <What to do>.`
2. Each changed file, with why it's needed.
3. The launch command for each place the service runs.
4. Real follow-ups only, such as connecting the Git provider (step 0), adding `DD_API_KEY` to deploy secrets, or moving `console.log`/`print` onto a logging library.
