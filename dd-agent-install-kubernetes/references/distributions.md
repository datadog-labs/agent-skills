# Distribution-specific configuration

Merge the blocks for the distribution and mode confirmed in SKILL.md Phase 1. Oracle OKE, VMware Tanzu
(TKG), and self-managed clusters need no changes; Rancher (RKE2/K3s) only needs `criSocketPath` when the
agent can't reach the embedded containerd (see SKILL.md Phase 1).

## Operator path (SKILL.md Phase 3)

**GKE Autopilot** (Operator 1.27+) - override the image registry, add the Autopilot annotation to
the `DatadogAgent` metadata, and set container resource requests.

GKE Autopilot enforces a WorkloadAllowlist through its Warden admission webhook. Warden only admits
Agent pods whose image digests appear in the GKE-synchronized allowlist, and that allowlist covers
the `gcr.io/datadoghq` images - not `registry.datadoghq.com`. The Phase 3 baseline sets
`registry: registry.datadoghq.com`; leaving that in place on Autopilot gets the DaemonSet rejected by
Warden with an admission-webhook denial, not a pull failure. Override the registry back to
`gcr.io/datadoghq` in `spec.global` (this is a deliberate exception to the baseline default; do not
reach for a private mirror or a different image path to work around the rejection):
```yaml
spec:
  global:
    registry: gcr.io/datadoghq
```

Autopilot's default container limits (50m CPU / 100Mi) are low enough to cause OOMKills, so set
resource requests as well. Include only the containers for the products actually enabled:
```yaml
metadata:
  annotations:
    experimental.agent.datadoghq.com/autopilot: "true"
spec:
  override:
    nodeAgent:
      containers:
        agent:
          resources:
            requests:
              cpu: "200m"
              memory: "256Mi"
        trace-agent:
          resources:
            requests:
              cpu: "100m"
              memory: "200Mi"
        process-agent:
          resources:
            requests:
              cpu: "100m"
              memory: "200Mi"
        system-probe:
          resources:
            requests:
              cpu: "100m"
              memory: "400Mi"
```
Do not add `spec.global.provider: gke-autopilot`; it is not part of the generated Operator
configuration. For Operators older than 1.27, use the Helm path in SKILL.md Phase 4 (with the GKE
Autopilot values under *Helm chart path* below) instead.

**OpenShift**: OpenShift users usually drive the cluster with `oc`, which is kubectl-compatible.
Use the OperatorHub path below and merge the generated distribution settings:
```yaml
spec:
  global:
    kubelet:
      tlsVerify: false
  features:
    apm:
      hostPortConfig:
        enabled: true
      unixDomainSocketConfig:
        enabled: false
    dogstatsd:
      unixDomainSocketConfig:
        enabled: false
  override:
    clusterAgent:
      serviceAccountName: datadog-agent-scc
    nodeAgent:
      serviceAccountName: datadog-agent-scc
      hostNetwork: true
```
Keep the security context and control-plane tolerations emitted by the current setup UI. The
certified Operator manages the Agent SCCs; verify them with `oc get scc | grep -i datadog`.

**EKS Fargate profiles**: Fargate nodes can't schedule the node-agent DaemonSet, so on Fargate the
Agent runs as a sidecar injected into workload pods by the admission controller.

On a Fargate-only cluster (no EC2 nodes), the Operator and Cluster Agent Pods in `datadog` also run
on Fargate, so an active Fargate profile must select the `datadog` namespace (and any Pod labels the
profile requires). Check before installing:

```bash
REGION='<REGION>'; CLUSTER='<CLUSTER_NAME>'
for fp in $(aws eks list-fargate-profiles --region "$REGION" --cluster-name "$CLUSTER" --query 'fargateProfileNames[]' --output text); do
  aws eks describe-fargate-profile --region "$REGION" --cluster-name "$CLUSTER" --fargate-profile-name "$fp" \
    --query 'fargateProfile.[fargateProfileName,status,selectors]' --output json
done
```

If no `ACTIVE` profile selects `datadog`, stop and ask the user to add one through their usual
workflow (Terraform, eksctl, or the console). Do not create a Fargate profile yourself without
showing the exact change and getting confirmation.

Add to `spec.features`:
```yaml
admissionController:
  agentSidecarInjection:
    enabled: true
    provider: fargate
```
The injected sidecar authenticates to the Cluster Agent with a shared token, so `datadog-secret`
must carry a `token` key alongside `api-key`, and the CR must reference it. The token must be at
least 32 alphanumeric characters. An unset `DD_CLUSTER_AGENT_TOKEN` produces an unusable Secret, and
an `export` from an earlier command does not survive into a fresh shell, so resolve the token in the
same block that writes the Secret. Injected sidecars read `datadog-secret` from their own Pod's
namespace, so the block writes the same Secret, with the same token, to `datadog` and to every
selected Fargate workload namespace. The block below uses `DD_CLUSTER_AGENT_TOKEN` from the environment
or `.env.local` / `.env` if set, otherwise reuses the token already in `datadog-secret`, otherwise
generates one with `openssl rand -hex 16`. It generates a token only when `datadog-secret` or its
`token` key is confirmed absent; any other read or decode error stops the block, so a transient API
failure cannot overwrite a token that running pods already use. If `openssl` is unavailable and no token exists yet, ask
the user to add a 32+ character alphanumeric `DD_CLUSTER_AGENT_TOKEN=` line to `.env.local` (not in
chat) and re-run. Run this block in place of the API-key-only Secret block (SKILL.md Phase 3 or
Phase 4):

Set `WORKLOAD_NAMESPACES` to the space-separated Fargate workload namespaces confirmed in SKILL.md
Phase 1.

```bash
for f in .env.local .env; do [ -f "$f" ] || continue; for k in DD_SITE DD_API_KEY DD_APP_KEY DD_CLUSTER_AGENT_TOKEN; do eval "[ -n \"\${$k:-}\" ]" && continue; v=$(grep -E "^$k=" "$f" | head -1 | cut -d= -f2- | tr -d '\r' | sed 's/^["'\'']//;s/["'\'']$//'); [ -n "$v" ] && export "$k=$v"; done; done
: "${DD_API_KEY:?not set - run dd-account-setup (commercial sites) or supply it directly (government sites)}"
WORKLOAD_NAMESPACES='<FARGATE_WORKLOAD_NAMESPACES>'   # <- e.g. 'shop payments'
case "$WORKLOAD_NAMESPACES" in *'<'*|'') echo "Set WORKLOAD_NAMESPACES to the Fargate workload namespaces - stop"; exit 1;; esac
if [ -z "${DD_CLUSTER_AGENT_TOKEN:-}" ]; then
  EXISTING_TOKEN=$(kubectl get secret datadog-secret -n datadog --ignore-not-found -o jsonpath='{.data.token}') || { echo "Could not read datadog-secret - fix cluster access and re-run"; exit 1; }
  if [ -n "$EXISTING_TOKEN" ]; then
    DD_CLUSTER_AGENT_TOKEN=$(printf '%s' "$EXISTING_TOKEN" | base64 --decode) || { echo "Could not decode the token in datadog-secret"; exit 1; }
  else
    DD_CLUSTER_AGENT_TOKEN=$(openssl rand -hex 16 2>/dev/null)
  fi
fi
case "${DD_CLUSTER_AGENT_TOKEN:-}" in ''|*[!A-Za-z0-9]*) echo "No usable cluster agent token - add DD_CLUSTER_AGENT_TOKEN to .env.local and re-run"; exit 1;; esac
[ "${#DD_CLUSTER_AGENT_TOKEN}" -ge 32 ] || { echo "DD_CLUSTER_AGENT_TOKEN must be at least 32 characters"; exit 1; }
for ns in datadog $WORKLOAD_NAMESPACES; do
  kubectl apply -f - <<EOF || { echo "Secret creation FAILED in $ns - stop"; exit 1; }
apiVersion: v1
kind: Secret
metadata:
  name: datadog-secret
  namespace: ${ns}
type: Opaque
stringData:
  api-key: "${DD_API_KEY}"
  token: "${DD_CLUSTER_AGENT_TOKEN}"
EOF
done
```

Then add the token wiring to `spec.global`:
```yaml
spec:
  global:
    clusterAgentTokenSecret:
      secretName: datadog-secret
      keyName: token
```
Create the token in advance so DaemonSet and Fargate sidecars share the same value. The Secret
must be named `datadog-secret` for Fargate admission-controller injection.

**Fargate RBAC.** The injected sidecar runs under the application Pod's service account, and the
Operator's own bindings do not cover it. Before labeling workloads, list the service accounts the
selected workloads use, show this manifest, and apply it after confirmation, with one subject per
service account (the admission controller does not change `serviceAccountName`):

```yaml
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRole
metadata:
  name: datadog-agent-fargate
rules:
  - apiGroups: [""]
    resources: ["nodes", "namespaces", "endpoints"]
    verbs: ["get", "list"]
  - apiGroups: [""]
    resources: ["nodes/metrics", "nodes/spec", "nodes/stats", "nodes/proxy", "nodes/pods", "nodes/healthz"]
    verbs: ["get"]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRoleBinding
metadata:
  name: datadog-agent-fargate
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: ClusterRole
  name: datadog-agent-fargate
subjects:
  - kind: ServiceAccount
    name: <SERVICE_ACCOUNT>
    namespace: <WORKLOAD_NAMESPACE>
```

**Fargate logs.** The baseline `logCollection` settings configure the node Agent, not the injected
sidecars. When logs are enabled, require Agent 7.71.0 or later and turn on kubelet log collection for
newly injected sidecars on the Cluster Agent. For logs from every container, add a sidecar profile;
otherwise rely on per-container Autodiscovery annotations:
```yaml
spec:
  features:
    admissionController:
      agentSidecarInjection:
        profiles:
          - env:
              - name: DD_LOGS_CONFIG_CONTAINER_COLLECT_ALL
                value: "true"
  override:
    clusterAgent:
      env:
        - name: DD_ADMISSION_CONTROLLER_AGENT_SIDECAR_KUBELET_API_LOGGING_ENABLED
          value: "true"
```

Label Fargate workload pods with `agent.datadoghq.com/sidecar: fargate`, or configure a
deliberate selector. Do not mutate every unlabelled workload by default. On a mixed cluster, keep
the DaemonSet for EC2 nodes. On a Fargate-only cluster, disable the node-agent DaemonSet after
confirming there are no schedulable EC2 nodes:

```yaml
spec:
  override:
    nodeAgent:
      disabled: true
```

**AKS kubelet certificate handling**: inspect
`kubernetes.azure.com/kubelet-serving-ca` on the selected cluster's nodes. Prefer the serving CA
configuration when it is available. Only disable verification when the user confirms the
workaround is required:
```yaml
global:
  kubelet:
    tlsVerify: false
```

This weakens verification of the kubelet API. Do not apply it preemptively.

## OpenShift-specific install

The Operator can be installed via the OperatorHub Bundle instead of Helm. The Datadog Operator is
published in the **certified-operators** catalog (not `redhat-operators`, which is Red Hat's own
operators), and the certified operator manages the Agent SCCs for you:
```yaml
apiVersion: operators.coreos.com/v1alpha1
kind: Subscription
metadata:
  name: datadog-operator
  namespace: openshift-operators
spec:
  channel: stable
  name: datadog-operator
  source: certified-operators
  sourceNamespace: openshift-marketplace
```

Show the Subscription and the confirmed context, and apply it with `kubectl apply -f` after
confirmation. OLM installs the Operator and its CRD asynchronously, so wait for both before applying
the `DatadogAgent`, and stop if they do not become ready:

```bash
ok=''
for i in $(seq 1 60); do
  csv=$(kubectl get subscription datadog-operator -n openshift-operators -o jsonpath='{.status.installedCSV}' 2>/dev/null)
  phase=$([ -n "$csv" ] && kubectl get csv "$csv" -n openshift-operators -o jsonpath='{.status.phase}' 2>/dev/null)
  [ "$phase" = Failed ] && { echo "Operator install FAILED (CSV $csv) - stop"; exit 1; }
  if [ "$phase" = Succeeded ] && kubectl wait --for=condition=established crd/datadogagents.datadoghq.com --timeout=10s >/dev/null 2>&1; then ok=1; break; fi
  sleep 10
done
[ -n "$ok" ] || { echo "Operator or DatadogAgent CRD not ready after 10 minutes - stop"; exit 1; }
echo "Operator $csv ready"
```

Create the API key secret **and** the `DatadogAgent` resource
in the `openshift-operators` namespace - both must live where the operator was deployed so the
CR can resolve `datadog-secret` and the operator picks up the CR. The Secret is created only by the
Phase 3 heredoc, with its namespace changed; it never goes into `datadog-agent.yaml` or any other
saved file (a saved copy holds the literal `${DD_API_KEY}`, and applying it overwrites the real key):

```bash
for f in .env.local .env; do [ -f "$f" ] || continue; for k in DD_SITE DD_API_KEY DD_APP_KEY; do eval "[ -n \"\${$k:-}\" ]" && continue; v=$(grep -E "^$k=" "$f" | head -1 | cut -d= -f2- | tr -d '\r' | sed 's/^["'\'']//;s/["'\'']$//'); [ -n "$v" ] && export "$k=$v"; done; done
: "${DD_API_KEY:?not set - run dd-account-setup (commercial sites) or supply it directly (government sites)}"
kubectl apply -f - <<EOF || { echo "Secret creation FAILED - do NOT apply datadog-agent.yaml"; exit 1; }
apiVersion: v1
kind: Secret
metadata:
  name: datadog-secret
  namespace: openshift-operators
type: Opaque
stringData:
  api-key: "${DD_API_KEY}"
EOF
kubectl apply -n openshift-operators -f datadog-agent.yaml
```

`datadog-agent.yaml` holds only the `DatadogAgent`, with the namespace pinned in its metadata:

```yaml
apiVersion: datadoghq.com/v2alpha1
kind: DatadogAgent
metadata:
  name: datadog
  namespace: openshift-operators
# ... rest of the DatadogAgent spec as in the Phase 3 baseline ...
```

Without the namespace pin, `kubectl apply` lands them in the user's current namespace (often
`default`), the operator running in `openshift-operators` can't see the CR, and the install
silently no-ops.

Show the completed manifests and the confirmed kubectl context, and ask for confirmation
immediately before applying them (SKILL.md Phase 3 *Apply*).

## Helm chart path (SKILL.md Phase 4)

**GKE Autopilot** - set:
```yaml
datadog:
  kubelet:
    useApiServer: true
  apm:
    portEnabled: true
    socketEnabled: false
agents:
  priorityClassCreate: true
  containers:
    agent:
      resources:
        requests:
          cpu: 200m
          memory: 256Mi
    traceAgent:
      resources:
        requests:
          cpu: 100m
          memory: 200Mi
    processAgent:
      resources:
        requests:
          cpu: 100m
          memory: 200Mi
    systemProbe:
      resources:
        requests:
          cpu: 100m
          memory: 400Mi
providers:
  gke:
    autopilot: true
```
The chart turns off Unix-socket APM intake on Autopilot, so with the baseline's `socketEnabled: true`
alone it omits the trace-agent. The `apm` values above switch trace intake to TCP port 8126. If the
user declined APM, keep the *Declining APM* values from SKILL.md Phase 4 instead of this `apm` block.

**OpenShift** - set:
```yaml
datadog:
  kubelet:
    tlsVerify: false
  apm:
    portEnabled: true
    socketEnabled: false
agents:
  useHostNetwork: true
  podSecurity:
    securityContextConstraints:
      create: true
clusterAgent:
  podSecurity:
    securityContextConstraints:
      create: true
```

**EKS Fargate profiles** - set:
```yaml
clusterAgent:
  tokenExistingSecret: datadog-secret
  admissionController:
    agentSidecarInjection:
      enabled: true
      provider: fargate
```

On a Fargate-only cluster, confirm a Fargate profile selects the `datadog` namespace first (check
under *Operator path* above). Apply the *Fargate RBAC* manifest from *Operator path* before labeling
workloads. When logs are enabled, require Agent 7.71.0 or later and add the Helm form of *Fargate
logs* (drop the `profiles` entry unless the user wants logs from every container):

```yaml
clusterAgent:
  env:
    - name: DD_ADMISSION_CONTROLLER_AGENT_SIDECAR_KUBELET_API_LOGGING_ENABLED
      value: "true"
  admissionController:
    agentSidecarInjection:
      profiles:
        - env:
            - name: DD_LOGS_CONFIG_CONTAINER_COLLECT_ALL
              value: "true"
```

The injected sidecar and the Cluster Agent must share a token, so `datadog-secret` needs a `token`
key alongside `api-key`. Create it in advance so every pod shares the same value - an unset value
yields an unusable Secret. Create the Secret with the Fargate Secret block under *Operator path*
above (it resolves or generates the token in the same shell and writes it to `datadog` and every
workload namespace), in place of the Secret part of the Phase 4 install block.

Label Fargate workload pods with `agent.datadoghq.com/sidecar: fargate`, or configure an
intentional selector. Keep the DaemonSet on mixed EC2/Fargate clusters and disable it only for a
confirmed Fargate-only cluster:

```yaml
agents:
  enabled: false
```

**AKS Windows nodes** - `targetSystem: windows` applies to a Windows-targeted release. Set it in
that release's values file, together with TCP trace intake:
```yaml
targetSystem: windows
datadog:
  apm:
    portEnabled: true
    socketEnabled: false
```
The chart does not use Unix-socket APM intake on Windows, so the baseline's `socketEnabled: true`
alone leaves the Windows release without a trace-agent. If the user declined APM, keep the
*Declining APM* values instead of this `apm` block.
Do not add it to a release covering Linux nodes. For a Windows-only cluster, add it to
`datadog-values.yaml` and use the Phase 4 *Install* command unchanged. For mixed clusters, confirm the
node pools and build separately scoped releases using the current chart guidance: keep the Linux
release from Phase 4, and give the Windows release its own values file and a distinct release name.
Show both values files and the confirmed kubectl context, and ask for confirmation before each
install (Phase 4 *Install*):
```bash
helm install datadog-agent-windows datadog/datadog \
  --namespace datadog \
  -f datadog-values-windows.yaml \
  --version <AGENT_CHART_VERSION>
```

For AKS, enable the provider block. If the user explicitly confirms kubelet TLS verification must
be disabled, add:
```yaml
providers:
  aks:
    enabled: true
datadog:
  kubelet:
    tlsVerify: false
```
