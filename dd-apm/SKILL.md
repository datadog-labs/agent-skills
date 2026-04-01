---
name: dd-apm
description: APM - traces, services, dependencies, performance analysis.
metadata:
  version: "1.0.1"
  author: datadog-labs
  repository: https://github.com/datadog-labs/agent-skills
  tags: datadog,apm,tracing,performance,distributed-tracing,dd-apm
  globs: "**/ddtrace*,**/datadog*.yaml,**/*trace*"
  alwaysApply: "false"
---

# Datadog APM

Distributed tracing, service maps, and performance analysis.

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
