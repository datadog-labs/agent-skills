---
name: dd-coreweave-agent-fleet-installation-on-clusters
description: Install the Datadog Agent on the CoreWeave Kubernetes (CKS) clusters of a tenancy that is already connected to Datadog, so container monitoring and APM telemetry flow into Datadog without connecting to each cluster or running Helm yourself. Discovers the tenant's integrated account and its CKS clusters, asks which clusters to cover, and creates the Datadog install rule that makes Datadog install and manage the Agent fleet. Datadog rolls the Operator out from there; the skill stops at rule creation. The feature is in preview. Use when the user wants Datadog monitoring on their CoreWeave clusters, wants to install the Datadog Agent fleet on CoreWeave, or wants container or APM telemetry from CoreWeave CKS clusters.
metadata:
  version: "0.1.0"
  author: datadog-labs
  repository: https://github.com/datadog-labs/agent-skills
  tags: datadog,coreweave,kubernetes,cks,agent,installation,containers,apm,monitoring,fleet
  alwaysApply: "false"
---

# Datadog Agent Fleet Installation on CoreWeave CKS Clusters

Datadog installs and manages the Agent on the tenancy's CoreWeave
Kubernetes (CKS) clusters: the Datadog Operator in a `datadog-managed`
namespace deploys the Agent on every Linux node and the Cluster Agent,
keeps them current, and covers clusters that start matching the rule
later. Nothing is installed until a rule exists, and clusters that already
run a Datadog installation are reported, never changed.

**The skill stops at rule creation.** Once the rule exists, Datadog's own
server-side Operator rollout takes over. The skill does not wait for the
rollout, poll it to completion, push any configuration, or verify
telemetry.

**Scope.** Container monitoring and APM are enabled by default on an
installed Agent. Private API endpoints are not supported (clusters
are public unless CoreWeave Support makes them private). Do not enable
other Datadog features (log collection, CWS, SBOM, profiling) unless the
user explicitly asks.

## Phase 0: Preflight

These prerequisites must be in place before any setup step runs.

**Datadog credentials and org — locked in before anything else.** Every
later step (integration detection, rule lookup, rule write) is
org-scoped, and a wrong org yields a confidently wrong answer instead of
an error. Nothing else in this skill runs — not even read-only discovery
— until the Datadog org and site are locked:

1. Source `<SKILL_DIR>/scripts/dd-env.sh` in the same shell as every
   script invocation: it loads `DD_SITE` / `DD_API_KEY` / `DD_APP_KEY`
   from the environment (exported values win) falling back to
   `.env.local` / `.env`, defaults `DD_SITE` to `datadoghq.com`, and
   prints the resolved state — `set`/`unset` only, never key values.
2. Both keys set → the script validates the pair and resolves its org
   at preflight (`datadog_session.env_keys` in the output, with
   `env_keys_wrong_site` / `env_keys_wrong_org` /
   `env_keys_org_unresolved` warnings when the pair cannot work for a
   keyed write on this session). Confirm the org with the user once.
3. Either key missing → run the **dd-account-setup** skill first and
   come back: it locks in the org and provisions the keys through a Pup
   login or a hand-rolled PKCE flow. Do not modify dd-account-setup for
   this, and reuse the API key it provisions as the Agents' API key
   below (the key the installed Agents use).

**The CoreWeave CLI (`cwic`).** Installed and logged in, with a session for
the same CoreWeave organization the Datadog integration account holds.
Check `command -v cwic`; if it is missing, point the user to the cwic
README (https://github.com/coreweave/cwic) for the install steps for their
platform. Have the user run `cwic auth login` themselves (it opens a
browser; the token page is https://console.coreweave.com/tokens) and
verify with `cwic auth whoami`. Never ask the user to paste a CoreWeave
token into chat.

**Pup.** Skip this when Pup is not installed — with `DD_API_KEY` and
`DD_APP_KEY` both exported, the pair authenticates the rule write on its
own (the script checks its site and org at preflight). Otherwise, check
the session on the site dd-env.sh resolved:

```bash
command -v pup >/dev/null 2>&1 && DD_SITE="${DD_SITE:-datadoghq.com}" pup auth status
```

The only scope this flow asks for beyond Pup's normal set is
`agent_install_write`, which lets rule writes ride the session token.
If the script reports a scope error (`pup_scopes_missing` or
`pup_scopes_missing_for_key_minting`), ask before running the login
command in the error's remediation — it names the exact scopes, and
`--extra-scopes` **adds** to Pup's normal set (never `--scopes`, which
replaces it). Run every Pup login on the resolved site
(`DD_SITE='<site>' pup auth login ...`): a login on Pup's default site
locks the token into the wrong org.

**Python 3**, for the scripts:

```bash
command -v python3 || echo "MISSING python3"
```

**Detect the existing integration before offering to create one.** The
tenancy is an existing CoreWeave Cloud integration, and the script
enforces the ordering — no reliance on the agent: the tenancy is
identified and org-paired (accounts → telemetry `cluster_org` /
`account_display_name` → cwic session's org) before required access,
rules, or clusters are read. An existing account whose org matches is
the tenancy to use; only when the right org holds no matching account
does the `dd-coreweave-integration` skill follow — never create a second
integration for the same CoreWeave org, because rules and install state
would strand across two accounts. The account's access token must carry
the **CKS Admin** role — CoreWeave API tokens inherit the roles of the
user who created them, so that user needs CKS Admin, because Datadog
needs cluster-admin access to install the Operator. Every discovery and
install result carries `datadog_session` (`site`, `org`, `env_keys`) so
a wrong-org run is visible immediately.

With the prerequisites in place, run the read-only discovery, which
validates Pup scopes, the cwic session, the provider's agent-fleet
availability for this organization, the integrated account, its existing
install rule, and the tenancy's CKS clusters, and lists the organization's
API keys:

```bash
python3 <SKILL_DIR>/scripts/setup.py
```

**Preview.** Agent fleet installation on CoreWeave is in preview. When
the script reports a preview error (`mci_not_enabled` or
`preview_feature`), relay this message verbatim and stop — do not guess
at another cause or suggest a workaround:

> This is a preview feature. Please reach out to Datadog support for early access.

The script is non-interactive and makes no changes in this mode. Handle its
errors before asking anything:

- `pup_cli_missing` / `pup_not_authenticated` / `pup_scopes_missing` —
  follow the remediation in the error, asking before any login command.
- `cwic_cli_missing` / `cwic_not_logged_in` / `coreweave_command_failed` —
  follow the CoreWeave CLI prerequisite above. `cwic auth whoami` exits 0
  even when logged out, so the script checks for a logged-in principal
  itself; a `cwic_not_logged_in` or login failure means the session is
  missing or expired, so have the user run `cwic auth login` again.
- `mci_not_enabled` / `preview_feature` — the feature is in preview and not
  enabled for this organization. Relay verbatim: "This is a preview
  feature. Please reach out to Datadog support for early access."
- `integration_not_configured` — the error names the session's org and
  site: no CoreWeave account exists **in that org**, not anywhere.
  Re-check the org/site pinning first; only when the right org is
  confirmed empty does the `dd-coreweave-integration` skill follow.
- `integration_not_available` — Datadog's CoreWeave integration account API
  is disabled for this organization; enable the CoreWeave integration here
  first, or ask your Datadog representative.
- `multiple_accounts` — the script first tries to select the account whose
  CoreWeave org matches the cwic session automatically (through the
  integration's telemetry); ask the user which Datadog account to use and
  rerun with `--account-id` only when that matching fails.
- `cluster_org_mismatch` — the cwic session is logged into a different
  CoreWeave org than the one the Datadog integration reports. Every later
  step would preview the session's clusters while the install covers the
  integration's org, so the run stops here. Have the user run
  `cwic auth login` into the CoreWeave organization the Datadog account
  holds (or pass `--account-id` for the matching account), then rerun.
- `coreweave_cluster_list_unparsed` — run `cwic cluster get` yourself,
  tell the user what it printed, and have the install use `--query`.

Report the discovery to the user as a table built straight from the
script's JSON: tenancy (account name and id), `datadog_session`,
`org_pairing.verified`, each cluster (name, id, zone, status, node
pools, GPU), the existing rule, `other_account_rules`, and every
warning with its message. Relay the script's fields and warnings
verbatim — they already say what each means, so do not re-derive or
re-explain them.

## Phase 1: Determine scope

- **Clusters:** multi-select from the discovered clusters, including an
  "All clusters in the tenancy" option. Selecting all (the default
  recommendation) covers current and future clusters. A single
  selected cluster is isolated by its own name; several selected clusters
  need a shared zone that no other cluster uses, or the script fails with
  `no_isolating_query`, in which case run the install with `--query`.
  When the tenancy has more clusters than the question tool can show,
  rely on the tool's built-in **Other** choice for unlisted clusters —
  never add a custom "Other — type X" option, which renders with no
  place to type and returns an empty value. If the user picks Other,
  ask for the cluster names or ids as text and pass those to
  `--clusters`.
- **Coverage when an `existing_rule` is reported:** still ask — the
  local cluster list may be incomplete, and the rule's current coverage
  may not match the intent. One lean question —
  `Which clusters should the Agent fleet rule cover?` — with the content
  in the options:
  - **<COVERED_CLUSTER_NAMES> only (Recommended)** — keeps the existing
    rule: nothing changes, the flow stops here. No approval question
    and no install command follow — a matching rule means there is no
    write to authorize.
  - **Tag filter for future clusters** — the user types a `TAG:VALUE`
    next; every matching cluster, future ones included, is covered.
    This replaces the existing rule: the install would stop with
  - **Keep existing coverage (Recommended)** — describe the existing
    rule's query (or all clusters when it has none), including future
    matching clusters. Nothing changes, and the flow stops here. No
    approval question or install command follows.
- **Datadog API key:** single-select from the discovered API-key names.
  This is the key the installed Agents use, not an authentication key for
  this flow.

Then use the interactive question tool once for approval:

- **Title:** `Approve CoreWeave agent fleet installation`
- **Question:** `Please confirm your Datadog Agent Remote Installation Selection:

  - Coverage: all clusters in the tenancy — <N> today (<NAMES>), plus any cluster added later` when the selection was all clusters, or `Only clusters matching the tag query <TAG:VALUE> — <N> today (<NAMES>); clusters that start matching later are covered automatically` when specific clusters or a custom query were chosen (name the isolating tag, or the cluster names when isolated by name).
  - Monitoring: container monitoring and APM are on by default.
  - API key for the Agents: <API_KEY_NAME>.

  Datadog will create one install rule for the tenancy: the Datadog Operator in a datadog-managed namespace deploys the Agent on every Linux node and a Cluster Agent. Clusters that already run a Datadog installation are reported, not changed. Once the rule exists, Datadog rolls the Operator out itself; this skill stops at rule creation.`
- **Approve option:** `Create the Agent fleet rule`
- **Cancel option:** `Cancel without making changes`

## Phase 2: Setup

After permission, create the rule in the foreground — the command no longer
waits for a rollout, so it needs no `nohup` and no log file. Source
`dd-env.sh` in the same shell as the invocation (shells do not persist
exports between commands):

```bash
python3 <SKILL_DIR>/scripts/setup.py --install \
  --api-key-name "<API_KEY_NAME>"
```

Add `--clusters "<COMMA_SEPARATED_IDS>"` when specific clusters were
selected, or `--query "<TAG:VALUE>"` when the user supplied their own
query. Launching the command is authorization for the rule creation.

The script prints one JSON result when the rule exists and exits. Report
the rule id (`rule.id`), the coverage, the selected clusters, the API key,
and all warnings, and tell the user that Datadog rolls out the Operator
from here. Do not wait for the rollout, poll it to completion, push any
configuration, or verify telemetry. If the user asks for progress later,
`python3 <SKILL_DIR>/scripts/setup.py --status <RULE_ID>` is a read-only
read.

Handle these install-time errors:

- `preview_feature` — the rules API returned a 404 or an
  unsupported-resource-type 400, so the preview flags are off. Relay the
  preview message verbatim (see Preview above).
- `rule_write_keys_wrong_org` — the exported `DD_API_KEY`/`DD_APP_KEY`
  pair does not see the selected account (or, for a deletion, the rule),
  so it belongs to a different Datadog org than the session. The write
  was not sent. Export a key pair from the same org as the session, or
  log in with `agent_install_write` so the write rides the token.
- `rule_write_not_permitted` — Datadog refused the rule write. When the
  error's details carry `transport: oauth`, the session token itself
  (which claims `agent_install_write`) was refused: rerun the login from
  the error's remediation and try again. Otherwise the token lacked the
  scope and the one-shot keys did not carry Agent Install — the fix is on
  the user side: export an API and application key pair owned by a user
  who holds it (Organization Settings > API Keys) and rerun, or ask their
  Datadog representative to grant the permission. Never ask for or paste
  key values into chat; the user exports the env vars themselves.
- `pup_scopes_missing_for_key_minting` — no env keys were exported and
  the Pup token lacks the key-creation scopes. Ask before running the
  full-scope login from the preflight section above.
- `existing_rule_coverage_mismatch` — the account already has an install
  rule whose query differs from the approved selection, and nothing was
  changed: reusing it would cover clusters the user did not select. Tell
  the user what the existing rule covers (`existing_query`; none means
  every cluster) and either rerun with that coverage or, with their
  approval, remove the rule with `--remove-rule <RULE_ID>` and rerun.

## Security and recovery

- The flow handles no cloud credentials at all: Datadog already holds the
  integration account's token and uses it itself.
- Never ask for or print Datadog API or application key values, or the
  CoreWeave token. The script references the Agents' API key by name or
  UUID only, and rule writes ride the session token when it carries
  `agent_install_write`; otherwise it resolves the rule-write pair
  itself: exported `DD_API_KEY`/`DD_APP_KEY` when present, otherwise
  one-shot keys minted through Pup and deleted after the write — the
  values never transit the conversation, stdout, or the emitted JSON.
- One-shot writes: the rule POST is sent once and reconciled with reads;
  no automatic retries.
- Cluster names, IDs, and org details are project-specific: never write
  them to persistent memory.
- To stop covering clusters, run
  `python3 <SKILL_DIR>/scripts/setup.py --remove-rule <RULE_ID>` with the
  user's approval. Rule deletion takes the same route as creation: the
  session's own token when it can write rules, otherwise the same key
  resolution. Datadog
  then removes the Operator, Agent, Cluster
  Agent, CRDs, cluster role, and admission webhooks, leaving only a small
  `datadog-operator-lock` Lease in `datadog-managed`. To only follow
  progress, use `--status <RULE_ID>` (read-only).
- For Pup failures, run `pup auth refresh`; if that fails, `pup auth
  login` with the scopes from the prerequisites.
