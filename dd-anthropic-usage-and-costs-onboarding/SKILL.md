---
name: dd-anthropic-usage-and-costs-onboarding
description: Connects Anthropic to Datadog so the user can see Claude usage and cost data — organization-wide usage on the Platform plan, per-user usage on the Enterprise plan, and costs in Cloud Cost Management when enabled. Guides creating the key the plan needs, entered in a separate secure prompt where the agent never sees it, and verifies that usage data is actually arriving. Use when the user wants Anthropic or Claude API usage and cost data in Datadog, or asks to connect, configure, or repair the Datadog Anthropic Usage and Costs integration. Not for tracing Anthropic SDK calls in an application; use dd-instrument-llmo for LLM Observability.
metadata:
  version: "1.0.0"
  author: datadog-labs
  repository: https://github.com/datadog-labs/agent-skills
  tags: datadog,anthropic,claude,integration,cost,ccm
  alwaysApply: "false"
  tools: pup,python3
---

# Datadog Anthropic Usage and Costs Integration

This skill connects the Anthropic usage and cost data feed. To trace LLM calls
made by an application, use `dd-instrument-llmo` instead.

Use `scripts/setup.py` relative to this file. It owns browser handoff, hidden
key entry in a separate terminal, the Anthropic key check, Pup OAuth
validation, duplicate prevention, one-shot registration, and secret redaction.
Do not reproduce its API reads or writes, except the read-only account
listing under [Results and recovery](#results-and-recovery).

The integration (`anthropic-usage-and-costs`) ingests token usage
(organization-wide on Platform, per user on Enterprise) as
`anthropic.messages.*` metrics and, when **cost data ingestion** is on,
Anthropic costs into Cloud Cost Management (CCM). Both are configured on one
Datadog account: the Anthropic key is its secret and `ccm_enabled` its setting,
so the script creates the account and turns on CCM in the same request.

## Phase 0: Preflight

**Pup.** Installed and authenticated. The setup script performs every Datadog
request through `pup api`, which owns authentication selection, OAuth
refresh, site, and organization selection, and it validates Pup itself before
touching anything:

```bash
pup auth status
```

- `authenticated: true` → continue.
- `authenticated: false` → the setup script reports `pup_not_authenticated`
  with the exact `pup auth login` command. Ask before running it, and do not
  ask the user to create or export Datadog API or application keys.
- `pup` is missing (`pup_cli_missing`) → ask before installing it:
  `brew tap datadog-labs/pack && brew install datadog-labs/pack/pup` (macOS or
  Linux with Homebrew),
  `curl -fsSL https://raw.githubusercontent.com/DataDog/pup/main/install.sh | sh`,
  or `npm install -g @datadog/pup` (Windows or anywhere with npm); see
  https://github.com/DataDog/pup. Then run `pup auth login`.

**Python.** The flow runs `scripts/setup.py`, so confirm `python3` is on
`PATH` before starting:

```bash
command -v python3 || echo "MISSING python3"
```

**Graphical terminal.** The key prompt opens in a new desktop terminal window
(macOS Terminal, Windows Terminal or `cmd.exe`, or a Linux terminal with
`DISPLAY` or `WAYLAND_DISPLAY` set). If the user is on SSH, in a container, or
has no desktop session, stop and point them to the Anthropic Usage and Costs
tile in the Datadog app instead of running the script.

**The Anthropic key, decided by the plan:**

- **Platform plan** → a key with **Admin API access**: an Admin key
  (`sk-ant-admin...`) or an organization-scoped key (`sk-ant-usr...`), created
  on the Claude Platform keys page (`https://platform.claude.com/settings/keys`).
  A workspace API key (`sk-ant-api...`) cannot read usage and cost data. This
  ingests organization-wide usage and cost by model, workspace, API key, and
  service tier.
- **Enterprise plan** → an **Analytics API key** with the `read:analytics`
  scope, created at `https://claude.ai/admin-settings/api-access`. Only the
  organization's **Primary Owner** can create one. This ingests per-user usage
  and cost by user, product (API, Claude Code, Claude.ai), model, and more.

Both: the key is shown only once, and cost data appears in Cloud Cost
Management only if the Datadog org uses CCM.

## Workflow

```markdown
- [ ] Plan asked (platform | enterprise)
- [ ] Display name and cost choice collected
- [ ] Key explained, approval given
- [ ] setup.py run with --plan, DD_SITE/DD_ORG, ≥10 min timeout
- [ ] Status handled
- [ ] Verification started (Platform) or hourly-delay message given (Enterprise)
```

1. **Ask the plan** with the host's native selector:

   > Is your Anthropic organization on the **Platform** plan (Claude API / Console)
   > or the **Enterprise** plan (Claude for Enterprise)?

   Pass the answer to every script command as `--plan platform` or
   `--plan enterprise`. Never guess the plan.

2. **Gather the values** not already supplied, in one interactive question form:

   - **Datadog integration display name:** recommend a name that identifies the
     Anthropic organization, such as `anthropic-production`.
   - **Cost data:** whether to ingest Anthropic costs into Cloud Cost Management
     (on by default).

3. **Explain the key** the user will create, with the matching prerequisite in
   [Phase 0: Preflight](#phase-0-preflight):

   - **Platform:** create a **key with Admin API access** on the Claude Platform
     keys page (the browser will open on it).
   - **Enterprise:** create an **Analytics API key** with the `read:analytics`
     scope at `https://claude.ai/admin-settings/api-access` (the browser will open
     on it); it must be created by the organization's Primary Owner.

     > **Note:** Anthropic's docs place Analytics API keys at
     > `https://claude.ai/admin-settings/api-access`, but this has not been
     > confirmed against a live Enterprise org. If the user cannot find the option
     > there, send them to the earlier location,
     > `https://claude.ai/analytics/api-keys`; the key prompt in the terminal
     > names both.

   Either way they paste it into a **separate terminal window** that the script
   opens.

4. **Get approval** with the interactive question tool, once:

   - **Title:** `Approve Anthropic integration setup`
   - **Question:** `May I run the interactive setup? It will open the Anthropic key page for your plan, ask you to paste the key into a hidden prompt in a new terminal, check the key with Anthropic, and register the named Datadog integration account with cost data ingestion <on|off>. The key is never visible to the agent, and will not be printed, passed as a command argument, stored in a file, or returned in output.`
   - **Approve option:** `Approve and open the secure key prompt`
   - **Cancel option:** `Cancel without making changes`

5. **Run setup:**

   ```bash
   python3 <SKILL_DIR>/scripts/setup.py --account-name "<DATADOG_INTEGRATION_NAME>" --plan <platform|enterprise>
   ```

   - On Windows, use `python` instead of `python3`. If the user declined cost
     data, append `--disable-ccm`.
   - Pup uses its default session, and picks the Datadog site from `DD_SITE`
     (default `datadoghq.com`): if the user's Pup session is on another site, run
     the command with `DD_SITE` set to it (e.g. `datadoghq.eu`); if they selected
     a named Pup organization session, also set the `DD_ORG` environment
     variable to that session's name. Set them the way the shell expects: a
     `NAME=value` prefix on POSIX shells, `$env:NAME="value";` in PowerShell, or
     `set "NAME=value" &&` in cmd.exe. Use the same settings for every later
     script command.
   - Do not detach the process or redirect its input. The script opens
     `https://platform.claude.com/settings/keys?ws=default` (Platform) or
     `https://claude.ai/admin-settings/api-access` (Enterprise), then waits for
     the user to create the key and paste it. Input is hidden like a password.
     Tell the user to switch to the new terminal when it appears.
   - Run the command with the longest command timeout your tool allows (at
     least 10 minutes). The user may take up to 5 minutes to enter the key, and
     Datadog requests have no client-side timeout. Killing the command during
     registration leaves the outcome unknown.
   - The key goes only through the new terminal, which hands it back over a
     one-shot protected socket. Never ask the user to paste the key into chat,
     and never pass it through a command argument, environment variable, shell
     pipe, redirected input, or file.
   - Setup checks Pup and lists existing accounts first, so a rerun with the
     same name returns `already_configured` without asking for a key.

6. **Handle the status** under [Results and recovery](#results-and-recovery).

7. **Verify** under
   [Getting the Most Out of Your Integration](#getting-the-most-out-of-your-integration).

## Results and recovery

Report the script's status and warnings without exposing credentials.

- **`complete`** → continue with
  [Getting the Most Out of Your Integration](#getting-the-most-out-of-your-integration),
  passing the `anthropic_organization.id` and `usage_baseline` it returned.
- **`already_configured`** → the account exists. If the user wanted cost data and
  `account.ccm_enabled` is `false`, ask whether to turn it on; if yes, run:

  ```bash
  python3 <SKILL_DIR>/scripts/setup.py --account-name "<DATADOG_INTEGRATION_NAME>" --turn-on-ccm
  ```

  with the same `DD_SITE` and `DD_ORG` as setup. It needs no key: it updates
  only `ccm_enabled` on that account and confirms the change (`ccm_enabled`,
  or `ccm_already_enabled`).
- **Repair (new key for an existing account)** → setup does not replace the
  key on an existing account. Send the user to the Anthropic Usage and Costs
  tile in the Datadog app to replace it in the configuration panel.
- **`invalid_anthropic_key`** or **`anthropic_key_rejected`** → a key Anthropic
  rejects never reaches Datadog, so nothing was written. Ask the user to create
  the right key for their plan (see [Phase 0: Preflight](#phase-0-preflight))
  and rerun — and if it keeps happening, confirm the plan: a Platform key is
  rejected on the Enterprise check and vice versa.
- **`datadog_create_failed`** on the **Enterprise** plan with a message about an
  admin key → Datadog did not accept the Analytics key for this account.
  Report the message plainly; the user can set it up from the Anthropic Usage
  and Costs tile in the Datadog app instead.
- **`anthropic_check_failed`** → Anthropic could not be reached to confirm the
  key; nothing was written. Rerun once the network allows `api.anthropic.com`.
- **`ambiguous_datadog_post`** or **`datadog_account_not_visible`** → the
  account may exist but not be listed yet, so a rerun could create a duplicate.
  After a minute, list the accounts read-only with
  `pup api v2/web-integrations/anthropic-usage-and-costs/accounts` (same
  `DD_SITE`/`DD_ORG`). If the name is listed, the account was created; a rerun
  with the same name confirms it as `already_configured`. If not, do not rerun;
  send the user to the tile to check.
- **`duplicate_datadog_accounts`** → report the account ids from
  `details.account_ids`, and ask the user which to keep. Setup cannot choose.
- **`credential_entry_timeout`** → nothing was written. Rerun when the user is
  ready to paste the key within 5 minutes.
- **`terminal_launch_failed`**, **`terminal_launch_unsupported`** or
  **`terminal_helper_not_connected`** → nothing was written. Ask the user to
  rerun from a desktop session, or to use the integration tile.
- Pup authentication failures → run `pup auth refresh`; if that fails, run
  `pup auth login` with the reported missing scopes.
- **Any other status** → relay the script's message and reason code to the
  user, and rerun once the cause is fixed. If it says the write may have
  landed, check the account list first, as for `ambiguous_datadog_post`.

## Getting the Most Out of Your Integration

After the setup script returns `complete`, confirm that data is arriving and
then show the user what they can do with it, without blocking the conversation.

**Enterprise plan: do not run the background check.** Anthropic reports
Enterprise usage in whole hours and Datadog only collects an hour once Anthropic
has finalized it, so the first metrics typically appear 1–3 hours after setup —
long after any 15-minute check would give up. Instead, tell the user, in these
words:

> "🎉 Your Anthropic integration is set up! Enterprise usage arrives in hourly
> batches, so your first metrics will show up in Datadog in about 1–3 hours. Look
> for `anthropic.*` metrics — each point covers one hour of
> usage and is stamped at the end of that hour."

Embed setup's `links.metrics` (the Metrics Summary page filtered to
`anthropic`, on the user's own Datadog site) as the link on `anthropic.*`; if
setup returned no `links`, write `anthropic.*` without a link.

Then give two or three one-sentence next steps for once the data is in (for
example: chart usage by `user_email` and `product`; set a monitor on
`anthropic.messages.output_tokens`; and, when cost ingestion is on, that costs
will appear in Cloud Cost Management within about 24 hours), and stop.

**Platform plan:**

1. Start a background subagent with this task, using `python` instead of
   `python3` on Windows. The subagent runs in a fresh shell, so give it the
   **same `DD_SITE` and `DD_ORG` settings setup used** and include them in its
   command; otherwise it falls back to the default site and could check the
   wrong one:

   > Run `python3 <SKILL_DIR>/scripts/verify_data.py --account-name "<DATADOG_INTEGRATION_NAME>" --anthropic-org-id "<ANTHROPIC_ORG_ID>" --baseline-since <usage_baseline.since> --baseline-total <usage_baseline.total>`
   > (drop both `--baseline-*` flags if setup's `usage_baseline` was `null`)
   > with the same `DD_SITE` and `DD_ORG` setup used (omit any it did not set),
   > passed through the execution tool's environment settings or set in the same
   > shell invocation: POSIX `DD_SITE=<site> DD_ORG=<session> python3 ...`;
   > PowerShell `$env:DD_SITE="<site>"; $env:DD_ORG="<session>"; python ...`;
   > cmd.exe `set "DD_SITE=<site>" && set "DD_ORG=<session>" && python ...`.
   > Run it with a command timeout of at least 16 minutes. It polls Datadog for up to
   > 15 minutes and prints JSON. Do not retry it or change anything. Report back
   > its `status` (`succeeded`, `failed`, or `error`), `checks`, `links`,
   > `ccm_enabled`, `already_reporting_at_install`, `message`, and any `error`
   > object.

2. Tell the user, in these words:

   > "Your Anthropic integration is set up! I'm now checking in the background
   > that your usage metrics are arriving in Datadog. This can take up to 15
   > minutes, and I'll let you know as soon as they show up. Feel free to keep
   > working in the meantime."

3. When the subagent reports, relay the result to the user.

**`succeeded`: celebrate.** Give a short, enthusiastic headline that starts with
🎉, such as "🎉 Your Anthropic usage metrics are now live in Datadog!", the `links.metrics` link
(`anthropic.messages.input_tokens` for this Anthropic organization in the
Metrics Explorer; usage points are stamped with when the usage happened, so tell
the user to widen the time range to the last few hours if the graph looks
empty), and two or three one-sentence next steps, for example: chart
token usage by `model` and `workspace_name`; set a monitor on
`anthropic.messages.output_tokens` to catch usage spikes; and, when
`ccm_enabled` is `true`, that Anthropic costs will appear in Cloud Cost
Management (`links.costs`) within about 24 hours (cost data is not part of the
check). If
`already_reporting_at_install` is `true`, add one sentence that this Anthropic
organization already had usage data in Datadog before setup (an earlier setup or
another account), so the new data could also come from there. Only describe what
the script confirmed.

**`failed`:** stay encouraging and relay the script's `message`. No new usage
data arrived during the wait: Platform usage reaches Datadog with a delay (often
~30 minutes) and only API usage produces data. If
`already_reporting_at_install` is `true`, mention that data for this
organization was already in Datadog before setup and did not count. Suggest
checking `anthropic.messages.*` metrics later before treating it as broken.

**`error`:** report the reason. For `datadog_read_not_permitted`, ask before
running the reported `pup auth login --extra-scopes ...` command.

If no subagent tool is available, run the same command yourself and report the
result the same way.
