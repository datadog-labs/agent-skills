# Compliance Control → Audit Trail Query Mapping

## Scope Boundary

Datadog Audit Trail documents actions **within the Datadog platform**:
- Who logged in, from where
- Who changed monitors, dashboards, log pipelines, integrations, roles, API keys
- What the Bits AI assistant did on behalf of users

It does **not** document:
- Actions within systems that Datadog monitors (AWS, GCP, application servers)
- Content of data ingested by Datadog (logs, traces, metrics values)
- Network activity between user systems and Datadog

## SOC 2 Trust Services Criteria

| Control | Description | Audit Trail Query | Fields Used |
|---------|-------------|-------------------|-------------|
| CC6.1 | Logical access controls implemented | Review role assignments | `@evt.name:"Access Management" @asset.type:role` |
| CC6.2 | User registration and deprovisioning | User lifecycle events | `@evt.name:"Access Management" @asset.type:user @action:(created OR deleted)` |
| CC6.3 | Role-based access | Permission change log | `@evt.name:"Access Management" @asset.type:role` |
| CC6.6 | Logical access boundaries | Failed logins, geo anomalies | `@evt.name:Authentication @action:login @status:error` |
| CC6.8 | Prevent unauthorized access | API key management | `@evt.name:Authentication @asset.type:api_key` |
| CC7.2 | System monitoring — anomaly detection | Privileged/support access | `@evt.actor.type:SUPPORT_USER` |
| CC7.3 | Event response | Changes during incident window | Time-scoped `@action:modified` + `@evt.name` filter |
| A1.1 | Availability monitoring | Monitor create/delete events | `@evt.name:Monitor` |

## PCI DSS Requirement 10 — Audit Logging

| Req | Description | Audit Trail Query | PCI Field Mapping |
|-----|-------------|-------------------|-------------------|
| 10.2.1 | Access to cardholder data | Dashboard/resource access events | `@http.method:GET @asset.type:dashboard` |
| 10.2.2 | Actions by root/privileged users | Support user and org admin events | `@evt.actor.type:SUPPORT_USER` |
| 10.2.3 | Access to audit trail | Audit Trail config events | `@evt.name:"Audit Trail"` |
| 10.2.4 | Invalid access attempts | Failed authentication events | `@evt.name:Authentication @status:error` |
| 10.2.5 | Use of identification/auth mechanisms | All login events | `@evt.name:Authentication @action:login` |
| 10.2.6 | Initialization/stopping of audit logs | Audit retention setting changes | `@evt.name:"Audit Trail" @action:modified` |
| 10.2.7 | Creation/deletion of system objects | All create/delete events | `@action:(created OR deleted)` |
| 10.3.1 | User identification | `@usr.email` field | Present on all user-initiated events |
| 10.3.2 | Event type | `@action`, `@evt.name` fields | Present on all events |
| 10.3.3 | Date and time | `timestamp` field | ISO 8601 UTC on all events |
| 10.3.4 | Success/failure indication | `@status` field | `info`/`error`/`warn` |
| 10.3.5 | Origination of event | `@network.client.ip` field | Present on most events |
| 10.3.6 | Identity of affected data/component | `@asset.type`, `@asset.id` fields | Present on resource events |
| 10.7 | Retain audit logs ≥12 months | Check archive config | Default 90 days — must configure archive |

## Retention Requirements by Framework

| Framework | Required retention | Datadog default | Gap? |
|-----------|-------------------|-----------------|------|
| SOC 2 | Auditor discretion (typically 12 months) | 90 days | Yes — configure archive |
| PCI DSS | 12 months minimum | 90 days | Yes — configure archive |
| ISO 27001 | 3 years typical | 90 days | Yes — configure archive |
| HIPAA | 6 years | 90 days | Yes — configure archive |

**To configure archive:** Datadog UI > Security > Audit Trail > Configure > Archive to S3/GCS/Azure Blob.
