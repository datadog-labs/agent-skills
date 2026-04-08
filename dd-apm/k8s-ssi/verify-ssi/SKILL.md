---
name: dd-apm-k8s-verify-ssi
description: Verify APM SSI is working end-to-end on Kubernetes. Confirms pod instrumentation, tracer telemetry, and tracer config using pup fleet and pup apm commands.
metadata:
  version: "1.0.0"
  author: datadog-labs
  repository: https://github.com/datadog-labs/agent-skills
  tags: datadog,apm,kubernetes,ssi,verification,instrumentation
  alwaysApply: "false"
---

# Verify APM SSI on Kubernetes

> **Before doing anything else:** Fully resolve all variables in `## Context to resolve before acting`. Do not begin Step 1 until every variable has a concrete value.

## Triggers

Invoke this skill when the user expresses intent to:
- Confirm SSI is working after enabling APM
- Check whether pods are being instrumented
- Verify the tracer is running and reporting telemetry
- Confirm tracer config is applied correctly

Do NOT invoke this skill if:
- SSI has not been enabled yet — run `enable-ssi` first
- Pods are not being instrumented at all — use `troubleshoot-ssi`

---

## Prerequisites

- [ ] `enable-ssi` is complete
- [ ] Application pods have been restarted since SSI was enabled

### pup-cli: check, install, and authenticate

### Claude runs

```bash
pup --version
```

If not found:

### Claude runs

```bash
brew tap datadog-labs/pack
brew install pup
```

Check auth:
```bash
pup auth status
```

If not authenticated:

### What you need to do in a terminal

```bash
pup auth login
```

Confirm with `pup auth status`.

✅ Valid token — proceed.
❌ No browser available — use API key fallback: `export DD_APP_KEY=<your-app-key>`

---

## Context to resolve before acting

| Variable | How to resolve |
|---|---|
| `CLUSTER_NAME` | Check `spec.global.clusterName` in `datadog-agent.yaml`, or `kubectl config current-context` |
| `ENV` | Check `tags.datadoghq.com/env` label on the application Deployment |
| `SERVICE_NAME` | Check `tags.datadoghq.com/service` label on the application Deployment |

---

## Step 1: Confirm Pods are Instrumented

### Claude runs

```bash
pup fleet instrumented-pods list <CLUSTER_NAME>
```

✅ Target pods appear with injected SDK language and version.

❌ Expected pod missing — go to `troubleshoot-ssi`. Common causes: pod in Agent namespace, namespace targeting filtering it out, pod not restarted after SSI enabled.

---

## Step 2: Confirm the Tracer is Reporting Telemetry

### Claude runs

```bash
pup fleet tracers list --filter "env:<ENV>"
```

✅ One or more tracer entries visible with service name, language, SDK version, and active status.

❌ Service missing — tracer may still be initializing. Wait and retry:

### Claude runs

```bash
sleep 120 && pup fleet tracers list --filter "env:<ENV>"
```

❌ Still missing after retry — go to `troubleshoot-ssi`.

---

## Step 3: Confirm Tracer Configuration

**Only run this step if `ddTraceConfigs` was explicitly configured in `enable-ssi`** (e.g. profiling, AppSec, Data Streams). If basic SSI was set up without `ddTraceConfigs`, skip this step — an empty response here is expected and not a failure.

### Claude runs

```bash
pup apm service-library-config get \
  --service-name <SERVICE_NAME> \
  --env <ENV>
```

✅ Output shows expected environment variables matching what was configured in `ddTraceConfigs`.

✅ Empty output and `ddTraceConfigs` was not configured — expected, not a failure.

❌ Config missing but `ddTraceConfigs` was configured — check it is present in the `DatadogAgent` manifest under the correct target, and that pods were restarted after the config change.

---

## Done

Exit when ALL of the following are true:
- [ ] Step 1: target pods appear in `instrumented-pods list`
- [ ] Step 2: service appears in `tracers list` with active status
- [ ] Step 3: tracer config matches what was set in `DatadogAgent`

If any check fails, go to `troubleshoot-ssi`.

When all steps pass, automatically proceed to `onboarding-summary` now — do not ask the user for permission.

---

## Security constraints

- Never write a raw API key into any file or chat message
- Never run `kubectl delete` without user confirmation
