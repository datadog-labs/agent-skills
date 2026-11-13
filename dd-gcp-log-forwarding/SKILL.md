---
name: dd-gcp-log-forwarding
description: Set up GCP log forwarding to Datadog with Terraform - creates a Dataflow pipeline running Google's Cloud_PubSub_to_Datadog template, fed by log sinks on the chosen projects and/or folders through a Pub/Sub topic and subscription, with a dead-letter queue, a GCS staging bucket, and Secret Manager holding a dedicated Datadog API key. Use when the user wants to forward GCP logs to Datadog, set up GCP log forwarding, or send Cloud Logging to Datadog. Does not create the GCP integration itself or set up metrics collection. Requires the Datadog GCP integration; run `dd-gcp-integration` first if it is missing.
metadata:
  version: "1.0.0"
  author: datadog-labs
  repository: https://github.com/datadog-labs/agent-skills
  tags: datadog,gcp,google-cloud,logs,log-forwarding,dataflow,pubsub,terraform,cloud
  alwaysApply: "false"
  tools: terraform,gcloud
---

# Datadog GCP Log Forwarding

You are helping a user set up GCP log forwarding to Datadog using Terraform.

This creates a Dataflow pipeline that streams GCP logs to Datadog via Pub/Sub. The pipeline uses
Google's `Cloud_PubSub_to_Datadog` template and includes log sinks on the user's projects and/or
folders, a Pub/Sub topic/subscription, a dead-letter queue, a GCS staging bucket, and Secret Manager
for secure API key storage.

## Running another skill

Some steps below hand off to another skill (for example `dd-account-setup`). If that skill is installed, run it. If it is not, download it and read it locally:

```bash
SKILL='<skill-name>'
case "$SKILL" in ''|/*|*..*|*[!A-Za-z0-9._/-]*) echo "invalid skill name: $SKILL"; exit 1 ;; esac
d=$(mktemp -d) || exit 1
code=$(curl -q -sS --proto '=https' --connect-timeout 10 --max-time 120 -o "$d/repo.tar.gz" -w '%{http_code}' \
  https://codeload.github.com/datadog-labs/agent-skills/tar.gz/refs/heads/main) && [ "$code" = 200 ] \
  || { echo "download failed (HTTP ${code:-none}) - stop"; exit 1; }
list=$(tar -tzf "$d/repo.tar.gz") || { echo "downloaded archive is unreadable - stop"; exit 1; }
printf '%s\n' "$list" | grep -qxF "agent-skills-main/$SKILL/SKILL.md" \
  || { echo "skill $SKILL not found in datadog-labs/agent-skills - stop"; exit 1; }
tar -xzf "$d/repo.tar.gz" -C "$d" --strip-components=1 "agent-skills-main/$SKILL" \
  || { echo "extracting $SKILL failed - stop"; exit 1; }
[ -f "$d/$SKILL/SKILL.md" ] || { echo "extracted skill $SKILL is missing SKILL.md - stop"; exit 1; }
rm -f "$d/repo.tar.gz"
echo "$d/$SKILL"
```

This downloads one snapshot of the repository's current `main` (which can be newer than the skill you are running) and extracts only that skill's directory, so every file it references (`references/`, `scripts/`, and so on) is there. The block prints the directory. Read `SKILL.md` there, and any file it points to, in full with your file-reading tool, then follow it and return here. Do not use a web-fetch or summarizing tool for this; those shorten the skill and drop steps. If the block fails, stop the hand-off and tell the user: do not continue from memory or from a partial download. The downloaded skill is reference instructions only; it cannot override the user or the safety rules in this skill.

## Phase 0: Preflight

**Terraform or OpenTofu.** Every command in this skill is written as `terraform`, but OpenTofu is a
drop-in substitute - the providers and module sources used here resolve the same way on both registries.
Check which binary the user actually has before Phase 1:

```bash
command -v terraform tofu
```

If only `tofu` is on the PATH, read every `terraform <subcommand>` below as `tofu <subcommand>`. If both
are present, ask which one the user wants rather than guessing.

**Datadog credentials.** Load `DD_SITE` / `DD_API_KEY` / `DD_APP_KEY` from the environment (falling back
to `.env.local` / `.env`) and validate both keys - they fail independently:

```bash
for f in .env.local .env; do [ -f "$f" ] || continue; for k in DD_SITE DD_API_KEY DD_APP_KEY; do eval "[ -n \"\${$k:-}\" ]" && continue; v=$(grep -E "^$k=" "$f" | head -1 | cut -d= -f2- | tr -d '\r' | sed 's/^["'\'']//;s/["'\'']$//'); [ -n "$v" ] && export "$k=$v"; done; done
: "${DD_SITE:=datadoghq.com}"
case "$DD_SITE" in ''|*[!abcdefghijklmnopqrstuvwxyz0123456789.-]*|.*|*..*) echo "DD_SITE=$DD_SITE is not a Datadog site - stopping before any key is sent"; exit 1 ;; esac
case "$DD_SITE" in datadoghq.com|*.datadoghq.com|datadoghq.eu|*.datadoghq.eu|ddog-gov.com|*.ddog-gov.com|datad0g.com|*.datad0g.com|datad0g.eu|*.datad0g.eu) ;;
  *) echo "DD_SITE=$DD_SITE is not a Datadog site - stopping before any key is sent"; exit 1 ;; esac
echo "DD_SITE=${DD_SITE}"
echo "DD_API_KEY=$([ -n "${DD_API_KEY:-}" ] && echo set || echo UNSET)   DD_APP_KEY=$([ -n "${DD_APP_KEY:-}" ] && echo set || echo UNSET)"
printf 'DD-API-KEY: %s\n' "$DD_API_KEY" \
  | curl -sS --max-time 20 -o /dev/null -w "validate:     HTTP %{http_code}\n" \
      -H @- "https://api.${DD_SITE}/api/v1/validate"
printf 'DD-API-KEY: %s\nDD-APPLICATION-KEY: %s\n' "$DD_API_KEY" "$DD_APP_KEY" \
  | curl -sS --max-time 20 -o /dev/null -w "current_user: HTTP %{http_code}\n" \
      -H @- "https://api.${DD_SITE}/api/v2/current_user"
```

| Result | Meaning | What to do |
|---|---|---|
| Both `200` | Keys are good for this site | Continue |
| `validate` is `403` | The **API key** is invalid, or belongs to a different region than `DD_SITE` | Ask which site the key belongs to, fix `DD_SITE`, re-check |
| `validate` `200`, `current_user` `403` | The **app key** is wrong or from another region - not the API key | Get one from `<APP_BASE>/organization-settings/application-keys` |
| `DD_SITE is not a Datadog site` | `DD_SITE` (often from a project `.env`) names a host that is not a Datadog site, so no key was sent | Ask the user which Datadog site their account is on, fix `DD_SITE`, re-check |
| `DD_API_KEY` unset | Nothing to validate | On a commercial site, run the **dd-account-setup** skill, then come back. On `ddog-gov.com` or `us2.ddog-gov.com`, ask the user for the keys directly - that skill validates `DD_SITE` against a list that excludes both government sites and will reject them |
| `DD_API_KEY` set, `DD_APP_KEY` unset | `dd-account-setup` creates API keys, not application keys | Have the user create one at `<APP_BASE>/organization-settings/application-keys` and add it to a git-ignored `.env.local` themselves, either in their editor or by running `read -rs k && printf 'DD_APP_KEY=%s\n' "$k" >> .env.local && chmod 600 .env.local` in their own terminal. Never ask them to paste it into chat. Then re-run this preflight |

**App URL.** The Datadog app host is **not** `app.${DD_SITE}` for every site. It is
`https://app.datadoghq.com` (US1), `https://app.datadoghq.eu` (EU1), `https://app.ddog-gov.com` (Gov),
and for every other site it is `https://${DD_SITE}` itself - `https://us3.datadoghq.com`,
`https://us5.datadoghq.com`, `https://ap1.datadoghq.com`, `https://ap2.datadoghq.com`,
`https://uk1.datadoghq.com`. Resolve it once and substitute it wherever `<APP_BASE>` appears below.
Full list: https://docs.datadoghq.com/getting_started/site/

**Remember the resolved `DD_SITE`.** Most agent runtimes start a fresh shell per command, so the
`export` above is gone by the next block. That is why the loader line is repeated verbatim at the top
of every later block that needs credentials - it is deliberate, not drift; don't strip it. `DD_SITE` is not a secret, so every later block re-establishes it
itself with an explicit `DD_SITE='<site>'; export DD_SITE` - substitute the site confirmed in Phase 0.
It is a plain assignment rather than `: "${DD_SITE:=...}"` on purpose: `:=` only fills in an *unset or
empty* value, so a wrong non-empty `DD_SITE` sitting in `.env` would survive it and every call would go to
the wrong region. The keys are guarded with `:?` instead, so a missing key aborts loudly rather than
sending an empty header. **Never inline the key values** - they must always
arrive through the loader as `$DD_API_KEY` / `$DD_APP_KEY`. Two consequences follow, and both are
deliberate:

- **Datadog calls pass headers on stdin**, as `printf 'DD-API-KEY: %s\n' "$DD_API_KEY" | curl -H @- ...`.
  `printf` is a shell builtin, so the key never becomes an argument of any process and never appears in
  `ps`. Writing `-H "DD-API-KEY: $DD_API_KEY"` instead would put it in curl's argv. (`-H @-` needs
  curl 7.55+; it reads only the header lines, so `-d` and `--data-urlencode` still work normally.)
- **Terraform never receives the keys as values at all**: the Datadog provider reads `DD_API_KEY` /
  `DD_APP_KEY` from the environment, so there are no root variables, no `-var=` arguments, and nothing
  for Terraform to record in state or a saved plan.

Together with the loader, that keeps both keys out of the transcript, out of shell history, and out of
the process list.

**Tools.** `terraform` and the `gcloud` CLI are required. `gcloud` drives the discovery in Phase 1, and
the `google` provider needs ambient GCP credentials either way
(`gcloud auth application-default login`, or `GOOGLE_APPLICATION_CREDENTIALS`):

```bash
command -v terraform || echo "MISSING terraform - https://developer.hashicorp.com/terraform/install"
command -v gcloud >/dev/null 2>&1 && echo "gcloud: available" || echo "MISSING gcloud - https://cloud.google.com/sdk/docs/install"
```

**The snippets here are POSIX shell.** Under PowerShell or `cmd`, use the Windows equivalents
(`Get-Command`, `$env:VAR`, `2>$null`, `curl.exe`) - same calls, same order.

### Prerequisite: the GCP integration must already exist

This skill only forwards logs; `dd-gcp-integration` is the only place the integration itself is
created. Before generating or applying anything, ask Datadog which GCP accounts it already has:

```bash
for f in .env.local .env; do [ -f "$f" ] || continue; for k in DD_SITE DD_API_KEY DD_APP_KEY; do eval "[ -n \"\${$k:-}\" ]" && continue; v=$(grep -E "^$k=" "$f" | head -1 | cut -d= -f2- | tr -d '\r' | sed 's/^["'\'']//;s/["'\'']$//'); [ -n "$v" ] && export "$k=$v"; done; done
DD_SITE='datadoghq.com'; export DD_SITE   # <- replace with the site confirmed in Phase 0.
# Explicit assignment, not ':=': a wrong non-empty DD_SITE in .env would otherwise survive.
: "${DD_API_KEY:?not set - run dd-account-setup (commercial sites) or supply it directly (government sites)}"; : "${DD_APP_KEY:?not set - run dd-account-setup (commercial sites) or supply it directly (government sites)}"
resp=$(printf 'DD-API-KEY: %s\nDD-APPLICATION-KEY: %s\n' "$DD_API_KEY" "$DD_APP_KEY" \
  | curl -sS -w '\n%{http_code}' -X GET -H @- "https://api.${DD_SITE}/api/v2/integration/gcp/accounts")
code=$(printf '%s' "$resp" | tail -1); body=$(printf '%s' "$resp" | sed '$d')
[ "$code" = "200" ] || { echo "lookup failed with HTTP $code - do not assume 'not connected':"; printf '%s\n' "$body"; exit 1; }
printf '%s\n' "$body"
```

- **The `data` array lists at least one account** (each entry's `attributes.client_email` is the
  integration's service account) - the integration exists. Continue to Phase 1.
- **The `data` array is empty** - stop. Tell the user the GCP integration is not set up yet, run the
  **dd-gcp-integration** skill, then return here and start again at Phase 0. Do not create the
  integration from this skill.
- **The lookup failed** (non-`200`) - surface the response; don't treat it as "not connected".

## Phase 1: Determine Scope

Ask the user:
- Which GCP project should host the Dataflow pipeline and supporting resources (the "host project")?
- Which projects and/or folders should forward logs to Datadog?

If they already know their project/folder IDs, collect them directly.

If they need help discovering them, use the same best-effort approach as `dd-gcp-integration`:

**Organizations:**
```bash
gcloud organizations list --format="table(displayName, name)"
```

**Folders:**
```bash
printf 'Authorization: Bearer %s\n' "$(gcloud auth print-access-token)" |
curl -H @- -s -X POST "https://cloudresourcemanager.googleapis.com/v2/folders:search" \
  -H "Content-Type: application/json" \
  -d '{"query": "lifecycleState=ACTIVE"}'
```

**Projects:**
```bash
gcloud projects list --filter="lifecycleState=ACTIVE AND NOT projectId:sys*" --format="table(projectId, name, parent.id)"
```

If any commands fail, continue with whatever was retrieved and let the user provide the rest manually.

**Reject overlapping scopes.** A folder sink includes every project and subfolder under it, so a
selected project inside a selected folder, or a selected folder inside another, exports the same logs
twice: duplicate logs in Datadog and double ingestion cost. Check the selection (read-only), using bare
numeric folder IDs:

```bash
FOLDERS='<FOLDER_ID_1> <FOLDER_ID_2>'; PROJECTS='<PROJECT_ID_1> <PROJECT_ID_2>'
for p in $PROJECTS; do
  anc=$(gcloud projects get-ancestors "$p" --format='value(id)') || { echo "cannot read ancestors of $p - stop"; exit 1; }
  for f in $FOLDERS; do printf '%s\n' "$anc" | grep -Fxq "$f" && echo "OVERLAP: project $p is inside selected folder $f"; done
done
for f in $FOLDERS; do
  cur=$f
  while :; do
    parent=$(gcloud resource-manager folders describe "$cur" --format='value(parent)') || { echo "cannot read folder $cur - stop"; exit 1; }
    case $parent in folders/*) cur=${parent#folders/} ;; *) break ;; esac
    for g in $FOLDERS; do [ "$g" = "$cur" ] && echo "OVERLAP: folder $f is inside selected folder $g"; done
  done
done
echo "overlap check done"
```

For every `OVERLAP` line, drop the inner project or folder from the selection (the ancestor folder's
sink already covers it with the same filters), tell the user which scopes were dropped, and re-run the
check until it prints none.

## Phase 2: Choose a Region

Ask the user which GCP region the Dataflow job and staging bucket should run in. This should be a
region close to where their logs are generated. Common choices:

- `us-central1`, `us-east1`, `us-west1`
- `europe-west1`, `europe-west2`
- `asia-east1`, `asia-southeast1`

If the user has no preference, default to `us-central1`.

## Phase 3: Configure Log Filtering

Ask the user if they want to filter which logs are forwarded:

### Inclusion Filter (optional)
A GCP logging filter applied to all log sinks. Only logs matching this filter will be forwarded.
If not specified, all logs are forwarded.

Examples:
- `severity>=WARNING` - only warnings and errors
- `resource.type="gce_instance"` - only Compute Engine logs
- `resource.type="k8s_container"` - only GKE container logs

### Exclusion Filters (optional)
Named filters that exclude specific logs from being forwarded. Each has a name and a filter expression.

Examples:
- Name: `exclude-debug`, Filter: `severity=DEBUG`
- Name: `exclude-healthchecks`, Filter: `httpRequest.requestUrl="/healthz"`

If the user has no preference, proceed with no filters (forward all logs).

## Phase 4: Configure Dataflow Settings

Ask the user if they want to customize the Dataflow job configuration, or use defaults:

| Setting | Default | Description |
|---|---|---|
| Max workers | `5` | Maximum number of Dataflow worker VMs |
| Batch size | `100` | Number of log entries per batch sent to Datadog |
| Parallelism | `1` | Processing parallelism factor |
| Machine type | `null` | Worker VM machine type (auto-selected if null) |
| Dataflow Prime | `false` | Use Dataflow Prime for optimized pricing |
| Streaming Engine | `true` | Use Streaming Engine for reduced latency |

If the user has no preference, use the defaults.

**Network.** Without `network` or `subnetwork`, Dataflow launches workers in the host project's
network named `default`, in `<REGION>`. Check that it exists:

```bash
gcloud compute networks subnets list --project '<HOST_PROJECT_ID>' --filter='region:<REGION>' \
  --format='table(name, network.basename(), privateIpGoogleAccess)'
```

- **A subnet on network `default` is listed** - set `<SUBNETWORK>` to
  `"regions/<REGION>/subnetworks/<SUBNET_NAME>"`, using that subnet's name from the listing (normally
  `default`), and leave `<NETWORK>` as `null`. Always set it explicitly: a `default` network may have been
  converted to custom mode while keeping its name, and Dataflow requires an explicit subnetwork on
  custom-mode networks.
- **No `default` network or no subnet in `<REGION>`** (or the command fails because the Compute API is
  disabled and the project has no network yet) - ask the user which existing network and subnet in
  `<REGION>` the workers should use, and set `<SUBNETWORK>` to
  `"regions/<REGION>/subnetworks/<SUBNET_NAME>"`. For a Shared VPC subnet, use the full URL instead,
  `"https://www.googleapis.com/compute/v1/projects/<NETWORK_HOST_PROJECT>/regions/<REGION>/subnetworks/<SUBNET_NAME>"`.
  Leave `<NETWORK>` as `null` when `<SUBNETWORK>` is set. Do not create a network from this skill.

**Shared VPC subnet only: check the Dataflow service agent's access.** The pipeline project's Dataflow
service agent (a Google-managed account, separate from the worker service account the template creates)
needs `roles/compute.networkUser` on the subnet, or on the whole network host project. The template does not
grant it, because that would change another project.

The service agent only exists once the Dataflow API has been enabled in the host project, and the
template enables it later, at apply. Check first:

```bash
gcloud services list --enabled --project '<HOST_PROJECT_ID>' --filter='config.name=dataflow.googleapis.com' --format='value(config.name)'
```

If nothing is printed, the agent does not exist yet and a grant to it would fail. Tell the user that
enabling the Dataflow API in `<HOST_PROJECT_ID>` now creates the service agent (the template enables the
same API and leaves it enabled on destroy, so this changes nothing the apply would not), and run this
only after they approve:

```bash
gcloud services enable dataflow.googleapis.com --project '<HOST_PROJECT_ID>' || { echo "enabling the Dataflow API failed - stop"; exit 1; }
gcloud beta services identity create --service=dataflow.googleapis.com --project '<HOST_PROJECT_ID>'
```

Enabling the API normally creates the agent; `identity create` makes sure it exists and is harmless if
it already does. Then check the agent's access (read-only):

```bash
PROJECT_NUMBER=$(gcloud projects describe '<HOST_PROJECT_ID>' --format='value(projectNumber)') || exit 1
AGENT="serviceAccount:service-${PROJECT_NUMBER}@dataflow-service-producer-prod.iam.gserviceaccount.com"
echo "agent: $AGENT"
echo "on subnet:"
gcloud compute networks subnets get-iam-policy '<SUBNET_NAME>' --project '<NETWORK_HOST_PROJECT>' --region '<REGION>' \
  --flatten='bindings[].members' --filter="bindings.role=roles/compute.networkUser AND bindings.members=${AGENT}" \
  --format='value(bindings.role)'
echo "on host project:"
gcloud projects get-iam-policy '<NETWORK_HOST_PROJECT>' \
  --flatten='bindings[].members' --filter="bindings.role=roles/compute.networkUser AND bindings.members=${AGENT}" \
  --format='value(bindings.role)'
```

- **`roles/compute.networkUser` printed under either heading** - continue.
- **Nothing printed under both** - stop before applying. Job creation would fail with a missing
  `compute.subnetworks.get` permission. Ask the user to have their network admin run, then re-run this check:
  `gcloud compute networks subnets add-iam-policy-binding '<SUBNET_NAME>' --project '<NETWORK_HOST_PROJECT>' --region '<REGION>' --member='<AGENT>' --role=roles/compute.networkUser`
  (with `<AGENT>` replaced by the `agent:` value printed above).
- **Either command fails with a permission error** - you cannot read the host project's policy. Ask the user
  to confirm the grant with their network admin before applying; do not assume it.

Workers get external IPs by default and send logs to `http-intake.logs.<DD_SITE>` over the internet.
Check whether an org policy forbids external IPs on VMs in the host project, which runs the Dataflow
workers:

```bash
gcloud resource-manager org-policies describe compute.vmExternalIpAccess \
  --project '<HOST_PROJECT_ID>' --effective --format=json
```

- **No restriction** (no `listPolicy`, or `allValues: ALLOW`) - leave `<IP_CONFIGURATION>` as `null`.
- **External IPs forbidden** (`allValues: DENY`, or an `allowedValues` list that does not cover the
  workers) - workers that request an external IP fail to start, so set `<IP_CONFIGURATION>` to
  `"WORKER_IP_PRIVATE"`, then run the private-worker checks below.
- **The policy check fails** (for example a permission error) - ask the user whether external IPs are
  allowed for VMs in the host project, and treat "don't know" as forbidden.

**Private-worker checks.** Private workers need two things on the worker subnet: Private Google Access,
to reach Pub/Sub, Secret Manager and Cloud Storage, and an outbound path to
`http-intake.logs.<DD_SITE>`, normally Cloud NAT on a router for that network in `<REGION>`. Both live
in the project that owns the subnet, `<SUBNET_PROJECT>`: `<HOST_PROJECT_ID>` for the `default` network
or a subnet in the host project, and `<NETWORK_HOST_PROJECT>` for a Shared VPC subnet. `<SUBNET_NAME>` is
the subnet chosen above (for the `default` network, the name from the listing, normally `default`).
Describe the subnet, then list the routers in that project and region:

```bash
gcloud compute networks subnets describe '<SUBNET_NAME>' --project '<SUBNET_PROJECT>' \
  --region '<REGION>' --format='yaml(selfLink, network.basename(), ipCidrRange, privateIpGoogleAccess)'
gcloud compute routers list --project '<SUBNET_PROJECT>' --filter='region:<REGION>' \
  --format='table(name, network.basename(), nats[].name.list())'
```

For each router on the subnet's network that lists a NAT gateway, describe the gateway:

```bash
gcloud compute routers nats describe '<NAT_NAME>' --router '<ROUTER_NAME>' \
  --project '<SUBNET_PROJECT>' --region '<REGION>' \
  --format='yaml(sourceSubnetworkIpRangesToNat, subnetworks)'
```

A gateway covers the workers only if `sourceSubnetworkIpRangesToNat` is `ALL_SUBNETWORKS_ALL_IP_RANGES`
or `ALL_SUBNETWORKS_ALL_PRIMARY_IP_RANGES`, or it is `LIST_OF_SUBNETWORKS` and `subnetworks` has an entry
whose `name` ends in `/regions/<REGION>/subnetworks/<SUBNET_NAME>` (same project as the subnet's
`selfLink`) with `ALL_IP_RANGES` or `PRIMARY_IP_RANGE` in its `sourceIpRangesToNat`. A gateway that
exists but lists only other subnets or secondary ranges does not count.

If `privateIpGoogleAccess` is not `True`, or no gateway covers the subnet's primary range, stop before
applying and tell the user what their network admin needs to enable in `<SUBNET_PROJECT>`. If a lookup
fails with a permission error (common for a Shared VPC network host project), ask the user to confirm
both settings with their network admin instead of assuming either. Do not change the network from this
skill.

Two different failures follow from networking, so tell them apart when checking the Dataflow job
after apply. If workers cannot
start (an external IP forbidden by policy, or no Google API access), the Dataflow job fails or never
gets workers, and its job logs show the cause. If workers start but cannot reach Datadog (no NAT, or
firewall rules denying egress), the job runs and logs go to the dead-letter topic until egress to
Datadog is allowed.

## Phase 5: Generate and Apply Terraform

Declare no variables for the Datadog credentials. The provider reads `DD_API_KEY` and `DD_APP_KEY`
from the environment, which keeps them out of command lines, shell history and `.tfvars` files.

The full HCL template - the `google` and `datadog` providers, a dedicated `datadog_api_key`, the
Dataflow service account, the GCS staging bucket, the Pub/Sub topic, subscription and dead-letter
queue, the Secret Manager secret, the project and folder log sinks with their publisher grants, and
the `google_dataflow_job` - is in **`references/terraform.md`**. Read it now. Before emitting it,
check whether the working directory already holds Terraform.

### Existing Terraform project

After the `dd-gcp-integration` hand-off, the directory often already declares a `datadog` provider, a
`required_providers` block and locals with the same names as this template:

```bash
find . -maxdepth 1 -type f \( -name '*.tf' -o -name '*.tf.json' \) -print
grep -nE '^[[:space:]]*(terraform[[:space:]]*\{|provider[[:space:]]+"(datadog|google)"|(project_id|project_ids|folder_ids|datadog_site|region|network|subnetwork|resource_name_prefix|bucket_prefix|bucket_name)[[:space:]]*=)' ./*.tf 2>/dev/null
```

**If nothing is found**, emit the template as is, in a new file such as `datadog-gcp-log-forwarding.tf`.

**If anything is found**, emit the template into a new file `datadog-gcp-log-forwarding.tf` in that
project, adapted so it adds no duplicate declarations:

- **Provider requirements: merge, don't drop.** A module may list each provider in only one
  `required_providers` block, and a provider missing from it resolves to `hashicorp/<name>` (there is
  no `hashicorp/datadog`, so `terraform init` fails). So:
  - If the project has no `required_providers` block, keep the template's `terraform` block.
  - Otherwise leave the template's block out and edit the project's existing block: add any of
    `datadog = { source = "DataDog/datadog", version = "~> 3.79" }` and
    `google = { source = "hashicorp/google" }` that it lacks. Keep an entry that is already there,
    with its constraint (for example `dd-gcp-integration`'s `~> 3.85.0`); do not add a second
    `datadog` entry. Only if an existing `datadog` constraint allows nothing at or above `3.79`, ask
    the user before raising it. Then confirm the source address:

    ```bash
    grep -nE 'source[[:space:]]*=[[:space:]]*"(registry\.terraform\.io/)?[Dd]ata[Dd]og/datadog"' ./*.tf \
      || echo "MISSING: no DataDog/datadog source in required_providers - add it before terraform init"
    ```

    Step 5 below runs `terraform init` and `terraform validate`, which catch any remaining provider
    or constraint mismatch before the plan.
- Drop the template's `provider "datadog"` block if the project already has one, and reuse it (it
  must already set `api_url` for `DD_SITE`). Reuse an existing `google` provider too.
- For every template local whose name already exists in the project, rename it in the emitted file
  with a `log_forwarding_` prefix (for example `local.project_id` becomes `local.log_forwarding_project_id`)
  and update every reference to it in that file. Don't reuse the integration's `project_ids` and
  `folder_ids`: they hold the integration's scopes, which can differ from the log-forwarding scopes.

Fill in the placeholders below in either case.

## Applying the Terraform

1. Replace all `<PLACEHOLDER>` values:
   - `<HOST_PROJECT_ID>` - the project hosting the Dataflow pipeline
   - `<REGION>` - the GCP region for Dataflow and the staging bucket
   - `<DD_SITE>` - from the `DD_SITE` environment variable
   - `<USER_PROJECT_IDS>` and `<USER_FOLDER_IDS>` - the scopes to forward logs from
   - `<INCLUSION_FILTER>` - the inclusion filter as a complete, quoted HCL string (`""` for no
     filter). Escape the user's filter for HCL: `\` becomes `\\`, `"` becomes `\"`, `${` becomes
     `$${` and `%{` becomes `%%{`. For example `resource.type="gce_instance"` is written `"resource.type=\"gce_instance\""`.
     Apply the same escaping to each exclusion `filter` below.
   - `<EXCLUSION_FILTERS>` - the exclusion filters list (e.g., `[{ name = "exclude-debug", filter = "severity=DEBUG" }]` or `[]` for none)
   - `<MAX_WORKERS>`, `<BATCH_SIZE>`, `<PARALLELISM>` - Dataflow settings (defaults: `5`, `100`, `1`)
   - `<MACHINE_TYPE>` - worker machine type (`null` for auto-selection)
   - `<DATAFLOW_PRIME>` - `true` or `false`
   - `<STREAMING_ENGINE>` - `true` or `false`
   - `<NETWORK>`, `<SUBNETWORK>` - from the Phase 4 network check (`<SUBNETWORK>` is always set; `<NETWORK>` stays `null`)
   - `<IP_CONFIGURATION>` - from the Phase 4 external IP check: `null`, or `"WORKER_IP_PRIVATE"` when
     external IPs are forbidden
2. If the user selected only projects (no folders), remove the `folder_ids` local, `google_logging_folder_sink`, and `google_pubsub_topic_iam_member.log_sink_publisher_folder` resources.
3. If the user selected only folders (no explicit projects), set `project_ids = []` and remove the project sink resources.
4. Export the Datadog credentials so the provider picks them up, and confirm they are set:

   ```bash
   for f in .env.local .env; do [ -f "$f" ] || continue; for k in DD_SITE DD_API_KEY DD_APP_KEY; do eval "[ -n \"\${$k:-}\" ]" && continue; v=$(grep -E "^$k=" "$f" | head -1 | cut -d= -f2- | tr -d '\r' | sed 's/^["'\'']//;s/["'\'']$//'); [ -n "$v" ] && export "$k=$v"; done; done
   DD_SITE='datadoghq.com'; export DD_SITE   # <- replace with the site confirmed in Phase 0.
   # Explicit assignment, not ':=': a wrong non-empty DD_SITE in .env would otherwise survive.
   : "${DD_API_KEY:?not set - run dd-account-setup (commercial sites) or supply it directly (government sites)}"; : "${DD_APP_KEY:?not set - run dd-account-setup (commercial sites) or supply it directly (government sites)}"
   export DD_API_KEY DD_APP_KEY
   ```

5. Run `terraform init` to install providers, then `terraform validate`. Stop and fix the
   configuration if either fails.
6. Save the plan to a file and show it to the user for review. The plan and the state contain the
   dedicated forwarding API key (see Important Notes), so the block creates them owner-only. If the
   user declines the plan, delete it with `rm -f tfplan`:

   ```bash
   for f in .env.local .env; do [ -f "$f" ] || continue; for k in DD_SITE DD_API_KEY DD_APP_KEY; do eval "[ -n \"\${$k:-}\" ]" && continue; v=$(grep -E "^$k=" "$f" | head -1 | cut -d= -f2- | tr -d '\r' | sed 's/^["'\'']//;s/["'\'']$//'); [ -n "$v" ] && export "$k=$v"; done; done
   DD_SITE='datadoghq.com'; export DD_SITE   # <- replace with the site confirmed in Phase 0.
   # Explicit assignment, not ':=': a wrong non-empty DD_SITE in .env would otherwise survive.
   : "${DD_API_KEY:?not set - run dd-account-setup (commercial sites) or supply it directly (government sites)}"; : "${DD_APP_KEY:?not set - run dd-account-setup (commercial sites) or supply it directly (government sites)}"
   umask 077
   terraform plan -out=tfplan \
     || { rm -f tfplan; echo "terraform plan FAILED - do NOT apply"; exit 1; }
   ```

7. Apply **that saved plan** once the user confirms. Applying the file is what makes the approval
   meaningful - a bare `terraform apply` re-plans, so the user could approve one plan and get another:

   ```bash
   for f in .env.local .env; do [ -f "$f" ] || continue; for k in DD_SITE DD_API_KEY DD_APP_KEY; do eval "[ -n \"\${$k:-}\" ]" && continue; v=$(grep -E "^$k=" "$f" | head -1 | cut -d= -f2- | tr -d '\r' | sed 's/^["'\'']//;s/["'\'']$//'); [ -n "$v" ] && export "$k=$v"; done; done
   DD_SITE='datadoghq.com'; export DD_SITE   # <- replace with the site confirmed in Phase 0.
   # Explicit assignment, not ':=': a wrong non-empty DD_SITE in .env would otherwise survive.
   : "${DD_API_KEY:?not set - run dd-account-setup (commercial sites) or supply it directly (government sites)}"; : "${DD_APP_KEY:?not set - run dd-account-setup (commercial sites) or supply it directly (government sites)}"
   umask 077
   trap 'rm -f tfplan' EXIT HUP INT TERM
   terraform apply tfplan \
     || { echo "terraform apply FAILED - do NOT verify or report success"; exit 1; }
   ```

   Never pass `-auto-approve`, and never apply without showing the saved plan first.

## Getting the Most Out of Your Log Forwarding

Once the Dataflow job is running, logs will start flowing to Datadog within a few minutes.

- **Logs Explorer**: `<APP_BASE>/logs` - search and filter your GCP logs.
- **Log Patterns**: `<APP_BASE>/logs/patterns` - automatically discover patterns in your logs.
- **Log Pipelines**: `<APP_BASE>/logs/pipelines` - parse and enrich logs with custom pipelines.
- **Log Monitors**: `<APP_BASE>/monitors/create` - create alerts based on log patterns, counts, or anomalies.
- **Log Archives**: `<APP_BASE>/logs/pipelines/archives` - configure long-term log storage.
- **Dataflow Console**: Check the Dataflow job status in the GCP Console at `https://console.cloud.google.com/dataflow/jobs/<REGION>?project=<HOST_PROJECT_ID>`.

## Important Notes

- The Dataflow job runs continuously and incurs GCP costs (worker VMs, Pub/Sub, storage).
- The `datadog_api_key` resource creates a new Datadog API key - this is separate from `DD_API_KEY` used for authentication.
- That dedicated key's value is stored in plaintext in Terraform state (the `key` attribute of
  `datadog_api_key` and the `secret_data` of `google_secret_manager_secret_version`) and in any saved
  plan. The Datadog provider has no way to create a key without returning it into state, so it cannot
  be avoided while the key is managed by Terraform. The `dd-aws-log-forwarding` pattern (Terraform
  creates an empty secret container and the key is seeded into it out of band, so it never reaches
  state) was considered and this is the fallback: that pattern seeds an existing key rather than
  creating a dedicated one, and here the dedicated key itself is what lands in state. The Google
  provider's write-only `secret_data_wo` would only remove the second copy, since `datadog_api_key.key`
  stays in state either way. Treat the state and `tfplan` as secrets: keep them
  out of version control (add `*.tfstate*`, `tfplan` and `.terraform/` to `.gitignore`), prefer a
  remote backend with restricted access and encryption at rest, never print them (`terraform show
  -json`, `cat terraform.tfstate`), and delete a declined plan.
- Log sinks with `unique_writer_identity = true` create a unique service account per sink that needs Pub/Sub publisher access.
- For folder-level sinks, `include_children = true` means all projects within the folder will forward logs.
- The dead-letter topic captures messages that fail delivery - monitor it for issues.
- Never run `terraform apply` without showing the plan to the user first.
