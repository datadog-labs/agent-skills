# Phase 3C: Terraform-provisioned Agent

Part of the `dd-dbm-rds-postgres` skill. Run Phases 0-2 in `SKILL.md` first; when this phase is done, return to Phase 4 there.

Start this path after Phase 2. Complete C1, run B2/B3 when C1 directs, then return for C2/C3. Terraform
replaces the CloudFormation Agent stack; it does not replace the explicit reboot approval or the SQL
bootstrap. If `terraform` is unavailable, stop and ask the user to install it; do not silently fall back
to CloudFormation. Prefer the user's existing Terraform project and backend. If `.tf` files already own
the VPC, RDS instance, ECS/EKS cluster, or Agent, add the
DBM resources there rather than creating a second root with competing ownership. Ask which workspace and
backend to use before planning; do not import an existing RDS instance or parameter group without the
user explicitly choosing that ownership change.

The in-product `/databases/setup` ZIP may be used as a starting point, but inspect every generated file
and apply the hardening below before `terraform plan`. Do not treat the ZIP's "module is ready" message as
proof that the database or secrets are configured.

### C1: Secret and check-config contract

Terraform may manage **secret containers, names, ARNs, and IAM policies only**. It must not receive the
Datadog API key, RDS master password, or `datadog` password as a variable, local, data-source result,
`aws_secretsmanager_secret_version`, Helm value, user data value, or provisioner environment variable.
Marking a variable `sensitive` is insufficient because the value still lands in state.

Declare two empty secret containers in the same Terraform root: B2's DB password container (the
`aws_secretsmanager_secret.dbm_password` resource and its `dbm_password_secret_arn` output shown in B2)
and, for Fargate or EC2, a dedicated API-key container:

```hcl
resource "aws_secretsmanager_secret" "datadog_api_key" {
  name = "datadog/dbm/postgres/<DB_INSTANCE_IDENTIFIER>-apikey"
}

output "datadog_api_key_secret_arn" {
  value = aws_secretsmanager_secret.datadog_api_key.arn
}
```

Plan these empty containers first, show the plan, and get confirmation before applying them. Then go to
B2 to seed the DB password and run B3. For Fargate/EC2, seed the API key out-of-band with A4's protected
temp-file pattern:

```bash
for f in .env.local .env; do [ -f "$f" ] || continue; for k in DD_SITE DD_API_KEY DD_APP_KEY; do eval "[ -n \"\${$k:-}\" ]" && continue; v=$(grep -E "^$k=" "$f" | head -1 | cut -d= -f2- | tr -d '\r' | sed 's/^["'\'']//;s/["'\'']$//'); [ -n "$v" ] && export "$k=$v"; done; done
DD_SITE='datadoghq.com'; export DD_SITE   # <- replace with the site confirmed in Phase 0.
# Explicit assignment, not ':=': a wrong non-empty DD_SITE in .env would otherwise survive.
: "${DD_API_KEY:?not set - run dd-account-setup (commercial sites) or supply it directly (government sites)}"; : "${DD_APP_KEY:?not set - run dd-account-setup (commercial sites) or supply it directly (government sites)}"
umask 077
apikey_file=$(mktemp); trap 'rm -f "$apikey_file"' EXIT
printf '%s' "${DD_API_KEY}" > "$apikey_file"
APIKEY_SECRET_ARN=$(terraform output -raw datadog_api_key_secret_arn)
: "${APIKEY_SECRET_ARN:?terraform output datadog_api_key_secret_arn is empty - apply the secret containers first}"
aws secretsmanager put-secret-value --region <REGION> --secret-id "$APIKEY_SECRET_ARN" \
  --secret-string "file://$apikey_file" >/dev/null \
  || { echo "put-secret-value failed - the API key was not stored" >&2; exit 1; }
```

Never pass the key on argv or through `-var` / `TF_VAR_*`. After B3 succeeds, return here and continue
with C2.

Use the B4 Postgres instance config, including the `ENC[...;password]` handle, `aws.instance_endpoint`,
region, and `dbinstanceidentifier` tag. The rendered check may live in Terraform state only because it
contains the secret *handle*, not the secret value. Remove `datadog_user_password` and
`datadog_api_key` from a generated `/databases/setup` module's variables and `terraform.tfvars`; a
base64-encoded password is still plaintext and is not acceptable.

Grant `secretsmanager:GetSecretValue` on the narrowest resources:

- The runtime identity (ECS **task role**, EC2 instance profile, or EKS runner IRSA role) reads the B2 DB
  password secret so the Agent's `aws.secrets` backend can resolve `ENC[…]`.
- For Fargate, the ECS **task execution role** reads the API-key secret used by the task definition's
  `secrets` field. Do not put `DD_API_KEY` in `environment`.
- For EC2, configure `api_key: ENC[<API_KEY_SECRET_NAME_OR_ARN>]` in `datadog.yaml` so the instance role
  resolves it; do not put it in user data or a `docker run -e` argument.
- For EKS, create/reuse the Kubernetes API-key Secret out-of-band as described by the Terraform path in
  `dd-agent-install-kubernetes`; reference it by name from the existing Helm release or `DatadogAgent`.
  Do not put the API or application key in `helm_release.values`.

Use Agent >= 7.70 for the native AWS Secrets Manager backend (Cluster Agent >= 7.77 where applicable),
and configure the region exactly as in B4. Keep the Terraform backend encrypted and access-controlled;
do not commit plan files, state, `.tfvars`, or downloaded bundles containing customer identifiers.

### C2: Runtime-specific Terraform

Choose one runtime and keep its resources in the user's Terraform ownership boundary:

**Dedicated ECS Fargate Agent - default Terraform runtime when there is no Agent.** Create or reuse an
ECS cluster; create the Agent security group and a per-database ingress rule on the actual DB port; create
the log group, task execution role, task role, task definition, and singleton ECS service. The task
definition must:

- inject `DD_API_KEY` through ECS `secrets` from the API-key secret ARN;
- set `DD_SECRET_BACKEND_TYPE=aws.secrets` and
  `DD_SECRET_BACKEND_CONFIG={"aws_session":{"aws_region":"<REGION>"}}`;
- write only the non-secret check config containing `ENC[...;password]`;
- use ECS-capable subnets with Datadog, registry, and Secrets Manager egress; and
- run a pinned, approved Agent version that satisfies the secret-backend minimum.

If starting from the `/databases/setup` Fargate module, replace its plaintext environment values and
password-bearing `DD_CHECK_CONFIG_B64`, add the separate task role and least-privilege policies, and add
the missing RDS `aws` block and identifier tag before planning.

**Dedicated EC2 Agent.** Use the existing Terraform project and the `dd-agent-install-linux` security model:
an instance profile reads the two secrets, the instance is SSM-managed, and user data contains no secret
values. Do not create or use an SSH key for this flow. Deliver/restart the check through SSM or a
continuously reconciled mechanism. If starting from the generated EC2 module, remove the password from
the rendered config and API key from `user_data` / `docker run`, then add the instance profile, SSM, the
secret backend, and the RDS correlation fields.

**Existing EKS Agent.** Update the Terraform resource that already owns the Datadog Helm release or
Operator; do not install a second full Agent release into the cluster. If an Agent exists but Terraform
does not yet own it, adopt the existing release into Terraform (e.g. `terraform import`) as an explicit,
user-approved ownership change - first reproduce its current chart, version, and values so the initial
post-import plan is a no-op, then add only the DBM delta so Terraform never resets unrelated Agent
settings - or use 3B if the user won't hand ownership to Terraform, rather than installing a second
release. If no Agent exists, load `dd-agent-install-kubernetes` and use its Terraform
path first. Configure this as the B4 cluster check:
enable cluster checks and add `postgres.yaml` with `cluster_check: true` using the placement B4 gives for
your install type (Helm `clusterAgent.confd`, Operator `spec.override.clusterAgent.extraConfd.configDataMap`),
and put the secret backend plus DB-secret IRSA permission on the cluster-check runner (or node Agents when
there are no runners). If the Cluster Agent already has its own secret backend, apply the B4 note on
`DD_SECRET_BACKEND_SKIP_CHECKS` or Cluster Agent read access so the handle resolves. A generated module that puts the check under `datadog.confd`, omits `cluster_check:
true`, or puts API/app keys in Helm values must be corrected before use.

For every runtime, preserve existing provider pins and naming conventions. Use plan-time account, VPC,
subnet, and security-group validation when those values are discovered. The Agent and RDS instance may
use routed networks, but an SG-to-SG ingress rule must be valid for the selected topology; do not silently
force a same-VPC design when the user's existing network uses peering or transit routing.

### C3: Plan, approve, and apply

Format and validate the scoped Terraform root, then save a reviewable plan. The plan file stays in the
Terraform root (`dbm.tfplan`, owner-only) until the apply block below removes it, because agent runtimes
start a fresh shell per command and a temp directory cleaned up at the end of this block would be gone
before approval. Stop here if any step fails:

```bash
umask 077
terraform fmt
terraform init || { echo "terraform init FAILED - stop" >&2; exit 1; }
terraform validate || { echo "terraform validate FAILED - stop" >&2; exit 1; }
terraform plan -out=dbm.tfplan || { echo "terraform plan FAILED - stop" >&2; exit 1; }
terraform show dbm.tfplan
```

Before applying, confirm that the plan:

- contains no secret values or password-bearing encoded strings;
- does not replace, destroy, or import the RDS instance, its parameter group, or unrelated shared
  infrastructure, and does not import an existing Agent release unless the user explicitly approved
  adopting it for the EKS path (C2);
- grants secret access only to the runtime/execution identities that require it; and
- changes only the intended Agent, IAM, logging, and network resources.

Show that summary and get explicit confirmation before applying. Do not use `-auto-approve`. If the
API-key container was empty when the plan was made, seed it first and create a fresh plan so the service
never launches against a missing secret version. Only after the user confirms, apply exactly the
reviewed plan from the same Terraform root:

```bash
[ -f dbm.tfplan ] || { echo "dbm.tfplan not found - re-run the plan block and get approval again" >&2; exit 1; }
trap 'rm -f dbm.tfplan' EXIT HUP INT TERM
if terraform apply dbm.tfplan; then
  echo "apply complete"
else
  echo "terraform apply FAILED - do NOT verify or report success"
  exit 1
fi
```

Do not commit `dbm.tfplan`; the apply block removes it on every exit path. If the user declines the
plan, delete it with `rm -f dbm.tfplan` instead.

After apply, inspect the runtime without bypassing its management plane: ECS service/tasks and CloudWatch
logs for Fargate, SSM plus Agent status for EC2, or Helm/Operator and cluster-check-runner status for EKS.
Then continue to Phase 4; infrastructure creation alone is not proof that DBM is working.
