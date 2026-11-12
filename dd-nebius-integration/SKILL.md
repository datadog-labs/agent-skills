---
name: dd-nebius-integration
description: Connect a Nebius Cloud tenant to Datadog so its log streams — Kubernetes, PostgreSQL, Compute, applications, MLflow, and more — are collected through the Datadog Nebius Cloud integration. Creates the Nebius service account and keys the integration needs, and registers and verifies the integration. Use when the user wants to collect Nebius Cloud logs, connect a Nebius tenant to Datadog, or set up or repair the Datadog Nebius Cloud integration.
metadata:
  version: "1.1.0"
  author: datadog-labs
  repository: https://github.com/datadog-labs/agent-skills
  tags: datadog,nebius,integration,logs,cloud
  alwaysApply: "false"
  tools: pup,python3,nebius
---

# Datadog Nebius Cloud Integration

Use `scripts/setup.py` relative to this file. It owns discovery, deterministic
selection, access checks, credential handling, duplicate prevention,
one-shot registration, verification, and cleanup. Do not reproduce its Nebius
or Datadog reads or writes.

## Phase 0: Preflight

**Datadog for Government is not supported.** If `pup auth status` reports a
`ddog-gov.com` site, stop and tell the user to set up the integration from the
Datadog app instead. The script refuses Government sites too.

**Pup.** Installed and authenticated. The setup script performs every Datadog
request through `pup api`, which owns authentication selection, OAuth
refresh, site, and organization selection, and it validates Pup itself before
touching anything:

```bash
pup auth status
```

- `authenticated: true` → continue.
- `pup` is missing → ask before installing it:
  `brew tap datadog-labs/pack && brew install datadog-labs/pack/pup` (macOS or
  Linux with Homebrew),
  `curl -fsSL https://raw.githubusercontent.com/DataDog/pup/main/install.sh | sh`,
  or `npm install -g @datadog/pup` (Windows or anywhere with npm); see
  https://github.com/DataDog/pup. Then run `pup auth login`.
- `authenticated: false` → continue to the Datadog org step below, whose
  browser login establishes authentication. Do not ask the user to create or
  export Datadog API or application keys.

**Pup version.** The scripts use Pup v2 CLI flags (such as `--no-envelope`);
Pup 1.x fails with an opaque `pup_request_failed`. Check:

```bash
pup --version
```

A major version of 2 or later is required. If it is older, ask before
upgrading. When the environment allows it, upgrade with the official
installer or npm as above. Some workspaces block piping a remote script to a
shell (`curl | sh`), and `/usr/local/bin` may not be user-writable; there,
install from the release archive in a fresh empty directory instead (replace
`2.4.3` and the platform suffix with the latest release):

```bash
VERSION=2.4.3
curl -fsSLO "https://github.com/DataDog/pup/releases/download/v${VERSION}/pup_${VERSION}_Linux_x86_64.tar.gz" &&
curl -fsSLO "https://github.com/DataDog/pup/releases/download/v${VERSION}/pup_${VERSION}_checksums.txt" &&
grep "pup_${VERSION}_Linux_x86_64.tar.gz$" "pup_${VERSION}_checksums.txt" | sha256sum -c - &&
tar -xzf "pup_${VERSION}_Linux_x86_64.tar.gz" &&
sudo install -m 0755 ./pup /usr/local/bin/pup &&
pup auth login
```

**Datadog org.** Always establish the run's org with a fresh `pup auth login`
before setup — run it in the background, tell the user to complete the browser
login (this is where they select the org), and wait for it to finish. Do not
ask the user which org to target, and do not infer the org from the stored
session: `pup auth status`'s `org` (`null` for the default session) and the
login's "Hinting org UUID" line are stored defaults, not the org the fresh
token will belong to. If the user selected a named Pup organization session,
run setup with the `DD_ORG` environment variable set to that session's name.

**Python and OpenSSL.** The scripts need Python 3.9 or later. `openssl` is used
only to reconcile an ambiguous authorized-key creation; if it is missing, tell
the user that such a failure cannot be reconciled automatically.

```bash
python3 -c 'import sys; sys.exit(sys.version_info < (3, 9))' || echo "NEED python3 >= 3.9"
command -v openssl || echo "MISSING openssl"
```

**The Nebius CLI (`nebius`).** Installed and authenticated, with a profile
whose tenant and project the integration will use. Check
`command -v nebius`; if it is missing, follow the steps below.

### Installing and authenticating the Nebius CLI

Use this when `nebius` is not installed, or when the script reports
`nebius_cli_missing` (its `details` repeat these steps). Nebius documents the
CLI for macOS and Ubuntu; on other systems, point the user to
https://docs.nebius.com/cli/install or to Cloud Shell in the Nebius web console.

1. **Install.** Explain that the official installer downloads and runs a remote
   script, and ask before running it:

   ```bash
   curl -sSL https://artifacts.nebius.cloud/cli/install.sh | bash
   ```

2. **Reload and verify.** The installer updates the shell profile, so restart
   the terminal or run `exec -l $SHELL`, then confirm with `nebius version`.
   If `nebius` is still not found in the agent's shell, use the path the
   installer printed or ask the user to start a new session.
3. **Authenticate.** `nebius profile create` writes local configuration and
   opens a browser federation login. Run it on the user's behalf in this
   session, supplying the defaults directly as arguments (profile name,
   endpoint `api.nebius.cloud`, federation endpoint `auth.nebius.com`, and
   the tenant and project to integrate); the user only completes the browser
   federation login it opens. `nebius profile list` should then show the new
   profile as `[default]`.
4. Continue with the workflow below.

If profile authentication is missing or expired later, run `nebius profile
create` again on the user's behalf, as above.

### Confirm the logged-in Datadog org and Nebius tenant

Before gathering anything else, ask whether to set up the integration for the
accounts the user is currently logged in to. Read the Datadog org's name from
the same pup session and site this run uses with `pup api /api/v2/current_user`
(the `name` of the `orgs` entry under `included`; the site is the one `pup auth
status` reported). Read the Nebius tenant from the active profile's tenant (`nebius profile current`, then `nebius iam whoami`).

Then ask once with the host's native selector:

> Do you want to set up this integration for the Nebius tenant **<tenant>** and the Datadog org
> **<org name>** on **<site>**?

- **Yes** → continue.
- **No, a different Datadog org** → stop. Have the user sign pup into the right
  org (see the pup step above), then ask again. Never continue on an org the
  user did not confirm.
- **No, a different Nebius tenant** → have the user switch with `nebius profile activate <name>` (or create a profile), then ask again.

## Workflow

```
- [ ] 1. Preflight: pup >= 2, fresh `pup auth login` completed (org selected in the browser), python3 >= 3.9, nebius on PATH with a profile
- [ ] 2. Gather log streams and account tags (one form)
- [ ] 3. One approval question
- [ ] 4. Run setup.py; handle the result per "Results"
- [ ] 5. Start verify_logs.py in the background; tell the user
- [ ] 6. Relay succeeded / failed / error
```

1. **Preflight.** Complete [Phase 0: Preflight](#phase-0-preflight).
2. **Gather.** Use one interactive question form to gather any of these values
   not already supplied by the user:

   - **Log streams:** allow multiple selections from `nebius-cloud-kubernetes`,
     `nebius-cloud-postgresql`, `nebius-cloud-compute`,
     `nebius-cloud-applications`, `nebius-cloud-mlflow`, and `default`.
   - **Account tags (optional):** `key:value` tags to add to this account's
     data, such as `team:infra`; no spaces. Leave empty for none.

   Everything else is fixed and must not be asked about:

   - the service account is always named `datadog-integration` (reused when it
     already exists in the profile's project, created when it does not)
   - the Datadog display name is always derived from the tenant's
     human-readable Nebius name (for example `nebius-teal-condor-tenant-f2j`),
     falling back to the tenant ID when the tenant reports no name
   - resource metadata collection and service quota collection are always enabled
   - a suspended tenancy is never onboarded; the script detects suspension and
     refuses with `tenant_suspended` before any write

3. **Approve.** Use the interactive question tool once for approval:

   - **Title:** `Approve complete Nebius integration setup`
   - **Question:** ask this, with each step on its own line:

     > May I run the setup script? It will:
     >
     > - Use your active Nebius CLI profile's tenant and parent project.
     > - Reuse the uniquely named `datadog-integration` service account in that project, or create it there.
     > - Make sure it is a member of the tenant's admins group.
     > - Generate a fresh authorized (private) key and an OBSERVABILITY static key, both expiring in one year.
     > - Enable the selected log streams, plus resource and service quota collection.
     > - Register the Datadog integration account under a script-derived name, and verify it.
     > - Refuse a suspended tenancy.
     > - Delete the local secrets on success.
   - **Approve option:** `Approve and run the complete setup`
   - **Cancel option:** `Cancel without making changes`

4. **Run.** After permission is granted, run this with a generous command
   timeout, such as 10 minutes, because registration waits for Datadog's answer:

   ```bash
   python3 <SKILL_DIR>/scripts/setup.py \
     --streams "<COMMA_SEPARATED_STREAM_IDS>"
   ```

   Append `--account-tags <key:value>` once per account tag the user supplied.
   Pup uses its default session; if the user selected a named Pup organization
   session, run the command with the `DD_ORG` environment variable set to that
   session's name.

   Do not ask other setup questions or pass other configuration. Running the
   script is authorization for all setup writes. The script is non-interactive
   and never displays prompts or menus. Handle its output per
   [Results](#results).

5. **Verify.** Start the background log check and tell the user, as in
   [Getting the Most Out of Your Integration](#getting-the-most-out-of-your-integration).
6. **Relay** its `succeeded`, `failed`, or `error` result as described there.

## Results

The script prints one JSON object. Report every returned warning to the user.

- `complete` → continue with step 5.
- `already_configured` → the tenant's Datadog Nebius account already uses this
  service account, the derived display name, the requested streams, tags, and
  both collections enabled. Tell the user nothing changed,
  and still continue with step 5.
- `tenant_suspended` → the tenancy is suspended (state in
  `error.details.suspension_state`); no Nebius or Datadog writes were made.
  Do not onboard a suspended tenancy; tell the user to resume it in Nebius
  and rerun afterwards.
- `invalid_streams` → the selection was empty or named an unsupported stream;
  the supported streams are in `error.details.supported_streams`. Gather the
  streams again and rerun.
- `datadog_tenant_already_registered` or `datadog_account_mismatch` → no Nebius
  writes were made. Show the `name` and `enabled_services` of
  `error.details.existing_account`. The script never updates or replaces a
  Datadog Nebius account, so ask the user how to proceed (for example, by
  reviewing that account in Datadog's Nebius Cloud integration tile), and do
  not rerun the script unchanged.
- `duplicate_datadog_accounts` → several Datadog Nebius accounts use this
  tenant; show `error.details.account_ids` and ask the user to resolve them in
  Datadog's Nebius Cloud integration tile before rerunning.
- `duplicate_service_accounts` → several Nebius service accounts are named
  `datadog-integration`; show `error.details.service_account_ids` and ask the
  user to delete the extra accounts in Nebius before rerunning.
- `nebius_profile_identity_incomplete` → run `nebius profile create` again on
  the user's behalf (see
  [Phase 0](#installing-and-authenticating-the-nebius-cli)) so the profile
  selects the tenant and project.
- `pup_cli_missing` → follow [Pup](#phase-0-preflight).
- `pup_cli_outdated` → the installed Pup is older than v2 and cannot run this
  setup. Ask before upgrading it (see the Pup version preflight above), then
  rerun once the upgrade is done.
- `pup_not_authenticated`, `pup_authentication_failed`, or
  `pup_scopes_missing` → ask before running `pup auth login`, adding the
  `--extra-scopes` that `error.details.remediation` reports.
- Any other `pup_*` reason (a failed request, transport, or response) → relay
  `message`, and rerun once the user has fixed the cause.
- Any other error → report `reason` and `message`, and follow
  [Security and recovery](#security-and-recovery) before any rerun.

## Security and recovery

- Secret values never belong in command arguments, chat, logs, or source files.
  Never ask for or print credential values.
- Every successful setup creates fresh dedicated credentials.
- The Datadog registration POST is sent once; a failed or ambiguous result is
  reconciled by listing accounts, and the script never automatically POSTs
  again. Nebius write failures are never automatically repeated.
- Success, failure, Ctrl-C, and SIGTERM all delete the temporary secret files.
  A failure or interruption after a Nebius write may leave an active cloud
  credential whose secret is no longer available; report the error and do not
  rerun until the user inspects and revokes any orphaned credential identified
  by the error.
- Do not revoke credentials after successful registration because Datadog
  needs them for collection.

Stored configuration does not prove ingestion; continue with
[Getting the Most Out of Your Integration](#getting-the-most-out-of-your-integration).

## Getting the Most Out of Your Integration

After the setup script returns `complete` or `already_configured`, confirm that logs are arriving and then show
the user what they can do with them, without blocking the conversation:

1. Start a background subagent with this task, using the `account.name` value
   from the setup output (the script always derives the name from the tenant's
   human-readable Nebius name) and the same `DD_ORG` setting if setup used one:

   > Run `python3 <SKILL_DIR>/scripts/verify_logs.py --account-name "<ACCOUNT_NAME>"`
   > with a command timeout of at least 8 minutes. It polls Datadog for up to 7 minutes and prints JSON.
   > Do not retry it or change anything. Report back its `status`
   > (`succeeded`, `failed`, or `error`), `message`, `sample`, `links`, and any
   > `error` object.

2. Tell the user, in these words:

   > "Your Nebius integration is set up! I'm now checking in the background that
   > your logs are arriving in Datadog. This can take up to 7 minutes, and I'll
   > let you know as soon as they show up. Feel free to keep working in the
   > meantime."

3. When the subagent reports, relay the result to the user.

**`succeeded`: celebrate.** This is the payoff of the setup, so make it feel like
one. Lead with an upbeat headline such as "Your Nebius logs are now live in
Datadog!" and give `links.logs` as a clickable link that opens this account's
Nebius logs in the Log Explorer, mentioning the sample source so the user knows
what to look for. Then suggest two or three concrete next steps in a sentence
each, for example:

- Save the query as a view so the team can return to these logs in one click.
- Create a log monitor to get alerted when errors or unusual volumes appear.
- Generate metrics from the logs to track trends on a dashboard.

Keep it short and enthusiastic, and only describe the data the script actually
confirmed. If `links` is missing, tell the user to search for
`source:nebius-cloud*` in the Datadog Log Explorer instead.

**`failed`:** stay encouraging. No logs arrived within 7 minutes, which is not
necessarily a problem because logs can take longer to start. Suggest checking
`source:nebius-cloud*` in Datadog later before treating it as broken.

**`error`:** report the reason. For `datadog_read_not_permitted`, ask before
running the reported `pup auth login --extra-scopes logs_read_data`.

If no subagent tool is available, run the same command yourself and report the
result the same way.
