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
# Usage Metering queries also need DD_API_KEY + DD_APP_KEY
export DD_API_KEY=<your-api-key>
export DD_APP_KEY=<your-app-key>
export DD_SITE=datadoghq.com
```

## Scope Boundary

This skill identifies **configuration changes** that may have caused a spike. It does not identify which specific user or process *submitted* the data (e.g., which service sent the LLM spans). For per-submission attribution, use LLM Observability traces or APM instrumentation.

## Investigation Workflow

### Step 1 — Identify the spike window and product family

```bash
START=$(date -u -v-7d +"%Y-%m-%dT%H:%M:%SZ" 2>/dev/null || date -u -d "7 days ago" +"%Y-%m-%dT%H:%M:%SZ")
END=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

curl -s -G "https://api.${DD_SITE}/api/v2/usage/hourly_usage" \
  -H "DD-API-KEY: ${DD_API_KEY}" \
  -H "DD-APPLICATION-KEY: ${DD_APP_KEY}" \
  --data-urlencode "filter[timestamp][start]=${START}" \
  --data-urlencode "filter[timestamp][end]=${END}" \
  --data-urlencode "filter[product_families]=all" \
  | jq '[.data[] | {
      timestamp: .attributes.timestamp,
      product: .attributes.product_family,
      measurements: [.attributes.measurements[] | {type: .usage_type, value: .value}]
    }]'
```

**Product families with LLM/AI coverage:** `llm_observability`, `bits_ai`, `logs`, `apm`

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
      user: .attributes.attributes["@usr.email"],
      actor_type: .attributes.attributes["@evt.actor.type"],
      action: .attributes.attributes["@action"],
      event_category: .attributes.attributes["@evt.name"],
      resource_type: .attributes.attributes["@asset.type"],
      resource_id: .attributes.attributes["@asset.id"]
    }]'
```

> **Note:** `--from` and `--to` accept ISO timestamps (e.g., `2026-05-01T14:00:00Z`) or relative values (`1h`, `24h`, `7d`).

### Step 4 — Narrow to product-relevant config changes

Filter to the audit categories most likely to affect the spiking product:

| If this product spiked | Add to query |
|------------------------|-------------|
| `llm_observability` | `@evt.name:(Integration OR APM OR "Log Management")` |
| `logs` / `indexed_logs` | `@evt.name:"Log Management" @asset.type:(pipeline OR index OR exclusion_filter)` |
| `apm` / `indexed_spans` | `@evt.name:APM @asset.type:(retention_filter OR sampling_rate)` |
| `rum` | `@evt.name:RUM` |
| `metrics` | `@evt.name:Metrics` |

Example for LLM Observability spike:

```bash
pup audit-logs search \
  --query "@evt.name:(Integration OR APM OR \"Log Management\") @action:(created OR modified)" \
  --from "SPIKE_TIME_MINUS_24H" \
  --to "SPIKE_TIME" \
  --limit 100 \
  -o json \
  | jq '[.data[] | {
      timestamp: .attributes.timestamp,
      user: .attributes.attributes["@usr.email"],
      action: .attributes.attributes["@action"],
      category: .attributes.attributes["@evt.name"],
      resource_type: .attributes.attributes["@asset.type"],
      resource_id: .attributes.attributes["@asset.id"]
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
- [Audit Trail API](https://docs.datadoghq.com/api/latest/audit/)
- [LLM Observability](https://docs.datadoghq.com/llm_observability/)
