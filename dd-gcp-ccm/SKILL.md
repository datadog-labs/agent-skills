---
name: dd-gcp-ccm
description: Set up Datadog Cloud Cost Management for GCP - enables the BigQuery and BigQuery Data Transfer APIs, walks the user through the Cloud Billing detailed-usage export to BigQuery, then uses Terraform to provision the GCS bucket and the predefined IAM role bindings Datadog documents for the Datadog service account, and registers the cost config through datadog_gcp_uc_config. Use when the user wants to track their GCP cloud costs, asks to set up a GCP billing export for Datadog, or asks to set up Cloud Cost Management for GCP. Does not create the GCP integration or the Datadog service account itself. Requires an existing Datadog GCP integration; run `dd-gcp-integration` first if it is missing.
metadata:
  version: "1.0.1"
  author: datadog-labs
  repository: https://github.com/datadog-labs/agent-skills
  tags: datadog,gcp,cost,ccm,cloud-cost-management,bigquery,billing,terraform,cloud
  alwaysApply: "false"
  tools: terraform
---

# Datadog Cloud Cost Management for GCP

You are helping a user set up Datadog Cloud Cost Management (CCM) for GCP using Terraform.

CCM ingests GCP billing data from a BigQuery dataset to provide cost visibility in Datadog.
The setup creates:
- A GCS bucket (or uses an existing one) for intermediate data
- A BigQuery dataset for the billing export
- IAM bindings granting the Datadog service account access to BigQuery and GCS
- Cloud Billing export configuration pointing to the dataset

This is a **product integration (PINT) flow** - it requires an existing Datadog GCP integration.

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
| Both `200` | Keys are good for this site | Continue to Phase 1 |
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
- **Terraform never receives the keys as values at all** for AWS, Azure, and GCP: the Datadog provider
  reads `DD_API_KEY` / `DD_APP_KEY` from the environment, so there are no root variables, no `-var=`
  arguments, and nothing for Terraform to record in state or a saved plan. (OCI is the exception - its
  module needs them as inputs, so there they travel as `TF_VAR_*`.)

Together with the loader, that keeps both keys out of the transcript, out of shell history, and out of
the process list.

**Tools.** `terraform` is required; the `gcloud` CLI is optional (it lists billing accounts and enables
the APIs), and so is `jq` (only the completion callback at the end uses it):

```bash
command -v terraform || echo "MISSING terraform - https://developer.hashicorp.com/terraform/install"
command -v gcloud >/dev/null 2>&1 && echo "gcloud: available" || echo "gcloud: not installed"
command -v jq >/dev/null 2>&1 && echo "jq: available" || echo "jq: not installed"
```

**The snippets here are POSIX shell.** Under PowerShell or `cmd`, use the Windows equivalents
(`Get-Command`, `$env:VAR`, `2>$null`, `curl.exe`) - same calls, same order.

## Phase 1: Select GCP Integration

Fetch the list of GCP STS accounts already integrated with Datadog:

```bash
for f in .env.local .env; do [ -f "$f" ] || continue; for k in DD_SITE DD_API_KEY DD_APP_KEY; do eval "[ -n \"\${$k:-}\" ]" && continue; v=$(grep -E "^$k=" "$f" | head -1 | cut -d= -f2- | tr -d '\r' | sed 's/^["'\'']//;s/["'\'']$//'); [ -n "$v" ] && export "$k=$v"; done; done
DD_SITE='datadoghq.com'; export DD_SITE   # <- replace with the site confirmed in Phase 0.
# Explicit assignment, not ':=': a wrong non-empty DD_SITE in .env would otherwise survive.
: "${DD_API_KEY:?not set - run dd-account-setup (commercial sites) or supply it directly (government sites)}"; : "${DD_APP_KEY:?not set - run dd-account-setup (commercial sites) or supply it directly (government sites)}"
resp=$(printf 'DD-API-KEY: %s\nDD-APPLICATION-KEY: %s\n' "$DD_API_KEY" "$DD_APP_KEY" \
  | curl -sS -w '\n%{http_code}' -X GET -H @- "https://api.${DD_SITE}/api/v2/integration/gcp/accounts")
code=$(printf '%s' "$resp" | tail -1); body=$(printf '%s' "$resp" | sed '$d')
[ "$code" = "200" ] || { echo "lookup failed with HTTP $code - do not assume 'no accounts':"; printf '%s\n' "$body"; exit 1; }
printf '%s\n' "$body"
```

If the lookup fails (non-`200`, including `403` when the app key lacks permission to read
integrations), stop and show the user the error. Do not treat a failed lookup as "no accounts" and
do not start a new integration setup because of it.

The response contains an array of accounts under `data`. Each includes:
- `id` - the Datadog config ID of the service account integration (used for PATCH requests)
- `attributes.client_email` - the Datadog service account email
- `attributes.resource_collection_enabled` - whether resource collection is on
- `meta.accessible_projects` - the GCP project IDs this service account can see (may be absent)

Each entry is a **service account**, not a project: one service account can cover several projects,
and the project in its email domain (`...@<HOST_PROJECT>.iam.gserviceaccount.com`) is only where the
account lives, not necessarily a project the user wants cost data for.

### If accounts are found

Present the service accounts with their accessible projects, including an "add a new project"
option, and ask the user to pick the service account and then the GCP project to set up CCM for.
If `meta.accessible_projects` is missing or empty, ask the user to type the project ID. **Always ask -
never auto-pick, even if exactly one account or project is returned, and never assume the project the
active `gcloud` config is set to is the one the user means.** Confirm the selected service account and
project before any change (the resource-collection PATCH and the Terraform apply). For example:

> I found these GCP service accounts connected to Datadog:
>
> 1. `<CLIENT_EMAIL_1>` - projects `<PROJECT_ID_A>`, `<PROJECT_ID_B>`
> 2. `<CLIENT_EMAIL_2>` - project `<PROJECT_ID_C>`
>
> Which service account and project would you like to set up Cloud Cost Management for? Or I can
> help you add a new project.

If the user wants to add a **new** project, complete the `dd-gcp-integration` skill first,
then return here.

Record the selected account's `id` and `client_email`, and the selected project ID (called
`<PROJECT_ID>` below) - all are needed for IAM bindings and the update call.

**Enable resource collection** if it is not already enabled. Resource collection is required for CCM
cost recommendations and resource-level attribution.

First check whether the integration is managed by Terraform: look in the local `*.tf` files for a
`datadog_integration_gcp_sts` resource whose `client_email` is the selected `client_email` (as a literal,
or as a reference to a `google_service_account` with that email). If one exists, do **not** PATCH: the
next `terraform apply` would set `resource_collection_enabled` back to whatever that resource says.
Instead, set `resource_collection_enabled = true` in that resource in Phase 3, so the change is part of
the plan the user approves.

Otherwise, before sending the `PATCH`, tell the user which project (and account `id`) you are about to
change and wait for explicit confirmation. Then update the account:

```bash
for f in .env.local .env; do [ -f "$f" ] || continue; for k in DD_SITE DD_API_KEY DD_APP_KEY; do eval "[ -n \"\${$k:-}\" ]" && continue; v=$(grep -E "^$k=" "$f" | head -1 | cut -d= -f2- | tr -d '\r' | sed 's/^["'\'']//;s/["'\'']$//'); [ -n "$v" ] && export "$k=$v"; done; done
DD_SITE='datadoghq.com'; export DD_SITE   # <- replace with the site confirmed in Phase 0.
# Explicit assignment, not ':=': a wrong non-empty DD_SITE in .env would otherwise survive.
: "${DD_API_KEY:?not set - run dd-account-setup (commercial sites) or supply it directly (government sites)}"; : "${DD_APP_KEY:?not set - run dd-account-setup (commercial sites) or supply it directly (government sites)}"
printf 'DD-API-KEY: %s\nDD-APPLICATION-KEY: %s\n' "$DD_API_KEY" "$DD_APP_KEY" |
curl -H @- -sS -w '\nHTTP %{http_code}\n' -X PATCH "https://api.${DD_SITE}/api/v2/integration/gcp/accounts/<ACCOUNT_ID>" \
  -H "Content-Type: application/json" \
  -d '{
    "data": {
      "type": "gcp_service_account",
      "id": "<ACCOUNT_ID>",
      "attributes": {
        "resource_collection_enabled": true
      }
    }
  }'
```

Use the account's `id` field as `<ACCOUNT_ID>` in both the path and the body. Confirm the response is HTTP `200` with
`resource_collection_enabled: true`; otherwise show the user the error before continuing.

### Check for Existing UC Configuration

Before generating any Terraform, check whether CCM is already set up for the selected project so
you don't create duplicate buckets, IAM bindings, or billing exports:

```bash
for f in .env.local .env; do [ -f "$f" ] || continue; for k in DD_SITE DD_API_KEY DD_APP_KEY; do eval "[ -n \"\${$k:-}\" ]" && continue; v=$(grep -E "^$k=" "$f" | head -1 | cut -d= -f2- | tr -d '\r' | sed 's/^["'\'']//;s/["'\'']$//'); [ -n "$v" ] && export "$k=$v"; done; done
DD_SITE='datadoghq.com'; export DD_SITE   # <- replace with the site confirmed in Phase 0.
# Explicit assignment, not ':=': a wrong non-empty DD_SITE in .env would otherwise survive.
: "${DD_API_KEY:?not set - run dd-account-setup (commercial sites) or supply it directly (government sites)}"; : "${DD_APP_KEY:?not set - run dd-account-setup (commercial sites) or supply it directly (government sites)}"
resp=$(printf 'DD-API-KEY: %s\nDD-APPLICATION-KEY: %s\n' "$DD_API_KEY" "$DD_APP_KEY" \
  | curl -sS -w '\n%{http_code}' -X GET -H @- "https://api.${DD_SITE}/api/v2/cost/gcp_uc_config")
code=$(printf '%s' "$resp" | tail -1); body=$(printf '%s' "$resp" | sed '$d')
[ "$code" = "200" ] || { echo "lookup failed with HTTP $code - do not assume 'no existing config':"; printf '%s\n' "$body"; exit 1; }
printf '%s\n' "$body"
```

As with the accounts lookup, a non-`200` response means the check failed, not that no config exists.
Stop and show the error.

Filter the response for entries whose `export_project_name` matches the selected `<PROJECT_ID>`.
If one or more match, surface them to the user and ask how to proceed - never silently continue
and never auto-mark the flow complete:

> I found an existing GCP UC config for project `<PROJECT_ID>` (dataset `<DATASET_NAME>`,
> bucket `<BUCKET_NAME>`). What would you like to do?
>
> 1. Confirm - CCM is already set up for this project. Verify and finish.
> 2. Set up CCM for a different GCP project.
> 3. Add a new GCP project.

Handle the choice:

- **(1)** - skip to Phase 4 to verify the config's status and report completion.
- **(2)** - go back to the project list and ask again.
- **(3)** - load the `dd-gcp-integration` skill now and follow it through its own apply and verification, then return to the integration lookup at the start of Phase 1: select and confirm the newly added target, record its identifiers, and repeat every check after that lookup. Do not fold this setup into the integration's apply; start it only once the integration exists and the user has confirmed it. The CCM pieces that wait are the billing export dataset's access grant (`roles/bigquery.dataEditor` for the Datadog service account) and `datadog_gcp_uc_config`: write them in their own file and apply them only after the integration exists and the billing export dataset has been created.

If no entries match, proceed to Phase 2. The export project can differ from the project selected above,
so this check is repeated in Phase 2, step 6, once the export project, dataset and billing account are
known.

### If no accounts are found

Tell the user they need to set up the GCP integration first. If yes, load the `dd-gcp-integration` skill now and follow it through its own apply and verification, then return to the integration lookup at the start of Phase 1: select and confirm the newly added target, record its identifiers, and repeat every check after that lookup. Do not fold this setup into the integration's apply; start it only once the integration exists and the user has confirmed it. The CCM pieces that wait are the billing export dataset's access grant (`roles/bigquery.dataEditor` for the Datadog service account) and `datadog_gcp_uc_config`: write them in their own file and apply them only after the integration exists and the billing export dataset has been created.

## Phase 2: Collect Configuration

### 1. Billing Account

Ask for the GCP Billing Account ID.

**If `gcloud` CLI is available** (`command -v gcloud >/dev/null 2>&1`), list them for the user:
```bash
gcloud billing accounts list --format="table(name,displayName,open)" 2>/dev/null
```

**Otherwise**, ask the user to copy the ID from console.cloud.google.com → **Billing** →
**Account management** (or **Manage billing accounts**).

Validate the format before continuing. GCP billing account IDs match the regex
`^[0-9A-Za-z]{6}[-_][0-9A-Za-z]{6}[-_][0-9A-Za-z]{6}$` - three groups of six alphanumeric
characters separated by `-` or `_`, e.g. `0X0X0X-0X0X0X-0X0X0X`. If the user's input does
not match, ask them to re-check it before proceeding.

### 2. Export Project

The BigQuery dataset and billing export live in a project. This is typically the project selected
in Phase 1, but can be different. Ask or confirm:

| Field | Description | Default |
|---|---|---|
| **Export Project ID** | Project to host the BigQuery dataset | The project selected in Phase 1 |

### 3. BigQuery Dataset

| Field | Description | Default |
|---|---|---|
| **Dataset Name** | BigQuery dataset for billing data | `datadog_billing_export` |

### 4. GCS Bucket

Ask if they want to **create a new** bucket or **use an existing** one.

| Field | Description | Default |
|---|---|---|
| **Bucket Name** | GCS bucket for intermediate data | `dd-billing-<PROJECT_ID>` |
| **Region** | GCS bucket region | `us-central1` |

The bucket **must be colocated** with the BigQuery export dataset (same region). If the user
picks an existing bucket, confirm its region matches the dataset's region.

### 5. Service Account

The Datadog service account email from Phase 1 (`client_email`) is used for all IAM bindings.
Confirm this with the user.

### 6. Re-check for an Existing UC Configuration

If the export project differs from the project selected in Phase 1, rerun the lookup from **Check for
Existing UC Configuration** in Phase 1 and filter for entries whose `export_project_name` matches the
export project (and, where present, whose dataset or billing account matches the values collected
above). If any match, present the same three choices as in Phase 1 before provisioning anything.

## Phase 3: Enable Billing Export, Apply Terraform, Register with Datadog

### Step 1: Enable APIs and BigQuery Billing Export (prerequisite)

Do this **before** writing any Terraform. Billing data takes 24 to 48 hours to first appear, so
starting early means data is flowing by the time the user finishes. The billing export also
creates the BigQuery dataset that the rest of the setup depends on.

**1a. Enable the required APIs:**

**If `gcloud` CLI is available** (`command -v gcloud >/dev/null 2>&1`):
```bash
gcloud services enable bigquery.googleapis.com \
  bigquerydatatransfer.googleapis.com \
  --project=<EXPORT_PROJECT_ID>
```

**Otherwise**, walk the user through enabling them in the Console:
1. Go to console.cloud.google.com → ensure the project picker shows `<EXPORT_PROJECT_ID>`.
2. Open **APIs & Services** → **Library**.
3. Search for and **Enable** each of:
   - **BigQuery API** (`bigquery.googleapis.com`)
   - **BigQuery Data Transfer API** (`bigquerydatatransfer.googleapis.com`)
4. Confirm both show as **Enabled** before continuing.

These are the same two APIs the manual onboarding flow requires.

**1b. Enable the BigQuery billing export** (GCP Console, manual):

There is no Terraform resource or gcloud command for this step, so it must be done in the Console.

Before starting, confirm the user has **Billing Account Costs Manager** or **Billing Account
Administrator** on the billing account, and **BigQuery User** (or equivalent) on `<EXPORT_PROJECT_ID>`.
Billing Account Viewer is not enough to enable the export. If they lack these roles, stop and ask them
to get the roles (or someone who has them) before continuing.

1. Go to **Billing** → **Billing Export** in the GCP Console.
2. Select the **BigQuery Export** tab.
3. Enable **Detailed usage cost** export.
4. Set the project to `<EXPORT_PROJECT_ID>` and dataset to `<DATASET_NAME>`. If the dataset does
   not exist, create it from this screen (location: `<REGION>`).
5. Save.

If the billing export was already enabled in a previous setup, confirm the project and dataset
with the user and skip the creation step.

Do **not** skip this step. Without the billing export, no data flows to BigQuery and CCM has
nothing to ingest.

### Step 2: Check for Existing Terraform

```bash
find . -maxdepth 1 -type f \( -name '*.tf' -o -name '*.tf.json' -o -name 'terraform.tfstate' \) -print
```

Reuse existing providers if found. If Phase 1 found the selected integration managed by a
`datadog_integration_gcp_sts` resource here, set `resource_collection_enabled = true` in it now.

### Step 3: Generate Terraform HCL

The BigQuery dataset is created in Step 1 and the APIs are enabled in Step 1, so Terraform only
provisions the GCS bucket, sets up the IAM bindings for the Datadog service account, and
registers the cost config with Datadog.

The full HCL is in **`references/terraform.md`**. Read it now and emit the sections that apply, with
the placeholders filled in:

| Section | When to include it |
|---|---|
| **Section 1: GCS Bucket** | Only if creating a new bucket |
| **Section 2: IAM Bindings** | Always - the predefined roles from the Datadog CCM docs; `roles/bigquery.admin` is broad on the export project, so flag it |
| **Section 3: Register GCP cost config with Datadog** | Always - pick one variant from the table below |

Pick the Section 3 variant that matches the path the user is on. `bucket_name` and `service_account`
are written DIFFERENTLY for the resource-reference case (unquoted) vs the literal case (quoted) - do
not mix the two.

| Path | Variant | `bucket_name` | `service_account` |
|---|---|---|---|
| New bucket, and `google_service_account.datadog_gcp_service_account` is declared in this Terraform root module with an email equal to the Phase 1 `client_email` | **A** | HCL reference, unquoted | HCL reference, unquoted |
| New bucket, and that service account is not declared in this root module, or its email is not the Phase 1 `client_email` | **B** | HCL reference, unquoted | literal Datadog `client_email`, quoted |
| Existing bucket | **C** | literal bucket name, quoted | literal Datadog `client_email`, quoted |

Use Variant A only after confirming that `resource "google_service_account" "datadog_gcp_service_account"`
exists in the `.tf` files of the current directory **and** that it is the service account selected in
Phase 1: its email (`<account_id>@<project>.iam.gserviceaccount.com`, from the resource's `account_id`
and `project`, or the `email` attribute in `terraform state show
google_service_account.datadog_gcp_service_account`) must equal the Phase 1 `client_email`. Otherwise
the IAM bindings would grant the selected account access while the cost config registers a different
one. Completing `dd-gcp-integration` does not guarantee a match; if the resource is absent, its email
differs, or you cannot tell, use the `client_email` from Phase 1 through Variant B or C.

Replace `<BUCKET_REF>` with:
- `google_storage_bucket.billing_bucket.name` if creating new
- `"<BUCKET_NAME>"` if using existing

### Step 4: Apply Terraform

1. Replace all placeholders with collected values.
2. Ensure the `google` provider is configured:
   ```hcl
   terraform {
     required_providers {
       google = {
         source = "hashicorp/google"
       }
       datadog = {
         source = "DataDog/datadog"
       }
     }
   }

   provider "google" {
     project = "<EXPORT_PROJECT_ID>"
   }

   provider "datadog" {
     api_url = "https://api.<DD_SITE>/"
   }
   ```

   The Datadog provider does not read `DD_SITE`; without `api_url` (or `DD_HOST`) it defaults to US1, so
   credentials for any other site fail. Replace `<DD_SITE>` with the site confirmed in Phase 0. If the
   project already has a `datadog` provider, check that its `api_url` points at the same site instead of
   adding a second one. Keys stay out of the provider block; it reads `DD_API_KEY` / `DD_APP_KEY` from
   the environment.
3. Confirm the Datadog credentials are exported for the provider:

   ```bash
   for f in .env.local .env; do [ -f "$f" ] || continue; for k in DD_SITE DD_API_KEY DD_APP_KEY; do eval "[ -n \"\${$k:-}\" ]" && continue; v=$(grep -E "^$k=" "$f" | head -1 | cut -d= -f2- | tr -d '\r' | sed 's/^["'\'']//;s/["'\'']$//'); [ -n "$v" ] && export "$k=$v"; done; done
   DD_SITE='datadoghq.com'; export DD_SITE   # <- replace with the site confirmed in Phase 0.
   # Explicit assignment, not ':=': a wrong non-empty DD_SITE in .env would otherwise survive.
   : "${DD_API_KEY:?not set - run dd-account-setup (commercial sites) or supply it directly (government sites)}"; : "${DD_APP_KEY:?not set - run dd-account-setup (commercial sites) or supply it directly (government sites)}"
   export DD_API_KEY DD_APP_KEY
   ```

4. Run `terraform init`, then save the plan and show it to the user for review:

   ```bash
   for f in .env.local .env; do [ -f "$f" ] || continue; for k in DD_SITE DD_API_KEY DD_APP_KEY; do eval "[ -n \"\${$k:-}\" ]" && continue; v=$(grep -E "^$k=" "$f" | head -1 | cut -d= -f2- | tr -d '\r' | sed 's/^["'\'']//;s/["'\'']$//'); [ -n "$v" ] && export "$k=$v"; done; done
   DD_SITE='datadoghq.com'; export DD_SITE   # <- replace with the site confirmed in Phase 0.
   # Explicit assignment, not ':=': a wrong non-empty DD_SITE in .env would otherwise survive.
   : "${DD_API_KEY:?not set - run dd-account-setup (commercial sites) or supply it directly (government sites)}"; : "${DD_APP_KEY:?not set - run dd-account-setup (commercial sites) or supply it directly (government sites)}"
   terraform plan -out=tfplan \
     || { echo "terraform plan FAILED - do NOT apply"; exit 1; }
   ```

   Stop here and wait for the user to approve the plan before applying it. If the plan changes a
   `datadog_integration_gcp_sts` for the selected account so that `resource_collection_enabled`
   becomes `false`, the project is undoing the Phase 1 change: set it to `true` in that resource and
   re-plan first.

5. Apply **that saved plan** once the user confirms. A bare `terraform apply` re-plans, so the user
   could approve one plan and get another:

   ```bash
   for f in .env.local .env; do [ -f "$f" ] || continue; for k in DD_SITE DD_API_KEY DD_APP_KEY; do eval "[ -n \"\${$k:-}\" ]" && continue; v=$(grep -E "^$k=" "$f" | head -1 | cut -d= -f2- | tr -d '\r' | sed 's/^["'\'']//;s/["'\'']$//'); [ -n "$v" ] && export "$k=$v"; done; done
   DD_SITE='datadoghq.com'; export DD_SITE   # <- replace with the site confirmed in Phase 0.
   # Explicit assignment, not ':=': a wrong non-empty DD_SITE in .env would otherwise survive.
   : "${DD_API_KEY:?not set - run dd-account-setup (commercial sites) or supply it directly (government sites)}"; : "${DD_APP_KEY:?not set - run dd-account-setup (commercial sites) or supply it directly (government sites)}"
   trap 'rm -f tfplan' EXIT HUP INT TERM
   terraform apply tfplan \
     || { echo "terraform apply FAILED - do NOT verify or report success"; exit 1; }
   ```

The `datadog_gcp_uc_config` resource in Section 3 registers the cost configuration as part of
the same apply, so a separate API call is not needed. Its `depends_on` ensures Datadog isn't
asked to read the bucket / dataset until the IAM bindings are in place.

## Phase 4: Verify Setup

After registration, verify the config exists in Datadog:

```bash
for f in .env.local .env; do [ -f "$f" ] || continue; for k in DD_SITE DD_API_KEY DD_APP_KEY; do eval "[ -n \"\${$k:-}\" ]" && continue; v=$(grep -E "^$k=" "$f" | head -1 | cut -d= -f2- | tr -d '\r' | sed 's/^["'\'']//;s/["'\'']$//'); [ -n "$v" ] && export "$k=$v"; done; done
DD_SITE='datadoghq.com'; export DD_SITE   # <- replace with the site confirmed in Phase 0.
# Explicit assignment, not ':=': a wrong non-empty DD_SITE in .env would otherwise survive.
: "${DD_API_KEY:?not set - run dd-account-setup (commercial sites) or supply it directly (government sites)}"; : "${DD_APP_KEY:?not set - run dd-account-setup (commercial sites) or supply it directly (government sites)}"
resp=$(printf 'DD-API-KEY: %s\nDD-APPLICATION-KEY: %s\n' "$DD_API_KEY" "$DD_APP_KEY" \
  | curl -sS -w '\n%{http_code}' -X GET -H @- "https://api.${DD_SITE}/api/v2/cost/gcp_uc_config")
code=$(printf '%s' "$resp" | tail -1); body=$(printf '%s' "$resp" | sed '$d')
[ "$code" = "200" ] || { echo "lookup failed with HTTP $code - do not assume 'not registered':"; printf '%s\n' "$body"; exit 1; }
printf '%s\n' "$body"
```

Find the config matching the export project and dataset, then check its `attributes.status` and
`attributes.error_messages`:

- `active` with no error messages - setup succeeded. Billing data may still take 24-48 hours to
  appear; say so rather than claiming costs are already flowing.
- `warn` or `error`, or any `error_messages` - show the user the messages. Do not report success or
  send the completion callback; help them fix the cause (usually a missing IAM binding, a
  non-colocated bucket, or a billing export that is not enabled yet) and verify again.
- `disabled`, or no matching config - setup is not complete. Tell the user and do not send the
  callback.

**Resource collection.** Re-run the account lookup from Phase 1 and check that the selected account
still has `resource_collection_enabled: true`. If it is `false`, a Terraform-managed
`datadog_integration_gcp_sts` has switched it back off: set `resource_collection_enabled = true` in
that resource and re-plan before reporting completion.

### Wrap Up

After verification, make one best-effort completion callback so the Datadog UI can finish this flow.

`DD_WORKFLOW_ID` is the UUID of the Datadog user the credentials belong to, which is how the UI
correlates this callback. Resolve it and post in **one** command: many agent runtimes start a fresh
shell per command, so a variable assigned in an earlier one would be empty here.

**Never print the `current_user` response**: it also carries the user's name, handle, and email. If no
JSON parser is available, skip this callback rather than installing one or blocking setup.

```bash
for f in .env.local .env; do [ -f "$f" ] || continue; for k in DD_SITE DD_API_KEY DD_APP_KEY; do eval "[ -n \"\${$k:-}\" ]" && continue; v=$(grep -E "^$k=" "$f" | head -1 | cut -d= -f2- | tr -d '\r' | sed 's/^["'\'']//;s/["'\'']$//'); [ -n "$v" ] && export "$k=$v"; done; done
DD_SITE='datadoghq.com'; export DD_SITE   # <- replace with the site confirmed in Phase 0.
# Explicit assignment, not ':=': a wrong non-empty DD_SITE in .env would otherwise survive.
: "${DD_API_KEY:?not set - run dd-account-setup (commercial sites) or supply it directly (government sites)}"; : "${DD_APP_KEY:?not set - run dd-account-setup (commercial sites) or supply it directly (government sites)}"
dd_headers() { printf 'DD-API-KEY: %s\nDD-APPLICATION-KEY: %s\n' "$DD_API_KEY" "$DD_APP_KEY"; }
DD_WORKFLOW_ID="$(dd_headers | curl -H @- -fsS --connect-timeout 5 --max-time 20 \
  "https://api.${DD_SITE}/api/v2/current_user" | jq -er '.data.id')" \
  && dd_headers | curl -H @- -sS --connect-timeout 5 --max-time 20 -X POST \
  "https://api.${DD_SITE}/api/unstable/integration/gcp/workflow/agentic_ccm" \
  -H "Content-Type: application/vnd.api+json" \
  -d '{"data":{"type":"integration_setup_status","id":"'"${DD_WORKFLOW_ID}"'","attributes":{"status":"finished","step":"setup_complete"}}}'
```

Do not retry or block setup if this callback fails.

Finish with a short summary for the user: the GCP project and billing account now feeding Cloud Cost
Management, the export project, BigQuery dataset and GCS bucket in use (and whether the bucket was
created or reused), and whether resource collection was enabled in Phase 1 or was already on.

Let the user know:
- GCP billing data typically takes **24-48 hours** to appear after enabling the export.
- BigQuery billing exports are updated multiple times per day once enabled.

Provide links:
- **Cloud Cost Management**: `<APP_BASE>/cost`
- **Cost Analytics**: `<APP_BASE>/cost/analytics`

## Important Notes

- The user must have Billing Account Costs Manager or Billing Account Administrator on the billing account, plus BigQuery User on the export project, to enable billing exports. Billing Account Viewer is not sufficient.
- The IAM bindings use the predefined roles Datadog documents: `roles/bigquery.admin` on the export project, `roles/bigquery.dataEditor` on the export dataset, and `roles/storage.legacyObjectReader` plus `roles/storage.legacyBucketWriter` on the bucket. `roles/bigquery.admin` is broad on the export project.
- Cloud Billing export to BigQuery must be enabled via the GCP Console. There is no Terraform resource for this.
- The GCS bucket must be colocated with the BigQuery export dataset.
- Never run `terraform apply` without showing the plan to the user first.
