---
name: dd-apm-k8s-agent-install
description: Install the Datadog Agent on Kubernetes using the Datadog Operator. Covers Operator install, API key setup, DatadogAgent CR deployment, and key verification.
metadata:
  version: "1.0.0"
  author: datadog-labs
  repository: https://github.com/datadog-labs/agent-skills
  tags: datadog,apm,kubernetes,agent,operator,install
  alwaysApply: "false"
---

# Install the Datadog Agent on Kubernetes

> **Before doing anything else:** Fully resolve all variables in `## Context to resolve before acting`. Do not begin Step 1 until every variable has a concrete value.

## Triggers

Invoke this skill when the user expresses intent to:
- Install or set up the Datadog Agent on a Kubernetes cluster
- Deploy the Datadog Operator or a `DatadogAgent` custom resource
- Get Datadog running on Kubernetes before enabling any product

Do NOT invoke this skill if:
- The Agent is already deployed — confirm with Step 1 first
- The user only wants APM and the Agent is confirmed healthy — use `enable-ssi`

---

## Phase 0: Load Credentials

### Claude runs

```bash
[ -f environment ] && source environment && echo "Loaded credentials from ./environment file" || echo "No environment file found"
echo "DD_API_KEY set: $([ -n "${DD_API_KEY:-}" ] && echo yes || echo no)"
echo "DD_SITE: ${DD_SITE:-not set}"
```

**If `DD_API_KEY` is already set** — proceed to Prerequisites.

**If `DD_API_KEY` is not set** — ask the user to create an `environment` file in this directory:

```bash
# environment  (this file is git-ignored — never commit it)
export DD_API_KEY='your-api-key-here'
export DD_SITE='datadoghq.com'   # change to your site
```

Tell the user: *"Create an `environment` file in this directory with your credentials (see above), then type `! source environment` to load it into our shared session. I'll wait."*

Once created, run `source environment` and verify `DD_API_KEY` is set before continuing.

> **Why a file?** Claude's shell session is separate from your terminal — `export` commands in your terminal don't reach here. The `environment` file is the persistent, session-safe way to pass credentials. It is git-ignored so it will never be committed.

---

## Prerequisites

- [ ] Kubernetes v1.20+ — `kubectl version`
- [ ] helm v3+ — `helm version`
- [ ] kubectl configured to target cluster — `kubectl config current-context`
- [ ] pup-cli installed — check with `pup --version`; if missing, install with `brew tap datadog-labs/pack && brew install pup`

---

## Context to resolve before acting

| Variable | How to resolve |
|---|---|
| `CLUSTER_NAME` | Check repo IaC, scripts, or `kubectl config current-context` |
| `DD_SITE` | Ask the user. Default: `datadoghq.com`. Options: `datadoghq.eu`, `us3.datadoghq.com`, `us5.datadoghq.com`, `ap1.datadoghq.com` |
| `AGENT_NAMESPACE` | Use `datadog` unless the repo already uses `datadog-agent` consistently |
| `CHART_VERSION` | Run `helm search repo datadog/datadog-operator --versions \| head -5` and use the latest stable |

---

## Step 1: Check for an Existing Agent Installation

### Claude runs

```bash
helm list -A | grep -i datadog
```

✅ A release shows `deployed` — Agent already installed. Skip to Step 5 to confirm health, then exit.

✅ No output — no existing install. Continue to Step 2.

---

## Step 2: Install the Datadog Operator

### Claude runs

```bash
helm repo add datadog https://helm.datadoghq.com
helm repo update

helm upgrade --install datadog-operator datadog/datadog-operator \
  --namespace <AGENT_NAMESPACE> \
  --create-namespace \
  --version <CHART_VERSION>

kubectl wait --for=condition=Ready pod \
  -l app.kubernetes.io/name=datadog-operator \
  -n <AGENT_NAMESPACE> \
  --timeout=120s
```

✅ Operator pod is Running.

❌ Pod not ready after 120s — check image pull: `kubectl describe pod -l app.kubernetes.io/name=datadog-operator -n <AGENT_NAMESPACE>`.

---

## Step 3: Create the API Key Secret

### What you need to do in a terminal

```bash
export DD_API_KEY=<your-api-key>

kubectl create secret generic datadog-secret \
  --from-literal api-key=$DD_API_KEY \
  --namespace <AGENT_NAMESPACE>
```

✅ `secret/datadog-secret created`

❌ `AlreadyExists` — confirm which key it holds via Step 5 before deciding whether to recreate.

---

## Step 4: Deploy the DatadogAgent Resource

[DECISION: cluster type]
- Self-hosted (minikube, kind): include `kubelet.tlsVerify: false` inside `spec.global`
- Managed (GKE, EKS, AKS): omit `kubelet.tlsVerify` entirely

[DECISION: APM/SSI also being enabled in this session]
- If yes: do not create a separate `DatadogAgent` for APM — extend this same manifest with `features.apm` per `enable-ssi`. One manifest, not two.
- If no: use the manifest below as-is.

Save the following as `datadog-agent.yaml`:

```yaml
apiVersion: datadoghq.com/v2alpha1
kind: DatadogAgent
metadata:
  name: datadog
  namespace: <AGENT_NAMESPACE>
spec:
  global:
    clusterName: <CLUSTER_NAME>
    site: <DD_SITE>
    credentials:
      apiSecret:
        secretName: datadog-secret
        keyName: api-key
    # Self-hosted clusters only (minikube, kind):
    # kubelet:
    #   tlsVerify: false
  features:
    orchestratorExplorer:
      enabled: true
    clusterChecks:
      enabled: true
    logCollection:
      enabled: true
      containerCollectAll: false
```

### Claude runs

```bash
kubectl apply -f datadog-agent.yaml

kubectl wait --for=condition=Ready pod \
  -l app.kubernetes.io/component=agent \
  -n <AGENT_NAMESPACE> \
  --timeout=120s 2>/dev/null || true
```

---

## Step 5: Verify the API Key

### Claude runs

```bash
kubectl logs -l app.kubernetes.io/component=agent \
  -n <AGENT_NAMESPACE> \
  --tail=50 2>/dev/null \
  | grep -iE "invalid.*api\.?key|api\.?key.*invalid" \
  || echo "No authentication errors found"
```

✅ `No authentication errors found` — key is accepted.

❌ Authentication errors found — validate the key directly:

### Claude runs

```bash
RESPONSE=$(curl -s -o /dev/null -w "%{http_code}" \
  -X GET "https://api.<DD_SITE>/api/v1/validate" \
  -H "DD-API-KEY: $DD_API_KEY")

if [ "$RESPONSE" = "200" ]; then
  echo "✅ API key is valid for <DD_SITE>"
else
  echo "❌ Validation failed (HTTP $RESPONSE) — check key and site alignment"
fi
```

If key is invalid:

### What you need to do in a terminal

```bash
export DD_API_KEY=<new-api-key>

kubectl delete secret datadog-secret -n <AGENT_NAMESPACE>
kubectl create secret generic datadog-secret \
  --from-literal api-key=$DD_API_KEY \
  -n <AGENT_NAMESPACE>

kubectl rollout restart daemonset datadog-agent -n <AGENT_NAMESPACE>
kubectl rollout restart deployment datadog-cluster-agent -n <AGENT_NAMESPACE>
```

---

## Done

Exit when ALL of the following are true:
- [ ] Datadog Operator pod is Running in `AGENT_NAMESPACE`
- [ ] `datadog-secret` exists in `AGENT_NAMESPACE`
- [ ] Agent DaemonSet pods are Running
- [ ] Step 5 returns no authentication errors

Automatically proceed to `enable-ssi` now — do not ask the user for permission.

---

## Security constraints

- Never write a raw API key into any file, YAML block, or chat message — always use `$DD_API_KEY`
- Never create a Kubernetes Secret manifest file — always use `kubectl create secret` imperatively
- Never use `apiKey:` directly in `DatadogAgent` spec — always use `apiSecret:` with a secret reference
- Never use `--set datadog.apiKey=...` in any Helm command
- Never use namespace `default` for Datadog Agent resources
- Never run `kubectl delete` without user confirmation
