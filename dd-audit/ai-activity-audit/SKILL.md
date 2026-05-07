---
name: dd-audit-ai-activity
description: Audit what the Bits AI assistant (MCP server) has done in your Datadog org — tool calls by user, resources accessed, and anomaly flags for AI governance.
metadata:
  version: "0.1.0"
  author: datadog-labs
  repository: https://github.com/datadog-labs/agent-skills
  tags: datadog,audit,ai,mcp,bits-ai,governance,dd-audit
  alwaysApply: "false"
---

# Audit Trail: AI Activity Audit

Every Datadog MCP tool call is recorded in Audit Trail under the `Bits AI SRE` category. This skill surfaces what the AI assistant has done in your org — which users invoked it, which tools were called, and which resources were affected.

## Prerequisites

```bash
pup auth login   # OAuth2 (recommended)
# or set DD_API_KEY + DD_APP_KEY with audit_logs_read scope
```

## Queries

### All MCP tool activity in a time window

```bash
pup audit-logs search --query "@evt.name:\"MCP Server\"" --from 7d --limit 500 -o json \
  | jq '[.data[] | {
      timestamp: .attributes.timestamp,
      user: .attributes.attributes.usr.email,
      actor_type: .attributes.attributes.evt.actor.type,
      action: .attributes.attributes.action,
      resource_type: .attributes.attributes.asset.type,
      resource_id: .attributes.attributes.asset.id,
      ip: .attributes.attributes.network.client.ip,
      country: .attributes.attributes.network.client.geoip.country.name
    }]'
```

### Activity by user (who is using the AI assistant most?)

```bash
pup audit-logs search --query "@evt.name:\"MCP Server\"" --from 30d --limit 1000 -o json \
  | jq '[.data[] | .attributes.attributes.usr.email]
    | group_by(.)
    | map({user: .[0], tool_calls: length})
    | sort_by(-.tool_calls)'
```

### Resources modified by AI tool calls

```bash
pup audit-logs search \
  --query "@evt.name:\"MCP Server\" @action:(created OR modified OR deleted)" \
  --from 7d --limit 500 -o json \
  | jq '[.data[] | {
      timestamp: .attributes.timestamp,
      user: .attributes.attributes.usr.email,
      action: .attributes.attributes.action,
      resource_type: .attributes.attributes.asset.type,
      resource_id: .attributes.attributes.asset.id
    }]'
```

### AI activity for a specific user

```bash
pup audit-logs search \
  --query "@evt.name:\"MCP Server\" @usr.email:user@example.com" \
  --from 30d --limit 500 -o json \
  | jq '[.data[] | {
      timestamp: .attributes.timestamp,
      action: .attributes.attributes.action,
      resource_type: .attributes.attributes.asset.type,
      resource_id: .attributes.attributes.asset.id
    }]'
```

### Weekly summary report

```bash
pup audit-logs search --query "@evt.name:\"MCP Server\"" --from 7d --limit 1000 -o json \
  | jq '{
      total_tool_calls: (.data | length),
      unique_users: ([.data[] | .attributes.attributes.usr.email] | unique | length),
      top_users: (
        [.data[] | .attributes.attributes.usr.email]
        | group_by(.)
        | map({user: .[0], calls: length})
        | sort_by(-.calls)
        | .[:5]
      ),
      actions_breakdown: (
        [.data[] | .attributes.attributes.action]
        | group_by(.)
        | map({action: .[0], count: length})
        | sort_by(-.count)
      ),
      resource_types: (
        [.data[] | .attributes.attributes.asset.type]
        | group_by(.)
        | map({type: .[0], count: length})
        | sort_by(-.count)
      )
    }'
```

## Anomaly Flags

| Signal | Governance concern |
|--------|--------------------|
| AI performing `deleted` actions on monitors or dashboards | Review whether destructive AI operations are expected |
| AI acting as `SUPPORT_USER` | Datadog support using AI on behalf of org |
| First-time user invoking AI tools | New user accessing AI assistant |
| High volume of tool calls in short window | Automated/batch AI usage |
| AI accessing resources outside user's normal scope | Potential over-permissioned AI session |

## Output Format

```
AI Activity Audit — [Org] — [Date Range]

Total MCP tool calls: [N]
Unique users: [N]

Top users:
  [user@example.com]: [N] calls

Actions breakdown:
  accessed: [N]
  modified: [N]
  created: [N]
  deleted: [N]

Resource types affected:
  dashboard: [N]
  monitor: [N]

Anomalies:
  [List any flagged events with timestamp, user, action, resource]
```

## Context

This skill is most useful for:
- **Security reviews:** Verifying AI actions were authorized and within expected scope
- **Compliance audits:** Demonstrating AI activity is logged and attributable to specific users
- **Governance reports:** Understanding adoption and risk surface of the AI assistant across the org

No other observability vendor audits their AI assistant's actions at this level of detail.

## References

- [Bits AI SRE documentation](https://docs.datadoghq.com/bits_ai/)
- [Audit Trail events — Bits AI SRE category](https://docs.datadoghq.com/account_management/audit_trail/events/)
- [MCP Server setup](https://docs.datadoghq.com/bits_ai/mcp_server/)
