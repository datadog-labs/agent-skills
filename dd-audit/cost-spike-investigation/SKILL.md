---
name: dd-audit-cost-spike-investigation
description: Investigate a Datadog product usage or cost spike by correlating Usage Metering data (when/what spiked) with Audit Trail config changes (who changed what in the preceding window).
metadata:
  version: "0.1.0"
  author: datadog-labs
  repository: https://github.com/datadog-labs/agent-skills
  tags: datadog,audit,cost,usage,spike,finops,dd-audit
  alwaysApply: "false"
---

# Audit Trail: Cost / Usage Spike Investigation

Identify what caused a Datadog usage spike by correlating billing data with configuration change history.

The causal chain is: **someone changed something → that change increased data volume → usage spiked → cost went up**. Usage Metering tells you when and what; Audit Trail tells you who made the change.

## Prerequisites

```bash
pup auth login   # OAuth2 (recommended) — covers audit queries
# Hourly usage requires the usage_read permission. OAuth apps need the usage_read scope.
# DD_API_KEY, DD_APP_KEY, and DD_SITE must already be set. Do not print the key values.
```

## Scope Boundary

This skill identifies **configuration changes** that may have caused a spike. It does not identify which specific user or process *submitted* the data (e.g., which service sent the LLM spans). For per-submission attribution, use LLM Observability traces or APM instrumentation.

## Investigation Workflow

### Step 1 — Identify the spike window and product family

```bash
START=$(date -u -v-7d +"%Y-%m-%dT%H:%M:%SZ" 2>/dev/null || date -u -d "7 days ago" +"%Y-%m-%dT%H:%M:%SZ")
END=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

printf 'DD-API-KEY: %s\nDD-APPLICATION-KEY: %s\n' "$DD_API_KEY" "$DD_APP_KEY" \
  | curl -s -G "https://api.${DD_SITE}/api/v2/usage/hourly_usage" \
  -H @- \
  --data-urlencode "filter[timestamp][start]=${START}" \
  --data-urlencode "filter[timestamp][end]=${END}" \
  --data-urlencode "filter[product_families]=all" \
  | jq '[.data[] | {
      timestamp: .attributes.timestamp,
      product: .attributes.product_family,
      measurements: [.attributes.measurements[] | {type: .usage_type, value: .value}]
    }]'
```

**Product families with LLM/AI coverage:** `llm_observability`, `bits_ai`. Logs are `logs` or `log_management`. APM is `application_performance_monitoring`, not `apm`. The `audit_logs` family is deprecated; the current family is `audit_trail`.

### Step 2 — Pinpoint the spike

From Step 1, identify the hour/day where volume jumped. Note the timestamp as `SPIKE_TIME`.

### Step 3 — Search Audit Trail for config changes in the 24h preceding the spike

```bash
pup audit-logs search \
  --query "@action:(created OR modified OR deleted)" \
  --from "SPIKE_TIME_MINUS_24H" \
  --to "SPIKE_TIME" \
  --limit 200 \
  -o json \
  | jq '[.data[] | {
      timestamp: .attributes.timestamp,
      user: .attributes.attributes.usr.email,
      actor_type: .attributes.attributes.evt.actor.type,
      action: .attributes.attributes.action,
      event_category: .attributes.attributes.evt.name,
      resource_type: .attributes.attributes.asset.type,
      resource_id: .attributes.attributes.asset.id
    }]'
```

> **Note:** `--from` and `--to` accept ISO timestamps (e.g., `2026-05-01T14:00:00Z`) or relative values (`1h`, `24h`, `7d`). Replace `SPIKE_TIME` and `SPIKE_TIME_MINUS_24H` before running. Pup rejects those placeholder strings.

### Step 3a — Notable billing and security changes

`is_notable_event:true` is the subset Datadog marks as a billing or security change: log indexes, exclusion filters, and custom metrics; APM retention filters and custom metrics; metric tag changes; RUM application creation and deletion; Sensitive Data Scanner groups; Synthetic test creation and deletion; Product Analytics enablement, disablement, and modification.

```bash
pup audit-logs search \
  --query "is_notable_event:true" \
  --from "SPIKE_TIME_MINUS_24H" \
  --to "SPIKE_TIME" \
  --limit 200 \
  -o json \
  | jq '[.data[] | {
      timestamp: .attributes.timestamp,
      user: .attributes.attributes.usr.email,
      action: .attributes.attributes.action,
      event_category: .attributes.attributes.evt.name,
      resource_type: .attributes.attributes.asset.type,
      resource_id: .attributes.attributes.asset.id
    }]'
```

### Step 4 — Narrow to product-relevant config changes

Use the event catalog names. Where the notable-events table publishes a different string, search both.

| If this product spiked | Add to query |
|------------------------|-------------|
| `llm_observability` | `@evt.name:"LLM Observability"` |
| `logs` / `log_management` | `@evt.name:"Log Management" @asset.type:(pipeline OR index OR "exclusion filter" OR exclusion_filter OR "custom metric" OR custom_metrics)` |
| `application_performance_monitoring` | `@evt.name:("Application Performance Monitoring (APM)" OR APM) @asset.type:(retention_filter OR custom_metrics OR samplerconfig)` |
| `rum` | `@evt.name:"Real User Monitoring"` |
| `metrics` | `@evt.name:Metrics @asset.type:metric @action:(created OR modified)` |

Log exclusion filters are `"exclusion filter"` in the event catalog and `exclusion_filter` on the notable-events table. Log custom metrics are `"custom metric"` and `custom_metrics`. The APM category is `Application Performance Monitoring (APM)` in the catalog and `APM` on the notable-events table. Remotely configured APM sampling is `@asset.type:samplerconfig`. There is no `sampling_rate` asset type.

Example for an LLM Observability spike:

```bash
pup audit-logs search \
  --query "@evt.name:\"LLM Observability\" @action:(created OR modified OR enabled OR disabled)" \
  --from "SPIKE_TIME_MINUS_24H" \
  --to "SPIKE_TIME" \
  --limit 100 \
  -o json \
  | jq '[.data[] | {
      timestamp: .attributes.timestamp,
      user: .attributes.attributes.usr.email,
      action: .attributes.attributes.action,
      category: .attributes.attributes.evt.name,
      resource_type: .attributes.attributes.asset.type,
      resource_id: .attributes.attributes.asset.id
    }]'
```

## Output Format

```
Usage spike detected:
  Product: <product_family>
  Spike time: <SPIKE_TIME>
  Volume: <baseline> → <spike_value> (<magnitude>×)

Configuration changes in 24h preceding spike:
  <timestamp> | <user_email> | <action> <resource_type> <resource_id> | <category>

Likely causal change: <most-proximate change matching the product family>

Confidence: HIGH (single clear change) / MEDIUM (multiple candidates) / LOW (no matching changes)

Next steps:
  - Confirm with <user_email> whether the change was intentional
  - If unintentional: revert <resource_id> and monitor volume
  - If intentional: update cost forecasts and alert thresholds
```

## When No Causal Change Is Found

1. The change may predate the 24h window — expand to 72h
2. The increase may be from application-side instrumentation changes — check deploys
3. The increase may be organic traffic growth — correlate with product launch or traffic event

## References

- [Usage Metering API](https://docs.datadoghq.com/api/latest/usage-metering/)
- [Audit Trail](https://docs.datadoghq.com/account_management/audit_trail/)
- [Audit Trail events](https://docs.datadoghq.com/account_management/audit_trail/events/)
- [LLM Observability](https://docs.datadoghq.com/llm_observability/)
