---
name: dd-github-onboarding
description: Connect GitHub to Datadog through the Datadog GitHub App so Datadog can show source code in stack traces and comment on pull requests. Checks whether a GitHub account is already connected first; the user installs the app on the GitHub organization or account they choose, and the skill verifies what the connection unlocked. Optionally also adds a `service.datadog.yaml` to a repository so its services appear in Datadog's Software Catalog. Use when the user wants to connect or configure the Datadog GitHub integration, code snippets in stack traces, or Datadog feedback on pull requests. Not for GitHub Copilot usage metrics (use dd-github-copilot-onboarding) or GitHub billing and costs in Cloud Cost Management (use dd-github-costs-onboarding).
metadata:
  version: "1.0.0"
  author: datadog-labs
  repository: https://github.com/datadog-labs/agent-skills
  tags: datadog,github,integration,source-code,pull-requests
  alwaysApply: "false"
  tools: pup,python3,gh
---

# Datadog GitHub integration

Connect GitHub to Datadog in two moves: **the install** on GitHub's install
screen (the user installs the Datadog GitHub App on a GitHub org/account they
own), then Datadog **linking** that installation so it can read the chosen
repositories. The background poll drives the link.

The install is the user's — this skill never installs on their behalf and
never writes credentials. All `/api/` calls go through `pup api`, which
authenticates with the user's own session; that session needs the
**Integrations Manage** (`manage_integrations`) permission, which is what
authorizes the connect (see **Important Notes** when it is missing). The install
URL is minted through the Datadog API, so it carries a short-lived Datadog
`state` and GitHub's post-install redirect lands back on Datadog cleanly; its
bearer is captured from `pup auth token` in process memory only.

`scripts/github_setup.py` owns site resolution, install-URL minting, account and
installation discovery, linking, verification, and the API calls — do not reproduce that logic
yourself. The API reference is
<https://docs.datadoghq.com/api/latest/integrations/>.

Not for GitHub Copilot usage metrics (use `dd-github-copilot-onboarding`) or
GitHub billing and costs in Cloud Cost Management (use
`dd-github-costs-onboarding`). Monitors and notifications are out of scope.

- [ ] 0. Preflight: `pup auth status`, `python3`
- [ ] 1. `accounts` → stop or continue
- [ ] 2. `authorize --open` (add `--continue` only after the user chose continue)
- [ ] 3. `wait` in the background → exit 0
- [ ] 4. `verify --account <name> --json` → exit 0
- [ ] 5. Offer the Software Catalog demo (optional, only after a yes)
- [ ] 6. Final message

## Phase 0: Preflight

**Datadog for Government is not supported.** If `pup auth status` reports a
`ddog-gov.com` site, stop and tell the user to set up the integration from the
Datadog app instead. The script refuses Government sites too.

**`pup` and the site.** This flow drives the Datadog API through `pup api`, and
the site is derived from the user's pup authentication, so the API
calls hit the same Datadog environment they are logged into:

```bash
pup auth status
```

- `authenticated: true` → use the reported `site`; continue.
- `authenticated: false` → **do not declare the user unauthenticated yet.** pup
  reports the session for the site in `DD_SITE` (default `datadoghq.com`) or the
  named `--org` session — a user logged into another site (e.g. staging
  `datad0g.com`) shows as unauthenticated under the default. Ask which site/org
  they authenticated for, then re-run with `DD_SITE=<site>`. If they
  authenticated a named (org-keyed) pup session instead, pass `--org <name>` to
  the script's commands — it threads the flag through `pup auth status`,
  `pup api` and `pup auth token`. Still `false` → **stop** and ask them to run
  `DD_SITE=<site> pup auth login` (it needs a browser). Never guess a site.
- `pup` is missing (`command -v pup` fails) → ask before installing it:
  `brew tap datadog-labs/pack && brew install datadog-labs/pack/pup` (macOS or
  Linux with Homebrew),
  `curl -fsSL https://raw.githubusercontent.com/DataDog/pup/main/install.sh | sh`,
  or `npm install -g @datadog/pup` (Windows or anywhere with npm); see
  <https://github.com/DataDog/pup>. Then run `pup auth login`.
- `pup` returns non-JSON → the `pup` on `PATH` may not be the Datadog CLI
  (e.g. a same-named proto tool shadowing it). Point the script at the real
  binary with `--pup <path-to-pup>`; if the user knows their site,
  `--site <site>` bypasses pup for site resolution, but the API
  steps still need a working `pup`.

Pass the same context to **every** script command in this skill: `--site
<site>` when Phase 0 needed `DD_SITE=<site>`, and the same `--pup` / `--org`.

**Python.** The flow runs `scripts/github_setup.py`, so confirm `python3` is on
`PATH` before starting:

```bash
command -v python3 || echo "MISSING python3"
```

**Browser.** The install needs a browser logged into both GitHub and the
Datadog app. If there is no browser (a headless host), Phase 2 drops `--open`
and prints the URL for the user to open where they are logged in.

**Permission.** A `403` means the session lacks `manage_integrations`; see **Important Notes**.

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

## Phase 1: Determine Scope

One check comes before anything is opened: whether the org already has a
GitHub account connected. Do not ask which GitHub org, personal account, or
repositories to connect — the user picks those on GitHub's install screen.

If the user's repositories are on GitHub Enterprise Server or GHE.com, stop and
point them to the GitHub integration tile in Datadog: this flow connects
github.com only.

### Is a GitHub account already connected?

Before opening anything, find out whether the org already has a GitHub
integration — the user may not want this flow at all. `accounts` is a
read-only GET (it also lists the user's app installations and their link
statuses):

```bash
python3 -I -B <skill>/scripts/github_setup.py accounts
```

- **Exit 2 / "no GitHub account is connected"** → nothing is connected yet;
  go straight to Phase 2. If it lists a `NOT_LINKED_LINKABLE` installation,
  mention it in passing: `link <installation_id>` connects it without a new
  install.
- **Exit 0 with accounts** → report **exactly what was discovered** before
  asking anything else. Name every account (with its repository count) and the
  installations with their link statuses — `--json` gives the full lists. For
  example:

  > We discovered these GitHub accounts are already connected: **acme** (3
  > repos). Your installations: acme — LINKED_TO_THIS_ORG; personal —
  > NOT_LINKED_LINKABLE.

  Then ask, with the host's native selector:

  > Would you like to: (1) stop — the existing connection already covers you,
  > or (2) continue and connect another GitHub?

  - **(1) stop** → end the flow here; do not modify the existing accounts or
    installations.
  - **(2) continue** → run Phase 2 with `--continue` (the script refuses to
    open the install screen without it when accounts exist, so the user's
    answer is what unlocks it).

## Phase 2: Perform the GitHub App install flow and validate

### Open GitHub's install screen

```bash
python3 -I -B <skill>/scripts/github_setup.py authorize --open
```

- Only when the user chose **(2) continue** in Phase 1, add `--continue`.
- It mints a state-bearing install URL through the Datadog API and opens it
  (`--json`: `url`, `minted`, `mint_error`, `existing_accounts`). If the mint
  is unavailable it falls back to GitHub's public install screen and reports
  `minted: false`.
- Headless, or exit 3 (no browser opened): drop `--open` and give the user the
  URL.

Never complete the install for the user, and **do not wait for them to report
back** — start the poll below as soon as the screen is open. Tell the user:

- The URL expires in about **five minutes** (re-run `authorize` if it does),
  and the browser needs a Datadog session for the site. After Install they
  land on Datadog's GitHub configuration page.
- **Only when `minted: false`:** GitHub's redirect after Install shows a
  Datadog error page — harmless, the poll still lands the connect; say so
  before they click Install.
- Install on a GitHub org they own (or their personal account); otherwise the
  installation is `NOT_LINKED_MISSING_PERMISSIONS` and cannot land — say so
  plainly rather than retrying.

### Poll until the connect lands (no user confirmation)

```bash
python3 -I -B <skill>/scripts/github_setup.py wait --interval 5 --timeout 300
```

It waits through the pending-install state and retries transient errors.

Start this **as a background process immediately after the install screen is
open** (background it with the host's process manager or a shell `&`), then
continue when it reports the connection — no question to the user in between.
It links **the installation that becomes linkable while it polls**, then polls
the accounts and exits 0 printing the new connection. `--json` emits `site`,
`installation_id`, `github_account_name` and `new_accounts`. Exit 2 means it
timed out: re-check the caveats above (the installation landed on a GitHub org
the user owns), and if the install completed *before* this poll started, link
it by hand (below); otherwise re-run this step.

When **two installations become linkable at once** the poll stops with them
listed — pick one with the user and run:

```bash
python3 -I -B <skill>/scripts/github_setup.py link <installation_id>
```

To re-check by hand (for example, the connect predates this run), run
`accounts` (Phase 1).

### Verify what the connect unlocked

After `wait` exits 0, run (read-only, in the background — it waits up to 120s
for Datadog's repository sync):

```bash
python3 -I -B <skill>/scripts/github_setup.py verify --account <github_account_name> --json
```

It reports `sync_status`, `source_code_integration` (Datadog can read the code
— what puts snippets in stack traces), and `code_repositories` /
`pr_comment_repositories` (up to 10 names each, with the full totals in
`code_repository_count` / `pr_comment_repository_count`). Exit 2 → say which
check failed: empty
`code_repositories` usually means no repositories were picked on GitHub's
install screen; `sync_status: running` → re-run `verify` in a few minutes.

### Uninstall (when asked)

The Software Catalog only drops a GitHub-synced entry while the integration can
still see the change, so remove the demo files **before** unlinking:

1. `catalog-cleanup --account <github_account_name> --dry-run --json` — finds
   the repositories with demo entries through the Software Catalog (no scan of
   every repository) and lists the demo files `catalog-pr` added (only files
   tagged `source:dd-github-onboarding`; the user's own definitions are never
   touched). Relay any per-repository `error` or `needs_choice` (ask which `gh`
   account, pass `--gh-user`). No `service_names` → go to step 4.
2. Ask with the host's native selector, listing the files: **Yes, open the
   removal PRs** / **No, unlink now** (the demo entries then stay in the
   catalog until deleted there by hand). On yes, re-run without `--dry-run`
   and give the user each `pr_url` to merge.
3. After they merge, in the background, per name in `service_names`:
   `catalog-wait --service-name <name> --absent --json`. Exit 2 → not merged
   yet: ask whether to keep waiting or unlink anyway.
4. `unlink <installation_id>` (`accounts` lists the ids). Uninstalling the app
   on GitHub, if wanted, comes after this — never before step 3.

Prefix each command with `python3 -I -B <skill>/scripts/github_setup.py`.

## Getting the Most Out of Your Integration

**Show it in action (optional).** A PR that adds a `service.datadog.yaml` to one
of `code_repositories`; once merged, the repository appears in the Software
Catalog within seconds. It writes to the user's repository, so it starts with
a question. First run `gh auth status --hostname github.com`; if `gh` is
missing (see <https://cli.github.com>) or not logged in, skip the demo and say
why.

0. **Offer it with the host's native selector** (e.g. AskUserQuestion) — never
   just in prose: "Want to see it in action? I can open a pull request that
   adds your repository to Datadog's Software Catalog." Options: one per
   repository in `code_repositories` (up to three, e.g. **Yes, use
   `Test-Repository`**) and **No, skip it**. No → go to **Final message**.

1. **Preview** the chosen repository (writes nothing):

   ```bash
   python3 -I -B <skill>/scripts/github_setup.py catalog-pr --repo <owner>/<repo> --dry-run --json
   ```

   The script picks the GitHub account — never switch the user's active `gh`
   account or try accounts yourself. `gh_user` is the only logged-in account
   that can push; `needs_choice: true` → ask which (list the `push` ones in
   `accounts`) and re-run with `--gh-user`. It refuses, before writing, if the
   repo already has a catalog file (skip the demo), if the service name is
   already in the catalog (offer `--service-name <other>`), or if an earlier
   demo PR is still open. Each run uses a fresh branch
   (`datadog/software-catalog-<6 hex>`, shown in `branch`); it never reuses or
   deletes one.
2. **Ask** with the host's native selector, showing the file and naming the
   account: "I'll open a pull request in `<owner>/<repo>` as `<gh_user>` adding
   this `service.datadog.yaml`; once you merge it, the repository shows up in
   the Software Catalog." **Yes, open the pull request** / **No, skip it**.
   Never open it without a yes.
3. **Open** it: the same command without `--dry-run`, always with
   `--gh-user <gh_user>`. It never touches the default branch or merges. Give
   the user `pr_url` to merge.
4. **Wait** in the background (up to an hour, for the merge):
   `catalog-wait --service-name <service_name> --json`. Exit 0 → include both
   links in the final message, `link` (the catalog; search for the name) and
   `entity.github_url` (the file it syncs from). Exit 2 → check the PR is
   merged, and look in Event Management for
   `source:software_catalog status:error`.

### Final message

Only after `verify` exits 0 (never celebrate after a failure), built only from
what it confirmed, in this order:

1. 🎉 GitHub is connected: `<account>` — Datadog can show code from
   `<n>` repositories (`<first names>`) in stack traces, and comment on pull
   requests in `<m>` (mention PR comments only for `pr_comment_repositories`).
2. Only if the demo ran: Software Catalog: `<service_name>` — `link`,
   `entity.github_url` (or the open `pr_url` if not merged yet).
3. What else it unlocks, in a sentence or two: **code in context** (code
   snippets and GitHub links in Error Tracking, APM, and Profiler stack
   traces, with Error Tracking assigning issues to the owning team),
   **feedback on pull requests** (Code Security and Test Optimization), and
   **ownership** (Software Catalog definitions in GitHub, GitHub Teams mapped
   to Datadog Teams). Docs: <https://docs.datadoghq.com/integrations/github/>.

## Important Notes

`authorize` appends the guidance below to the error, so relay that text rather
than inventing a route. When classifying by hand:

- **Existing-accounts refusal** (only when `--continue` was not passed) → go
  back to Phase 1's ask; the user decides.
- **Permission / `403` / `Forbidden` / `manage_integrations`** — the user's
  Datadog session is not allowed to start the connect. Tell them they need
  **Integrations Manage** (`manage_integrations`) on their Datadog org: an org
  admin grants it through RBAC, and it is on the default Datadog Standard
  Role. The related **Integrations Read** (`integrations_read`) is view-only
  and is **not** sufficient. Reference:
  <https://docs.datadoghq.com/account_management/rbac/permissions/>. Once an
  admin grants the permission, re-run `authorize --open`.
- **`401` / not authenticated** — the pup token itself is missing, stale, or
  org-unbound; re-authenticate (`pup auth login`, see Phase 0) and re-run.
- **Error page after Install** — expected only when `minted: false`; ignore
  it. With a minted URL it means the URL expired or the browser had no
  Datadog session — the poll still lands an install that happened, otherwise
  re-run `authorize --open`.
