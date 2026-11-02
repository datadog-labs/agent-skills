# Phase 3B: Self-managed database bootstrap and existing Agent

Part of the `dd-dbm-rds-postgres` skill. Run Phases 0-2 in `SKILL.md` first; when this phase is done, return to Phase 4 there (or, on the Terraform path, to C2 in `references/terraform.md`).

The **Terraform path reaches this phase from C1, runs B2 and B3, then returns to C2**. The
**existing-Agent path runs all of B1-B4**. Use the Agent the user already runs. If they don't have one
yet and don't want Terraform to own the install, install it via the matching
`dd-agent-install-*` skill first (`dd-agent-install-linux`, `dd-agent-install-kubernetes`),
ensuring it can reach the RDS endpoint.

### B1: Existing-Agent network access

Allow the Agent's security group inbound on the RDS security group:

```bash
sg_err=$(aws ec2 authorize-security-group-ingress --region <REGION> \
  --group-id <RDS_SECURITY_GROUP_ID> \
  --protocol tcp --port <PORT> \
  --source-group <AGENT_SECURITY_GROUP_ID> 2>&1) \
  || echo "$sg_err" | grep -q 'InvalidPermission.Duplicate' \
  || { echo "$sg_err" >&2; exit 1; }
```

Rerun-safe as well: a duplicate rule (`InvalidPermission.Duplicate`) is ignored; any other error aborts.

(Terraform: `aws_vpc_security_group_ingress_rule` with `referenced_security_group_id` = the Agent's SG.)

### B2: Password secret - Terraform and existing-Agent paths

Generate the `datadog` user's password and store it as JSON in Secrets Manager (the Agent reads it via
its secrets backend; it never lands in a config file or state). **For the Terraform path, Terraform owns
the empty secret container but never its value.** C1 adds and applies this resource before sending the
flow here:

```hcl
resource "aws_secretsmanager_secret" "dbm_password" {
  name = "datadog/dbm/postgres/<DB_INSTANCE_IDENTIFIER>"
}

output "dbm_password_secret_arn" {
  value = aws_secretsmanager_secret.dbm_password.arn
}
```

Do not add an `aws_secretsmanager_secret_version`, a password variable, or a `secret_string` expression -
`sensitive = true` only redacts output and the value would still persist in state. On the Terraform
path the container C1 applied already exists under this name, so skip straight to seeding. For the
existing-Agent path, create or reuse the container directly:

```bash
DBM_PASSWORD_SECRET_ID="datadog/dbm/postgres/<DB_INSTANCE_IDENTIFIER>"
aws secretsmanager describe-secret --region <REGION> --secret-id "$DBM_PASSWORD_SECRET_ID" >/dev/null 2>&1 \
  || aws secretsmanager create-secret --region <REGION> --name "$DBM_PASSWORD_SECRET_ID" >/dev/null
```

Then seed either container out-of-band. Both paths use the same secret name, so the block sets it
itself rather than relying on a variable from an earlier shell, and fails if the container is missing or
the write fails. The value goes straight from the protected file to Secrets Manager and never through
Terraform:

```bash
umask 077
DBM_PASSWORD_SECRET_ID="datadog/dbm/postgres/<DB_INSTANCE_IDENTIFIER>"
aws secretsmanager describe-secret --region <REGION> --secret-id "$DBM_PASSWORD_SECRET_ID" >/dev/null \
  || { echo "secret container $DBM_PASSWORD_SECRET_ID not found - create it first" >&2; exit 1; }
if aws secretsmanager get-secret-value --region <REGION> --secret-id "$DBM_PASSWORD_SECRET_ID" \
     --query SecretString --output text 2>/dev/null \
     | jq -e '.password | type == "string" and length > 0' >/dev/null 2>&1; then
  echo "Reusing the existing datadog password in $DBM_PASSWORD_SECRET_ID"
else
  secret_file=$(mktemp); trap 'rm -f "$secret_file"' EXIT
  DBM_PASSWORD=$(aws secretsmanager get-random-password --region <REGION> \
    --password-length 32 --exclude-punctuation --require-each-included-type \
    --query RandomPassword --output text)
  [ -n "$DBM_PASSWORD" ] || { echo "could not generate a password" >&2; exit 1; }
  DBM_PASSWORD="$DBM_PASSWORD" jq -n '{password:$ENV.DBM_PASSWORD}' > "$secret_file"
  unset DBM_PASSWORD
  aws secretsmanager put-secret-value --region <REGION> --secret-id "$DBM_PASSWORD_SECRET_ID" \
    --secret-string "file://$secret_file" >/dev/null \
    || { echo "put-secret-value failed - the datadog password was not stored" >&2; exit 1; }
fi
```

On a rerun the block keeps the current `AWSCURRENT` password instead of replacing it, so Agents already
using it keep authenticating even if B3 later fails. Rotate it only as a coordinated change: write a new
value, rerun B3 to set it on the `datadog` role, then restart or refresh the Agents.

### B3: Create the monitoring user and grants (psql) - Terraform and existing-Agent paths

This SQL runs over a real Postgres connection to the **primary/writer** endpoint, as the master user.

**First resolve the master credentials the same way A1 does.** If the instance uses RDS-managed master
credentials (created with *Manage master credentials in AWS Secrets Manager*), the password may not be
human-retrievable, so read it from Secrets Manager instead of prompting - otherwise this path strands the
user right before `CREATE ROLE datadog`. Run this on whichever host will run `psql` (see below):

```bash
ADMIN_SECRET_ARN=$(aws rds describe-db-instances --region <REGION> \
  --db-instance-identifier <DB_INSTANCE_IDENTIFIER> \
  --query "DBInstances[0].MasterUserSecret.SecretArn" --output text)

if [ -n "$ADMIN_SECRET_ARN" ] && [ "$ADMIN_SECRET_ARN" != "None" ]; then
  printf 'RDS-managed master secret: %s\n' "$ADMIN_SECRET_ARN"
  printf 'master username (use as <MASTER_USERNAME>): %s\n' "$(aws secretsmanager get-secret-value --region <REGION> \
    --secret-id "$ADMIN_SECRET_ARN" --query SecretString --output text | jq -r .username)"
else
  printf 'master username (use as <MASTER_USERNAME>): %s\n' "$(aws rds describe-db-instances --region <REGION> \
    --db-instance-identifier <DB_INSTANCE_IDENTIFIER> \
    --query "DBInstances[0].MasterUsername" --output text)"
fi
```

The `MasterUsername` above is instance metadata, not a secret, and only the username is printed - never
the password. Only prompt for the master **password** when the instance is **not** RDS-managed (the ARN
above is `None`).

**A local machine usually cannot reach a private RDS instance**, so decide where it runs:

- **If the machine driving this flow can reach the endpoint** (publicly accessible + SG allows your IP,
  or you're on a VPN), connect directly. First save the "In every monitored database" SQL below to
  `datadog_db.sql` (it holds no secrets). The block then reads the master password (from the RDS-managed
  secret, or a no-echo prompt when the instance is not RDS-managed) and the `datadog` password from the
  B2 secret, all in the same shell that runs `psql`, so neither value crosses a command boundary, lands on
  argv, or appears in output. The SQL is fed on stdin, not typed into an interactive session, so nothing
  reaches `psql` history, and `ON_ERROR_STOP` makes any SQL error fail the block. Replace `<DBNAMES>` with
  the space-separated databases to monitor (at least `postgres`):

  ```bash
  ADMIN_SECRET_ARN=$(aws rds describe-db-instances --region <REGION> \
    --db-instance-identifier <DB_INSTANCE_IDENTIFIER> \
    --query "DBInstances[0].MasterUserSecret.SecretArn" --output text)
  PGPASSWORD=
  if [ -n "$ADMIN_SECRET_ARN" ] && [ "$ADMIN_SECRET_ARN" != "None" ]; then
    PGPASSWORD=$(aws secretsmanager get-secret-value --region <REGION> --secret-id "$ADMIN_SECRET_ARN" \
      --query SecretString --output text | jq -r .password)
    [ -n "$PGPASSWORD" ] && [ "$PGPASSWORD" != "null" ] || { echo "could not read the RDS-managed master secret" >&2; exit 1; }
  fi
  # Not `read -rsp`: in zsh -p means coprocess, so it would export an empty password.
  if [ -z "$PGPASSWORD" ]; then
    stty_saved=$(stty -g 2>/dev/null); trap 'stty "$stty_saved" 2>/dev/null' EXIT HUP INT TERM
    printf 'RDS master password: '; stty -echo 2>/dev/null; read -r PGPASSWORD
    stty "$stty_saved" 2>/dev/null; printf '\n'
    [ -n "$PGPASSWORD" ] || { echo "password is required" >&2; exit 1; }
  fi
  export PGPASSWORD
  DBM_PASSWORD=$(aws secretsmanager get-secret-value --region <REGION> \
    --secret-id "datadog/dbm/postgres/<DB_INSTANCE_IDENTIFIER>" \
    --query SecretString --output text | jq -r .password)
  [ -n "$DBM_PASSWORD" ] && [ "$DBM_PASSWORD" != "null" ] || { echo "could not read the B2 datadog password" >&2; exit 1; }
  export DBM_PASSWORD
  [ -f datadog_db.sql ] || { echo "save the per-database SQL to datadog_db.sql first" >&2; exit 1; }
  # psql expands the backtick in the child shell, so the password never reaches argv or the transcript.
  # Drop the INHERIT line on Postgres 10-14 (see the version notes below).
  psql -X -v ON_ERROR_STOP=1 -h <RDS_ENDPOINT> -p <PORT> -U <MASTER_USERNAME> -d postgres <<'SQL' || exit 1
  \set dbm_password `printf '%s' "$DBM_PASSWORD"`
  SELECT format(CASE WHEN EXISTS (SELECT FROM pg_roles WHERE rolname = 'datadog')
                     THEN 'ALTER ROLE datadog WITH LOGIN PASSWORD %L'
                     ELSE 'CREATE ROLE datadog LOGIN PASSWORD %L' END, :'dbm_password') \gexec
  ALTER ROLE datadog INHERIT;
  GRANT pg_monitor TO datadog;
  SQL
  for db in <DBNAMES>; do
    psql -X -v ON_ERROR_STOP=1 -h <RDS_ENDPOINT> -p <PORT> -U <MASTER_USERNAME> -d "$db" -f datadog_db.sql || exit 1
  done
  unset PGPASSWORD DBM_PASSWORD
  ```

  Remove the two-space list indentation before running it: the closing `SQL` heredoc marker must start at
  column 0 or the shell won't recognize it. This block already runs both SQL sections below, so skip them
  on the direct path.

- **Otherwise (private RDS - common)**, run it from a host inside the VPC that can reach the DB (the
  Agent host or a bastion). If that host is SSM-managed, deliver it agentless via `aws ssm send-command`
  (never SSH). The remote script must read **both** credentials it needs from Secrets Manager **on that
  host** (never passed through `send-command`, which SSM logs), so grant that host's role
  `secretsmanager:GetSecretValue` on both. Make the master-credentials grant temporary: add it as a
  separate, named policy just for bootstrap and remove it as soon as the role exists or the bootstrap is
  abandoned, so the Agent host keeps only the B2 password access that B4 needs:
  - **Master credentials** - to connect as the master user. For RDS-managed instances the script reads
    them from the `MasterUserSecret` ARN resolved above. For non-managed instances the B2 secret holds
    only the `datadog` user's password, not the master credentials, so first store the master credentials
    in a temporary Secrets Manager secret (the A1 pattern) for the script to read and delete it once the
    role exists, or fall back to an interactive client that can reach the DB.
  - **The `datadog` user's password** (`<DBM_PASSWORD>` in the SQL below) - from the B2 secret
    (`datadog/dbm/postgres/<DB_INSTANCE_IDENTIFIER>`). The local `DBM_PASSWORD` from B2 isn't available in
    the remote command, and passing it via `send-command` would leak it in SSM logs, so the script reads
    it from Secrets Manager on the host too.

  If the host isn't SSM-managed, hand the user the SQL to run from a reachable machine. Classic RDS
  Postgres has no browser Query Editor or Data API, so there is no console-only fallback.

**Once per cluster** (run once, against any database, using the Phase B2 password; the SSM script or manual client substitutes `<DBM_PASSWORD>` from the B2 secret on the host, never from this transcript):

```sql
-- Idempotent: create the role if absent, otherwise reset its password (safe to rerun).
DO $$
BEGIN
   IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'datadog') THEN
      CREATE ROLE datadog LOGIN PASSWORD '<DBM_PASSWORD>';
   ELSE
      ALTER ROLE datadog WITH LOGIN PASSWORD '<DBM_PASSWORD>';
   END IF;
END
$$;

-- Postgres >= 15 only: allow the role to inherit pg_monitor privileges.
ALTER ROLE datadog INHERIT;

GRANT pg_monitor TO datadog;
```

**In every monitored database** (connect with `-d <DBNAME>` and run this in *each* database you want
DBM to monitor - the schema, grants, extension, and function live inside a database):

```sql
CREATE SCHEMA IF NOT EXISTS datadog;
GRANT USAGE ON SCHEMA datadog TO datadog;
GRANT USAGE ON SCHEMA public TO datadog;
CREATE EXTENSION IF NOT EXISTS pg_stat_statements SCHEMA public;

CREATE OR REPLACE FUNCTION datadog.explain_statement(
   l_query TEXT,
   OUT explain JSON
)
RETURNS SETOF JSON AS
$$
DECLARE
   curs REFCURSOR;
   plan JSON;
BEGIN
   SET TRANSACTION READ ONLY;
   OPEN curs FOR EXECUTE pg_catalog.concat('EXPLAIN (FORMAT JSON) ', l_query);
   FETCH curs INTO plan;
   CLOSE curs;
   RETURN QUERY SELECT plan;
END;
$$
LANGUAGE 'plpgsql'
RETURNS NULL ON NULL INPUT
SECURITY DEFINER;
```

The `SET TRANSACTION READ ONLY` guard is required - it keeps the `SECURITY DEFINER` function from
executing anything that mutates data. Do not drop it. Version notes: **≥15** run `ALTER ROLE datadog
INHERIT;`; **10-14** omit it; **9.6** (EOL on RDS) has no `pg_monitor` - use the 9.6-specific grants
from the Datadog DBM Postgres docs. If `psql` isn't installed but the endpoint is reachable, hand the
user the SQL to run from their preferred client.

### B4: Configure the existing Agent check

Let the Agent read the password: (1) grant the Agent's IAM role (instance profile / ECS task role /
EKS IRSA role) `secretsmanager:GetSecretValue` on the secret ARN; (2) enable the AWS Secrets Manager
backend. The native `aws.secrets` backend requires **Agent ≥ 7.70** (and, for a containerized Cluster
Agent, **Cluster Agent ≥ 7.77**), so check the Agent you're reusing (`datadog-agent version`, or the
container image tag) first. On an older Agent, either upgrade it or point `secret_backend_command` at a
helper executable that resolves the handle (the `ENC[…]` references still work through it). Then set
`secret_backend_type: aws.secrets` and the region via `secret_backend_config`
(`aws_session.aws_region: <REGION>`) in `datadog.yaml`, or `DD_SECRET_BACKEND_TYPE=aws.secrets` plus
`DD_SECRET_BACKEND_CONFIG='{"aws_session":{"aws_region":"<REGION>"}}'` for a containerized Agent.

The check instance config:

```yaml
init_config:
instances:
  - dbm: true
    host: <RDS_ENDPOINT>
    port: <PORT>
    username: datadog
    password: 'ENC[datadog/dbm/postgres/<DB_INSTANCE_IDENTIFIER>;password]'
    aws:
      instance_endpoint: <RDS_ENDPOINT>
      region: <REGION>
    tags:
      - "dbinstanceidentifier:<DB_INSTANCE_IDENTIFIER>"
```

If you're reusing an **older Agent (≤ 7.49)**, also add `ssl: allow` to the instance config. Those
versions default to a non-TLS connection and fail against RDS instances that enforce SSL
(`rds.force_ssl`); newer Agents negotiate TLS without it.

**Monitoring more than one database on the instance.** The config above collects from the default
`postgres` database only. If you ran the B3 per-database SQL in additional application databases, enable
`database_autodiscovery` so the Agent connects to each and collects relation, schema, and per-database
explain-plan data (see the [RDS DBM docs](https://docs.datadoghq.com/database_monitoring/setup_postgres/rds/#monitoring-multiple-databases-on-a-database-host)):

```yaml
    database_autodiscovery:
      enabled: true
      # optional include/exclude patterns, e.g.:
      # include: ["app.*"]
```

Autodiscovery only works against databases where the B3 "in every monitored database" step has run (the
`datadog` schema, `pg_stat_statements` extension, and explain function must exist there), so run that SQL
in each database you want covered or those databases surface collection errors.

**Host / EC2** - write `conf.d/postgres.d/conf.yaml` and restart the Agent **on the Agent host** (not
the machine driving this flow, and **not over SSH**): deliver agentless via `aws ssm send-command`
(`AWS-RunShellScript`) when SSM-managed, otherwise hand the commands to the user.

**ECS / Docker** - deliver via an Autodiscovery label or a mounted `conf.d/postgres.d/conf.yaml`, and
set `DD_SECRET_BACKEND_TYPE=aws.secrets` on the Agent container; the **task role** needs the
secret-read policy.

**Kubernetes** - runs as a **cluster check** (single instance). Operator: enable cluster checks in the
`DatadogAgent` spec via `spec.features.clusterChecks.enabled: true` (add
`spec.features.clusterChecks.useClusterChecksRunners: true` to run them on dedicated runner pods), then
add the check under `spec.override.clusterAgent.extraConfd.configDataMap` keyed `postgres.yaml` with
`cluster_check: true`. Without the feature toggle the `cluster_check` config is never scheduled.
Helm: enable `datadog.clusterChecks.enabled: true`, `clusterChecksRunner.enabled: true`,
`clusterAgent.enabled: true`, and put the check under `clusterAgent.confd."postgres.yaml"` (with
`cluster_check: true`) - putting it under `datadog.confd` runs it on node Agents instead of as a
cluster check. Because `clusterChecksRunner.enabled` makes the runner pods *execute* the check, set
`DD_SECRET_BACKEND_TYPE` / `secret_backend_config` and the `secretsmanager:GetSecretValue` IRSA on the
**clusterChecksRunner** (that's where `ENC[…]` resolves), not the Cluster Agent - if no runners are
enabled the node Agents execute the check, so configure the backend there instead. If the reused Cluster
Agent already has a secret backend configured, it can try to resolve the handle itself before dispatch,
with an identity that cannot read the DB secret. Either grant the Cluster Agent's identity read access to
the B2 secret too, or set `DD_SECRET_BACKEND_SKIP_CHECKS=true` on the Cluster Agent so the runners
resolve it, after confirming no other cluster check relies on Cluster Agent-side resolution.
