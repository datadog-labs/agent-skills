---
name: sampling
description: Diagnose and change APM trace sampling — set per-resource rates, configure adaptive sampling, adjust agent samplers (priority/error/rare TPS), or figure out why a sampling rule isn't taking effect. Use for any request involving "change sample rate", "drop X% of traces", "keep all traces for service Y", "why is my trace missing", "ingestion control", "adaptive sampling", "APM_TRACING sample rules", or "remote config sampling".
metadata:
  version: "1.0.0"
  author: datadog-labs
  repository: https://github.com/datadog-labs/agent-skills
  tags: datadog,apm,sampling,ingestion,adaptive-sampling,remote-config,dd-apm
  alwaysApply: "false"
  tools: pup
---

# APM Sampling

> **Before doing anything else:** Fully resolve every variable in `## Context to resolve before acting`. Do not skip Step 0 — sampling configurations are precedence-sensitive and the wrong mechanism is a top-three failure mode.

---

## How Sampling Works — Domain Knowledge

Read this before changing anything. The pipeline has multiple layers and the wrong layer wastes the customer's day.

### Two-stage pipeline

1. **Tracer / SDK** decides at the root span whether to keep or drop the trace, then propagates that decision in the trace context. Head-based — cannot be revisited downstream.
2. **Agent** receives traces from tracers and can additionally keep them via its priority sampler (auto rate), error sampler, or rare sampler.

A third layer at the backend — **retention filters** — decides which already-ingested traces stay searchable for 15 days. Retention filters are NOT sampling — handle those with `pup apm retention-filters`, not this skill.

> **APM stats are computed pre-sample and shipped separately.** `trace.<svc>.hits/errors/duration` stay 100% accurate at any sample rate. The customer's RED-metric dashboards don't break when you drop sample rate.

### Precedence — memorize this order

When multiple sampling sources conflict, the higher-numbered wins:

| Priority | Source | How it's set |
|---|---|---|
| 1 | **Remote resource-based sampling rules** | UI → Ingestion Control → "Manage Ingestion Rate" (`pup apm sampling-rules`) |
| 2 | **Adaptive sampling rules** | UI → Ingestion Control + onboarding (`pup apm adaptive-sampling`) |
| 3 | **Local `DD_TRACE_SAMPLING_RULES`** | Env var on the service |
| 4 | **Remote global sample rate** | Older RC mechanism |
| 5 | **Local `DD_TRACE_SAMPLE_RATE`** | Env var (deprecated — prefer rules) |
| 6 | **Agent target TPS** (`DD_APM_TARGET_TPS`) | Env var / `apm_config.target_traces_per_second` |

If a customer is "setting `DD_TRACE_SAMPLE_RATE=0.1` and nothing happens", check whether a UI rule at priority 1 or 2 is overriding it.

### Decision-maker tag `_dd.p.dm` — read this on the trace to know which mechanism fired

Append `?config_trace_show_hidden_metadata=true` to any trace URL in the UI to see `_dd.p.dm` and `ingestion_reason`:

| `_dd.p.dm` value | Mechanism | Means |
|---|---|---|
| `-0` | DEFAULT | Tracer started, no rates/rules yet |
| `-1` | AGENT_RATE | Agent's per-service rate (priority sampler) |
| `-3` | LOCAL_USER_RULE | `DD_TRACE_SAMPLING_RULES` matched |
| `-4` | MANUAL | `manual.keep` / `manual.drop` tag set in code |
| `-5` | APPSEC | ASM force-kept for a threat |
| `-8` | SPAN_SAMPLING_RATE | Span kept by `DD_SPAN_SAMPLING_RULES` (whole trace likely dropped) |
| `-11` | REMOTE_USER_RULE | UI-authored resource-based rule (`provenance:customer`) |
| `-12` | REMOTE_ADAPTIVE_RULE | Datadog-computed adaptive rule (`provenance:dynamic`) |
| `-13` | AI_GUARD | AI Guard kept the trace |

Sampling priority on a span (`_sampling_priority_v1`): `-1` UserDrop, `0` AutoDrop, `1` AutoKeep, `2` UserKeep.

> **Gotcha**: `_sampling_priority_v1=-1` does NOT mean the span was dropped if `_dd.span_sampling.*` tags are present — single-span sampling rescued it.

### Three things this skill changes — pick the right one

| Want to… | Use | Affects | Granularity |
|---|---|---|---|
| Set a specific rate for a (service, env, resource) | `pup apm sampling-rules` | Tracer (head-based) | per-resource glob, customer-authored |
| Let Datadog auto-tune rates to fit a byte/percent budget | `pup apm adaptive-sampling` | Tracer (head-based, Datadog-computed) | per-service automatically |
| Change agent's target TPS / error TPS / rare sampler | `pup apm agent-sampling` | Agent only (post-tracer) | org-wide or per-env |

If unsure, **start by diagnosing** — Step 0.

---

## Triggers

Invoke this skill when the user wants to:
- Set or change a sample rate for a service, env, or resource
- Drop more / fewer / specific traces
- Keep all traces for a service or resource
- Reduce APM ingestion volume / bill
- Onboard a service to adaptive sampling, or change the monthly allotment
- Change agent target TPS, error TPS, or enable/disable the rare sampler
- Diagnose why a sampling rule isn't being applied
- Understand why a trace is or isn't appearing
- Audit existing remote sampling rules in their org

Do NOT invoke this skill if:
- The user wants a **retention filter** (post-ingest, controls what stays searchable for 15d) → use `pup apm retention-filters`
- The user wants to **rename a service** → use the service-remapping skill
- The user wants to **drop entire traces in code** without changing rates → that's `manual.drop` / `Tracing.reject!` in the tracer, not a config change
- The user wants **single-span sampling** rules — that's a tracer env var (`DD_SPAN_SAMPLING_RULES`), not a remote config

---

## Prerequisites

### pup: check, install, authenticate

### Claude runs

```bash
pup --version || (brew tap datadog-labs/pack && brew install pup)
pup auth status || pup auth login
```

> `pup auth login` opens a browser tab for OAuth. Complete the login there.

### Permissions for write operations

All sampling writes require these Datadog permissions on the API key + app key (or the OAuth identity):

| Operation | Required permissions |
|---|---|
| Read any sampling config | `ApmRemoteConfigurationRead` (+ `ApmServiceIngestRead` for adaptive/agent) |
| Write resource-based rules | `ApmRemoteConfigurationWrite` |
| Write adaptive allotment / onboarding | `ApmServiceIngestWrite` + `ApmRemoteConfigurationWrite` |
| Write agent samplers | `ApmRemoteConfigurationWrite` |

If write commands fail with `403 Forbidden`, check the user's role grants these.

### Agent + tracer version gate (for remote sampling features)

| Component | Minimum |
|---|---|
| Datadog Agent | `7.42.0` (both resource-based and adaptive) |
| dd-trace-java | `1.34.0` |
| dd-trace-go | `1.64.0` |
| dd-trace-py | `2.9.0` |
| dd-trace-rb | `2.4.0` (Rack only) |
| dd-trace-js | `5.16.0` |
| dd-trace-php | `1.4.0` |
| dd-trace-dotnet | `2.53.2` |
| dd-trace-cpp | `0.2.2` |

If the customer's setup is below any of these, remote sampling rules will be silently ignored even if they appear in RC admin.

---

## Context to resolve before acting

| Variable | How to resolve |
|---|---|
| `ACTION` | One of: **diagnose**, **set-rate**, **set-adaptive**, **set-agent**. If the user described a symptom (e.g. "my rule isn't working"), start with **diagnose**. |
| `ENV` | Ask the user explicitly. Do NOT assume `prod` / `production` / `prd`. |
| `SERVICE` | Specific service to target. Use `pup apm services list --env <ENV>` if unclear. |
| `RESOURCE` | (For set-rate) Specific resource pattern, or `*` for whole service. |
| `RATE` | (For set-rate) 0.0–1.0. Anything `>1e-6` is honored. |
| `TARGET` | (For set-adaptive) Byte budget OR percent of allotment. |

---

## Step 0: Diagnose first (do this before any change)

Whatever the user is asking, run this to ground the conversation in what's actually happening.

### Claude runs

```bash
# 1. Confirm traces are flowing for the service
pup traces search --query "service:<SERVICE> env:<ENV>" --from 15m --limit 5

# 2. List the org's existing remote sampling rules
pup apm sampling-rules list --service <SERVICE> --env <ENV>

# 3. Is this service onboarded to adaptive?
pup apm adaptive-sampling onboarding-status --service <SERVICE> --env <ENV>

# 4. What are the current agent-side TPS targets?
pup apm agent-sampling get
```

Then ask the user for a trace ID and have them open it in the UI with the hidden-metadata trick:

> *"Open one of these traces in the UI and append `?config_trace_show_hidden_metadata=true` to the URL. What does `_dd.p.dm` show? And `ingestion_reason`?"*

Map their answer:

| `ingestion_reason` | What's currently sampling this trace |
|---|---|
| `remote_rule` | A customer resource-based rule (priority 1) — use `set-rate` to change |
| `adaptive_rule` | Adaptive sampling (priority 2) — use `set-adaptive` to change target |
| `rule` | Local `DD_TRACE_SAMPLING_RULES` (priority 3) — change the env var |
| `auto` | Agent priority sampler (priority 6) — use `set-agent` to change target TPS |
| `manual` | `manual.keep`/`manual.drop` in code — code change needed |
| `error` / `rare` | Agent error/rare sampler kept this trace — use `set-agent` |
| `single_span` | Whole trace dropped, span rescued by `DD_SPAN_SAMPLING_RULES` |

If the user said "my rule isn't taking effect" and `ingestion_reason` shows anything other than `remote_rule`, the rule isn't being applied — go to **Troubleshooting** below before writing more rules.

---

## Action: set-rate (customer per-resource head sampling rule)

This writes a `provenance:customer` rule to the `APM_TRACING` remote config product. It will appear with mechanism `_dd.p.dm:-11` on traces.

### Step 1: Build the rule

| Field | Notes |
|---|---|
| `--service` | Exact service name from `pup apm services list` |
| `--env` | Confirmed env (NEVER assume) |
| `--resource-glob` | `*` for whole service, or e.g. `GET /api/users`, `POST /checkout*`. First-match-wins ordering. |
| `--rate` | 0.0–1.0. `1e-6` is the floor of what's honored. |

### Step 2: Preview impact

### Claude runs

```bash
# Volume of traces this will affect
pup traces search --query "service:<SERVICE> env:<ENV> resource_name:<RESOURCE>" --from 1h --limit 1

# Existing rules — first match wins, so ordering matters
pup apm sampling-rules list --service <SERVICE> --env <ENV>

# Is this service onboarded to adaptive? Customer rules take precedence over adaptive but
# the ingested volume still counts against the allotment.
pup apm adaptive-sampling onboarding-status --service <SERVICE> --env <ENV>
```

Surface these to the user:

| Item | Why |
|---|---|
| Trace volume | Confirms the filter matches real traffic |
| Existing rules | The new rule may shadow or be shadowed by them (first match wins) |
| Adaptive status | If onboarded, customer rule still applies but eats the allotment |

### Step 3: Confirm and apply

> *"I'm going to set sampling rate for `<SERVICE>` env `<ENV>` resource `<RESOURCE>` to **<RATE>** (mechanism: remote customer rule). This will take effect within 30 seconds of the next tracer RC poll. Ready?"*

### Claude runs

```bash
pup apm sampling-rules create \
  --service "<SERVICE>" \
  --env "<ENV>" \
  --resource-glob "<RESOURCE>" \
  --rate <RATE>
```

Record the returned `id` and `version` — needed for update/delete.

### Step 4: Verify (wait 60–90 seconds)

### Claude runs

```bash
# Tracer RC poll happens every 5s by default — give it 60s, then check a fresh trace
pup traces search --query "service:<SERVICE> env:<ENV> resource_name:<RESOURCE>" --from 2m --limit 3
```

Have the user open a recent trace with `?config_trace_show_hidden_metadata=true` and confirm:
- `ingestion_reason: remote_rule`
- `_dd.p.dm: -11`

If `_dd.p.dm` still shows `-0`, `-1`, `-3`, or `-12` after 2 minutes → **Troubleshooting** below.

---

## Action: set-adaptive (let Datadog auto-tune rates)

Onboards a service so Datadog computes per-resource sampling rates to fit the configured byte/percent allotment. Rules appear with mechanism `_dd.p.dm:-12`.

### Step 1: Check current state and budget

### Claude runs

```bash
pup apm adaptive-sampling get-allotment
pup apm adaptive-sampling check                    # is allotment sufficient for current traffic?
pup apm adaptive-sampling onboarding-status --service <SERVICE> --env <ENV>
```

Allotment formula: `150GB × #APM_hosts + 10GB × #Fargate_tasks + 50GB × #M-traces`. If `check` reports the customer is over budget, sampling rates will floor at **1 trace per 5 minutes per (service, env, resource)** — surface this before promising results.

### Step 2: Preview the rates Datadog would set

### Claude runs

```bash
pup apm adaptive-sampling preview --service <SERVICE> --env <ENV>
```

Show the user the predicted per-resource rates. If they look wrong, do not onboard — investigate first.

### Step 3: Confirm and apply

> *"I'm going to onboard `<SERVICE>` env `<ENV>` to adaptive sampling. Datadog will recompute per-resource rates every 5–10 minutes to fit your monthly allotment. Existing manual `DD_TRACE_SAMPLING_RULES` will still take precedence. Ready?"*

### Claude runs

```bash
pup apm adaptive-sampling onboard --service <SERVICE> --env <ENV>
```

### Step 4: Verify

Adaptive rules are computed on a 5–10 minute cycle. Wait at least one full cycle, then:

### Claude runs

```bash
pup apm sampling-rules list --service <SERVICE> --env <ENV>     # adaptive rules show provenance:dynamic
```

Have the user open a trace with `?config_trace_show_hidden_metadata=true` and confirm `_dd.p.dm: -12`.

---

## Action: set-agent (priority / error / rare TPS — affects whole org or whole env)

This writes the `APM_SAMPLING` remote config product. Affects every agent in the org (or env). Be careful — it's a blunt instrument compared to resource-based rules.

### Claude runs

```bash
pup apm agent-sampling get          # show current values, all envs vs by-env
```

### Confirm before applying

> *"I'm going to set agent target TPS to **<N>** (priority sampler), error TPS to **<E>**, rare sampler **<enabled/disabled>** for **<scope>**. This affects every datadog-agent in scope and is org-wide unless you specify `--env`. Ready?"*

### Claude runs

```bash
# All environments
pup apm agent-sampling set --target-tps <N> --errors-tps <E> --rare <true|false>

# Per-env override
pup apm agent-sampling set --env <ENV> --target-tps <N> --errors-tps <E> --rare <true|false>
```

### Verify

Agent 7.42.0+ exposes its current target TPS as a metric on traces. Search a recent trace for `_dd.agent_priority_sampler.target_tps` to confirm the new value is in effect.

---

## Troubleshooting

When a rule was created but `_dd.p.dm` doesn't reflect it. Walk these in order — they're ordered by frequency.

### 1. `DD_ENV` not set on the application OR the agent

The agent fetches RC config keyed by `env`. If either is missing, no config is fetched and the "Remote" option appears grayed out in the UI.

Fix: Set `DD_ENV` on the application AND ensure `apm_config.env` (or `DD_ENV` on the agent) matches.

### 2. Tracer or agent below minimum version

See prerequisites. Older versions silently ignore RC sampling.

Diagnose:
```bash
pup fleet agents list | grep <HOSTNAME>     # agent version
# For tracer: ask user for startup log or check DATADOG TRACER CONFIGURATION log line
```

### 3. Resource name modified after sampling decision (Java `TraceInterceptor`, Ruby post-processing)

The sampling decision is made when the root span starts. If a `TraceInterceptor` rewrites the resource name later, RC rules that match on the original name won't apply.

Diagnose: Compare the resource name in tracer debug logs at flush vs the rule's resource glob.

Fix: Remove the interceptor, or match the rule to the final resource name.

Reference: `APMS-14373`.

### 4. `DD_REMOTE_CONFIG_POLL_INTERVAL_SECONDS` set too high

Default 5s. Higher values cause the agent to clear its cache between polls and re-apply rules as new, leading to inconsistent application across hosts.

Fix: Remove the override or set back to `5`.

Reference: `APMS-16332`.

### 5. Python: in-code APM port change

If the customer changed APM port in their code (not via `DD_TRACE_AGENT_PORT`), the tracer can't discover the RC endpoint. Tracer logs: `"Agent is down or Remote Config is not enabled in the Agent."`

Fix: Use `DD_TRACE_AGENT_PORT` env var.

### 6. Ruby tracer: Rack only

The Ruby tracer only applies RC sampling to Rack requests. Background jobs / Sidekiq workers won't receive RC rules. No workaround in the tracer — use local `DD_TRACE_SAMPLING_RULES` for those.

### 7. Adaptive monthly target lower than month-to-date ingested volume

Sampling rates drop to the 1-per-5-min floor. The system aggressively reduces rates to try to meet an already-exceeded target.

Fix: Raise the target above current month-to-date volume, or wait for the new month.

### 8. Service overrides (e.g. `kafka`, `aws-sdk`, `grpc-client`)

These integration-generated service names cannot be targeted by RC sampling rules. The "Remote" option will be grayed out for them.

Fix: Toggle the `apm-remove-integration-service-overrides` feature flag (Datadog support can do this), or target the actual root service.

### Where to escalate

| Symptom | Channel |
|---|---|
| RC admin shows the rule but tracer never applies | tracer team (`#dd-trace-<lang>`) |
| Agent not connecting to RC | `#support-remote-config` |
| Adaptive rates look wrong / API error | `#apm-trace-intake` |

---

## Managing existing rules

### List

### Claude runs

```bash
pup apm sampling-rules list                         # all rules
pup apm sampling-rules list --service <SERVICE>     # filter by service
pup apm sampling-rules list --env <ENV>             # filter by env
```

### Update

Updates require the rule's current `version` from the list/get output:

### Claude runs

```bash
pup apm sampling-rules update --id <ID> --rate <NEW_RATE> --version <VERSION>
```

ERROR: `409 Conflict` — rule was modified since you fetched it. Re-fetch and retry.

### Delete

Show the user the rule before deleting:

### Claude runs

```bash
pup apm sampling-rules get --id <ID>
# confirm with user, then:
pup apm sampling-rules delete --id <ID> --version <VERSION>
```

### Audit the org

For a full read-only overview when the customer asks "what sampling is even happening in my org":

### Claude runs

```bash
pup apm sampling-rules list
pup apm adaptive-sampling get-allotment
pup apm adaptive-sampling onboarding-status
pup apm agent-sampling get
```

---

## Done

Exit when ALL of the following are true:
- [ ] Diagnosed which mechanism is currently sampling the user's traces (or confirmed there is no relevant mechanism yet)
- [ ] Picked the right action (`set-rate`, `set-adaptive`, or `set-agent`) for the user's goal
- [ ] User confirmed the planned change before any write
- [ ] Write succeeded and an `id` was returned
- [ ] Verified `_dd.p.dm` on a fresh trace reflects the new mechanism (or set expectation: adaptive needs one 5–10 min cycle)
- [ ] Surfaced relevant gotchas (precedence, allotment floor, version gates) if they apply

---

## Security constraints

- Never hardcode `DD_API_KEY` or `DD_APP_KEY` into files or chat messages — always use environment variables
- Never write a sampling rule without explicit user confirmation — show the full rule first
- Never assume `prod` / `production` as the environment — always confirm with the user
- Never run `set-agent` without confirming scope (all envs vs single env) — it affects every agent in scope
- Never delete a rule without showing the user the rule's current value first
- Never set a sample rate below `1e-6` — it floors silently and confuses the customer
- Never recommend `DD_REMOTE_CONFIG_POLL_INTERVAL_SECONDS` higher than `5` — it breaks RC consistency

---

## See also

- `RESEARCH.md` (this directory) — internal architecture, RC product schemas, decision-maker IDs, all source citations
- `dd-apm/service-remapping/SKILL.md` — for renaming services (not changing their sample rate)
- `pup apm retention-filters` — for post-ingest searchability (15-day retention), not sampling
- [docs.datadoghq.com/tracing/trace_pipeline/ingestion_controls](https://docs.datadoghq.com/tracing/trace_pipeline/ingestion_controls/)
- [docs.datadoghq.com/tracing/trace_pipeline/adaptive_sampling](https://docs.datadoghq.com/tracing/trace_pipeline/adaptive_sampling/)
- RC admin (internal): `https://remote-config-admin.us1.prod.dog/` → product `APM_TRACING` or `APM_SAMPLING`
