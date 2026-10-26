---
name: dd-nebius-integration
description: Set up the Datadog Nebius Cloud integration for log collection using a dedicated tenant-admin Nebius service account, an authorized public key, and an OBSERVABILITY static key. Use when the user wants to collect Nebius Cloud logs, connect a Nebius tenant to Datadog, or set up or repair the Datadog Nebius Cloud integration.
metadata:
  version: "1.0.0"
  author: datadog-labs
  repository: https://github.com/datadog-labs/agent-skills
  tags: datadog,nebius,integration,logs,cloud
  alwaysApply: "false"
---

# Datadog Nebius Cloud Integration

Use `scripts/setup.py` relative to this file. It owns discovery, deterministic
selection, access checks, credential handling, duplicate prevention,
one-shot registration, verification, and cleanup. Do not reproduce its Nebius
or Datadog reads or writes.

## Workflow

First use one interactive question form to gather any of these values not
already supplied by the user:

- **Log streams:** allow multiple selections from `nebius-cloud-kubernetes`,
  `nebius-cloud-postgresql`, `nebius-cloud-compute`, `nebius-cloud-applications`,
  `nebius-cloud-mlflow`, and `default`.
- **Service-account name:** offer `datadog-integration` as the recommended default and an Other
  response for a custom name.
- **Datadog integration display name:** offer the script-derived stable name as the recommended
  default and an Other response for a custom name.

Then use the interactive question tool once for approval:

- **Title:** `Approve complete Nebius integration setup`
- **Question:** `May I run the setup script with the selected log streams and names? It will use your active Nebius CLI
  profile's tenant and parent project; reuse the uniquely named service account in that project or
  create it there; ensure admins membership; generate a fresh
  authorized/private key and OBSERVABILITY static key with one-year expiration; register the
  selected Datadog integration account; verify it; and delete local secrets on success.`
- **Approve option:** `Approve and run the complete setup`
- **Cancel option:** `Cancel without making changes`

After permission is granted, run:

```bash
python3 <SKILL_DIR>/scripts/setup.py \
  --streams "<COMMA_SEPARATED_STREAM_IDS>" \
  --service-account-name "<SERVICE_ACCOUNT_NAME>"
```

If the user supplied a custom Datadog display name, append
`--account-name "<DATADOG_INTEGRATION_NAME>"` to that same command. If the user
selected a named Pup organization session, append `--org "<ORG>"`.

Do not ask other setup questions or pass other configuration. Running the script
is authorization for all setup writes. The script is non-interactive and never
displays prompts or menus.

Pup must be installed and authenticated. The script performs every Datadog
request through `pup api`, which owns authentication selection, OAuth refresh,
site, and organization selection. If validation reports missing authentication
or OAuth scopes, ask before running the reported `pup auth login` command. Do
not ask the user to create or export Datadog API or application keys.

The Nebius CLI must already be installed and authenticated. Never ask for or
print credential values.

## Deterministic behavior

The script reads the tenant and default parent project IDs from the active Nebius
profile without printing credential material. It lists service accounts in that
project, re-listing read-only when needed to reconcile a write, without
enumerating projects. It then:

- reuses exactly one service account with the selected name in that project and ensures admins membership
- creates that service account in the active profile's project and adds admins membership when none exists
- fails on duplicate same-name accounts
- uses the requested Datadog display name or derives one from tenant/project identity
- enables the requested supported streams, defaulting to all six including `default`
- sets fresh credential expiration to one year from execution

The script validates Pup authentication, checks OAuth scopes when Pup selected a
Bearer session, checks `/api/v2/current_user` through `pup api`, and lists
Datadog accounts before writes. Datadog enforces permissions during
registration. If the tenant
has no Datadog account, setup continues. If its one account uses the selected
service account, deterministic name, and requested streams, the script returns
`already_configured` without creating credentials. An account for the same
tenant using another service account, mismatched settings, or multiple accounts
for one tenant fails closed before Nebius writes. Report every returned warning
to the user.

## Security and recovery

- Secret values never belong in command arguments, chat, logs, or source files.
- Temporary storage is mode `0700`; credential and payload files are mode
  `0600`. The payload contains exactly `static_api_key` and `sa_private_key`.
- Every successful setup creates fresh dedicated credentials. Static-key names
  use a deterministic collision-free suffix.
- The Datadog registration POST is sent once through `pup api`, with the payload
  passed on Pup's stdin. A failed or ambiguous result is reconciled by listing
  accounts; the script never automatically POSTs again.
- Nebius write failures are reconciled with reads where safe and are never
  automatically repeated.
- Success and failure delete all temporary secret files. A failure after a
  Nebius write may leave an active cloud credential whose secret is no longer
  available; report the error and do not rerun until the user inspects and
  revokes any orphaned credential identified by the error.
- Do not revoke credentials after successful registration because Datadog
  needs them for collection.

Stored configuration does not prove ingestion. Logs may take several minutes
and can be checked with `source:nebius-cloud*`.

If the Nebius CLI is unavailable, ask before helping the user install it from
https://docs.nebius.com/cli/install. If profile authentication is missing or
expired, explain that `nebius profile create` writes local configuration and
opens browser federation login, then ask permission before running it.

For Pup authentication failures, run `pup auth refresh`; if that fails, run
`pup auth login` with the reported missing scopes.
