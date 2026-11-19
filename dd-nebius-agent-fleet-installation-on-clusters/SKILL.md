---
name: dd-nebius-agent-fleet-installation-on-clusters
description: Install the Datadog Agent on the Nebius Managed Kubernetes clusters of a tenant that is already connected to Datadog, so container monitoring and APM telemetry flow into Datadog without connecting to each cluster or running Helm yourself. Discovers the tenant's integrated account and its clusters, asks which clusters to cover and which API key the Agents should use, and creates one managed install rule that Datadog's Operator rollout applies to those clusters. Use when the user wants Datadog monitoring on their Nebius Kubernetes clusters, wants to install the Datadog Agent fleet on Nebius, or wants container or APM telemetry from Nebius MK8s clusters.
metadata:
  version: "0.1.1"
  author: datadog-labs
  repository: https://github.com/datadog-labs/agent-skills
  tags: datadog,nebius,kubernetes,mk8s,agent,installation,containers,apm,monitoring,fleet
  alwaysApply: "false"
---

# Datadog Agent Fleet Installation on Nebius Managed Kubernetes

Datadog installs and manages the Agent on the tenant's Managed Kubernetes
(mk8s) clusters: the Datadog Operator in a `datadog-managed` namespace
deploys the Agent on every Linux node and the Cluster Agent, keeps them
current, and covers clusters that start matching the rule later. Nothing is
installed until a rule exists, and clusters that already run a Datadog
installation are reported, never changed.

The skill's job ends when the rule exists. From there Datadog's own
server-side Operator rollout installs the fleet: this skill does not wait
for rollout, push configuration, or verify telemetry.

**Scope.** Container monitoring and APM are enabled by default on an
installed Agent. Private API endpoints are not supported. Do not enable
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

**The Nebius CLI (`nebius`).** Installed and authenticated, with a profile
whose tenant and project cover the clusters. Check `command -v nebius`; if
it is missing, follow the install and authentication steps in the
`dd-nebius-integration` skill's preflight: install with
`curl -sSL https://artifacts.nebius.cloud/cli/install.sh | bash` (ask
before running), reload the shell, then have the user run
`nebius profile create` interactively in their own terminal and choose the
tenant and project.

**Pup.** Required and authenticated on the site dd-env.sh resolved.
Exported `DD_API_KEY` and `DD_APP_KEY` replace only rule-write
authentication; preflight and discovery still require Pup. Check:

```bash
DD_SITE="${DD_SITE:-datadoghq.com}" pup auth status
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

With the prerequisites in place, run the read-only discovery, which
validates Pup scopes, the Nebius profile, the provider's agent-fleet
availability for this organization, the integrated account, its existing
install rule, and the tenant's clusters, and lists the organization's API
keys:

```bash
python3 <SKILL_DIR>/scripts/setup.py
```

**Preview.** Agent fleet installation on Nebius is in preview. When the
script reports a preview error (`mci_not_enabled` or `preview_feature`),
relay this message verbatim and stop — do not guess at another cause or
suggest a workaround:

> This is a preview feature. Please reach out to Datadog support for early access.

The script is non-interactive and makes no changes in this mode. Every
failure is emitted as structured JSON: `error.reason` is the code,
`error.message` says what happened, and `error.details.remediation` (when
present) says what to do, with `details`, `stderr`, and `command` carrying
the evidence. Relay the `message` and the remediation verbatim, follow the
remediation, and ask the user before any login or destructive command —
do not re-derive or re-explain the cause yourself. Two things are policy,
not script output:

- **Preview:** on `mci_not_enabled` or `preview_feature`, relay the message
  above verbatim and stop — never guess at a cause or workaround.
- **Key values:** never ask for or paste key values into chat; the user
  exports `DD_API_KEY`/`DD_APP_KEY` themselves.

If an error carries no remediation, its `message` and `details` say what to
fix; for a missing, unknown, or conflicting rule or cluster during an
update, rerun discovery and use the ids it reports. `rule_coverage_mismatch`
is the one error whose fix is a flow, not a command: make the update
through [Change an existing rule](#change-an-existing-rule) so it is
previewed and approved first.

Report the discovery to the user as a table built straight from the
script's JSON: the tenancy (account name and tenant id), `datadog_session`
(site, org, `env_keys`), each cluster (name, id, region, status, labels,
node-group health), the existing rule and its `rule_coverage`, the
required cloud role, the API keys, and every warning with its message.
Relay the script's fields and warnings verbatim — they already say what
each means, so do not re-derive or re-explain them.

## Phase 1: Determine scope

- **Clusters:** multi-select from the discovered clusters, including an
  "All clusters in the tenancy" option. Selecting all (the default
  recommendation) covers current and future clusters. Selecting specific
  clusters requires a label or region that isolates them; the script
  derives it or fails with `no_isolating_query`, in which case tell the
  user to add a unique label to those clusters (for example
  `datadog-managed:true`) and rerun. When the tenancy has more clusters
  than the question tool can show, rely on the tool's built-in **Other**
  choice for unlisted clusters — never add a custom "Other — type X"
  option, which renders with no place to type and returns an empty value.
- **Datadog API key:** single-select from the discovered API-key names.
  This is the key the installed Agents use, not an authentication key for
  this flow.

When discovery reports an `existing_rule`, still ask — the local cluster
list may be incomplete, and the rule's current coverage may not match the
intent. One lean question —
`Which clusters should the Agent fleet rule cover?` — with the content in
the options:

- **<COVERED_CLUSTER_NAMES> only (Recommended)** — keeps the existing
  rule: nothing changes, the flow stops here. No approval question and no
  install command follow — a matching rule means there is no write to
  authorize.
- **Tag filter for future clusters** — the user types a `TAG:VALUE`
  next; every matching cluster, future ones included, is covered. This
  replaces the existing rule: the install would stop with
  `rule_coverage_mismatch`, so with the user's approval remove the old
  rule with `--remove-rule <RULE_ID>` first and rerun with
  `--query "<TAG:VALUE>"` to create the new one.
- **Other** — cluster names. Add or remove those clusters through
  [Change an existing rule](#change-an-existing-rule), which writes only
  the rule's exact exceptions.

For a new rule (or the replacement after `--remove-rule`), use the
interactive question tool once for approval:

- **Title:** `Approve Nebius agent fleet installation`
- **Question:** `Please confirm your Datadog Agent Remote Installation Selection:

  - Coverage: all clusters in the tenancy — <N> today (<NAMES>), plus any cluster added later` when the selection was all clusters, or `Only clusters matching the tag query <TAG:VALUE> — <N> today (<NAMES>); clusters that start matching later are covered automatically` when specific clusters or a custom query were chosen (name the isolating tag, or the cluster names when isolated by name).
  - API key for the Agents: <API_KEY_NAME>.

  Datadog will create one install rule for the tenancy: the Datadog Operator in a datadog-managed namespace deploys the Agent on every Linux node and a Cluster Agent. Clusters that already run a Datadog installation are reported, not changed.`
- **Approve option:** `Approve and install the Agent fleet`
- **Cancel option:** `Cancel without making changes`

## Phase 2: Setup

After permission, create the rule. Source `dd-env.sh` in the same shell
as the invocation (shells do not persist exports between commands):

```bash
source <SKILL_DIR>/scripts/dd-env.sh
python3 <SKILL_DIR>/scripts/setup.py --install \
  --api-key-name "<API_KEY_NAME>"
```

Add `--clusters "<COMMA_SEPARATED_IDS>"` when specific clusters were
selected, or `--query "<LABEL:VALUE>"` when the user supplied their own
query. Creating the rule is the end of the skill's work: Datadog's
server-side Operator rollout installs and manages the fleet from there.
This script does not wait for rollout, push configuration, or verify
telemetry.

Read the final JSON and report the rule id, the rule's query, and the
clusters it covers. Tell the user plainly that Datadog now rolls the
Operator out to those clusters itself, that clusters can take a few
minutes to appear, and that they can follow progress in Fleet Automation
or with `python3 <SKILL_DIR>/scripts/setup.py --status <RULE_ID>`
(read-only). Do not poll in the conversation.

Script errors follow the Phase 0 handling: relay the JSON and its
remediation, asking before any login or destructive command. A
`rule_coverage_mismatch` means the change goes through
[Change an existing rule](#change-an-existing-rule).

## Change an existing rule

Reached when the user picks **Other** (cluster names) in the coverage
question above. Show the rule's `rule_coverage` table (Phase 0), then use
one interactive question form:

- **Clusters to add:** multi-select from the **Not covered** and
  **Excluded** clusters.
- **Clusters to remove:** multi-select from the **Included** clusters.
  Removing a cluster uninstalls the Agent from it.

Preview the change without writing anything:

```bash
python3 <SKILL_DIR>/scripts/setup.py --update-rule <RULE_ID> \
  --add-clusters "<IDS>" --remove-clusters "<IDS>" --dry-run
```

Pass only the flags with a selection. A `no_change` status means every
selected cluster is already where the user wants it (see `unchanged`);
report that and stop. Otherwise use the interactive question tool once
for approval:

- **Title:** `Approve install rule change`
- **Question:** `Please confirm the change to the Datadog Agent install rule:

  - Add: <NAMES> (the Agent is installed on them).
  - Remove: <NAMES> (the Agent is uninstalled from them; they stay out even if their labels match later).
  - Coverage after the change: <the coverage_after table, grouped into Included, Excluded and Not covered>.
  - Rule query: <query_before> → <query_after>.

  Clusters that match the rule's labels later are still covered automatically.`
- **Approve option:** `Approve and update the rule`
- **Cancel option:** `Cancel without making changes`

After permission, run the same command without `--dry-run`:

```bash
python3 <SKILL_DIR>/scripts/setup.py --update-rule <RULE_ID> \
  --add-clusters "<IDS>" --remove-clusters "<IDS>"
```

Report `added`, `removed`, and the rule's new `query_after`, plus all
warnings. Datadog installs the Agent on added clusters and uninstalls it
from removed ones itself; nothing is pushed or verified here. Follow
progress in Fleet Automation or with `--status <RULE_ID>` (read-only).

Script errors follow the Phase 0 handling: relay the JSON and its
remediation, asking before any login or destructive command.

## Security and recovery

- The flow handles no cloud credentials at all: Datadog already holds the
  integration account's credentials and uses them itself.
- Never ask for or print Datadog API or application key values. The
  script references the Agents' API key by name or UUID only, and rule
  writes ride the session token when it carries `agent_install_write`;
  otherwise it resolves the rule-write pair itself: exported
  `DD_API_KEY`/`DD_APP_KEY` when present, otherwise one-shot keys minted
  through Pup and deleted after the write — the values never transit the
  conversation, stdout, or the emitted JSON.
- One-shot writes: the rule POST, PUT, and DELETE are sent once and
  reconciled with reads; no automatic retries.
- Cluster names, IDs, tenant IDs, and org details are project-specific:
  never write them to persistent memory.
- To stop covering clusters, run
  `python3 <SKILL_DIR>/scripts/setup.py --remove-rule <RULE_ID>` with the
  user's approval. Datadog then removes the Operator, Agent, Cluster
  Agent, CRDs, cluster role, and admission webhooks, leaving only a small
  `datadog-operator-lock` Lease in `datadog-managed`. To only follow
  progress, use `--status <RULE_ID>` (read-only).
- For Pup failures, run `pup auth refresh`; if that fails, `pup auth
  login` with the scopes from the prerequisites.

## After the rule exists

Datadog rolls the Operator out to the rule's clusters on its own. The
skill's work is done at that point: it does not wait for rollout, push
configuration, or check that telemetry arrives. Point the user at Fleet
Automation (or `--status <RULE_ID>`) to follow progress, and suggest
concrete next steps in a sentence each, for example:

- Build a Kubernetes overview dashboard for the clusters' containers,
  pods, and nodes.
- Use Fleet Automation to upgrade the Agents across the fleet later,
  without touching the clusters.
