---
name: sampling
description: Diagnose and change APM trace sampling — set per-resource rates, configure adaptive sampling, adjust agent samplers (priority/error/rare TPS), or figure out why a sampling rule isn't taking effect. Use for any request involving "change sample rate", "drop X% of traces", "keep all traces for service Y", "why is my trace missing", "ingestion control", "adaptive sampling", "APM_TRACING sample rules", or "remote config sampling".
metadata:
  version: "1.0.4"
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
>
> **How that works under the hood**: even when a trace is decided to be dropped, the tracer still flushes all its spans to the local Datadog Agent. The Agent computes the unsampled APM stats over everything it receives, *then* drops the spans for ingestion if the priority is 0 or -1. So sampling decisions only affect what reaches the Datadog backend as traces — they never affect the stats stream.

### Precedence — memorize this order

When multiple sampling sources conflict, **lower-numbered priority wins** (priority 1 overrides all others):

| Priority | Source | How it's set |
|---|---|---|
| 1 | **Manual** (`manual.keep` / `manual.drop`) | Code change — `span.set_tag(MANUAL_KEEP)` / `Tracing.keep!` etc. Overrides everything else. |
| 2 | **Remote resource-based sampling rules** | UI → Ingestion Control → "Manage Ingestion Rate" (`pup apm sampling-rules`) |
| 3 | **Adaptive sampling rules** | UI → Ingestion Control + onboarding (`pup apm adaptive-sampling`) |
| 4 | **Local `DD_TRACE_SAMPLING_RULES`** | Env var on the service |
| 5 | **Remote global sample rate** | Older RC mechanism |
| 6 | **Local `DD_TRACE_SAMPLE_RATE`** | Env var (deprecated — prefer rules) |
| 7 | **Agent target TPS** (`DD_APM_TARGET_TPS`) | Env var / `apm_config.target_traces_per_second` |

If a customer is "setting `DD_TRACE_SAMPLE_RATE=0.1` and nothing happens", check whether a `manual.keep` in code, a UI rule (priority 2), or adaptive sampling (priority 3) is overriding it.

### Decision-maker tag `_dd.p.dm` — read this on the trace to know which mechanism fired

Append `?config_trace_show_hidden_metadata=true` to any trace URL in the UI to see `_dd.p.dm` and `ingestion_reason`:

| `_dd.p.dm` value | Mechanism | Means |
|---|---|---|
| `-0` | DEFAULT | Tracer started, no rates/rules yet |
| `-1` | AGENT_RATE | Agent's per-service rate (priority sampler) |
| `-3` | LOCAL_USER_RULE | `DD_TRACE_SAMPLING_RULES` matched |
| `-4` | MANUAL | `manual.keep` / `manual.drop` tag set in code |
| `-5` | APPSEC | ASM force-kept for a threat |
| n/a — see note | SPAN_SAMPLING_RATE (`8`) | Single-span sampling. Value `8` lives in the NUMERIC tag `_dd.span_sampling.mechanism`, NOT in the string tag `_dd.p.dm`. If you see `_dd.span_sampling.mechanism: 8` on a span, it was kept by `DD_SPAN_SAMPLING_RULES` even though the enclosing trace was dropped. |
| `-11` | REMOTE_USER_RULE | UI-authored resource-based rule (`provenance:customer`) |
| `-12` | REMOTE_ADAPTIVE_RULE | Datadog-computed adaptive rule (`provenance:dynamic`) |
| `-13` | AI_GUARD | AI Guard kept the trace (dd-trace-java only; not yet in all tracer SDKs) |

Sampling priority on a span (`_sampling_priority_v1`): `-1` UserDrop, `0` AutoDrop, `1` AutoKeep, `2` UserKeep.

> **Gotcha**: `_sampling_priority_v1=-1` does NOT mean the span was dropped if `_dd.span_sampling.*` tags are present — single-span sampling rescued it.

### Two things this skill changes — pick the right one

| Want to… | Use | Affects | Granularity |
|---|---|---|---|
| Set a specific rate for a (service, env, resource) | `pup apm sampling-rules` | Tracer (head-based) | per-resource glob, customer-authored |
| Let Datadog auto-tune rates to fit a byte/percent budget | `pup apm adaptive-sampling` | Tracer (head-based, Datadog-computed) | per-service automatically |

If unsure, **start by diagnosing** — Step 0.

> **Agent-side TPS (`DD_APM_TARGET_TPS`, `DD_APM_ERROR_TPS`, `DD_APM_ENABLE_RARE_SAMPLER`)** is not yet covered by a pup command. For that, point users to the Ingestion Control UI ("Remotely Configure Agent Ingestion") or the agent config flags directly.

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

### Permissions for read/write operations

Every sampling action needs the user's Datadog **role** to grant the underlying permission — not just the OAuth scope on the token. OAuth scope == "this token is *allowed to request* this permission"; the role membership is what actually grants it. Both must be present for the request to succeed.

| Operation | Required permissions on the user's role |
|---|---|
| Read sampling rules / adaptive status | `apm_remote_configuration_read` (+ `apm_service_ingest_read` for adaptive) |
| Write resource-based rules (`create`/`update`/`delete`) | `apm_remote_configuration_write` |
| Write adaptive allotment / onboarding | `apm_service_ingest_write` + `apm_remote_configuration_write` |

**The Datadog Admin Role grants all of these by default.** Standard and custom roles often don't.

If the user is unsure whether they have the permission, the diagnostic is simple: any read command (Step 0) will succeed → they have at least read. A write command failing with `403 Forbidden` + body `"Failed permission authorization checks"` → they have the scope on their token but their role doesn't grant the permission.

> ⚠️ The user **cannot grant themselves** the permission. An admin in their Datadog org goes to *Organization Settings → Roles → (the user's role) → Permissions* and ticks the box. If the user gets a 403, surface this immediately rather than retrying the command. See Troubleshooting #9.

### Agent + tracer version gate (for remote sampling features)

The minimum versions differ between the two features. Use the right column for the action you're taking.

| Component | Resource-based rules (`set-rate`) | Adaptive sampling (`set-adaptive`) |
|---|---|---|
| Datadog Agent | `7.41.1` | `7.53.0` |
| dd-trace-java | `1.34.0` | `1.34.0` |
| dd-trace-go | `1.64.0` | `1.68.0` |
| dd-trace-py | `2.9.0` | `2.9.6` |
| dd-trace-rb | `2.4.0` (Rack only) | `2.0.0` (Rack only) |
| dd-trace-js | `5.16.0` | `5.16.0` |
| dd-trace-php | `1.4.0` | `1.4.0` |
| dd-trace-dotnet | `2.53.2` | `2.54.0` |
| dd-trace-cpp | `0.2.2` | `0.2.2` |

If the customer's setup is below these minimums, remote sampling rules will be silently ignored even if they appear in RC admin. Adaptive sampling additionally requires Remote Configuration to be enabled on the Agent (`remote_configuration.enabled: true` — the default since Agent 7.47.0).

---

## Context to resolve before acting

| Variable | How to resolve |
|---|---|
| `ACTION` | One of: **diagnose**, **set-rate**, **set-adaptive**. If the user described a symptom (e.g. "my rule isn't working"), start with **diagnose**. (Agent-side TPS changes — `DD_APM_TARGET_TPS` etc. — aren't yet exposed via pup; redirect to the Ingestion Control UI.) |
| `ENV` | Ask the user explicitly. Do NOT assume `prod` / `production` / `prd`. |
| `SERVICE` | Specific service to target. Use `pup apm services list --env <ENV>` if unclear. |
| `RESOURCE` | (For set-rate) Specific resource pattern, or `*` for whole service. |
| `RATE` | (For set-rate) 0.0–1.0. Anything `>1e-6` is honored. |
| `TARGET` | (For set-adaptive) Byte budget OR percent of allotment. |

> **When a variable is missing:** Ask for it AND simultaneously present the full proposed plan with what you already know — use `<ENV>`, `<SERVICE>`, etc. as placeholders where values are unknown. Do NOT wait silently for the missing variable before showing the plan. The user should see exactly what will be done so they can confirm it along with providing the missing information.

---

## Step 0: Diagnose first (mandatory — do not skip)

> ⛔ **Do not skip Step 0.** Even when the action seems obvious, you must run the diagnosis commands before any write. Sampling configurations are precedence-sensitive — a remote rule or adaptive configuration may already exist that changes what you should do.

Whatever the user is asking, run this to ground the conversation in what's actually happening.

### Claude runs

```bash
# 1. Confirm traces are flowing for the service
pup traces search --query "service:<SERVICE> env:<ENV>" --from 15m --limit 5

# 2. List the org's existing remote sampling rules
pup apm sampling-rules list --service <SERVICE> --env <ENV>

# 3. Is this service onboarded to adaptive?
pup apm adaptive-sampling onboarding-status --service <SERVICE> --env <ENV>
```

> **Interpreting the output:**
> - **Command 2 returning `HTTP 404` is not an error** — it means no remote sampling rules exist for this (service, env) yet. That's the expected empty state on a clean service. The backend's `by_target` endpoint returns 404 instead of an empty list. Continue with Step 0; don't treat this as broken.
> - **Command 1 returning zero traces** means the service hasn't sent traces in the window. Either the service isn't running, traces aren't reaching the Agent, or the env tag is different — not a sampling-skill problem; redirect to APM install/onboarding troubleshooting.

Then ask the user for a trace ID and have them open it in the UI with the hidden-metadata trick:

> *"Open one of these traces in the UI and append `?config_trace_show_hidden_metadata=true` to the URL. What does `_dd.p.dm` show? And `ingestion_reason`?"*

> **`_dd.p.dm` is the authoritative signal.** `ingestion_reason` is helpful context but tracers emit `rule` for both local and remote rules — the only reliable way to tell them apart is `_dd.p.dm` (`-3` = local rule, `-11` = remote customer rule, `-12` = remote adaptive rule). The UI's hidden-metadata view may display more granular labels (`remote_rule`, `adaptive_rule`) computed by the Datadog backend, but those are UI representations, not the raw span-tag value emitted by the tracer.
>
> **Where to read `ingestion_reason` from**: the UI is the cleanest source (with the URL trick above). If you're reading from `pup traces search` output directly, the real value is nested inside `span_tags` (e.g., `"ingestion_reason:auto"` as a string entry in the tag list) — NOT in the top-level `"ingestion_reason"` field, which pup leaves empty in many responses.

Map their answer (the `_dd.p.dm` column is definitive; `ingestion_reason` values shown here are as displayed in the UI hidden-metadata view):

| `_dd.p.dm` | `ingestion_reason` (UI) | What's currently sampling this trace |
|---|---|---|
| `-11` | `remote_rule` | A customer resource-based rule (priority 2) — use `set-rate` to change |
| `-12` | `adaptive_rule` | Adaptive sampling (priority 3) — use `set-adaptive` to change target |
| `-3` | `rule` | Local `DD_TRACE_SAMPLING_RULES` (priority 4) — change the env var |
| `-1` | `auto` | Agent priority sampler (priority 7) — change via Ingestion Control UI or `DD_APM_TARGET_TPS` on the agent (not pup) |
| `-4` | `manual` | `manual.keep`/`manual.drop` in code — code change needed |
| n/a | `error` / `rare` | Agent error/rare sampler kept this trace — change via `DD_APM_ERROR_TPS` / `DD_APM_ENABLE_RARE_SAMPLER` on the agent (not pup) |
| `-8` (in `_dd.span_sampling.mechanism`) | `single_span` | Whole trace dropped, span rescued by `DD_SPAN_SAMPLING_RULES` |

If the user said "my rule isn't taking effect" and `_dd.p.dm` shows anything other than `-11` (or UI shows anything other than `remote_rule`), the rule isn't being applied — go to **Troubleshooting** below before writing more rules.

> **If Step 0 commands fail or return errors** (auth error, connection refused, 404, empty output): note what you attempted and the error, then **proceed immediately to the action step**. Diagnostic failures mean "this information wasn't available" — they do not block the workflow. Continue with the best information you have and still show the user the full proposed change and confirmation message.

---

## Action: set-rate (customer per-resource head sampling rule)

This writes a `provenance:customer` rule to the `APM_TRACING` remote config product. It will appear with mechanism `_dd.p.dm:-11` on traces.

### Step 1: Build the rule

| Field | Notes |
|---|---|
| `--service` | Exact service name from `pup apm services list` |
| `--env` | Confirmed env (NEVER assume) |
| `--resource` | `*` for whole service, or e.g. `GET /api/users`, `POST /checkout*`. First-match-wins ordering. |
| `--sample-rate` | 0.0–1.0. `1e-6` is the floor of what's honored. |

### Step 2: Preview impact

### Claude runs

```bash
# Volume of traces this will affect
pup traces search --query "service:<SERVICE> env:<ENV> resource_name:<RESOURCE>" --from 1h --limit 1

# Existing rules — first match wins, so ordering matters.
# HTTP 404 here means "no rules configured for this target yet" — expected on first-time setup.
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

> **If any of these commands fail:** note the error and proceed to Step 3 anyway. Missing diagnostic data does not block the confirmation — present the proposed rule and ask for confirmation regardless.

### Step 3: Confirm and apply

> *"I'm going to set sampling rate for `<SERVICE>` env `<ENV>` resource `<RESOURCE>` to **<RATE>** (mechanism: remote customer rule). This will take effect within 30 seconds of the next tracer RC poll. Note: this requires `apm_remote_configuration_write` on your Datadog role — Admin role has it by default, others may not. If it fails with `403 Forbidden`, see Troubleshooting #9. Ready?"*

### Claude runs

```bash
pup apm sampling-rules create \
  --service "<SERVICE>" \
  --env "<ENV>" \
  --resource "<RESOURCE>" \
  --sample-rate <RATE>
```

Record the returned `id` from the response — needed for update/delete.

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

> **Always surface these two facts when explaining adaptive sampling — do not omit either:**
> 1. **Floor warning**: If the monthly allotment target is below the current month-to-date ingestion volume, adaptive sampling rates will floor at **roughly 1 trace per 5 minutes per (service, env, resource)** — this aggressively reduces visibility. The user must know this before onboarding.
> 2. **Precedence note**: Existing local `DD_TRACE_SAMPLING_RULES` env vars on the service **still take precedence over adaptive rules** (priority 4 > priority 3). If the service has `DD_TRACE_SAMPLING_RULES` set, adaptive sampling will be silently overridden for those resources.

### Step 1: Check current state and budget

### Claude runs

```bash
pup apm adaptive-sampling get-allotment
pup apm adaptive-sampling check                    # is allotment sufficient for current traffic?
pup apm adaptive-sampling onboarding-status --service <SERVICE> --env <ENV>
```

Allotment formula: `150GB × #APM_hosts + 10GB × #Fargate_tasks + 50GB × #serverless_invocations`. If `check` reports the customer is over budget, sampling rates will floor at **1 trace per 5 minutes per (service, env, resource)** — surface this before promising results.

> **If these commands fail:** note the error and proceed to Step 2 anyway. You cannot gate the confirmation on having live allotment data. Still warn the user: "If your current month-to-date ingestion is already above your monthly allotment, adaptive rates will floor at roughly 1 trace per 5 minutes per (service, env, resource)."

### Step 2: Confirm and apply

> *"I'm going to onboard `<SERVICE>` env `<ENV>` to adaptive sampling. Datadog will recompute per-resource rates every 5–10 minutes to fit your monthly allotment. Existing manual `DD_TRACE_SAMPLING_RULES` will still take precedence. Note: this requires both `apm_service_ingest_write` AND `apm_remote_configuration_write` on your Datadog role — Admin role has them by default. If it fails with `403 Forbidden`, see Troubleshooting #9. Ready?"*

### Claude runs

```bash
pup apm adaptive-sampling onboard --service <SERVICE> --env <ENV>
```

### Step 3: Verify

Adaptive rules are computed on a 5–10 minute cycle. Wait at least one full cycle, then:

### Claude runs

```bash
pup apm sampling-rules list --service <SERVICE> --env <ENV>     # adaptive rules show provenance:dynamic
```

Have the user open a trace with `?config_trace_show_hidden_metadata=true` and confirm `_dd.p.dm: -12`.

---

## Action: agent-side TPS (priority / error / rare samplers) — not yet in pup

Org-wide agent sampler tuning (`APM_SAMPLING` remote config product) is **not yet exposed via pup**. If the user actually needs to change agent target TPS, error TPS, or the rare sampler:

- **UI path**: Ingestion Control page → "Remotely Configure Agent Ingestion" button. Requires `ApmRemoteConfigurationWrite` permission and Agent ≥ 7.42.0.
- **Local-config alternative**: set `DD_APM_TARGET_TPS`, `DD_APM_ERROR_TPS`, `DD_APM_ENABLE_RARE_SAMPLER` env vars (or the matching keys in `datadog.yaml`) on the agent and restart. Affects only that agent.

Verify either path with Agent ≥ 7.42.0 by searching a recent trace for `_dd.agent_priority_sampler.target_tps` — the metric reflects the current value the agent is using.

> **Don't roll your own pup command** for this. The endpoint lives at `/api/ui/remote_config/products/apm_sampling/samplerconfig`. It's a real follow-up for pup but out of scope for this skill's current capabilities.

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

### 9. `403 Forbidden` — "Failed permission authorization checks"

This is by far the most-misdiagnosed failure on writes. The error string is `403 Forbidden — Failed permission authorization checks` — that exact phrase means the user's OAuth token was accepted but their Datadog **role** doesn't grant the underlying permission.

#### The red herring to call out explicitly

A user (or a model) will often run `pup auth status`, see `apm_remote_configuration_write` in the scope list, and conclude they have access. **They don't.** OAuth scopes are a **ceiling**, not a grant.

- **Scope on token (visible in `pup auth status`)** = "this token is *allowed to request* this permission"
- **Permission on the user's role (in Org Settings → Roles)** = "this user *has* the permission"

The server checks both independently on every request. Seeing the scope in `pup auth status` is necessary but **not sufficient**. If you're walking a customer through this:

1. **Stop them from trying the command again** with different env vars or re-logging in — none of that changes their role.
2. Confirm the error string is exactly `Failed permission authorization checks` (vs `invalid_token`, which would mean AuthN/OAuth failure — different problem).
3. Then go to the fix below.

#### Which permission is needed

| Action attempted | Required Datadog permission |
|---|---|
| Read sampling rules / adaptive status | `apm_remote_configuration_read` |
| Write sampling rules (`create`/`update`/`delete`) | `apm_remote_configuration_write` |
| Read adaptive sampling allotment | `apm_service_ingest_read` |
| Write adaptive sampling (`onboard`, `set-allotment`) | `apm_service_ingest_write` |

#### Fix

An admin in the user's Datadog org needs to add the permission to their role:

> Organization Settings → Roles → *(user's role)* → Permissions → tick the required permission(s) → Save.

The **Datadog Admin Role** grants all four of these by default. The standard **Datadog Standard Role** and custom roles often don't.

The user **cannot grant themselves** the permission. They have to ask their org admin. If they're unsure who that is, point them at their internal Datadog support channel.

### 10. "My traces are disappearing" — Agent CPU/memory self-throttling

The Datadog Agent has an internal rate limiter tied to its CPU and memory usage. When the Agent exceeds the configured limit, it **silently drops trace payloads** (returns `200` to the tracer so nothing is retried) until usage drops back below the threshold.

This is independent of every sampling mechanism above — it can drop already-kept traces simply because the Agent is starved for resources.

Defaults that often trigger this:
- `DD_APM_MAX_CPU_PERCENT` = `50` (half a core)
- `DD_APM_MAX_MEMORY` = `5e8` (500 MB)

If the Agent reaches `1.5 × DD_APM_MAX_MEMORY`, it kills itself outright. Watch for `datadog.trace_agent.receiver.oom_kill` metric for confirmation.

**Diagnose:** look for the tag `_dd1.sr.rapre` on a trace — if present, the Agent's pre-sampler dropped some portion of that payload. Also check the metric `datadog.trace_agent.receiver.payload_refused` over time, broken down by host.

**Fix:** for Agents running as a dedicated service (their own host, k8s pod, etc.) the defaults are too low. Either:
- Raise: `DD_APM_MAX_CPU_PERCENT=0` and `DD_APM_MAX_MEMORY=0` (both disable the throttle)
- Or set realistic limits well above observed usage
- Or move to a larger Agent host / pod

The defaults are designed as a safety net so the Agent never starves the application it's monitoring — they're tuned for the sidecar-on-the-app-host case, not the dedicated-Agent case.

### Where to escalate

| Symptom | Channel |
|---|---|
| `403 Forbidden` / "Failed permission authorization checks" | The user's Datadog org admin (NOT Datadog support — this is a role-membership issue, not a Datadog-side bug) |
| RC admin shows the rule but tracer never applies | tracer team (`#dd-trace-<lang>`) |
| Agent not connecting to RC | `#support-remote-config` |
| Adaptive rates look wrong / API error | `#apm-trace-intake` |

---

## Managing existing rules

### List

### Claude runs

```bash
pup apm sampling-rules list                                  # all rules
pup apm sampling-rules list --service <SERVICE> --env <ENV>  # narrow to one target (both flags required together)
```

> Note: `list` with only `--service` or only `--env` falls back to the unfiltered list — the by_target endpoint requires both.
>
> Also: when both flags are passed and no rules exist for that (service, env) yet, the backend returns `HTTP 404` rather than an empty list. That's the expected empty state — treat it as "no rules configured yet", not as a failure. The unfiltered `list` returns an empty array in the same situation.

### Update

`update` REPLACES all attributes — pass every field, not just the changing one. The `id` is positional, not a flag.

### Claude runs

```bash
pup apm sampling-rules update <ID> \
  --service <SERVICE> \
  --env <ENV> \
  --resource <RESOURCE> \
  --sample-rate <NEW_RATE>
```

### Delete

Show the user the rule before deleting:

### Claude runs

```bash
pup apm sampling-rules get <ID>
# confirm with user, then:
pup apm sampling-rules delete <ID>
```

### Audit the org

For a full read-only overview when the customer asks "what sampling is even happening in my org":

### Claude runs

```bash
pup apm sampling-rules list
pup apm adaptive-sampling get-allotment
pup apm adaptive-sampling check
pup apm adaptive-sampling onboarding-status --service <SERVICE> --env <ENV>  # repeat per service of interest
```

(Agent-side TPS — `DD_APM_TARGET_TPS` etc. — isn't enumerable via pup yet; check the Ingestion Control UI if needed.)

---

## Done

Exit when ALL of the following are true:
- [ ] Diagnosed which mechanism is currently sampling the user's traces (or confirmed there is no relevant mechanism yet)
- [ ] Picked the right action (`set-rate` or `set-adaptive`) for the user's goal
- [ ] User confirmed the planned change before any write
- [ ] Write succeeded — for `set-rate`: an `id` was returned; for `set-adaptive`: `onboard` command returned without error
- [ ] Verified `_dd.p.dm` on a fresh trace reflects the new mechanism (or set expectation: adaptive needs one 5–10 min cycle)
- [ ] Surfaced relevant gotchas (precedence, allotment floor, version gates) if they apply
- [ ] If the user needed agent-side TPS changes, redirected them to the Ingestion Control UI (pup doesn't cover that path yet)

---

## Security constraints

- Never hardcode `DD_API_KEY` or `DD_APP_KEY` into files or chat messages — always use environment variables
- Never write a sampling rule without explicit user confirmation — show the full rule first
- Never assume `prod` / `production` as the environment — always confirm with the user
- Never run `adaptive-sampling set-allotment` without confirming scope — it changes the org-wide monthly target and floors low-traffic resources to 1 trace per 5 minutes if the target is too low
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
