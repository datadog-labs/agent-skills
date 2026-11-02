# Terraform Install (IaC path)

Use this when the user manages the cluster (or their infra) with Terraform. Installing imperatively
with `helm`/`kubectl` into a Terraform-managed setup leaves the Datadog install as drift that the
next `terraform plan` won't know about; put it in their IaC instead. Add these resources to the
user's existing Terraform project rather than creating a standalone one.

**The API key never goes into Terraform.** `sensitive = true` only redacts CLI output - the
value still lands plaintext in `terraform.tfstate`. Create the Secret out-of-band with the same
kubectl heredoc from SKILL.md Phase 3 and reference it by name from the CR.

## Providers

The `helm` and `kubernetes` providers authenticate from the kubeconfig you connected in Phase 1.
The example below is for new configurations using Helm provider v3 syntax. In an existing project,
respect its lock file and provider major version; do not silently upgrade a v2 configuration while
adding the Datadog resources.

```hcl
terraform {
  required_providers {
    helm = {
      source  = "hashicorp/helm"
      version = "~> 3.0"
    }
    kubernetes = {
      source  = "hashicorp/kubernetes"
      version = "~> 3.0"
    }
  }
}

provider "helm" {
  kubernetes = {
    config_path    = "~/.kube/config"
    config_context = "<KUBE_CONTEXT>"
  }
}

provider "kubernetes" {
  config_path    = "~/.kube/config"
  config_context = "<KUBE_CONTEXT>"
}
```

If the cluster itself is Terraform-managed, wire the providers to the cluster resource/data source
(`aws_eks_cluster` + `aws_eks_cluster_auth`, `google_container_cluster` + `google_client_config`,
`azurerm_kubernetes_cluster.kube_config`) instead of a static kubeconfig.

## Operator (`datadog.tf`)

```hcl
resource "helm_release" "datadog_operator" {
  name             = "datadog-operator"
  repository       = "https://helm.datadoghq.com"
  chart            = "datadog-operator"
  version          = "<OPERATOR_CHART_VERSION>"
  namespace        = "datadog"
  create_namespace = true
}
```

Pin `version` (resolve via `helm search repo datadog/datadog-operator --versions`) for the same
reproducibility reason as the imperative install.

For a private mirror, add
`values = [file("${path.module}/datadog-operator-values.yaml")]` to the release and use the Operator
image and pull-secret values from *Private registries* in `references/advanced.md`. Create the
`datadog` namespace and its pull secret through the user's approved secret-management workflow before
`terraform apply`; otherwise the Operator pod cannot pull its own image. Do not put registry
credentials in Terraform state.

For GKE Autopilot, the Operator `helm_release` above works on Operator 1.27+ - apply the CR with the
`experimental.agent.datadoghq.com/autopilot` annotation and the Autopilot resource requests from
the Operator path in `references/distributions.md`.

Use this resource only when SKILL.md Phase 2 selected the Operator. When it selected the Helm chart
(the user asked for Helm, cannot install CRDs, or runs GKE Autopilot on an older Operator), the
Operator chart would try to install CRDs; replace this resource with a `helm_release` for chart
`datadog` and follow *Direct chart release* at the end of this file instead of the CR and sequenced
apply below.

## DatadogAgent CR

The `DatadogAgent` is a CRD the Operator installs, so `kubernetes_manifest` can't plan it until that
CRD exists (it validates against the API server at plan time). Two reliable options:

- **Recommended:** keep the CR as `datadog-agent.yaml` (the SKILL.md Phase 3 baseline plus whatever
  distribution / product blocks apply) and `kubectl apply` it after `terraform apply`. This mirrors
  the Phase 3 flow and sidesteps the plan-time CRD dependency.
- **All-Terraform:** manage the CR with `kubernetes_manifest`, but split the apply
  (`terraform apply -target=helm_release.datadog_operator` first, then a full `terraform apply`) so
  the CRD is registered before the manifest is planned.

## Sequenced apply

```bash
terraform init
terraform plan -out=tfplan
```

Show the plan to the user; apply **that saved file** after they've reviewed it, since a bare
`terraform apply` re-plans and could differ from what they approved. Stop if the plan fails. Apply the
Operator first, then seed the Secret (kept out of state), then the CR. When the CR includes the EKS
Fargate distribution block it references `spec.global.clusterAgentTokenSecret`, so the Secret must
also carry a `token` key. Set `EKS_FARGATE='true'` in the block below in that case; the block then
resolves the token in the same shell as in `references/distributions.md` (from `.env.local` / `.env`,
else the existing `datadog-secret`, else `openssl rand -hex 16`), writes the Secret to `datadog` and to
every namespace in `WORKLOAD_NAMESPACES` (injected sidecars read it from their own namespace), and stops before `terraform apply`
if no usable token is available or the existing Secret cannot be read. It also stops before the CR if
the Secret apply fails. Leave it `'false'` for non-Fargate installs.

For a private Operator image, create the namespace and registry pull secret before the
`terraform apply` shown below. The API-key Secret remains after the Operator apply so it cannot be
captured in Terraform state.

```bash
for f in .env.local .env; do [ -f "$f" ] || continue; for k in DD_SITE DD_API_KEY DD_APP_KEY DD_CLUSTER_AGENT_TOKEN; do eval "[ -n \"\${$k:-}\" ]" && continue; v=$(grep -E "^$k=" "$f" | head -1 | cut -d= -f2- | tr -d '\r' | sed 's/^["'\'']//;s/["'\'']$//'); [ -n "$v" ] && export "$k=$v"; done; done
: "${DD_API_KEY:?not set - run dd-account-setup (commercial sites) or supply it directly (government sites)}"
EKS_FARGATE='false'   # <- 'true' when datadog-agent.yaml includes the EKS Fargate block.
WORKLOAD_NAMESPACES=''   # <- with EKS_FARGATE='true', the space-separated Fargate workload namespaces.
TOKEN_LINE=''
if [ "$EKS_FARGATE" = true ]; then
  [ -n "$WORKLOAD_NAMESPACES" ] || { echo "Set WORKLOAD_NAMESPACES to the Fargate workload namespaces - stop"; exit 1; }
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
  TOKEN_LINE="  token: \"${DD_CLUSTER_AGENT_TOKEN}\""
fi
trap 'rm -f tfplan' EXIT HUP INT TERM
if ! terraform apply tfplan; then
  echo "terraform apply FAILED - do NOT create the Secret or the CR"
  exit 1
fi
for ns in datadog $WORKLOAD_NAMESPACES; do
  if ! kubectl apply -f - <<EOF
apiVersion: v1
kind: Secret
metadata:
  name: datadog-secret
  namespace: ${ns}
type: Opaque
stringData:
  api-key: "${DD_API_KEY}"
${TOKEN_LINE}
EOF
  then
    echo "Secret creation FAILED in $ns - do NOT apply datadog-agent.yaml"
    exit 1
  fi
done
kubectl apply -f datadog-agent.yaml
```

## Direct chart release (Helm method)

This path installs the Agent from the `datadog` chart directly, so there is no Operator, no CRD and
no `DatadogAgent` CR. Do not build or apply `datadog-agent.yaml` here; applying it would fail without
the CRD or create a second Agent installation through an Operator that is already present.

1. Build `datadog-values.yaml` with the complete SKILL.md Phase 4 values (baseline, products, options)
   plus the distribution values from *Helm chart path* in `references/distributions.md` (for example
   GKE Autopilot).
2. The chart reads the key via `apiKeyExistingSecret`, so the Secret must exist before the release's
   pods start, and it stays out of Terraform state. Create it only after the user approves the plan
   in step 4: run the namespace and Secret lines of the SKILL.md Phase 4 install block (or the
   Fargate Secret block on EKS Fargate) without its `helm install`, then apply the saved plan.
3. Use this release in place of `helm_release.datadog_operator`:

   ```hcl
   resource "helm_release" "datadog_agent" {
     name             = "datadog-agent"
     repository       = "https://helm.datadoghq.com"
     chart            = "datadog"
     version          = "<AGENT_CHART_VERSION>"
     namespace        = "datadog"
     create_namespace = true
     values           = [file("${path.module}/datadog-values.yaml")]
   }
   ```

4. Run the plan block from *Sequenced apply*, show the plan to the user, and after they approve it
   apply the saved plan:

   ```bash
   trap 'rm -f tfplan' EXIT HUP INT TERM
   if ! terraform apply tfplan; then
     echo "terraform apply FAILED"
     exit 1
   fi
   ```

5. Continue to SKILL.md Phase 5 to verify the installation.
