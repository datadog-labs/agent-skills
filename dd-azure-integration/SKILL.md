---
name: dd-azure-integration
description: Set up the Datadog Azure integration with Terraform - creates an Entra ID app registration and service principal, assigns Monitoring Reader across the chosen subscriptions and management groups, grants the Microsoft Graph permissions Datadog needs for resource discovery, and registers the tenant so Azure metrics and resource collection start flowing. Use when the user wants to monitor Azure VMs, App Service, SQL Database, or AKS, wants to connect an Azure subscription or management group or tenant to Datadog, or asks to set up or repair the Azure integration. Does not set up log forwarding.
metadata:
  version: "1.0.0"
  author: datadog-labs
  repository: https://github.com/datadog-labs/agent-skills
  tags: datadog,azure,integration,terraform,entra,cloud
  alwaysApply: "false"
  tools: terraform
---

# Datadog Azure Integration

You are helping a user set up the Datadog Azure integration using Terraform.

The integration creates an Azure AD app registration with a service principal, assigns the Monitoring Reader
role to the user's subscriptions and/or management groups, grants Microsoft Graph API permissions for
resource discovery, and registers the integration with Datadog.

This is a hands-on setup: run the commands yourself as part of the conversation rather than handing the
user a list, keep them in the loop, and pause for confirmation before `terraform apply`.

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
for f in .env.local .env; do [ -f "$f" ] || continue; for k in DD_SITE DD_API_KEY DD_APP_KEY; do eval "[ -n \"\${$k:-}\" ]" && continue; v=$(grep -E "^$k=" "$f" | head -1 | cut -d= -f2- | sed 's/^["'\'']//;s/["'\'']$//'); [ -n "$v" ] && export "$k=$v"; done; done
: "${DD_SITE:=datadoghq.com}"
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
| Either key unset | Nothing to validate | On a commercial site, run the **dd-account-setup** skill, then come back. On `ddog-gov.com` or `us2.ddog-gov.com`, ask the user for the keys directly - that skill validates `DD_SITE` against a list that excludes both government sites and will reject them |

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

**Tools.** `terraform` is required; the `az` CLI is optional (it only discovers subscriptions and
management groups):

```bash
command -v terraform || echo "MISSING terraform - https://developer.hashicorp.com/terraform/install"
command -v az >/dev/null 2>&1 && echo "az: available" || echo "az: not installed"
```

The `azurerm` and `azuread` providers read the ambient Azure credentials, so the user must be signed in
(`az login`) with permission to create app registrations and assign roles.

**The snippets here are POSIX shell.** Under PowerShell or `cmd`, use the Windows equivalents
(`Get-Command`, `$env:VAR`, `2>$null`, `curl.exe`) - same calls, same order.

## Phase 1: Determine Scope

Ask the user if they already know which Azure subscription IDs and/or management group names they want
Datadog to monitor.

If they do, collect:
- The **tenant ID** - always, even on this path. The duplicate check and `datadog_integration_azure` are
  both keyed on the tenant, so you cannot skip it. With `az` available:
  `az account show --query tenantId -o tsv`; otherwise portal.azure.com → **Microsoft Entra ID** →
  **Overview** → **Tenant ID**.
- The list of subscription IDs to monitor
- The list of management group names to monitor (optional)

If they don't know or want help figuring it out:

**If the `az` CLI is available**, offer to discover their Azure environment.
Explain that you will use `az` to list their subscriptions and management groups so they can pick
which ones to monitor. This is best-effort - run each command independently and work with whatever
succeeds:

First, get the current tenant ID:
```bash
az account show --query "tenantId" -o tsv
```

**Subscriptions:**
```bash
az account list --query "[?tenantId=='<TENANT_ID>'].{id:id, name:name}" -o table
```

**Management Groups:**
```bash
az account management-group list --query "[?tenantId=='<TENANT_ID>'].{name:name, displayName:displayName}" -o table
```

If any individual command fails (e.g., the user lacks permission to list management groups),
inform the user which command failed and why, but continue with whatever information was
successfully retrieved.

**Otherwise** (`az` not installed or not authenticated), ask the user to gather the IDs from the
Azure portal:
- **Tenant ID**: portal.azure.com → **Microsoft Entra ID** → **Overview** → **Tenant ID**.
- **Subscription IDs**: portal.azure.com → **Subscriptions**.
- **Management Group names** (optional): portal.azure.com → **Management groups**.

Present whatever results were gathered in a readable format and let the user choose:
- **Specific subscriptions**: list of subscription IDs
- **Management groups**: list of management group names (Datadog gets Monitoring Reader on the group scope)
- **Both**: a combination of explicit subscriptions and management groups

## Phase 2: Generate and Apply Terraform

### Check what already exists - local Terraform first, then Datadog

Do this **before** generating or applying anything, and in this order. Local state first, because it
decides whether an existing integration is something you can update or something you must not touch:

```bash
find . -maxdepth 1 -type f \( -name '*.tf' -o -name 'terraform.tfstate' \) -print
# A project is "present" if it has configuration - .terraform/ may simply not exist yet on a fresh clone
# with a remote backend, and terraform.tfstate does not exist at all when state is remote.
if [ -n "$(find . -maxdepth 1 -type f \( -name '*.tf' -o -name '*.tf.json' \) -print -quit)" ]; then
  terraform init -input=false >/dev/null || { echo "terraform init failed - resolve that before concluding anything about existing state"; exit 1; }
  out=$(terraform state list 2>&1); rc=$?
  if [ "$rc" -ne 0 ]; then
    case $out in
      *'No state file'*|*'no state'*|*'Backend initialization required'*)
        echo "project is initialized but has no state yet - treat as a clean install" ;;
      *)
        printf '%s\n' "$out"
        echo "could not read state (backend or credentials problem) - do NOT treat this as 'nothing exists'"; exit 1 ;;
    esac
  elif [ -z "$out" ]; then
    echo "state is empty - treat as a clean install"
  else
    printf '%s\n' "$out" | grep -F 'datadog_integration_azure' || echo "state exists but holds no datadog_integration_azure resource"
  fi
else
  echo "no Terraform configuration here yet - clean install"
fi
```

Match the **exact** resource address `datadog_integration_azure`, not a loose `grep -i datadog`: unrelated Datadog
resources, or cloud IAM left behind by a partial apply, would otherwise read as a managed integration.

Then ask Datadog what it already has:

```bash
for f in .env.local .env; do [ -f "$f" ] || continue; for k in DD_SITE DD_API_KEY DD_APP_KEY; do eval "[ -n \"\${$k:-}\" ]" && continue; v=$(grep -E "^$k=" "$f" | head -1 | cut -d= -f2- | sed 's/^["'\'']//;s/["'\'']$//'); [ -n "$v" ] && export "$k=$v"; done; done
DD_SITE='datadoghq.com'; export DD_SITE   # <- replace with the site confirmed in Phase 0.
# Explicit assignment, not ':=': a wrong non-empty DD_SITE in .env would otherwise survive.
: "${DD_API_KEY:?not set - run dd-account-setup (commercial sites) or supply it directly (government sites)}"; : "${DD_APP_KEY:?not set - run dd-account-setup (commercial sites) or supply it directly (government sites)}"
resp=$(printf 'DD-API-KEY: %s\nDD-APPLICATION-KEY: %s\n' "$DD_API_KEY" "$DD_APP_KEY" \
  | curl -sS -w '\n%{http_code}' -X GET -H @- "https://api.${DD_SITE}/api/v1/integration/azure")
code=$(printf '%s' "$resp" | tail -1); body=$(printf '%s' "$resp" | sed '$d')
[ "$code" = "200" ] || { echo "lookup failed with HTTP $code - do not assume 'not connected':"; printf '%s\n' "$body"; exit 1; }
printf '%s\n' "$body"
```

Now reconcile the two answers before doing anything:

- **Not in Datadog, nothing in local state** - a clean install. Continue.
- **In Datadog *and* present in local state** - this is the update/repair case, not a duplicate.
  Continue into the Terraform below as a change to the existing resources, and let the plan show
  what it will alter.
- **In Datadog but *absent* from local state** (the tenant from Phase 1 is already registered) - stop. Applying would either create a
  duplicate or fight with whatever manages it. Say so plainly and offer the options: import the existing
  object into this project (`terraform import datadog_integration_azure.datadog_integration "${tenant_name}:${client_id}"`,
  with the existing app's secret supplied as the `CLIENT_SECRET` environment variable), manage it where it is already managed, or delete it in Datadog first.
  Only continue if the user picks one and confirms.
  Two things about importing, in this order. **Generate the configuration first** (the Terraform below,
  adapted to the identity that already exists - same role/app/service-account name), because `terraform
  import` binds an existing object to a *configured* resource address and fails without one. And importing
  the Datadog registration alone is not enough: the cloud-side identity (the IAM role, the app registration,
  the service account) is still outside state, so either import those too or reference them with data
  sources, or the next apply will try to create them again and collide.

   ```bash
   # the existing app's secret must be in the environment for the import to validate
   CLIENT_SECRET='<existing-app-secret>' terraform import datadog_integration_azure.datadog_integration "<tenant_name>:<client_id>"
   ```
- **Present in local state but *absent* from Datadog** - a partial or rolled-back install. The cloud-side
  resources may exist while the registration does not. Do not start from scratch: run the plan and let it
  show what is missing, and expect it to re-create only the registration.

### Check for Existing Terraform

Before generating a new Terraform configuration, check if the user already has a Terraform project
in the current directory or nearby:

```bash
find . -maxdepth 1 -type f \( -name '*.tf' -o -name 'terraform.tfstate' \) -print
```

**If existing `.tf` files are found:**
- Read them to understand what providers and resources are already configured.
- If a `datadog` provider already exists, reuse its configuration - do not create a duplicate.
- If `azurerm` or `azuread` providers already exist, reuse them.
- Only add the **new resources** needed (app registration, role assignments, `datadog_integration_azure`)
  to the existing project. Do not regenerate providers, variables, or terraform blocks that
  already exist.
- If the user has a modular layout, create a new file like `datadog-azure-integration.tf` for the
  Datadog resources.

**Before generating anything, settle where state will live.** This template creates a client secret that
is stored in state (see Important Notes), so a default local `terraform.tfstate` means a plaintext secret
on disk. Confirm with the user that state goes to an encrypted, access-controlled remote backend, and
configure that backend **before** `terraform init` - moving state afterwards leaves the plaintext copy
behind. **If they will not use an encrypted remote backend, stop here.** Explain that the generated client
secret would sit in cleartext in a local `terraform.tfstate`, and offer the alternative: configure the
integration through the Azure integration tile in the Datadog UI, which stores the secret server-side and
writes no state file. An acknowledgement is not a substitute for the backend - this matches the family rule
for this family of skills, and it holds however this skill was invoked.

**If no existing Terraform is found**, generate a standalone configuration.

The full HCL template - providers, the app registration and rotating secret, the Monitoring Reader
assignments for both scopes, the Graph API grants, and the `datadog_integration_azure` registration -
is in **`references/terraform.md`**, along with the subscriptions-only and management-groups-only
variants. Read it now and emit it with the placeholders filled in.

## Applying the Terraform

1. Replace all `<PLACEHOLDER>` values in the template with the actual values gathered:
   - `<TENANT_ID>` from Phase 1
   - `<USER_SUBSCRIPTION_IDS>` from Phase 1
   - `<USER_MANAGEMENT_GROUP_NAMES>` from Phase 1
   - `<DD_SITE>` from Phase 0
2. If the user selected only subscriptions (no management groups), set `management_group_names = []` and remove the `azurerm_role_assignment.monitoring_reader_management_group` resource.
3. If the user selected only management groups (no explicit subscriptions), they still need at least one subscription ID for the `azurerm` provider - use a subscription from within one of their management groups.
4. Run `terraform init` to install providers.
5. Plan, and **save the plan to a file**. The Datadog provider reads `DD_API_KEY` / `DD_APP_KEY`
   straight from the environment, so there are no root variables and no `-var=` arguments - nothing secret
   ends up in the plan file, in state, or on a command line:

   ```bash
   for f in .env.local .env; do [ -f "$f" ] || continue; for k in DD_SITE DD_API_KEY DD_APP_KEY; do eval "[ -n \"\${$k:-}\" ]" && continue; v=$(grep -E "^$k=" "$f" | head -1 | cut -d= -f2- | sed 's/^["'\'']//;s/["'\'']$//'); [ -n "$v" ] && export "$k=$v"; done; done
   DD_SITE='datadoghq.com'; export DD_SITE   # <- replace with the site confirmed in Phase 0.
   # Explicit assignment, not ':=': a wrong non-empty DD_SITE in .env would otherwise survive.
   : "${DD_API_KEY:?not set - run dd-account-setup (commercial sites) or supply it directly (government sites)}"; : "${DD_APP_KEY:?not set - run dd-account-setup (commercial sites) or supply it directly (government sites)}"
   umask 077          # tighten permissions on the plan file anyway
   terraform plan -out=tfplan
   ```

   Show the plan output to the user and wait for explicit confirmation.
6. Apply **that saved plan**, only after the user confirms it. Applying the file is what makes the
   approval meaningful: `terraform apply` with no plan file computes a brand-new plan, and `-auto-approve`
   would execute it without anyone seeing it, so anything changed since the plan would go in unreviewed:

   ```bash
   for f in .env.local .env; do [ -f "$f" ] || continue; for k in DD_SITE DD_API_KEY DD_APP_KEY; do eval "[ -n \"\${$k:-}\" ]" && continue; v=$(grep -E "^$k=" "$f" | head -1 | cut -d= -f2- | sed 's/^["'\'']//;s/["'\'']$//'); [ -n "$v" ] && export "$k=$v"; done; done
   DD_SITE='datadoghq.com'; export DD_SITE   # <- replace with the site confirmed in Phase 0.
   # Explicit assignment, not ':=': a wrong non-empty DD_SITE in .env would otherwise survive.
   : "${DD_API_KEY:?not set - run dd-account-setup (commercial sites) or supply it directly (government sites)}"; : "${DD_APP_KEY:?not set - run dd-account-setup (commercial sites) or supply it directly (government sites)}"
   trap 'rm -f tfplan' EXIT HUP INT TERM   # the plan file goes away even if this is interrupted
   if terraform apply tfplan; then
     echo "apply complete"
   else
     echo "terraform apply FAILED - do NOT verify or report success"
     exit 1
   fi
   ```

   The `trap` removes the plan file on every exit path, including Ctrl-C while the user is deciding. The
   `if`/`else` around the apply matters because a cleanup command as the block's last line would make a
   failed apply exit 0, and the agent would go on to "verify" a deployment that never happened. (It is an
   `if` rather than `status=$?` on purpose: `status` is a read-only variable in zsh.)

   The plan file holds no key material at all, because the keys never become Terraform values - the provider
   reads them from the environment. That is what makes `-out` safe here, on any Terraform version, and it is
   why an existing project needs no variable changes either.

7. After `terraform apply` succeeds, verify the integration registered with Datadog:

   ```bash
   for f in .env.local .env; do [ -f "$f" ] || continue; for k in DD_SITE DD_API_KEY DD_APP_KEY; do eval "[ -n \"\${$k:-}\" ]" && continue; v=$(grep -E "^$k=" "$f" | head -1 | cut -d= -f2- | sed 's/^["'\'']//;s/["'\'']$//'); [ -n "$v" ] && export "$k=$v"; done; done
   DD_SITE='datadoghq.com'; export DD_SITE   # <- replace with the site confirmed in Phase 0.
   # Explicit assignment, not ':=': a wrong non-empty DD_SITE in .env would otherwise survive.
   : "${DD_API_KEY:?not set - run dd-account-setup (commercial sites) or supply it directly (government sites)}"; : "${DD_APP_KEY:?not set - run dd-account-setup (commercial sites) or supply it directly (government sites)}"
   resp=$(printf 'DD-API-KEY: %s\nDD-APPLICATION-KEY: %s\n' "$DD_API_KEY" "$DD_APP_KEY" \
     | curl -sS -w '\n%{http_code}' -X GET -H @- "https://api.${DD_SITE}/api/v1/integration/azure")
   code=$(printf '%s' "$resp" | tail -1); body=$(printf '%s' "$resp" | sed '$d')
   [ "$code" = "200" ] || { echo "lookup failed with HTTP $code - do not assume 'not connected':"; printf '%s\n' "$body"; exit 1; }
   printf '%s\n' "$body"
   ```

   Confirm the response lists the tenant and client_id just provisioned. If the integration is missing, surface the response to the user so they can debug.

## Getting the Most Out of Your Integration

Once `terraform apply` completes successfully, congratulate the user and let them know metrics typically
arrive within 5-10 minutes. Then check for early metrics and show a widget.

### Checking for Metrics

Give it a few seconds, then make a single query to the metrics API:

```bash
for f in .env.local .env; do [ -f "$f" ] || continue; for k in DD_SITE DD_API_KEY DD_APP_KEY; do eval "[ -n \"\${$k:-}\" ]" && continue; v=$(grep -E "^$k=" "$f" | head -1 | cut -d= -f2- | sed 's/^["'\'']//;s/["'\'']$//'); [ -n "$v" ] && export "$k=$v"; done; done
DD_SITE='datadoghq.com'; export DD_SITE   # <- replace with the site confirmed in Phase 0.
# Explicit assignment, not ':=': a wrong non-empty DD_SITE in .env would otherwise survive.
: "${DD_API_KEY:?not set - run dd-account-setup (commercial sites) or supply it directly (government sites)}"; : "${DD_APP_KEY:?not set - run dd-account-setup (commercial sites) or supply it directly (government sites)}"
sleep 10
resp=$(printf 'DD-API-KEY: %s\nDD-APPLICATION-KEY: %s\n' "$DD_API_KEY" "$DD_APP_KEY" \
  | curl -sS -w '\n%{http_code}' -G -H @- "https://api.${DD_SITE}/api/v1/query" \
  --data-urlencode "from=$(($(date +%s) - 900))" \
  --data-urlencode "to=$(date +%s)" \
  --data-urlencode "query=avg:azure.vm.percentage_cpu{*} by {name}")
code=$(printf '%s' "$resp" | tail -1); body=$(printf '%s' "$resp" | sed '$d')
[ "$code" = "200" ] || { echo "metric query failed with HTTP $code - that is NOT 'metrics still propagating':"; printf '%s\n' "$body"; exit 1; }
printf '%s\n' "$body"
```

### Rendering the Widget

**If the `series` array is non-empty**, render an ASCII chart from the real data:
- Use the `pointlist` values to plot the line, scaling Y-axis to actual min/max.
- Use box-drawing characters (`╭`, `╰`, `─`, `│`, `┤`) for the line.
- List the VM names from each series `scope` at the bottom.
- Show the top 3 series by average value if multiple are returned.

**If the `series` array is empty**, show this static preview instead and let the user know
metrics are still propagating:

    ┌─────────────────────────────────────────────────────────┐
    │  azure.vm.percentage_cpu            ▂▃▅▆▇▆▅▃▂▁▂▃▅▆▇█  │
    │  100% ┤                                          ╭──╮   │
    │   75% ┤                    ╭───╮              ╭──╯  │   │
    │   50% ┤              ╭────╯   ╰──╮     ╭────╯     │   │
    │   25% ┤    ╭────────╯            ╰────╯           │   │
    │    0% ┤────╯                                       │   │
    │       └────────────────────────────────────────────┘   │
    │                                                         │
    │  Metrics are on their way - check back in a few minutes │
    └─────────────────────────────────────────────────────────┘
    Metrics Explorer: <APP_BASE>/metric/explorer?exp_metric=azure.vm.percentage_cpu

Confirm to the user that their integration is configured and data will appear shortly. All links use `DD_SITE` - construct them as `<APP_BASE>/...`.

- **Azure Integration tile**: `<APP_BASE>/integrations/azure` - access the pre-built dashboard and verify the integration is active.
- **Metrics Explorer**: `<APP_BASE>/metric/explorer?exp_metric=azure.vm.percentage_cpu` - confirm data is flowing.
- Each Azure service (Virtual Machines, App Service, SQL Database, AKS, etc.) has its own dashboard that activates automatically when metrics for that service are detected.

**Recommended Monitors** - suggest creating monitors for common Azure health signals at `<APP_BASE>/monitors/create`:
- Virtual Machine CPU exceeding a threshold
- App Service HTTP error rate spikes
- SQL Database DTU consumption approaching limits
- AKS node pool availability

**If resource collection was enabled:**
- **Resource Catalog**: `<APP_BASE>/infrastructure/catalog` - browse Virtual Machines, App Services, SQL Databases, AKS clusters, and more.
- **Infrastructure Map**: `<APP_BASE>/infrastructure/map` - visualize Azure infrastructure.

**Explore more Datadog products:**
- **Log Management**: `<APP_BASE>/logs` - stream Azure activity and resource logs for centralized search and alerting. Setup: https://docs.datadoghq.com/integrations/azure/#log-collection
- **APM & Traces**: `<APP_BASE>/apm/getting-started` - distributed tracing for applications on App Service, AKS, or Virtual Machines.
- **Notebooks**: `<APP_BASE>/notebook` - shareable investigations combining metrics, logs, and events.

## Important Notes

- **Concrete permissions the template needs** - "can create app registrations" is not enough, and a
  Contributor or Application Developer will get through discovery and then fail with 403 at apply:
  - **Application Administrator** or **Global Administrator** in Entra ID, because
    `azuread_app_role_assignment` grants Microsoft Graph app roles (admin consent).
  - `Microsoft.Authorization/roleAssignments/write` at **every** selected subscription and management-group
    scope - typically **User Access Administrator** or **Owner** there - for the Monitoring Reader
    assignments.
  Check these before Phase 2; if the user lacks them, they need their Entra administrator rather than a
  retry.
- The app registration secret expires after 1 year - remind the user they'll need to rotate it.
- The Datadog API and app keys are never passed to Terraform as values: the `datadog` provider reads
  `DD_API_KEY` and `DD_APP_KEY` from the environment, so there are no root variables, no `-var=` arguments,
  and nothing for Terraform to record in state or a saved plan. Don't declare key variables, and don't
  write the keys into a committed `.tfvars` file or any other persistent file. (The *client secret* this
  template generates is a separate matter - it is a resource attribute and does land in state; see below.)
- Never run `terraform apply` without showing the plan to the user first.
- The `azurerm` provider requires at least one subscription ID even when using management groups.
- Unlike the AWS role and GCP impersonation flows, this one issues a **client secret** that Datadog
  stores, which is why it is created through a `time_rotating` resource rather than as a static value.
- **The client secret is written to Terraform state.** `azuread_application_password.value` and
  `datadog_integration_azure.client_secret` are resource attributes, so `sensitive = true` redacts them
  from CLI output but not from state
  ([HashiCorp docs](https://developer.hashicorp.com/terraform/language/manage-sensitive-data)). Say this
  out loud to the user: state must be encrypted and access-controlled, and `terraform.tfstate` must not
  be committed. A secretless flow does exist -
  `secretless_auth_enabled = true`, federated workload identity, Preview - but it requires a Datadog
  federated credential **on the app registration**, and this template's newly created app has none. Don't
  offer it as a flag flip; see the note in `references/terraform.md` for what it would actually take.
