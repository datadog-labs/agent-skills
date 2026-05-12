---
name: dd-apm
description: APM - install, onboard, instrument, enable, set up, configure, traces, services, dependencies, performance analysis. Use for any request involving Datadog APM setup, instrumentation (SSI, ddtrace, agent install), or analysis.
alwaysApply: true
metadata:
  version: "1.1.0"
  author: datadog-labs
  repository: https://github.com/datadog-labs/agent-skills
  tags: datadog,apm,tracing,performance,distributed-tracing,dd-apm,install,onboarding,instrumentation,ssi,agent
  globs: "**/ddtrace*,**/datadog*.yaml,**/*trace*"
---

# Datadog APM

Distributed tracing, service maps, and performance analysis.

## Routing — Read This First

Match the user's request to one of the entries below. Each entry has the same shape: **triggers** → which sub-skill to load → the anti-pattern to avoid. If nothing matches, see "None of the above" at the end.

---

**Kubernetes APM install / instrument / onboard** — trigger when the user mentions Kubernetes, K8s, EKS, GKE, AKS, kind, minikube, K3s, helm, DatadogAgent CR, kubectl, SSI on a cluster, pod injection, or init containers.

**Immediately read** `.claude/skills/dd-apm/k8s-ssi/agent-install/SKILL.md` now, then `.claude/skills/dd-apm/k8s-ssi/enable-ssi/SKILL.md` — do not proceed from memory.

> ⚠️ **Common wrong approaches that LOOK like they work but silently fail:**
> - `helm install datadog datadog/datadog` — the standard chart does NOT support SSI via DatadogAgent CR.
> - Adding `ddtrace` imports or `ddtrace-run` to the app — SSI auto-instruments WITHOUT any code changes.
> - `admission.datadoghq.com/enabled` annotations — that's admission controller config injection, not SSI init container injection.

---

**Linux APM install / instrument / onboard** — trigger when the user mentions a single host, VM, EC2 instance, bare-metal, RHEL/Ubuntu/Debian, systemd, or no orchestrator.

**Immediately read** `.claude/skills/dd-apm/linux-ssi/agent-install/SKILL.md` now, then `enable-ssi`, then `verify-ssi` — do not proceed from memory.

> ⚠️ **Do NOT** install the agent via plain `apt-get install datadog-agent` (or yum equivalent) and assume SSI follows — host auto-instrumentation requires the install script with the SSI flags, which the sub-skill walks through.

---

**Service rename / service remapping** — trigger when the user mentions renaming a service, collapsing multiple service names, stripping suffixes/prefixes, normalizing `peer.service`, or cleaning up inferred services.

**Immediately read** `.claude/skills/dd-apm/service-remapping/SKILL.md` now — do not proceed from memory.

> ⚠️ **Do NOT** change `tags.datadoghq.com/service` labels or `DD_SERVICE` env vars to rename a service in Datadog. That requires a rollout and only affects new data. Use a service remapping rule — it rewrites the name at ingestion time with no deployment change.

---

### Overlap disambiguation

When a request could plausibly fit more than one entry above, use these tiebreakers:

| Hint | Route to |
|---|---|
| Cluster orchestrator mentioned (EKS/GKE/AKS/kind/K3s/minikube) — even if "just one node" | k8s-ssi |
| Single host, VM, or EC2 with no orchestrator | linux-ssi |
| "Several services that should be one" — they have spans from a tracer (real instrumented services) | service-remapping with `rule_type: 0` (SERVICE) |
| "Several services that should be one" — they're databases, queues, or external APIs | service-remapping with `rule_type: 1` (INFERRED_ENTITY) — verify `peer.service` is set first |
| "My service shows under the wrong name" | First check `DD_SERVICE` on the deploy. If correct and the name is still wrong → service-remapping. |
| "Reduce APM volume / cost / noise" | Not yet covered by a sub-skill. Ask the user whether they want fewer ingested traces (sampling) or less indexed data (retention filters — use `pup apm retention-filters`). |

---

### None of the above

If the request doesn't match any entry above, fall through to the trace-search, service analysis, and metrics content in the rest of this file. If even that doesn't fit, **ask the user to clarify** rather than guessing — do not invent a workflow.

---

## Requirements

Datadog Labs Pup should be installed. See [Setup Pup](https://github.com/datadog-labs/agent-skills/tree/main?tab=readme-ov-file#setup-pup) if not.

## Command Execution Order (Token-Efficient)

For scoped commands, use this order:

1. Check context first (prior outputs, conversation, saved values).
2. If a required value is missing, run a discovery command first.
3. If still ambiguous, ask the user to confirm.
4. Then run the target command.
5. Avoid speculative commands likely to fail.

## Quick Start

```bash
pup auth login
# Confirm env tag with the user first (do not assume production/prod/prd).
pup apm services list --env <env> --from 1h --to now
pup traces search --query "service:api-gateway" --from 1h
```

## Services

### List Services

```bash
pup apm services list --env <env> --from 1h --to now
pup apm services stats --env <env> --from 1h --to now
```

### Service Stats

```bash
pup apm services stats --env <env> --from 1h --to now
```

### Service Map

```bash
# View dependencies
pup apm flow-map --query "service:api-gateway&from=$(($(date +%s)-3600))000&to=$(date +%s)000" --env <env> --limit 10
```

## Traces

### Search Traces

```bash
# By service
pup traces search --query "service:api-gateway" --from 1h

# Errors only
pup traces search --query "service:api-gateway status:error" --from 1h

# Slow traces (>1s)
pup traces search --query "service:api-gateway @duration:>1000ms" --from 1h

# With specific tag
pup traces search --query "service:api-gateway @http.url:/api/users" --from 1h
```

### Trace Detail

```bash
# No direct get command for a single trace ID.
# Use traces search with a narrow query and time window.
pup traces search --query "trace_id:<trace_id>" --from 1h
```

## Key Metrics

| Metric | What It Measures |
|--------|------------------|
| `trace.http.request.hits` | Request count |
| `trace.http.request.duration` | Latency |
| `trace.http.request.errors` | Error count |
| `trace.http.request.apdex` | User satisfaction |

## Service Level Objectives

Link APM to SLOs:

```bash
pup slos create --file slo.json
```

## Common Queries

| Goal | Query |
|------|-------|
| Slowest endpoints | `avg:trace.http.request.duration{*} by {resource_name}` |
| Error rate | `sum:trace.http.request.errors{*} / sum:trace.http.request.hits{*}` |
| Throughput | `sum:trace.http.request.hits{*}.as_rate()` |

## Troubleshooting

| Problem | Fix |
|---------|-----|
| No traces | Check ddtrace installed, DD_TRACE_ENABLED=true |
| Missing service | Verify DD_SERVICE env var |
| Traces not linked | Check trace headers propagated |
| High cardinality | Don't tag with user_id/request_id |

## References/Docs

- [APM Setup](https://docs.datadoghq.com/tracing/)
- [Trace Search](https://docs.datadoghq.com/tracing/trace_explorer/)
