---
name: dd-apm-k8s-enable-ssi
description: Enable APM on Kubernetes via Single Step Instrumentation (SSI). Configures the DatadogAgent CR, adds Unified Service Tags, restarts pods, and triggers verification.
metadata:
  version: "1.0.0"
  author: datadog-labs
  repository: https://github.com/datadog-labs/agent-skills
  tags: datadog,apm,kubernetes,ssi,instrumentation,single-step
  alwaysApply: "false"
---

# Enable APM on Kubernetes via Single Step Instrumentation

> **Before doing anything else:** Fully resolve all variables in `## Context to resolve before acting`. Do not begin Step 0 until every variable has a concrete value.

## Triggers

Invoke this skill when the user expresses intent to:
- Enable APM on a Kubernetes cluster
- Instrument Kubernetes applications with Datadog tracing
- Set up Single Step Instrumentation (SSI)

Do NOT invoke this skill if:
- The Datadog Agent is not yet installed — run `agent-install` first
- The user wants to verify SSI after setup — use `verify-ssi`
- The user wants to enable Profiler, AppSec, or Data Streams — use `dd-apm-k8s-sdk-features`

---

## Prerequisites

**Environment**
- [ ] Datadog Agent is installed and healthy — `agent-install` complete
- [ ] Kubernetes v1.20+
- [ ] Linux node pools only — Windows pods require explicit namespace exclusion
- [ ] Cluster is not ECS Fargate — unsupported
- [ ] Not a hardened SELinux environment — unsupported
- [ ] Not a very small VM instance (e.g. t2.micro) — SSI can hit init timeouts
- [ ] No PodSecurity baseline or restricted policy enforced
- [ ] Application container is **not Alpine Linux** — SSI requires glibc; Alpine uses musl libc which is ABI-incompatible. Use `python:3.x-slim`, `node:x-bookworm`, or any Debian/Ubuntu-based image

**Language and runtime**
- [ ] Application language is one of: Java, Python, Ruby, Node.js, .NET, PHP
- [ ] Runtime version is within SSI's supported range — verify against the [SSI compatibility matrix](https://docs.datadoghq.com/tracing/trace_collection/automatic_instrumentation/single-step-apm/compatibility/)
- [ ] Node.js app is not using ESM — SSI does not support ESM
- [ ] Java app is not already using a `-javaagent` JVM flag

**Existing instrumentation**
- [ ] Application has no existing `ddtrace` imports, OTel SDK calls, or custom `tracer.trace()` calls — SSI silently disables itself when detected; complete Step 0 first
- [ ] `ddtrace` is not listed in the app's dependency manifest (`requirements.txt`, `package.json`, `Gemfile`, etc.) — SSI installs the tracer automatically

---

## Context to resolve before acting

| Variable | How to resolve |
|---|---|
| `AGENT_NAMESPACE` | Same namespace used in `agent-install` (e.g. `datadog`) |
| `APP_NAMESPACE` | Ask the user which namespace their application runs in |
| `TARGET_LANGUAGES` | Identify from repo — check Dockerfiles, package manifests, or ask the user |
| `DEPLOYMENT_NAME` | Identify from repo or ask the user |
| `APP_LABEL` | Check `spec.selector.matchLabels.app` in the Deployment manifest |
| `CLUSTER_NAME` | Check `spec.global.clusterName` in `datadog-agent.yaml`, or `kubectl config current-context` — needed for kind clusters in Step 0 |

---

## Step 0 (Only if existing instrumentation detected): Remove Manual Instrumentation

Scan all source files for: `import ddtrace`, `from ddtrace`, `require 'ddtrace'`, `require("dd-trace")`, `opentelemetry`, `tracer.trace(`

Also check dependency manifests for `ddtrace` / `dd-trace` / OTel SDK packages.

If found — remove the import/package, then rebuild and reload:

### Claude runs

```bash
docker build -f <DOCKERFILE_PATH> -t <IMAGE_NAME> <BUILD_CONTEXT>
```

[DECISION: cluster type]
- kind (local): load the image into the cluster

### Claude runs

```bash
kind load docker-image <IMAGE_NAME> --name <CLUSTER_NAME>
```

- Registry-based: skip — image will be pulled on next deployment

### Claude runs

```bash
kubectl rollout restart deployment/<DEPLOYMENT_NAME> -n <APP_NAMESPACE>
kubectl wait --for=condition=Ready pod \
  -l app=<APP_LABEL> \
  -n <APP_NAMESPACE> \
  --timeout=120s
```

---

## Step 1: Extend the DatadogAgent Manifest with APM

SSI is configured on the existing `DatadogAgent` resource — do not create a separate manifest.

[DECISION: targeting scope — ask the user if unclear]
- Cluster-wide: `enabled: true` with no `targets` or `enabledNamespaces`
- Specific namespaces: `enabledNamespaces`
- Specific pods: `targets` with `podSelector`
- Excluding namespaces: `disabledNamespaces`

Recommended `ddTraceVersions`: `java: "1"`, `python: "2"`, `js: "5"`, `dotnet: "3"`, `ruby: "2"`, `php: "1"`

**Option A — Target specific workloads (recommended for production):**
```yaml
features:
  apm:
    instrumentation:
      enabled: true
      targets:
        - name: <TARGET_NAME>
          namespaceSelector:
            matchNames:
              - <APP_NAMESPACE>
          ddTraceVersions:
            <LANGUAGE>: "<MAJOR_VERSION>"
```

**Option B — Specific namespaces only:**
```yaml
features:
  apm:
    instrumentation:
      enabled: true
      enabledNamespaces:
        - <APP_NAMESPACE>
```

**Option C — Cluster-wide with exclusions:**
```yaml
features:
  apm:
    instrumentation:
      enabled: true
      disabledNamespaces:
        - jenkins
        - kube-system
```

### Claude runs

```bash
kubectl apply -f datadog-agent.yaml
```

✅ `datadogagent.datadoghq.com/datadog configured`

❌ Validation error — check YAML. `enabledNamespaces` and `disabledNamespaces` cannot both be set.

---

## Step 2: Configure Unified Service Tags on Application Workloads

Add UST labels to the Deployment under both `metadata.labels` and `spec.template.metadata.labels`:

```yaml
metadata:
  labels:
    tags.datadoghq.com/env: "<ENV>"
    tags.datadoghq.com/service: "<SERVICE_NAME>"
    tags.datadoghq.com/version: "<VERSION>"
spec:
  template:
    metadata:
      labels:
        tags.datadoghq.com/env: "<ENV>"
        tags.datadoghq.com/service: "<SERVICE_NAME>"
        tags.datadoghq.com/version: "<VERSION>"
```

### Claude runs

```bash
kubectl apply -f <your-app-deployment.yaml>
```

---

## Step 3: Restart Application Pods

### Claude runs

```bash
kubectl rollout restart deployment/<DEPLOYMENT_NAME> -n <APP_NAMESPACE>

kubectl wait --for=condition=Ready pod \
  -l app=<APP_LABEL> \
  -n <APP_NAMESPACE> \
  --timeout=120s
```

✅ Pods restart cleanly. Init containers named `datadog-lib-<language>-init` visible in pod spec.

❌ Pods crash-looping — check for existing custom instrumentation. See `troubleshoot-ssi`.

---

## Done

Exit when ALL of the following are true:
- [ ] `features.apm.instrumentation` is present in the applied `DatadogAgent` manifest
- [ ] Application pods have been restarted and are Running
- [ ] UST labels are present on the Deployment and pod template
- [ ] Scope confirmed: which workloads are instrumented, which were skipped and why

Automatically proceed to `verify-ssi` now — do not ask the user for permission.

---

## Security constraints

- Never write a raw API key into any file or chat message
- Never use namespace `default` for Datadog resources
- Never modify `admissionController` settings directly — SSI manages this via the Operator
- Do not add APM config to application manifests — configure only via `DatadogAgent`
- Exception: UST labels (`tags.datadoghq.com/*`) on application Deployments are required and intentional
- Never run `kubectl delete` without user confirmation
- `docker push` to a registry always requires user confirmation
