---
name: dd-audit-compliance-report
description: Generate auditor-ready compliance evidence from Datadog Audit Trail for SOC 2 and PCI DSS. Maps framework controls to specific query patterns and produces formatted output.
metadata:
  version: "0.1.0"
  author: datadog-labs
  repository: https://github.com/datadog-labs/agent-skills
  tags: datadog,audit,compliance,soc2,pci,dd-audit
  alwaysApply: "false"
---

# Audit Trail: Compliance Evidence Report

Generate auditor-ready evidence from Datadog Audit Trail for SOC 2 and PCI DSS control requirements.

## Prerequisites

```bash
pup auth login   # OAuth2 (recommended)
# or set DD_API_KEY + DD_APP_KEY with audit_logs_read scope
```

## Read First

See `references/control-mapping.md` for the full control → query mapping table and retention requirements by framework.

## Retention Check (Run First)

PCI requires 12 months. Datadog default retention is 90 days. Check whether archive is configured:

```bash
pup audit-logs search --query "@evt.name:\"Audit Trail\" @action:modified" --from 90d -o json \
  | jq '[.data[] | {
      timestamp: .attributes.timestamp,
      user: .attributes.attributes["@usr.email"],
      action: .attributes.attributes["@action"],
      resource: .attributes.attributes["@asset.type"]
    }]'
```

If the requested time window exceeds 90 days and no archive is confirmed, surface this gap in the report header.

## Workflow

1. Confirm: framework (SOC 2 / PCI DSS), time window, org scope
2. Run retention check
3. Run each relevant control query
4. Format output using the Evidence Report template

---

## SOC 2 Queries

### CC6.2 — User Provisioning / Deprovisioning

```bash
pup audit-logs search \
  --query "@evt.name:\"Access Management\" @asset.type:user @action:(created OR deleted OR modified)" \
  --from PERIOD_START --to PERIOD_END --limit 500 -o json \
  | jq '[.data[] | {
      timestamp: .attributes.timestamp,
      actor: .attributes.attributes["@usr.email"],
      action: .attributes.attributes["@action"],
      affected_user: .attributes.attributes["@asset.id"]
    }]'
```

### CC6.3 — Role and Permission Changes

```bash
pup audit-logs search \
  --query "@evt.name:\"Access Management\" @asset.type:role" \
  --from PERIOD_START --to PERIOD_END --limit 500 -o json \
  | jq '[.data[] | {
      timestamp: .attributes.timestamp,
      actor: .attributes.attributes["@usr.email"],
      action: .attributes.attributes["@action"],
      role_id: .attributes.attributes["@asset.id"]
    }]'
```

### CC6.6 — Failed Logins and Suspicious Access

```bash
pup audit-logs search \
  --query "@evt.name:Authentication @action:login @status:error" \
  --from PERIOD_START --to PERIOD_END --limit 500 -o json \
  | jq '[.data[] | {
      timestamp: .attributes.timestamp,
      user: .attributes.attributes["@usr.email"],
      ip: .attributes.attributes["@network.client.ip"],
      country: .attributes.attributes["@network.client.geoip.country.name"]
    }]'
```

### CC7.2 — Privileged / Support User Actions

```bash
pup audit-logs search \
  --query "@evt.actor.type:SUPPORT_USER" \
  --from PERIOD_START --to PERIOD_END --limit 500 -o json \
  | jq '[.data[] | {
      timestamp: .attributes.timestamp,
      support_actor: .attributes.attributes["@usr.email"],
      action: .attributes.attributes["@action"],
      resource_type: .attributes.attributes["@asset.type"],
      resource_id: .attributes.attributes["@asset.id"]
    }]'
```

---

## PCI DSS Queries

### PCI 10.2.2 — Actions by Privileged Users

Same as CC7.2 above. Also include org-level admin actions:

```bash
pup audit-logs search \
  --query "@evt.name:\"Organization Management\"" \
  --from PERIOD_START --to PERIOD_END --limit 200 -o json \
  | jq '[.data[] | {
      timestamp: .attributes.timestamp,
      actor: .attributes.attributes["@usr.email"],
      action: .attributes.attributes["@action"],
      resource_type: .attributes.attributes["@asset.type"]
    }]'
```

### PCI 10.2.3 — Access to Audit Trail Itself

```bash
pup audit-logs search \
  --query "@evt.name:\"Audit Trail\"" \
  --from PERIOD_START --to PERIOD_END --limit 200 -o json \
  | jq '[.data[] | {
      timestamp: .attributes.timestamp,
      actor: .attributes.attributes["@usr.email"],
      action: .attributes.attributes["@action"],
      resource_type: .attributes.attributes["@asset.type"]
    }]'
```

### PCI 10.2.4 — Invalid Access Attempts

Same as CC6.6 failed logins above.

### PCI 10.2.5 — All Authentication Events

```bash
pup audit-logs search \
  --query "@evt.name:Authentication @action:login" \
  --from PERIOD_START --to PERIOD_END --limit 1000 -o json \
  | jq '[.data[] | {
      timestamp: .attributes.timestamp,
      user: .attributes.attributes["@usr.email"],
      auth_method: .attributes.attributes["@auth_method"],
      result: .attributes.attributes["@status"],
      ip: .attributes.attributes["@network.client.ip"],
      country: .attributes.attributes["@network.client.geoip.country.name"]
    }]'
```

### PCI 10.2.7 — Object Creation and Deletion

```bash
pup audit-logs search \
  --query "@action:(created OR deleted)" \
  --from PERIOD_START --to PERIOD_END --limit 1000 -o json \
  | jq '[.data[] | {
      timestamp: .attributes.timestamp,
      user: .attributes.attributes["@usr.email"],
      action: .attributes.attributes["@action"],
      resource_type: .attributes.attributes["@asset.type"],
      resource_id: .attributes.attributes["@asset.id"],
      ip: .attributes.attributes["@network.client.ip"]
    }]'
```

---

## Evidence Report Template

```
# Datadog Audit Trail — Compliance Evidence Report
Framework: [SOC 2 / PCI DSS]
Organization: [org name]
Period: [start] to [end]
Generated: [date]

## Scope Boundary
This report covers administrative actions within the Datadog platform.
It does not cover actions taken within systems that Datadog monitors.

## Retention Status
[✓ Full period covered by Audit Trail retention]
[⚠ Requested period exceeds 90-day default. Archive config required for complete coverage.]

---

## [Control ID] — [Control Name]
Events found: [N]

| Timestamp | Actor | Action | Resource Type | Resource ID | IP | Country |
|-----------|-------|--------|---------------|-------------|-----|---------|
| ...       | ...   | ...    | ...           | ...         | ... | ...     |

[Repeat per control]

---

## Gaps
[List any controls where data was unavailable or incomplete, and why]
```

## Scope Caveat

Datadog Audit Trail covers the **Datadog platform** as the system being audited. For PCI purposes, this is evidence that the monitoring platform's access controls are functioning — not direct evidence about the cardholder data environment (CDE) itself. Auditors should understand this scope boundary.

## References

- [Audit Trail events reference](https://docs.datadoghq.com/account_management/audit_trail/events/)
- [PCI DSS Requirement 10](https://www.pcisecuritystandards.org/document_library/)
- [SOC 2 Trust Services Criteria](https://us.aicpa.org/interestareas/frc/assuranceadvisoryservices/aicpasoc2report)
