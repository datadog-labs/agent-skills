---
name: dd-audit
description: Audit Trail investigations - who changed what, key compromise, cost spike root cause, compliance evidence (SOC 2/PCI), and AI activity auditing.
metadata:
  version: "0.1.0"
  author: datadog-labs
  repository: https://github.com/datadog-labs/agent-skills
  tags: datadog,audit,audit-trail,security,compliance,dd-audit
  alwaysApply: "false"
---

# Datadog Audit Trail

Investigate user activity, configuration changes, access patterns, and compliance evidence using `pup audit-logs`.

## Sub-Skills

| Sub-skill | Use when |
|-----------|----------|
| **security-investigation** | "Who changed X?", "What did this user do?", "Show me deletions in the last 24h" |
| **key-compromise** | "Was this API key compromised?", "What did key XYZ do?", "Investigate suspicious key activity" |
| **cost-spike-investigation** | "Why did my bill go up?", "What caused this usage spike?", "Investigate LLM cost increase" |
| **compliance-report** | "Generate SOC 2 evidence", "PCI audit log", "User provisioning report for auditor" |
| **ai-activity-audit** | "What did the AI assistant do?", "Audit MCP tool calls", "AI governance report" |

## Prerequisites

```bash
pup auth login   # OAuth2 (recommended)
# or set DD_API_KEY + DD_APP_KEY with audit_logs_read scope
```

## Commands

```bash
# List recent events
pup audit-logs list --from 1h --limit 100

# Search with a query
pup audit-logs search --query "@action:deleted" --from 24h

# JSON output for piping to jq
pup audit-logs search --query "@usr.email:alice@example.com" --from 7d -o json | jq '.data[].attributes'
```

## Event Schema Quick Reference

| Field | Description | Example values |
|-------|-------------|----------------|
| `@usr.email` | Actor email | `alice@example.com` |
| `@evt.actor.type` | How action was taken | `USER`, `API_KEY`, `SUPPORT_USER` |
| `@action` | Verb | `created`, `modified`, `deleted`, `accessed`, `login` |
| `@evt.name` | Event category | `Dashboard`, `Monitor`, `Authentication`, `Access Management` |
| `@asset.type` | Resource type | `dashboard`, `monitor`, `api_key`, `role`, `user` |
| `@asset.id` | Resource identifier | `abc-123` |
| `@metadata.api_key.id` | API key used (if applicable) | `key_abc123` |
| `@metadata.app_key.id` | App key used (if applicable) | `app_abc123` |
| `@network.client.ip` | Client IP address | `1.2.3.4` |
| `@network.client.geoip.country.name` | Country | `United States` |
| `@network.client.geoip.as.name` | ASN name | `Amazon.com` |
| `@http.url_details.path` | API endpoint path | `/api/v1/dashboard/xyz` |
| `@evt.actor.mode` | Datadog AI agent mode | `interactive`, `autonomous` |
| `@delegator.id` | User a Datadog AI agent acted for | user id |
| `is_mcp_call` | Action went through the Datadog MCP server | `true` |
| `is_notable_event` | Billing or security change Datadog marks notable | `true` |

## Search Syntax

Same Lucene-style syntax as Log Explorer:

| Query | Meaning |
|-------|---------|
| `@evt.name:Dashboard` | Exact field match |
| `@action:deleted` | Action filter |
| `@usr.email:alice@example.com` | Specific user |
| `@evt.name:Monitor AND @action:modified` | Compound |
| `-@action:deleted` | Negation |
| `@usr.email:*` | Field exists |
| `@network.client.ip:1.2.3.4` | IP filter |

## Retention

When Audit Trail is enabled, the default retention is **90 days**. An admin can set it to 3, 7, 15, 30, or 90 days. When Audit Trail is disabled, retention resets to 7 days. Archiving copies events to Amazon S3, Google Cloud Storage, or Azure Storage for an external SIEM. It does not extend the window you can query in Audit Trail. Check the configured retention before running a query.

## Troubleshooting

| Problem | Cause | Fix |
|---------|-------|-----|
| 403 Forbidden | The role lacks Audit Trail Read (`audit_logs_read`), or the app key or OAuth token lacks the `audit_logs_read` scope | Grant Audit Trail Read and add the scope. Enabling Audit Trail itself requires Audit Trail Write (`audit_logs_write`) |
| Empty results | Time window is longer than the configured retention, or Audit Trail is disabled | Check Audit Trail Settings. Disabled retention is 7 days |
| Timeout | Query too broad | Narrow time window or add more filters |
| No IP data | Internal action or pre-enrichment event | Not all events have geo data |

## References

- [Audit Trail API](https://docs.datadoghq.com/api/latest/audit/)
- [Audit Trail documentation](https://docs.datadoghq.com/account_management/audit_trail/)
- [Audit Trail events](https://docs.datadoghq.com/account_management/audit_trail/events/)
- [Track agentic usage](https://docs.datadoghq.com/account_management/audit_trail/guides/track_agentic_usage_in_your_organization/)
- [Search syntax](https://docs.datadoghq.com/logs/explorer/search_syntax/)
