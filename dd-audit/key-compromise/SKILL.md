---
name: dd-audit-key-compromise
description: Investigate a potentially compromised Datadog API key — timeline of actions, geo/IP breakdown, endpoints called, anomaly flags, and remediation steps.
metadata:
  version: "0.1.0"
  author: datadog-labs
  repository: https://github.com/datadog-labs/agent-skills
  tags: datadog,audit,security,api-key,compromise,dd-audit
  alwaysApply: "false"
---

# Audit Trail: API Key Compromise Investigation

Reconstruct what a Datadog API key did, where requests originated, and which resources were affected.

## Prerequisites

```bash
pup auth login   # OAuth2 (recommended)
# or set DD_API_KEY + DD_APP_KEY with audit_logs_read scope
```

You need the **key ID** of the suspect key (not the key value). Find it in Datadog UI under Organization Settings > API Keys, or from context showing `@metadata.api_key.id`.

## Investigation Workflow

### Step 1 — Establish timeline

```bash
pup audit-logs search --query "@metadata.api_key.id:KEY_ID" --from 90d --limit 200 -o json \
  | jq '[.data[] | {
      timestamp: .attributes.timestamp,
      action: .attributes.attributes.action,
      event: .attributes.attributes.evt.name,
      resource_type: .attributes.attributes.asset.type,
      resource_id: .attributes.attributes.asset.id,
      endpoint: .attributes.attributes.http.url_details.path,
      method: .attributes.attributes.http.method,
      ip: .attributes.attributes.network.client.ip,
      city: .attributes.attributes.network.client.geoip.city.name,
      country: .attributes.attributes.network.client.geoip.country.name,
      asn: .attributes.attributes.network.client.geoip.as.name
    }]'
```

### Step 2 — Geo/IP breakdown

```bash
pup audit-logs search --query "@metadata.api_key.id:KEY_ID" --from 90d --limit 500 -o json \
  | jq '[.data[] | {
      country: .attributes.attributes.network.client.geoip.country.name,
      asn: .attributes.attributes.network.client.geoip.as.name,
      ip: .attributes.attributes.network.client.ip
    }]
    | group_by(.country)
    | map({
        country: .[0].country,
        count: length,
        asns: [.[].asn] | unique,
        ips: [.[].ip] | unique
      })
    | sort_by(-.count)'
```

### Step 3 — Endpoint breakdown

```bash
pup audit-logs search --query "@metadata.api_key.id:KEY_ID" --from 90d --limit 500 -o json \
  | jq '[.data[] | {
      method: .attributes.attributes.http.method,
      path: .attributes.attributes.http.url_details.path
    }]
    | group_by(.path)
    | map({path: .[0].path, methods: [.[].method] | unique, count: length})
    | sort_by(-.count)'
```

### Step 4 — Destructive action check

```bash
pup audit-logs search --query "@metadata.api_key.id:KEY_ID @action:deleted" --from 90d -o json \
  | jq '[.data[] | {
      timestamp: .attributes.timestamp,
      resource_type: .attributes.attributes.asset.type,
      resource_id: .attributes.attributes.asset.id,
      ip: .attributes.attributes.network.client.ip,
      country: .attributes.attributes.network.client.geoip.country.name
    }]'
```

### Step 5 — When was the key created and by whom?

```bash
pup audit-logs search --query "@asset.type:api_key @asset.id:KEY_ID @action:created" --from 90d -o json \
  | jq '[.data[] | {
      created_at: .attributes.timestamp,
      created_by: .attributes.attributes.usr.email,
      creator_ip: .attributes.attributes.network.client.ip,
      creator_country: .attributes.attributes.network.client.geoip.country.name
    }]'
```

## Anomaly Flags

| Signal | Why it matters |
|--------|----------------|
| Country not in org's normal baseline | Possible exfiltration from unexpected region |
| ASN is a cloud/VPN provider (AWS, Cloudflare, NordVPN, etc.) | Proxied traffic; obscured origin |
| DELETE actions on monitors, dashboards, or log pipelines | Potential sabotage |
| Burst of activity in short window | Automated scraping or bulk exfiltration |
| Activity outside business hours | Off-hours access |
| Key used from multiple IPs simultaneously | Key shared or stolen |

## Investigation Output Format

```
Key ID: <key_id>
Created: <timestamp> by <user_email>
Active period: <first_seen> to <last_seen>
Total events: <N>

Origins:
  - <Country> (<ASN>): <N> events — [NORMAL / FLAG: first-time origin]

Endpoints called (top 5):
  - <METHOD> <path>: <N> calls

Destructive actions: <N> deletions — [resource types affected]

Recommended actions:
  1. Revoke the key immediately if not already done
  2. Review affected resources: [list]
  3. Check if any deleted resources need restoration
  4. Audit who else had access to this key
```

## Remediation

Revoke in Datadog UI: Organization Settings > API Keys > Revoke.

Or via API (requires `manage_api_keys` scope):
```bash
pup api-keys delete KEY_ID
```

## References

- [Audit Trail API](https://docs.datadoghq.com/api/latest/audit/)
- [API Keys management](https://docs.datadoghq.com/account_management/api-app-keys/)
