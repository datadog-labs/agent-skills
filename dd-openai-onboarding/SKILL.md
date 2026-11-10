---
name: dd-openai-onboarding
description: Connects OpenAI to Datadog so the user can see organization-wide OpenAI API usage metrics and OpenAI costs in Cloud Cost Management (on by default). Guides creating the OpenAI admin key the integration needs (organization owners only), entered in a separate secure prompt where the agent never sees it, and verifies that usage data is actually arriving. Use when the user wants OpenAI API usage and cost data in Datadog, or asks to connect, configure, or repair the Datadog OpenAI integration. Not for tracing OpenAI calls from application code, which LLM Observability covers.
metadata:
  version: "1.0.0"
  author: datadog-labs
  repository: https://github.com/datadog-labs/agent-skills
  tags: datadog,openai,integration,cost,ccm
  alwaysApply: "false"
  tools: pup,python3
---

# Datadog OpenAI Integration

Use `scripts/setup.py` relative to this file. It owns browser handoff, hidden
key entry in a separate terminal, the OpenAI key check, Pup OAuth validation,
duplicate prevention, one-shot registration, and secret redaction. Do not
reproduce its API reads or writes.

The integration (`openai`) ingests organization-wide usage as `openai.*`
metrics and, when **cost data ingestion** is on, OpenAI costs into Cloud Cost
Management (CCM). Both are configured on one Datadog account: the OpenAI key is
its secret, and `ccm_enabled` and `llm_observability_enabled` are its settings,
so the script creates the account and turns on CCM in the same request.

Out of scope: tracing an application's own OpenAI calls. That is LLM
Observability instrumentation (the `dd-instrument-llmo` skill), not this
integration.

## Phase 0: Preflight

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
- `authenticated: false` → the setup script reports `pup_not_authenticated`
  with the exact `pup auth login` command. Ask before running it, and do not
  ask the user to create or export Datadog API or application keys.

**Python.** The flow runs `scripts/setup.py`, so confirm `python3` is on
`PATH` before starting:

```bash
command -v python3 || echo "MISSING python3"
```

**Desktop session.** Key entry needs a local graphical terminal: Terminal.app
on macOS; gnome-terminal, konsole, xterm or x-terminal-emulator with `DISPLAY`
or `WAYLAND_DISPLAY` set on Linux; a console on Windows or WSL. If none is
available, such as over SSH or in a container, stop before approval and ask the
user to run this from a machine with a desktop session.

**OpenAI admin key.** Only organization owners can create one, and the key is
shown only once by OpenAI. A **project key** (`sk-proj-...`) connects but
collects no usage or cost data, so the script refuses it. If the user is not an
OpenAI organization owner, stop before approval: ask them to have an owner
create the admin key and paste it into the prompt, or run the setup themselves.

**Cost data.** Appears in Cloud Cost Management only if the Datadog org uses
CCM.

## Workflow

```markdown
- [ ] Values gathered (name, cost data, LLM Observability, custom tags)
- [ ] User told about the admin key page and the separate terminal
- [ ] Approval received
- [ ] setup.py ran; status recorded
- [ ] Result handled per Results and recovery
- [ ] Background verification started and reported
```

1. **Gather values.** Use one interactive question form for values not already
   supplied:

   - **Datadog integration display name:** recommend a name that identifies
     the OpenAI organization, such as `openai-production`.
   - **Cost data:** whether to ingest OpenAI costs into Cloud Cost Management
     (on by default).
   - **LLM Observability:** whether LLM Observability may also use this key to
     run evaluations (off by default; those calls are billed by OpenAI).
   - **Custom tags (optional):** `key:value` tags to add to this account's
     OpenAI metrics and logs, such as `team:ml`; no spaces or commas. Leave
     empty for none.

2. **Explain.** Before opening anything, tell the user what they will need to
   do: create an **OpenAI admin key** (the browser opens on
   `https://platform.openai.com/settings/organization/admin-keys`) and paste it
   into a **separate terminal window** that the script opens. Explain the
   prerequisites in [Phase 0: Preflight](#phase-0-preflight).

3. **Ask for approval.** Use the interactive question tool once:

   - **Title:** `Approve OpenAI integration setup`
   - **Question:** `May I run the interactive setup? It will open the OpenAI admin keys page, ask you to paste an OpenAI admin key into a hidden prompt in a new terminal, check the key with OpenAI, and register the named Datadog integration account with cost data ingestion on. The key is never visible to the agent, and will not be printed, passed as a command argument, stored in a file, or returned in output.`
   - **Approve option:** `Approve and open the secure key prompt`
   - **Cancel option:** `Cancel without making changes`

4. **Run setup.** After approval, run:

   ```bash
   python3 <SKILL_DIR>/scripts/setup.py --account-name "<DATADOG_INTEGRATION_NAME>"
   ```

   On Windows, use `python` instead of `python3`. If the user declined cost
   data, append `--disable-ccm`; if they opted into LLM Observability, append
   `--enable-llm-observability`; append `--custom-tags <key:value>` once per
   custom tag. Pup uses its default session, and picks the
   Datadog site from `DD_SITE` (default `datadoghq.com`): if the user's Pup
   session is on another site, run the command with `DD_SITE` set to it (e.g.
   `datadoghq.eu`); if they selected a named Pup organization session, also set
   the `DD_ORG` environment variable to that session's name. Set them the way
   the shell expects: a `NAME=value` prefix on POSIX shells, `$env:NAME="value";`
   in PowerShell, or `set "NAME=value" &&` in cmd.exe. Do not detach the
   process or redirect its input.

   Run the command with the longest command timeout your tool allows (at least
   10 minutes). The user may take up to 5 minutes to enter the key, and Datadog
   requests have no client-side timeout. Killing the command during
   registration leaves the outcome unknown.

5. **Hand off to the terminal.** Tell the user to switch to the new terminal
   when it appears. The script always opens a new graphical terminal for key
   entry and fails instead of prompting in place. Never ask the user to paste
   the key into chat, and never pass it through a command argument,
   environment variable, shell pipe, redirected input, or file.

6. **Handle the result** per [Results and recovery](#results-and-recovery). If
   `status` is not `complete` or `already_configured`, do not start
   verification.

7. **Verify in the background** per
   [Getting the Most Out of Your Integration](#getting-the-most-out-of-your-integration).

Before asking for a key, the script lists existing OpenAI accounts. One account
with the same display name returns `already_configured` without requesting a
key, and reports its `ccm_enabled` and `llm_observability_enabled` plus any
that differ from the request in `settings_differ`. Multiple
accounts with the same name fail closed. The script checks the key with OpenAI
before anything reaches Datadog, never POSTs twice, and keeps the key in memory
only.

## Results and recovery

Report the script's status and warnings without exposing credentials. On
failure, the script prints `status: error` and an `error.reason`.

- **`complete`** → continue with
  [Getting the Most Out of Your Integration](#getting-the-most-out-of-your-integration).
- **`already_configured`** → the account exists and was left unchanged. If
  `settings_differ` is present, tell the user which settings differ from what
  they asked for (`current` vs `requested`). If they wanted cost data and
  `account.ccm_enabled` is `false`, ask whether to turn it on with
  `--turn-on-ccm`; if yes, run:

  ```bash
  python3 <SKILL_DIR>/scripts/setup.py --account-name "<DATADOG_INTEGRATION_NAME>" --turn-on-ccm
  ```

  It needs no key: it changes only `ccm_enabled` on that account, sending back
  every other setting (LLM Observability, custom tags) as it was, and confirms
  the change (`ccm_enabled`, or `ccm_already_enabled`). If the user is
  repairing an account whose key was revoked or replaced, the script does not
  ask for a new key: send them to the **Configuration** tab of the OpenAI
  integration tile in Datadog to update the key on that account.
- **`admin_key_required`** → the user pasted a project key; nothing was written.
  Ask them to create an admin key at
  `https://platform.openai.com/settings/organization/admin-keys` (organization
  owners only) and rerun.
- **`invalid_openai_key`** or **`openai_key_rejected`** → nothing was written to
  Datadog. Ask the user to create a fresh admin key and rerun.
- **`openai_check_failed`** → OpenAI could not be reached, or answered with a
  temporary error, so the key was neither accepted nor rejected; nothing was
  written. Rerun shortly (and make sure the network allows `api.openai.com`).
- **`credential_entry_timeout`**, **`terminal_helper_not_connected`** or
  **`credential_entry_cancelled`** → nothing was written. Ask whether the user
  is ready, then rerun the same command.
- **`terminal_launch_failed`** or **`interactive_tty_required`** → nothing was
  written. Follow the **Desktop session** item in Phase 0.
- **`datadog_create_failed`** → Datadog rejected the request and nothing was
  written. Report `details.pup_error.http_status`, and fix the cause before
  rerunning.
- **`duplicate_datadog_accounts`** → several accounts share the name. Ask the
  user which one to keep, and do not create another.
- **`ambiguous_datadog_post`** → do not rerun. Inspect the list with
  `pup api --no-envelope --output json --method GET /api/v2/web-integrations/openai/accounts`,
  which is read-only and the one exception to the rule at the top. If the name
  is listed, the account was created. If not, do not rerun: the original write
  may still land or become visible later. Send the user to the OpenAI
  integration tile in Datadog to check the account.
- **`datadog_account_not_visible`** → the POST succeeded, but the account is not
  listed yet. Wait a minute and inspect the list as above. Do not rerun.
- Pup authentication failures → run `pup auth refresh`; if that fails, run
  `pup auth login` with the reported missing scopes.
- Any other reason → report `error.message` and fix the cause before rerunning.
  If the key had already been entered, inspect the account list as above first.

## Getting the Most Out of Your Integration

After the setup script returns `complete`, confirm that data is arriving and
then show the user what they can do with it, without blocking the conversation:

1. Start a background subagent with this task, using `python` instead of
   `python3` on Windows. The subagent runs in a fresh shell, so give it the
   **same `DD_SITE` and `DD_ORG` settings setup used**; otherwise it falls back
   to the default site and could check the wrong one:

   > Run `python3 <SKILL_DIR>/scripts/verify_data.py --account-name "<DATADOG_INTEGRATION_NAME>"`
   > with the same `DD_SITE` and `DD_ORG` setup used (omit any it did not set),
   > passed through the execution tool's environment settings or set in the same
   > shell invocation: POSIX `DD_SITE=<site> DD_ORG=<session> python3 ...`;
   > PowerShell `$env:DD_SITE="<site>"; $env:DD_ORG="<session>"; python ...`;
   > cmd.exe `set "DD_SITE=<site>" && set "DD_ORG=<session>" && python ...`.
   > Run it with a command timeout of at least 16 minutes. It polls Datadog for
   > up to 15 minutes and prints JSON. Do not retry it or change anything.
   > Report back its `status` (`succeeded`, `failed`, or `error`), `checks`,
   > `links`, `ccm_enabled`, `message`, and any `error` object.

2. Tell the user, in these words:

   > "Your OpenAI integration is set up! I'm now checking in the background that
   > your usage metrics are arriving in Datadog. This can take up to 15 minutes,
   > and I'll let you know as soon as they show up. Feel free to keep working in
   > the meantime."

3. When the subagent reports, relay the result to the user.

**`succeeded`: celebrate.** Give a short, enthusiastic headline that starts with
🎉, such as "🎉 Your OpenAI usage metrics are now live in Datadog!", the
`links.metrics` link (an `openai.*` metric from this account in the Metrics
Explorer; usage points are stamped with when the usage happened, so tell the
user to widen the time range if the graph looks empty), and two or three
one-sentence next steps, for example: chart token usage by `model` and
`project_name`; set a monitor on token usage to catch spikes; and, when
`ccm_enabled` is `true`, that OpenAI costs will appear in Cloud Cost Management
(`links.costs`) within about 24 hours. Only describe what the script confirmed.

**`failed`:** stay encouraging and relay the script's `message`. Metrics only
exist for API usage, OpenAI reports usage with a delay, and the first data can
take longer to appear; suggest checking `openai.*` metrics later before treating it as
broken.

**`error`:** report the reason. For `datadog_read_not_permitted`, ask before
running the reported `pup auth login --extra-scopes ...` command.

If no subagent tool is available, run the same command yourself and report the
result the same way.
