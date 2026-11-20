---
name: dd-audit-ai-activity
description: Audit Datadog MCP calls and changes made through MCP, plus Datadog AI agent activity, from Audit Trail. MCP calls and Bits SRE investigations are different event categories.
metadata:
  version: "0.1.0"
  author: datadog-labs
  repository: https://github.com/datadog-labs/agent-skills
  tags: datadog,audit,ai,mcp,bits-ai,governance,dd-audit
  alwaysApply: "false"
---

# Audit Trail: AI Activity Audit

MCP calls are recorded under the `MCP Server` category: `@evt.name:"MCP Server" @asset.type:mcp @action:executed`. A connection, tool call, resource read, or prompt read on the Datadog MCP server uses that event.

Changes the MCP server makes to another resource are recorded on that resource's own category with `is_mcp_call:true`. They are not `MCP Server` events, and their action is not `executed`.

`Bits AI SRE` is a separate category for Bits SRE investigations and Bits SRE tool calls (`@asset.type:tool_call @action:created`). Do not use it for MCP.

Datadog-managed AI agent attribution (`@evt.actor.mode`, Bits agent fields) is not available on GovCloud. On other sites, onboarded Datadog AI agent actions are `@evt.actor.mode:*`. That query is not complete. Bits SRE is tracked for most actions. Bits Chat is tracked from the full Bits Chat page, not the inline panel. Bits Security Analyst runs as `autonomous` and has no delegator. Bits Agent Builder is not tracked yet. Third-party agents are not distinguished from API keys, application keys, personal access tokens, or service account tokens. Some action types on a tracked agent still omit these fields.

A user's own actions are `@usr.id:<USER_ID>`. Actions an agent took for that user are `@delegator.id:<USER_ID>`, and `@usr.*` on those events is the agent. `@delegator.name` is the full name, not the email. A user's complete activity is `(@usr.id:<USER_ID> OR @delegator.id:<USER_ID>)`.

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
  --query "is_mcp_call:true @action:(created OR modified OR deleted)" \
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

### Onboarded Datadog AI agent actions

```bash
pup audit-logs search --query "@evt.actor.mode:*" --from 7d --limit 500 -o json \
  | jq '[.data[] | {
      timestamp: .attributes.timestamp,
      mode: .attributes.attributes.evt.actor.mode,
      actor: .attributes.attributes.usr.email,
      delegator_id: .attributes.attributes.delegator.id,
      action: .attributes.attributes.action,
      event: .attributes.attributes.evt.name,
      resource_type: .attributes.attributes.asset.type
    }]'
```

### One user's actions, including agent-delegated actions

```bash
pup audit-logs search \
  --query "(@usr.id:USER_ID OR @delegator.id:USER_ID)" \
  --from 30d --limit 500 -o json \
  | jq '[.data[] | {
      timestamp: .attributes.timestamp,
      actor: .attributes.attributes.usr.email,
      delegator_id: .attributes.attributes.delegator.id,
      mode: .attributes.attributes.evt.actor.mode,
      action: .attributes.attributes.action,
      event: .attributes.attributes.evt.name,
      resource_type: .attributes.attributes.asset.type
    }]'
```

### MCP call volume

Audit Trail search is capped by `--limit`. These metrics count every call. `datadog.mcp.tool.usage` is a distribution, so count it with `.as_count()`. Both metrics are tagged with `user_email`, `client`, and `tool_name`.

```bash
pup metrics query \
  --query "count:datadog.mcp.tool.usage{*} by {user_email,client,tool_name}.as_count()" \
  --from 7d

pup metrics query \
  --query "count:datadog.mcp.session.starts{*} by {client}.as_count()" \
  --from 7d
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

- [Audit Trail events](https://docs.datadoghq.com/account_management/audit_trail/events/)
- [Track agentic usage](https://docs.datadoghq.com/account_management/audit_trail/guides/track_agentic_usage_in_your_organization/)
- [Datadog MCP Server](https://docs.datadoghq.com/mcp_server/)
- [Access control for MCP](https://docs.datadoghq.com/account_management/rbac/mcp_access/)
