---
name: dd-audit-security-investigation
description: Answer "who did what" security questions from Audit Trail — deletions, config changes, login activity, permission changes, actions from a specific user or IP.
metadata:
  version: "0.1.0"
  author: datadog-labs
  repository: https://github.com/datadog-labs/agent-skills
  tags: datadog,audit,security,investigation,dd-audit
  alwaysApply: "false"
---

# Audit Trail: Security Investigation

Answer common security investigation questions using `pup audit-logs`.

## Prerequisites

```bash
pup auth login   # OAuth2 (recommended)
# or set DD_API_KEY + DD_APP_KEY with audit_logs_read scope
```

## Command Execution Order

1. Clarify the investigation scope: who, what resource type, what time window.
2. Run the most specific query first; broaden only if results are empty.
3. If results are large, pipe to `jq` to group or summarize.
4. Highlight anomalies: bulk operations, unusual geo, off-hours activity, support user actions.

## Common Investigation Queries

### Who deleted resources in a time window?

```bash
pup audit-logs search --query "@action:deleted" --from 24h -o json \
  | jq '[.data[] | {
      timestamp: .attributes.timestamp,
      user: .attributes.attributes["@usr.email"],
      actor_type: .attributes.attributes["@evt.actor.type"],
      resource_type: .attributes.attributes["@asset.type"],
      resource_id: .attributes.attributes["@asset.id"],
      country: .attributes.attributes["@network.client.geoip.country.name"]
    }]'
```

### Who modified a specific resource (by ID)?

```bash
pup audit-logs search --query "@asset.id:RESOURCE_ID" --from 7d -o json \
  | jq '[.data[] | {
      timestamp: .attributes.timestamp,
      user: .attributes.attributes["@usr.email"],
      action: .attributes.attributes["@action"],
      event: .attributes.attributes["@evt.name"]
    }]'
```

### What did a specific user do?

```bash
pup audit-logs search --query "@usr.email:user@example.com" --from 7d --limit 200 -o json \
  | jq '[.data[] | {
      timestamp: .attributes.timestamp,
      action: .attributes.attributes["@action"],
      event: .attributes.attributes["@evt.name"],
      resource_type: .attributes.attributes["@asset.type"],
      resource_id: .attributes.attributes["@asset.id"],
      ip: .attributes.attributes["@network.client.ip"],
      country: .attributes.attributes["@network.client.geoip.country.name"]
    }]'
```

### Login activity — all logins with geo

```bash
pup audit-logs search --query "@evt.name:Authentication @action:login" --from 7d --limit 200 -o json \
  | jq '[.data[] | {
      timestamp: .attributes.timestamp,
      user: .attributes.attributes["@usr.email"],
      status: .attributes.attributes["@status"],
      ip: .attributes.attributes["@network.client.ip"],
      city: .attributes.attributes["@network.client.geoip.city.name"],
      country: .attributes.attributes["@network.client.geoip.country.name"],
      asn: .attributes.attributes["@network.client.geoip.as.name"]
    }]'
```

### Failed logins only

```bash
pup audit-logs search --query "@evt.name:Authentication @action:login @status:error" --from 7d --limit 200 -o json \
  | jq '[.data[] | {
      timestamp: .attributes.timestamp,
      user: .attributes.attributes["@usr.email"],
      ip: .attributes.attributes["@network.client.ip"],
      country: .attributes.attributes["@network.client.geoip.country.name"]
    }]'
```

### Who changed roles or permissions?

```bash
pup audit-logs search --query "@evt.name:\"Access Management\"" --from 30d --limit 200 -o json \
  | jq '[.data[] | {
      timestamp: .attributes.timestamp,
      user: .attributes.attributes["@usr.email"],
      action: .attributes.attributes["@action"],
      resource_type: .attributes.attributes["@asset.type"],
      resource_id: .attributes.attributes["@asset.id"]
    }]'
```

### What actions came from a specific IP?

```bash
pup audit-logs search --query "@network.client.ip:1.2.3.4" --from 30d --limit 200 -o json \
  | jq '[.data[] | {
      timestamp: .attributes.timestamp,
      user: .attributes.attributes["@usr.email"],
      actor_type: .attributes.attributes["@evt.actor.type"],
      action: .attributes.attributes["@action"],
      event: .attributes.attributes["@evt.name"],
      resource_type: .attributes.attributes["@asset.type"]
    }]'
```

### Who created or deleted API keys?

```bash
pup audit-logs search --query "@evt.name:Authentication @asset.type:api_key" --from 90d --limit 200 -o json \
  | jq '[.data[] | {
      timestamp: .attributes.timestamp,
      user: .attributes.attributes["@usr.email"],
      action: .attributes.attributes["@action"],
      key_id: .attributes.attributes["@asset.id"],
      ip: .attributes.attributes["@network.client.ip"],
      country: .attributes.attributes["@network.client.geoip.country.name"]
    }]'
```

## Event Category Reference

| Category (`@evt.name`) | What it covers |
|------------------------|----------------|
| `Authentication` | Logins, API key create/delete/modify |
| `Access Management` | Roles, user add/remove, restriction policies |
| `Dashboard` | Create, modify, delete, share |
| `Monitor` | Create, modify, delete, resolve |
| `Log Management` | Pipelines, indexes, archives, exclusion filters |
| `Integration` | Add/modify/delete integrations |
| `Metrics` | Custom metric create/modify/delete |
| `Organization Management` | Child org creation, org settings |
| `Notebook` | Create, modify, delete |
| `APM` | Retention filters, sampling config |
| `Cloud Security Platform` | CWS rules, security signal state changes |
| `Bits AI SRE` | MCP tool calls, AI investigations |

## Anomaly Flags to Surface

When presenting investigation results, call out:
- **Actor type `SUPPORT_USER`** — Datadog support accessed the org
- **Bulk deletions** — same user, same action, many resources in a short window
- **Unexpected geography** — country not seen in prior logins for this user
- **Off-hours activity** — actions at unusual times for the user's typical timezone
- **First-time ASN** — action from a cloud provider or VPN not seen before (`@network.client.geoip.as.name`)

## References

- [Audit Trail API](https://docs.datadoghq.com/api/latest/audit/)
- [Audit Trail event categories](https://docs.datadoghq.com/account_management/audit_trail/events/)
