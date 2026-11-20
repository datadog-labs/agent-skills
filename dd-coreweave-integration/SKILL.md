---
name: dd-coreweave-integration
description: Connect a CoreWeave namespace to Datadog so its metrics and logs are collected through the Datadog CoreWeave Cloud integration. Guides creating the CoreWeave access token the integration needs — the token is entered in a separate secure prompt and never shown to the agent — and registers the integration and checks that its data arrives. Use when the user wants to connect CoreWeave to Datadog, collect CoreWeave metrics or logs, or configure the Datadog CoreWeave integration. Not for changing an existing CoreWeave account.
metadata:
  version: "1.0.0"
  author: datadog-labs
  repository: https://github.com/datadog-labs/agent-skills
  tags: datadog,coreweave,integration,metrics,logs,cloud
  alwaysApply: "false"
---

# Datadog CoreWeave Cloud Integration

Use `scripts/setup.py` relative to this file. It owns browser handoff, hidden
credential entry, Pup OAuth validation, duplicate prevention, one-shot
registration, the registration check (that the new account is listed), and
secret redaction. Do not reproduce its API
reads or writes.

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

- `authenticated: true` → the organization in the response is where the
  account will be registered. If it may not be the intended one, run
  `pup auth login` and have the user select the organization in the browser
  flow; do not ask them to type an organization name or an org UUID.
- `pup` is missing → ask before installing it:
  `brew tap datadog-labs/pack && brew install datadog-labs/pack/pup` (macOS or
  Linux with Homebrew),
  `curl -fsSL https://raw.githubusercontent.com/DataDog/pup/main/install.sh | sh`,
  or `npm install -g @datadog/pup` (Windows or anywhere with npm); see
  https://github.com/DataDog/pup. Then run `pup auth login`.
- `authenticated: false` → ask before running `pup auth login`, and do not ask
  the user to create or export Datadog API or application keys.

**Pup version.** The setup script uses Pup v2 CLI flags (such as
`--no-envelope`); Pup 1.x fails with an opaque `pup_request_failed`. Check:

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

**Python.** The flow runs `scripts/setup.py`, so confirm Python 3 is on `PATH`
before starting:

```bash
command -v python3 || echo "MISSING python3"
```

On Windows, in PowerShell (no output means Python is missing):

```powershell
Get-Command python -ErrorAction SilentlyContinue
```

If Python 3 is not found, ask the user to install it and stop.

**CoreWeave access token.** The token owner needs the **Observability
Viewer** role; billing metrics additionally require **Billing Viewer**.
Datadog recommends one access token per CoreWeave namespace, and the token
secret is displayed only once by CoreWeave.

### Confirm the logged-in Datadog org

Before gathering anything else, ask whether to set up the integration for the
Datadog org the user is currently logged in to. Read the org's name from the
same pup session and site this run uses with `pup api /api/v2/current_user`
(the `name` of the `orgs` entry under `included`; the site is the one `pup auth
status` reported). Then ask once with the host's native selector:

> Do you want to set up this integration for the Datadog org **<org name>** on
> **<site>**?

- **Yes** → continue.
- **No** → stop. Have the user sign pup into the right org (see the pup step
  above), then ask again. Never continue on an org the user did not confirm.

## Workflow

1. **Gather values.** Use one interactive question form for values not
   already supplied:
   - **CoreWeave organization name:** ask which CoreWeave organization the
     token is for, as named in the CoreWeave console (for example
     `Datadog POC`), and use it verbatim as the Datadog integration display
     name. CoreWeave has no organization-name API, so the name must come from
     the user; do not ask them to invent a Datadog account name separately.
     Only pass a different `--account-name` when the user explicitly asks
     for one.
   - **Account tags:** offer suggested tags to pick directly in the same
     question — for example `env:prod`, `env:staging`, or a `team:<name>`
     tag — plus no tags. Do not first ask whether to suggest tags and then
     ask for them in another question.
   - **Data to collect:** metrics and logs together (recommended), metrics
     only, or logs only. At least one must be selected.

2. **Explain the prerequisites** in [Phase 0: Preflight](#phase-0-preflight).

3. **Ask for approval** once with the interactive question tool:
   - **Title:** `Approve CoreWeave integration setup`
   - **Question:** ask this, with each step on its own line:

     > May I run the interactive setup? It will:
     >
     > - Open the CoreWeave token page.
     > - Ask you to paste the token into a hidden terminal prompt.
     > - Register the named Datadog integration account, collecting <metrics and logs|metrics|logs>.
     > - Verify the account.
     >
     > The token is never visible to the agent, and will not be printed, passed as a command argument, stored in a file, or returned in output.
   - **Approve option:** `Approve and open the secure token prompt`
   - **Cancel option:** `Cancel without making changes`

   Never ask the user to paste a CoreWeave token into chat, and never pass it
   through a command argument, environment variable, shell pipe, redirected
   input, or file; the script keeps it in memory and redacts it from output.
   If the user pastes the token into chat anyway, do not use it: tell them to
   revoke it, create a new one, and rerun.

4. **Run the setup script** after approval. Before starting, make sure the
   user has a CoreWeave **Token Secret** ready (or can create one quickly):
   the hidden prompt accepts the token for only 5 minutes after it opens.

   ```bash
   python3 <SKILL_DIR>/scripts/setup.py \
     --account-name "<COREWEAVE_ORGANIZATION_NAME>"
   ```

   On Windows, use one PowerShell line without Bash `\` continuations:

   ```powershell
   python <SKILL_DIR>/scripts/setup.py --account-name "<COREWEAVE_ORGANIZATION_NAME>"
   ```

   If tags were selected, append one `--tag "<KEY:VALUE>"` argument per tag.
   Metrics and logs are both on by default. If the user declined logs, append
   `--disable-logs`; if they declined metrics, append `--disable-metrics`. Pup uses its default session; if the
   user selected a named Pup organization session, run the command with the
   `DD_ORG` environment variable set to that session's name. Do not detach the
   process or redirect its input.

   Use the longest command timeout your tool allows (at least 10 minutes). The
   user may take up to 5 minutes to enter the token, and Datadog requests have
   no client-side timeout. Killing the command during registration leaves the
   outcome unknown.

5. **Point the user to the new terminal.** The script opens
   `https://console.coreweave.com/tokens` and a new graphical terminal, a new
   tmux window when the agent session runs inside tmux, or a Windows console
   for hidden token entry, even when the agent has a TTY, because that TTY may
   not be visible to the user. Tell the user to create a **Token Secret** and
   paste it into that terminal when it appears.

6. **Check `status`** in the script's JSON output:
   - `complete` or `already_configured` → step 7.
   - `error` → act on it as in [Results and recovery](#results-and-recovery)
     and stop.

7. **Run the data check in the background** as in
   [Getting the Most Out of Your Integration](#getting-the-most-out-of-your-integration).
   Passing the registration check does not prove data is arriving.

## Results and recovery

Report the script's status, warnings and error reason without exposing
credentials.

| Result | Action |
| --- | --- |
| `complete` | Account registered; go to step 7. |
| `complete` with warning `post_reported_error_but_account_exists` | Tell the user the account exists even though the registration request reported an error; go to step 7. |
| `already_configured` | An account with this display name already exists, and no token was requested. Compare its `metrics_config_enabled` and `logs_config_enabled` with what the user asked for and tell them about any difference; go to step 7. |
| `terminal_launch_failed`, `terminal_launch_unsupported`, `terminal_helper_not_connected` | The token terminal could not be opened or did not connect within 60 seconds; the script never prompts in place. Point the user to the CoreWeave integration tile (`/integrations/coreweave` on their Datadog app host) to enter the token there. |
| `invalid_coreweave_token`, `datadog_create_failed` | The token was rejected or Datadog refused the account. Check the token's `CW-SECRET-...` format, expiration, namespace access, and the owner's Observability Viewer role. Create a replacement token in the CoreWeave console and rerun only after confirming that Datadog did not create the account. |
| `datadog_account_not_visible` | Datadog accepted the account, but the registration check could not see it. Inspect the Datadog CoreWeave account list (read-only) before rerunning, or a rerun can create a duplicate. |
| `ambiguous_datadog_post` | The write may still land, so do not rerun. Inspect the Datadog CoreWeave account list (read-only): if the name is listed, the account was created. If not, the write may still become visible later; send the user to the CoreWeave integration tile to check the account. |
| `duplicate_datadog_accounts` | Several accounts share this display name, so it no longer identifies one. Ask the user to pick a unique name. |
| `pup_not_authenticated`, `pup_authentication_failed`, `pup_scopes_missing` | Run `pup auth refresh`; if that fails, ask before running `pup auth login` with the reported missing scopes. |
| `pup_cli_outdated` | The installed Pup is older than v2 and cannot run this setup; ask before upgrading it (see the Pup version preflight above), then rerun once the upgrade is done. |
| Any other status | Relay the script's message and reason code, and rerun once the cause is fixed. |

This skill does not change an existing account. If the user wants to update
one (a new token, or metrics or logs toggled), point them to the Datadog
CoreWeave integration tile (`/integrations/coreweave` on their Datadog app
host). Do not delete or recreate the account.

## Getting the Most Out of Your Integration

After the setup script returns `complete` or `already_configured`, confirm that
data is arriving and then show the user what they can do with it, without
blocking the conversation:

1. Start a background subagent with this task,
   using `python` instead of `python3` on Windows and the same `DD_ORG` setting
   if setup used one:

   > Run `python3 <SKILL_DIR>/scripts/verify_data.py --account-name "<DATADOG_INTEGRATION_NAME>"`
   > with a command timeout of at least 8 minutes. It polls Datadog for up to
   > 7 minutes and prints JSON. Do not retry it or change anything. Report back
   > its `status` (`succeeded`, `partial`, `failed`, or `error`), `arrived`,
   > `pending`, `checks`, `links`, `message`, and any `error` object.

2. Tell the user, in these words:

   > "Your CoreWeave integration is set up! I'm now checking in the background
   > that your data is arriving in Datadog. This can take up to 7 minutes, and
   > I'll let you know as soon as it shows up. Feel free to keep working in the
   > meantime."

3. When the subagent reports, relay the result to the user.

**`succeeded`: celebrate.** This is the payoff of the setup, so make it feel like
one. Lead with an upbeat headline such as "Your CoreWeave metrics are now live
in Datadog!", naming each data type that arrived, and give the matching
`links` entries as clickable links so the user can see their data right away:
`links.metrics` opens a sample `coreweave.*` metric for this account in the
Metrics Explorer, and `links.logs` opens this account's CoreWeave logs. Then
suggest two or three concrete next steps in a sentence each, for example:

- Build a dashboard from the `coreweave.*` metrics to watch GPU workloads.
- Create a monitor so the team is alerted when a metric crosses a threshold or
  error logs spike.
- If logs are off (the account's `logs_config_enabled` is `false`), mention that
  turning on log collection in the Datadog CoreWeave integration settings adds
  logs alongside these metrics.

Keep it short and enthusiastic, and only describe the data the script actually
confirmed. If `links` is missing, tell the user to search for `coreweave.*`
metrics or `source:coreweave` logs in Datadog instead.

**`partial`: celebrate what arrived.** Some enabled data types arrived and others
(`pending`) did not within 7 minutes. Celebrate the `arrived` types exactly as
for `succeeded`, with their `links`, then add one sentence naming the pending
types, noting that they can take longer to start and suggesting the user check
back later, for example "Your CoreWeave logs haven't shown up yet; they can
take a little longer, so check `source:coreweave` in Datadog in a bit."

**`failed`:** stay encouraging. No data arrived within 7 minutes, which is not
necessarily a problem because collection can take longer to start. Suggest
checking `coreweave.*` metrics or `source:coreweave` logs in Datadog later
before treating it as broken.

**`error`:** report the reason. For `datadog_read_not_permitted`, ask before
running the reported `pup auth login --extra-scopes ...` command.

If no subagent tool is available, run the same command yourself and report the
result the same way.

A match comes from this account only, not from other CoreWeave accounts in
the same org.
