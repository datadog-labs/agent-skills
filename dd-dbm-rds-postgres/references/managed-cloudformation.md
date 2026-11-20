# Phase 3A: Managed Agent (CloudFormation)

Part of the `dd-dbm-rds-postgres` skill. Run Phases 0-2 in `SKILL.md` first; when this phase is done, return to Phase 4 there.

Datadog runs a small Fargate Agent that connects to the RDS endpoint. The Agent's installer container
(running in-VPC) creates the `datadog` user and writes the check config; the `datadog` user's password
is generated inside that container and never leaves it. Everything here is control-plane, so it runs
from this shell. `pg_stat_statements` must be in `shared_preload_libraries` first (Phase 2) so the
installer's `CREATE EXTENSION pg_stat_statements` succeeds - if Phase 2 had to add it, wait for the
reboot; if it was already present, no reboot is needed.

> **Provenance - read before `curl`-ing anything below.** The `cluster.yaml`, `service.yaml`, and
> `install_dbm.sh` artifacts are **Datadog-maintained** and live in the Datadog-owned S3 bucket
> `dd-dbm-init-scripts` (`us-east-1`) - the same artifacts the in-product DBM Quick Install uses. The
> managed path fetches these, and inside the stack the installer container runs
> `curl -L "$DBMInstallScriptUrl" | bash` with DB credentials in its environment. That is real
> remote-code execution, so treat it as such: **fetch each template and the install script to disk and
> show the contents to the user for review before deploying** - do not pipe-to-bash, do not deploy blind,
> and never fabricate any of these files if a fetch is blocked. If your safety policy blocks the fetch or
> the deploy, stop the managed path and either hand the commands to the user or switch to Phase 3B/3C. The
> API key is passed as a Secrets Manager ARN (`DatadogAPIKeySecret`, A4) so it stays out of the task
> definition; a pinned/checksummed install script is still tracked with the DBM team, so the
> `curl … | bash` step remains the main reason this path can trip supply-chain classifiers.

### A1: RDS admin secret

The installer needs the instance's master credentials once (to create the `datadog` user), passed as
`RDSRootSecret` - a Secrets Manager secret shaped `{username, password}`.

**First check whether RDS already manages the master password.** If the instance was created with *Manage
master credentials in AWS Secrets Manager* (increasingly the console default), a secret with exactly this
shape already exists - and the password may not be retrievable by a human, so it can't be supplied by
hand. Reuse the secret and skip to A2:

```bash
ADMIN_SECRET_ARN=$(aws rds describe-db-instances --region <REGION> \
  --db-instance-identifier <DB_INSTANCE_IDENTIFIER> \
  --query "DBInstances[0].MasterUserSecret.SecretArn" --output text)
```

If that prints an ARN (not `None`), use it as `ADMIN_SECRET_ARN` and go to A2.

**Otherwise, create the secret from the master credentials.** Prompt for them with `read` so an arbitrary
password (single quotes, backslashes, `$`) never has to be pasted into a shell literal, never lands on
argv, and never enters this agent's context. Hand them to `jq` via `$ENV`, **not** `jq --arg`: `--arg`
would put the password on the `jq` process's argv, where `ps` and `/proc/<pid>/cmdline` expose it,
whereas `$ENV` keeps it off argv while still letting `jq` escape quotes/backslashes/newlines correctly:

```bash
umask 077
secret_file=$(mktemp); stty_saved=$(stty -g 2>/dev/null)
trap 'stty "$stty_saved" 2>/dev/null; rm -f "$secret_file"' EXIT HUP INT TERM
# zsh read has no -s, and -p means coprocess, so `read -rsp` there captures an
# empty string and this would store a blank credential without failing.
printf 'RDS master username: '; read -r DBM_ADMIN_USERNAME
printf 'RDS master password: '; stty -echo 2>/dev/null; read -r DBM_ADMIN_PASSWORD
stty "$stty_saved" 2>/dev/null; printf '\n'
[ -n "$DBM_ADMIN_USERNAME" ] && [ -n "$DBM_ADMIN_PASSWORD" ] || { echo "username and password are both required"; exit 1; }
export DBM_ADMIN_USERNAME DBM_ADMIN_PASSWORD
jq -n '{username:$ENV.DBM_ADMIN_USERNAME,password:$ENV.DBM_ADMIN_PASSWORD}' > "$secret_file"
unset DBM_ADMIN_PASSWORD
SECRET_ID="datadog/dbm/postgres/<DB_INSTANCE_IDENTIFIER>-admin"
if aws secretsmanager describe-secret --region <REGION> --secret-id "$SECRET_ID" >/dev/null 2>&1; then
  aws secretsmanager put-secret-value --region <REGION> --secret-id "$SECRET_ID" \
    --secret-string "file://$secret_file" >/dev/null
else
  aws secretsmanager create-secret --region <REGION> --name "$SECRET_ID" \
    --secret-string "file://$secret_file" >/dev/null
fi || { echo "could not store $SECRET_ID - do not deploy until it holds the current value" >&2; exit 1; }
ADMIN_SECRET_ARN=$(aws secretsmanager describe-secret --region <REGION> \
  --secret-id "$SECRET_ID" --query ARN --output text)
```

These are the instance's **master credentials** - treat the secret as sensitive (if you reused the
RDS-managed secret, RDS already scopes and auto-rotates it, so the scoping and rotation notes below apply
mainly to a secret you created here):
- Scope read access to the ECS execution role only (a resource policy on the secret), not account-wide.
- **Keep this secret valid; it is not one-time.** The installer container re-runs on every task launch
  (the Agent container waits on it via `DependsOn: SUCCESS`, and the task's config volume is ephemeral),
  so the service needs `RDSRootSecret` to relaunch and self-heal. Don't delete it or let it go stale; if
  you rotate the RDS master password, update this secret in lockstep. In particular, do **not** redeploy
  with `SkipInitialize=true` while dropping `RDSRootSecret` and providing no `DatadogUserSecret`: the
  installer would generate a fresh `datadog` password into the Agent's `conf.yaml` but skip the SQL that
  sets it on the role, leaving the Agent unable to authenticate.
- **To avoid standing master credentials, choose the self-managed path from the start** - you can't
  migrate to it afterward, because on the `RDSRootSecret` path the `datadog` password is generated
  in-container and never surfaced, so it can't be recovered into a secret. Create the `datadog` user
  out-of-band (the Phase 3B SQL, from a reachable host) and pass it as `DatadogUserSecret` with
  `SkipInitialize=true` instead of `RDSRootSecret`, so the managed flow never sees master credentials.
  That secret needs **both keys** - `{"username":"datadog","password":"..."}` (jq-encoded as above) -
  not the password-only secret from Phase 3B's B2.

### A2: Network access

The Fargate service runs in a security group that must reach the DB. Pick a security group for the
service (`<FARGATE_SG_ID>` - one with egress to the internet) and allow it inbound on the RDS security
group:

```bash
sg_err=$(aws ec2 authorize-security-group-ingress --region <REGION> \
  --group-id <RDS_SECURITY_GROUP_ID> \
  --protocol tcp --port <PORT> \
  --source-group <FARGATE_SG_ID> 2>&1) \
  || echo "$sg_err" | grep -q 'InvalidPermission.Duplicate' \
  || { echo "$sg_err" >&2; exit 1; }
```

This is rerun-safe: a pre-existing rule surfaces as `InvalidPermission.Duplicate` and is ignored, while
any other error still aborts.

### A3: Ensure the managed ECS cluster (once per region, idempotent)

Ensure the ECS **service-linked role** exists first - accounts that have never used ECS don't have it,
and `CreateCluster` fails with *"Unable to assume the service linked role"*. Creating it is one-time and
account-wide. It's rerun-safe: an already-created role surfaces as *"has been taken"* and is ignored,
but any other error (e.g. the caller lacks `iam:CreateServiceLinkedRole`) still aborts, so the failure
surfaces here instead of as a confusing `CreateCluster` error later:

```bash
slr_err=$(aws iam create-service-linked-role --aws-service-name ecs.amazonaws.com 2>&1) \
  || echo "$slr_err" | grep -q 'has been taken' \
  || { echo "$slr_err" >&2; exit 1; }
```

Then fetch the cluster template to disk and review it before deploying (same provenance rule as the
service template - don't deploy a fetched template unread):

```bash
curl -fsSL -o /tmp/dd-dbm-cluster.yaml \
  https://dd-dbm-init-scripts.s3.us-east-1.amazonaws.com/cloudformation/cluster.yaml
```

Read `/tmp/dd-dbm-cluster.yaml` and confirm with the user it only creates the expected ECS cluster + log
group (no IAM, no unexpected resources) before deploying. If your safety policy blocks the fetch, stop the
managed path and use Phase 3B/3C when the database bootstrap is reachable.

```bash
aws cloudformation deploy \
  --template-file /tmp/dd-dbm-cluster.yaml \
  --stack-name datadog-managed-dbm-cluster \
  --region <REGION>

CLUSTER_ARN=$(aws ecs describe-clusters --region <REGION> --clusters datadog-managed-dbm \
  --query "clusters[0].clusterArn" --output text)
```

If a first-time create fails, CloudFormation leaves the stack in `ROLLBACK_COMPLETE`, which cannot be
updated - delete it before retrying: `aws cloudformation delete-stack --stack-name
datadog-managed-dbm-cluster --region <REGION> && aws cloudformation wait stack-delete-complete
--stack-name datadog-managed-dbm-cluster --region <REGION>`.

### A4: Deploy the per-instance DBM Agent service

Fetch the template to disk first, so you can review it and aren't piping it straight into a deploy:

```bash
curl -fsSL -o /tmp/dd-dbm-service.yaml \
  https://dd-dbm-init-scripts.s3.us-east-1.amazonaws.com/cloudformation/service.yaml
```

**Disclose the IAM before deploying.** `--capabilities CAPABILITY_IAM` means this stack creates IAM
resources on the user's behalf, so don't deploy it as a black box. Read `/tmp/dd-dbm-service.yaml` and,
for each `AWS::IAM::Role` and `AWS::IAM::Policy` it defines, tell the user in plain language what the
role is, what service assumes it, and the concrete actions each attached policy grants - e.g. the ECS
task execution role reads the RDS and Datadog secrets (`secretsmanager:GetSecretValue` on those ARNs),
pulls the Agent image from ECR, and writes to CloudWatch Logs. Present that summary and get explicit
confirmation before running the deploy. This matches what the in-product UI and docs disclose, and is
the moment to make the user aware of exactly what is being granted on their behalf. (If Phase 1 had to
run `dd-aws-integration`, that step separately creates the cross-account `DatadogIntegrationRole` and
its attached policies - disclose those there too.)

**Fetch and review the install script too.** The stack's installer container runs
`curl -L "$DBMInstallScriptUrl" | bash` with the DB credentials in its environment (see the Phase 3A
provenance note), so that script is remote code executed with those credentials - reviewing only the
template isn't enough. Download it and show it to the user before deploying:

```bash
curl -fsSL -o /tmp/install_dbm.sh \
  https://dd-dbm-init-scripts.s3.us-east-1.amazonaws.com/scripts/install_dbm.sh
```

Read `/tmp/install_dbm.sh` and confirm it only performs the expected DBM bootstrap (create the `datadog`
role, grants, the `pg_stat_statements` extension, and the explain function) before continuing. Get the
user's confirmation, and if your safety policy blocks the fetch, stop the managed path and use Phase 3B/3C
when the database bootstrap is reachable.
The `DBMInstallScriptUrl` passed below must be the exact URL you just reviewed.

**Store the API key in Secrets Manager (default - keeps it out of the task definition).** The template
accepts the key as a `DatadogAPIKeySecret` ARN and injects `DD_API_KEY` into the Agent via ECS
`Secrets`/`ValueFrom`, so it never lands in the stack parameters or the task definition. Use a
**dedicated, revocable** key. Write it to a file (never onto argv) and store it as a **plaintext** secret
- the template expects the secret's raw value to be the key itself:

```bash
for f in .env.local .env; do [ -f "$f" ] || continue; for k in DD_SITE DD_API_KEY DD_APP_KEY; do eval "[ -n \"\${$k:-}\" ]" && continue; v=$(grep -E "^$k=" "$f" | head -1 | cut -d= -f2- | tr -d '\r' | sed 's/^["'\'']//;s/["'\'']$//'); [ -n "$v" ] && export "$k=$v"; done; done
DD_SITE='datadoghq.com'; export DD_SITE   # <- replace with the site confirmed in Phase 0.
# Explicit assignment, not ':=': a wrong non-empty DD_SITE in .env would otherwise survive.
: "${DD_API_KEY:?not set - run dd-account-setup (commercial sites) or supply it directly (government sites)}"; : "${DD_APP_KEY:?not set - run dd-account-setup (commercial sites) or supply it directly (government sites)}"
umask 077
# Chain onto A1's cleanup so the admin-secret temp file (if A1 created one) is still removed.
apikey_file=$(mktemp); trap 'rm -f "$apikey_file" "${secret_file:-}"' EXIT
printf '%s' "${DD_API_KEY}" > "$apikey_file"
SECRET_ID="datadog/dbm/postgres/<DB_INSTANCE_IDENTIFIER>-apikey"
if aws secretsmanager describe-secret --region <REGION> --secret-id "$SECRET_ID" >/dev/null 2>&1; then
  aws secretsmanager put-secret-value --region <REGION> --secret-id "$SECRET_ID" \
    --secret-string "file://$apikey_file" >/dev/null
else
  aws secretsmanager create-secret --region <REGION> --name "$SECRET_ID" \
    --secret-string "file://$apikey_file" >/dev/null
fi || { echo "could not store $SECRET_ID - do not deploy until it holds the current value" >&2; exit 1; }
APIKEY_SECRET_ARN=$(aws secretsmanager describe-secret --region <REGION> \
  --secret-id "$SECRET_ID" --query ARN --output text)
```

The template grants the ECS execution role `secretsmanager:GetSecretValue` on whichever ARN you pass, so
keep this secret valid for the service's lifetime. (Plaintext `DatadogAPIKey` is still accepted as a
fallback - see the API-key note below - but prefer the secret.)

Then deploy. The block re-resolves the three ARNs from A1, A3 and the step above, because a fresh shell
has none of them, and stops if any lookup comes back empty:

```bash
DD_SITE='datadoghq.com'; export DD_SITE   # <- replace with the site confirmed in Phase 0.
# Explicit assignment, not ':=': a wrong non-empty DD_SITE in .env would otherwise survive.
ADMIN_SECRET_ARN=$(aws rds describe-db-instances --region <REGION> \
  --db-instance-identifier <DB_INSTANCE_IDENTIFIER> \
  --query "DBInstances[0].MasterUserSecret.SecretArn" --output text)
if [ -z "$ADMIN_SECRET_ARN" ] || [ "$ADMIN_SECRET_ARN" = "None" ]; then
  ADMIN_SECRET_ARN=$(aws secretsmanager describe-secret --region <REGION> \
    --secret-id "datadog/dbm/postgres/<DB_INSTANCE_IDENTIFIER>-admin" --query ARN --output text)
fi
CLUSTER_ARN=$(aws ecs describe-clusters --region <REGION> --clusters datadog-managed-dbm \
  --query "clusters[0].clusterArn" --output text)
APIKEY_SECRET_ARN=$(aws secretsmanager describe-secret --region <REGION> \
  --secret-id "datadog/dbm/postgres/<DB_INSTANCE_IDENTIFIER>-apikey" --query ARN --output text)
for v in ADMIN_SECRET_ARN CLUSTER_ARN APIKEY_SECRET_ARN; do
  eval "val=\${$v:-}"
  case "$val" in ""|None) echo "$v could not be resolved - finish A1, A3 and the API-key step first" >&2; exit 1;; esac
done
aws cloudformation deploy \
  --template-file /tmp/dd-dbm-service.yaml \
  --stack-name "datadog-dbm-<DB_INSTANCE_IDENTIFIER>" \
  --region <REGION> \
  --capabilities CAPABILITY_IAM \
  --parameter-overrides \
    RDSEndpointAddress=<RDS_ENDPOINT> \
    RDSEndpointPort=<PORT> \
    RDSRootSecret="$ADMIN_SECRET_ARN" \
    ECSClusterArn="$CLUSTER_ARN" \
    ECSServiceName="dbm-<DB_INSTANCE_IDENTIFIER>" \
    SecurityGroupIDs=<FARGATE_SG_ID> \
    SubnetIDs=<ECS_CAPABLE_SUBNET_IDS> \
    VpcID=<VPC_ID> \
    CidrBlock=<VPC_CIDR> \
    DBMInstallScriptUrl=https://dd-dbm-init-scripts.s3.us-east-1.amazonaws.com/scripts/install_dbm.sh \
    DatadogAPIKeySecret="$APIKEY_SECRET_ARN" \
    DatadogSite=${DD_SITE}
```

The installer container creates the `datadog` user + grants + per-database schema/extension/explain
function, then the Agent container starts reporting. If it doesn't appear, check the ECS service and
the `datadog-managed-dbm` CloudWatch log group. The `cluster.yaml` and `service.yaml` templates are
Datadog-maintained (the same artifacts the product's Quick Install uses). `DBMInstallScriptUrl` and
`CidrBlock` are required template parameters with no default (despite `CidrBlock`'s description claiming
one), so omitting either fails the deploy. Pass `CidrBlock` to satisfy the parameter - the current
template accepts it but does not consume it in any resource, so it does not open network access on its
own; the RDS security-group ingress for the Agent is added explicitly in A2. Get the VPC's CIDR with
`aws ec2 describe-vpcs --region <REGION> --vpc-ids <VPC_ID> --query 'Vpcs[0].CidrBlock' --output text`.

> **API-key handling:** this skill passes the key as a `DatadogAPIKeySecret` ARN, so `service.yaml`
> injects `DD_API_KEY` into the Agent via ECS `Secrets`/`ValueFrom` and the key never appears in the
> stack parameters or the task definition. The older plaintext `DatadogAPIKey` parameter is still
> accepted, but it stores the key in cleartext in both the stack parameters and the task definition
> (readable by anyone with `cloudformation:DescribeStacks` or task-definition read access), so only use
> it with a dedicated, revocable key. Either way the managed path still fetches templates and an install
> script from S3 and deploys with `CAPABILITY_IAM`, so in an agent harness with a safety policy this
> deploy may be blocked from autonomous execution - expect to hand the `cloudformation deploy` to the
> user, or use the existing-Agent path (3B) / Terraform path (3C) when the database bootstrap is
> reachable.

Then go to **Phase 4**.
