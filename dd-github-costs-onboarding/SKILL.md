---
name: dd-github-costs-onboarding
description: Bring a GitHub enterprise's or organization's bill — Actions, Packages, Copilot, storage, and other metered products — into Datadog Cloud Cost Management through the Datadog GitHub Costs integration. Shows which enterprises and organizations the user can connect, guides creating the GitHub token the integration needs (entered where the agent never sees it), and warns before connecting an enterprise together with one of its organizations, which would count those costs twice. Use when the user wants GitHub billing or cost data (Actions, Packages, Copilot, storage) in Datadog Cloud Cost Management, or asks to connect or configure the Datadog GitHub Costs integration. Not for Copilot usage or seat metrics (use dd-github-copilot-onboarding) or for code links and PR comments through the GitHub App (use dd-github-onboarding).
metadata:
  version: "1.0.0"
  author: datadog-labs
  repository: https://github.com/datadog-labs/agent-skills
  tags: datadog,github,integration,cost,ccm
  alwaysApply: "false"
  tools: pup,python3,gh
---

# Datadog GitHub Costs Integration

Use `scripts/setup.py` relative to this file. It owns target discovery, the
browser handoff, hidden token entry in a separate terminal, the GitHub token
check, Pup OAuth validation, duplicate prevention, one-shot registration, and
secret redaction. Do not reproduce its API reads or writes.

The integration (`github-costs`) brings the GitHub bill of one **enterprise** or
one **organization** (Actions, Packages, Copilot, storage, and other metered
products) into Datadog Cloud Cost Management (CCM). Each Datadog GitHub Costs
account ("account" below) holds one GitHub personal access token (PAT) and the
enterprise slug or organization login it reads.

Out of scope: Copilot usage or seat metrics (use `dd-github-copilot-onboarding`),
and code in stack traces or PR comments through the GitHub App (use
`dd-github-onboarding`).

## Phase 0: Preflight

Check these before step 1; when a required one fails, stop with its fix:

- **`pup`, installed and authenticated.** The setup script performs every
  Datadog request through `pup api`, which owns authentication, OAuth refresh,
  site, and Datadog org selection. Run `pup auth status` and note the site for
  step 4. If `pup` is missing, ask before installing it:
  `brew tap datadog-labs/pack && brew install datadog-labs/pack/pup` (macOS or
  Linux with Homebrew),
  `curl -fsSL https://raw.githubusercontent.com/DataDog/pup/main/install.sh | sh`,
  or `npm install -g @datadog/pup` (Windows or anywhere with npm); see
  https://github.com/DataDog/pup. Then run `pup auth login`. Do not ask
  the user to create or store Datadog API or application keys.
- **`pup` at v2 or later.** The setup script uses v2-only CLI flags (such as
  `--no-envelope`); Pup 1.x fails with an opaque `pup_request_failed`. Check
  `pup --version`: a major version of 2 or later is required. If it is older,
  ask before upgrading. When the environment allows it, upgrade with the
  official installer or npm as above. Some workspaces block piping a remote
  script to a shell (`curl | sh`), and `/usr/local/bin` may not be
  user-writable; there, install from the release archive in a fresh empty
  directory instead (replace `2.4.3` and the platform suffix with the latest
  release):

  ```bash
  VERSION=2.4.3
  curl -fsSLO "https://github.com/DataDog/pup/releases/download/v${VERSION}/pup_${VERSION}_Linux_x86_64.tar.gz" &&
  curl -fsSLO "https://github.com/DataDog/pup/releases/download/v${VERSION}/pup_${VERSION}_checksums.txt" &&
  grep "pup_${VERSION}_Linux_x86_64.tar.gz$" "pup_${VERSION}_checksums.txt" | sha256sum -c - &&
  tar -xzf "pup_${VERSION}_Linux_x86_64.tar.gz" &&
  sudo install -m 0755 ./pup /usr/local/bin/pup &&
  pup auth login
  ```

- **`python3`** on `PATH` (`command -v python3`) to run the setup script.
- **The GitHub CLI (`gh`), optional, signed in** (`gh auth status`), to list the
  enterprises and organizations the user can connect. Without it the user
  types the enterprise slug or organization login (the script's
  `gh_unavailable` exit). To install it, see https://cli.github.com.
- **A graphical desktop session or a tmux session** (macOS, Linux with X11 or
  Wayland, or Windows; or the agent session inside tmux, which also works on
  headless hosts): the token prompt opens in a new terminal window — a new
  tmux window when the session runs inside tmux. Over SSH with no desktop
  session and no tmux, in a container, or on a headless host outside tmux,
  stop and point the user to the GitHub Costs integration tile in Datadog.
- **Enterprise:** an Enterprise Administrator creates a **classic** PAT with the
  `manage_billing:enterprise` and `read:org` scopes. Fine-grained tokens cannot
  read enterprise billing, so the script refuses them.
- **Organization:** an organization owner creates a **fine-grained** PAT whose
  resource owner is the organization, with Organization permissions →
  **Administration: Read-only**. If the organization requires approval for
  fine-grained tokens, an owner must approve it before it works. A classic PAT
  from an owner also works.
- **GitHub enhanced billing platform:** both enterprises and organizations must
  be on it, or the integration cannot read their costs (see the
  [GitHub Costs integration docs](https://docs.datadoghq.com/integrations/github-costs/)).
  If the target is not, stop.
- GitHub shows the token only once.
- Costs appear in Datadog only if the Datadog org uses Cloud Cost Management.

## Workflow

### 1. Enterprise or organization

Ask with one interactive question whether they want to connect a GitHub
**Enterprise** (enterprise-wide billing) or a single **Organization**. Warn that
connecting an enterprise *and* one of its organizations counts that
organization's costs twice; pick one level.

### 2. Choose the enterprise or organization

List what the signed-in GitHub CLI user can connect (read-only, needs no
Datadog credentials or token):

```bash
python3 <SKILL_DIR>/scripts/setup.py --list-targets
```

```json
{"status": "ok", "gh_login": "octocat", "organizations": ["acme"], "enterprises": null,
 "enterprises_note": "Listing enterprises needs the gh read:enterprise scope: gh auth refresh -h github.com -s read:enterprise"}
```

- `organizations` lists the organizations the `gh_login` user owns.
- `enterprises` lists the enterprises they administer or manage billing for. It
  is `null` when `gh` lacks the `read:enterprise` scope; `enterprises_note` then
  names the fix. If the user chose Enterprise, ask whether to run
  `gh auth refresh -h github.com -s read:enterprise` (it opens a browser) and
  rerun `--list-targets`, or let them type the enterprise slug instead.
- `gh_unavailable` → `gh` is missing or not signed in; ask for the enterprise
  slug or organization login directly.

Offer the matching list as choices with the host's interactive question tool
(the user can also type a slug or login). Confirm the `gh_login` is the GitHub account
they mean: the token must come from an owner or administrator of the target.
Never guess the target.

### 3. Name and tags

In one question form, gather values not already supplied:

- **Account name:** recommend one that names the target,
  such as `github-<target>`.
- **Tags (optional):** `key:value` tags to add to these costs, such as
  `team:platform`; no spaces or commas.

### 4. Approve and run

Before opening anything, tell the user what they will need to do: create a
GitHub personal access token on the page that opens (it is pre-filled with the
name, the permissions above, and, for organizations, the resource owner), and
paste it into a **separate terminal window** that the script opens. For an
organization the page presets a 365-day expiration; an enterprise's classic
token page defaults to 30 days. The terminal recommends **No expiration** (or,
for a classic token, at least 365 days) so costs keep flowing. Explain the
token requirements from Phase 0.

Then use the interactive question tool once for approval:

- **Title:** `Approve GitHub Costs integration setup`
- **Question:** `May I run the interactive setup? It will open GitHub's token page pre-filled for <TARGET>, ask you to paste the token into a hidden prompt in a new terminal, check with GitHub that it can read <TARGET>'s billing, and register the named Datadog GitHub Costs account. The token is never visible to the agent, and will not be printed, passed as a command argument, stored in a file, or returned in output.`
- **Approve option:** `Approve and open the secure token prompt`
- **Cancel option:** `Cancel without making changes`

After approval, run:

```bash
python3 <SKILL_DIR>/scripts/setup.py --account-name "<ACCOUNT_NAME>" --scope <enterprise|organization> --target "<TARGET>"
```

For example, an organization on the EU site with one tag:

```bash
DD_SITE=datadoghq.eu python3 <SKILL_DIR>/scripts/setup.py --account-name "github-acme" --scope organization --target "acme" --tag team:platform
```

Append `--tag <key:value>` once per tag. On Windows, use `python` instead of
`python3`. Pup uses its default session, and picks the Datadog site from
`DD_SITE` (default `datadoghq.com`): if the user's Pup session is on another
site, run the command with `DD_SITE` set to it (e.g. `datadoghq.eu`); if they
use a named Pup session (`pup auth login --org <name>`), also set the `DD_ORG`
environment variable to that session's name. Set them the way the shell expects: a
`NAME=value` prefix on POSIX shells, `$env:NAME="value";` in PowerShell, or
`set "NAME=value" &&` in cmd.exe. Do not detach the process or redirect its
input. Tell the user to switch to the new terminal when it appears — a
tmux window named `github-costs-token` when the session runs inside
tmux.

Run the command with the longest command timeout your tool allows (at least 10
minutes). The user may take up to 5 minutes to enter the token, and Datadog
requests have no client-side timeout. Killing the command during registration
leaves the outcome unknown.

The script always opens a new graphical terminal for token entry, because the
agent's own TTY may not be visible to the user; if it cannot, it fails instead
of prompting in place. Never ask the user to paste the token
into chat, and never pass it through a command argument, environment variable,
shell pipe, redirected input, or file.

## Deterministic behavior

Before asking for a token, the script validates Pup, returns an existing
account with the same name instead of creating one, and refuses a target that is
already connected. It checks the token with GitHub's billing API before
writing anything, sends one POST, and reconciles a failed POST by listing
accounts instead of retrying. The token stays in memory and is redacted from
all output.

## Results and recovery

Report the script's status and warnings without exposing credentials. Errors
print `{"status": "error", "error": {"reason": ..., "message": ...}}`; a
trimmed `complete` result looks like:

```json
{"status": "complete", "account": {"name": "github-acme", "target": "acme"},
 "cost_tag": "organization_name:acme",
 "links": {"costs": "https://app.datadoghq.eu/cost/summarize/overview?provider_id=GitHub%20Costs"}}
```

- **`complete`** → continue with
  [Getting the Most Out of Your Integration](#getting-the-most-out-of-your-integration).
- **`already_configured`** → an account with that name exists (`account.target`
  says what it reads); nothing changed. Offer a different name if they meant a
  new target.
- **`pup_cli_outdated`** → the installed Pup is older than v2 and cannot run this
  setup. Ask before upgrading it (see the Pup version preflight above), then rerun
  once the upgrade is done.
- **`target_already_connected`** → that enterprise or organization is already
  connected under `details.account.name`; nothing was written. Do not add it
  again: a second account would count those costs twice.
- **`duplicate_datadog_accounts`** → several accounts share that name. Do not
  create another; ask the user which one to keep, and stop.
- **`account_name_taken`** → while the token was being entered, another account
  with that account name appeared for a different target. If the POST was
  rejected, nothing was connected for the requested target; rerun with a
  different name. If it reports the POST succeeded, inspect the account list
  before rerunning.
- **`classic_token_required`** → an enterprise needs a classic token; the user
  pasted a fine-grained one. Rerun and create a classic token on the page that
  opens.
- **`invalid_github_token`** or **`github_token_rejected`** → nothing was
  written. Ask the user to create a fresh token and rerun.
- **`github_access_denied`** → GitHub refused the token for that target; relay
  the script's `message`. Usually the token lacks the permissions above, was
  created by someone who is not an owner/administrator, has the wrong resource
  owner, is awaiting organization approval, or the target slug or login is wrong.
- **`github_check_failed`** → GitHub could not be reached, rate-limited the
  check, or answered with a temporary error; nothing was written. Rerun shortly (and make sure the network
  allows `api.github.com`).
- **`terminal_launch_failed`** or **`terminal_launch_unsupported`** → no
  graphical terminal could be opened (headless, SSH, or container); nothing was
  written. Ask the user to rerun from a desktop session, or to connect it from
  the GitHub Costs integration tile.
- **`terminal_helper_not_connected`**, **`credential_entry_timeout`**, or
  **`credential_entry_cancelled`** → nothing was written. Ask whether to rerun.
- **`datadog_create_failed`** → Datadog rejected the request; nothing was
  written. Relay `details.pup_error`. A 403 usually means the user lacks the
  Integrations Manage (`manage_integrations`) permission.
- **`ambiguous_datadog_post`** → do not rerun, because the write may still land.
  Inspect the account list (read-only): report the account as created only if
  both its name and its `settings.enterprise_name` match the requested name and
  GitHub enterprise or organization. A `details.same_name_account` reads a
  different target, so it does not count. Otherwise keep the outcome ambiguous
  and send the user to the GitHub Costs integration tile to check the account.
- **`datadog_account_not_visible`** → Datadog accepted the account but it is not
  listed yet. Do not rerun; check the account list again later.
- Pup authentication failures → run `pup auth refresh`; if that fails, run
  `pup auth login` with the reported missing scopes.
- Any other status → relay the script's `message` and `reason`, and rerun once
  the cause is fixed. When the status says the write may have landed (as
  above), check the account list first.

## Getting the Most Out of Your Integration

After `complete`, give a short, enthusiastic headline that starts with 🎉, such
as "🎉 GitHub Costs is connected for <TARGET>!", then tell the user:

- GitHub costs appear in Cloud Cost Management (`links.costs`) under the
  **GitHub Costs** provider, typically within about 24 hours; GitHub's billing
  data is daily, so there is nothing to check sooner.
- They can filter to this connection with the `cost_tag` the script returned
  (`enterprise_name:<slug>` or `organization_name:<login>`), plus any tags they
  added.
- If the token has an expiration (365 days preset for an organization, 30 days
  by default for an enterprise's classic token), they should set a reminder to
  replace it before it expires. This skill does not update an existing
  account; manage it from the GitHub Costs integration tile in Datadog.
- For what the integration collects and how to configure it by hand, see the
  [GitHub Costs integration docs](https://docs.datadoghq.com/integrations/github-costs/).

Only describe what the script confirmed.
