---
name: dd-apm-k8s-onboarding-summary
description: Generate a live APM onboarding confirmation report with deep links into the Datadog UI. Collects real data from the cluster and Datadog backend to confirm everything is working end-to-end.
metadata:
  version: "1.0.0"
  author: datadog-labs
  repository: https://github.com/datadog-labs/agent-skills
  tags: datadog,apm,kubernetes,ssi,summary,verification
  alwaysApply: "false"
---

# APM Onboarding Summary

## Triggers

Invoke this skill when:
- All steps in `verify-ssi` have passed
- All checks in `troubleshoot-ssi` have been resolved
- The user asks "is everything working?", "show me the status", or "confirm APM is set up"

Do NOT invoke this skill if any verification or troubleshooting check is still failing — resolve those first.

---

## Context to resolve before acting

| Variable | How to resolve |
|---|---|
| `AGENT_NAMESPACE` | Namespace where Datadog Agent is installed |
| `APP_NAMESPACE` | Namespace of the application |
| `APP_LABEL` | Check `spec.selector.matchLabels.app` in the Deployment manifest |
| `CLUSTER_NAME` | `spec.global.clusterName` in `datadog-agent.yaml` |
| `SERVICE_NAME` | `tags.datadoghq.com/service` label on the Deployment |
| `ENV` | `tags.datadoghq.com/env` label on the Deployment |
| `DD_SITE` | `spec.global.site` in `datadog-agent.yaml` |

---

## Prerequisites

### Claude runs

```bash
pup auth status
```

✅ Valid token — proceed.

❌ Not authenticated:

### What you need to do in a terminal

```bash
pup auth login
```

Confirm with `pup auth status` before continuing.

---

## Collect live confirmation data

Run all of the following. Each populates a row in the final report.

### Claude runs

```bash
# Agent pod count and status
kubectl get pods -n <AGENT_NAMESPACE> \
  -l app.kubernetes.io/component=agent \
  --no-headers

# SSI instrumentation config live in cluster
kubectl get datadogagent datadog -n <AGENT_NAMESPACE> \
  -o jsonpath='{.spec.features.apm.instrumentation}'

# Init container confirmed in app pod spec
kubectl get pod -l app=<APP_LABEL> -n <APP_NAMESPACE> \
  -o jsonpath='{.items[0].spec.initContainers[*].name}'

# Pod confirmed in Datadog's instrumented-pods list
pup fleet instrumented-pods list <CLUSTER_NAME>

# Tracer active and reporting
pup fleet tracers list --filter "service:<SERVICE_NAME>"

# Service visible in APM
pup apm services list --env <ENV>

# Traces arriving in the last hour
pup traces search --query "service:<SERVICE_NAME>" --from 1h --limit 5
```

---

## Present the report

Fill in every value from live command output. Do not leave any placeholder unfilled. If a value cannot be confirmed, mark that row ❌ and link to `troubleshoot-ssi`.

---

**APM onboarding complete**

| Check | Detail | Status |
|---|---|---|
| Datadog Agent | `<N>` pod(s) Running in `<AGENT_NAMESPACE>` | ✅ |
| SSI enabled | Targeting namespace `<APP_NAMESPACE>`, language `<LANGUAGE>` v`<MAJOR_VERSION>` | ✅ |
| Init container injected | `datadog-lib-<language>-init` present in pod spec | ✅ |
| Pod instrumented | `<POD_NAME>` in `pup fleet instrumented-pods list` | ✅ |
| Tracer reporting | Service `<SERVICE_NAME>`, `<LANGUAGE>`, tracer v`<TRACER_VERSION>` | ✅ |
| APM service visible | `<SERVICE_NAME>` in env `<ENV>` | ✅ |
| Traces arriving | `<N>` trace(s) found in the last hour | ✅ |

---

**Your service in Datadog — click to open:**

Construct each URL by substituting real values. Do not print placeholder URLs.

| View | URL |
|---|---|
| Service overview | `https://app.<DD_SITE>/apm/services/<SERVICE_NAME>?env=<ENV>` |
| Traces explorer | `https://app.<DD_SITE>/apm/traces?query=service:<SERVICE_NAME>%20env:<ENV>` |
| Service map | `https://app.<DD_SITE>/apm/map?env=<ENV>&service=<SERVICE_NAME>` |
| Agent fleet | `https://app.<DD_SITE>/fleet-automation` |

---

## Security constraints

- Never write a raw API key into any file or chat message
