---
name: dd-github-copilot-onboarding
description: >-
  Connects GitHub Copilot to Datadog so the user can see Copilot usage and license metrics for
  the GitHub organizations they own. Checks whether a Copilot account is already connected
  first; confirms which GitHub account will be connected and which organizations it collects,
  and verifies those organizations allow usage-metrics collection before connecting. Can also
  remove a connected account for cleanup. Use when the user wants Copilot usage metrics in
  Datadog, or asks to connect or configure the Datadog GitHub Copilot integration. Not for
  Copilot billing or cost data in Cloud Cost Management (use dd-github-costs-onboarding), or
  for code in stack traces and pull request comments (use dd-github-onboarding).
metadata:
  version: "1.0.0"
  author: datadog-labs
  repository: https://github.com/datadog-labs/agent-skills
  tags: datadog,github,copilot,integration,oauth
  alwaysApply: "false"
  tools: pup,python3,gh
---

# Datadog GitHub Copilot integration

Connect GitHub Copilot to Datadog in two user-driven moves: the **consent** (the
OAuth grant) on GitHub's Authorize screen for the Datadog Copilot app, then Datadog writing
the account when GitHub redirects back. Once connected, Copilot usage and
license metrics (`github_copilot.usage.*`) start flowing.

The flow starts with a scope check (Phase 1): if the Datadog org already has
GitHub Copilot accounts connected, the skill reports exactly what is configured
and asks whether to stop or add another. A connection is a **GitHub user's**
grant — Datadog collects every GitHub org that user owns — so the skill
confirms the signed-in `gh` user, lists the GitHub orgs it will collect, checks
each one's usage metrics policy, and names the account after the GitHub login.
Copilot **costs** (Cloud Cost Management) are out of scope: hand off to
`dd-github-costs-onboarding`. Code in stack traces and pull request comments
belong to `dd-github-onboarding`.

The consent is the user's — this skill never authorizes on their behalf.
**Every call except the mint rides `pup api`**, which authenticates with the
user's own session. The mint POSTs straight to the site's UI host with the
session's OAuth token from `pup auth token`, held in memory only and never
printed (see *Why the mint bypasses `pup api`* in Phase 2). Nothing here waits
on the user to report back: the consent is detected by a background poll.

`scripts/github_copilot_setup.py` owns site resolution, the mint, account
discovery, and the API calls — do not reproduce that logic yourself.

This is a hands-on setup: run the commands yourself as part of the
conversation rather than handing the user a list, and keep them in the loop.

Copy this checklist and tick it off:

- [ ] 0. `pup auth status`, `python3`, `gh auth status` (Phase 0)
- [ ] 1. `accounts` → stop, or continue with `--continue`
- [ ] 2. `orgs` → confirm `<LOGIN>`
- [ ] 3. Ask about GitHub Enterprise
- [ ] 4. `check-org <github-org>` for each owned GitHub org
- [ ] 5. `authorize --account-name "<LOGIN>" --open`
- [ ] 6. `wait --account-name "<LOGIN>"` in the background
- [ ] 7. Hand off, then `verify` in the background

## Phase 0: Preflight

**pup, authenticated.** The whole flow rides on the user's `pup` session — it
resolves the site, carries every `pup api` call, and supplies the OAuth token
for the one call that does not ride `pup api`, the UI-host mint:

```bash
pup auth status
```

- `authenticated: true` → use the reported `site`; continue.
- The site is **Datadog for Government** (`ddog-gov.com`) → stop: the GitHub
  Copilot integration is not available there yet, and the script refuses it.
- `authenticated: false` → **do not declare the user unauthenticated yet.** pup reports the
  session for the site in `DD_SITE` (default `datadoghq.com`) or the named pup session —
  a user logged into another site (e.g. EU `datadoghq.eu`) shows as unauthenticated
  under the default. Ask which site or pup session they authenticated, then re-run with
  `DD_SITE=<site>`. If they authenticated a named (org-keyed) pup session instead, pass
  the pup session flag `--org <session>` to the script's commands — it threads the flag
  through `pup auth status` and the `pup api` calls. Still `false` → **stop** and ask them to run
  `DD_SITE=<site> pup auth login` (it needs a browser). Never guess a site.
- `pup` is missing → ask before installing it:
  `brew tap datadog-labs/pack && brew install datadog-labs/pack/pup` (macOS or
  Linux with Homebrew),
  `curl -fsSL https://raw.githubusercontent.com/DataDog/pup/main/install.sh | sh`,
  or `npm install -g @datadog/pup` (Windows or anywhere with npm); see
  <https://github.com/DataDog/pup>. Then run `pup auth login`.
- `pup` returns non-JSON → the `pup` on `PATH` may not be the Datadog CLI
  (e.g. a same-named proto tool shadowing it). Point the script at the real binary with
  `--pup <path-to-pup>`; if the user knows their site, `--site <site>` bypasses pup for
  site resolution, but the mint and the API steps still need a working `pup`.

**python3.** The flow runs through `scripts/github_copilot_setup.py`, invoked as
`python3 -I -B`, so `python3` must be on `PATH`:

```bash
command -v python3 >/dev/null 2>&1 && echo "python3: available" || echo "MISSING python3 - https://www.python.org/downloads/"
command -v pup >/dev/null 2>&1 && echo "pup: available" || echo "MISSING pup"
command -v gh >/dev/null 2>&1 && echo "gh: available" || echo "MISSING gh - https://cli.github.com"
```

**GitHub CLI (`gh`).** Signed in as the GitHub user whose Copilot usage should
be collected — the flow lists the organizations that user owns and probes each
org's usage-metrics policy through `gh`, read-only, with no Datadog credentials:

```bash
gh auth status
```

- Signed in → continue; Phase 1 confirms the exact account before anything is
  minted.
- `gh` is missing → ask before installing it from <https://cli.github.com>
  (it is used read-only), then continue as below.
- Not signed in → ask the user to run `gh auth login` (it needs a browser)
  before continuing.

**Browser.** The consent happens on GitHub's Authorize screen in a browser, and
`DD_SITE=<site> pup auth login` needs one too. If there is no browser
(headless), the flow still works — drop `--open` in Phase 2, print the returned
URL, and ask the user to open it where they are logged into GitHub.

**Permission.** The session that mints the connection needs **Integrations
Manage** (`manage_integrations`) on the Datadog org; an org admin grants it
through RBAC. The related **Integrations Read** (`integrations_read`) is
view-only and is **not** sufficient. A missing grant surfaces as a `403` on the
mint — the routing is in Phase 2 under "If the mint fails".

## Phase 1: Determine Scope

**What gets connected is a GitHub user, not an org.** The connection stores the
OAuth token of the GitHub user who clicks Authorize, and every day Datadog
collects Copilot metrics for **every org that user can read Copilot data for**
(the orgs they own, with the usage metrics policy on). Picking an org does not
limit the collection, so this flow never asks for one. The account is named
after the GitHub login.

### Is an account already connected?

Start here: find out whether this Datadog org already has the integration — the
customer may not want this flow at all. `accounts` is a read-only GET that goes
through `pup api`:

```bash
python3 -I -B <skill>/scripts/github_copilot_setup.py accounts
```

Pass the same `--pup`/`--site`/pup-session `--org <session>` context as the rest of the flow (Phase 0).

- **Exit 2 / "no GitHub Copilot account is connected"** → nothing is connected
  yet. Continue to **Which GitHub account?**
- **Exit 0 with accounts** → report **exactly what was discovered** before
  asking anything else. Name every account with its id — `--json` gives the
  full list. For example:

  > We discovered these GitHub accounts are already connected:
  > **account1** (a1b2c3d4).

  Then ask, with the host's native selector:

  > Would you like to: (1) stop — the existing account is sufficient, or
  > (2) continue and connect another GitHub account?

  - **(1) stop** → end the flow here; do not modify the existing accounts.
  - **(2) continue** → go to **Which GitHub account?**, and pass `--continue` in
    Phase 2 (the script refuses the mint without it when accounts exist, so the
    customer's answer is what unlocks it).

### Which GitHub account?

Find the signed-in `gh` user and the orgs it owns — read-only, no Datadog
credentials, and never blocks (a missing, unauthenticated or erroring `gh` exits
2 with a note rather than a traceback):

```bash
python3 -I -B <skill>/scripts/github_copilot_setup.py orgs
```

It prints `gh user: <login>` and then the orgs that user owns (role `admin`,
membership `active`); `--json` emits `{"gh_login": ..., "orgs": [...]}`. Tell the
user plainly, for example:

> Connecting as GitHub user **<LOGIN>**. Datadog will collect Copilot data for
> the orgs this user owns: **<ORG1>**, **<ORG2>**.

Then ask with the host's native selector whether that is the right GitHub user:

- **Yes** → continue. The login is the account name (Phase 2).
- **No** → they switch with `gh auth switch` (or `gh auth login`), then re-run
  `orgs`.
- **A connected account already has this login** (from the step above) → this
  GitHub user is already connected; reconnecting would duplicate its metrics and
  `authorize` refuses it. Offer `gh auth switch`, or `delete-account <id>` first
  if they want to reconnect it.
- **No owned orgs (discovery succeeded)** → this user owns no org, so the
  connection would collect nothing unless Copilot is managed by an enterprise
  (next step). Otherwise offer `gh auth switch` to the GitHub user that owns the
  orgs.
- **`orgs` failed** (exit 2: `gh` missing, not logged in, or an API error) →
  ownership is unknown, so do not tell the user they own nothing. Relay the
  error and help fix `gh`, then re-run `orgs`; or ask which GitHub login they
  will authorize as and which orgs it owns. Confirm that login as `<LOGIN>`
  before continuing — `authorize --account-name` and `wait` need it.

### Is Copilot managed by a GitHub Enterprise?

Datadog collects enterprise-level Copilot data only when the account carries the
enterprise name, and `gh` cannot detect it without the `read:enterprise` scope,
so ask with the host's native selector:

> Are your Copilot licenses managed by a GitHub Enterprise?

Options: **No — licenses are per org** (the common case), **Yes**, and **Not
sure**. Copilot Business licenses can be assigned by an organization or by an
enterprise; GitHub explains the plans at
<https://docs.github.com/en/copilot/get-started/plans> and enterprise-managed
Copilot at
<https://docs.github.com/en/copilot/how-tos/administer-copilot/manage-for-enterprise>.

- **Not sure** → suggest asking whoever manages the company's GitHub Copilot
  billing. If they still cannot tell, treat it as **No**: per-org data is
  collected either way for the orgs this user owns, and only enterprise-level
  totals need the enterprise name.

- **No** → continue; no enterprise flags.
- **Yes** → ask for the enterprise name (its slug, as in
  `github.com/enterprises/<name>`) and whether it is on **GHE.com** (GitHub
  Enterprise Cloud with data residency).
  - **Not GHE.com** → pass `--enterprise-name <name>` to `authorize` and
    `verify`. During consent the user picks "Authorize Enterprise Cloud".
  - **GHE.com** → this flow does not support it (the consent runs on github.com,
    and Datadog collects only enterprise billing metrics there). Point the user
    to the GitHub Copilot integration tile in the Datadog app and stop.

### Which tags should the account carry?

Ask whether to add tags to the new account (optional), such as
`team:dev-tools`: `key:value` pairs with no spaces or commas. Pass each one as
`--tags <key:value>` to `authorize`. Tags are set when the authorize URL is
minted, so ask before Phase 2; no answer means no tags.

### Is the Copilot usage metrics policy on?

Probe each owned org before minting — it is the same signal the metrics need, so
an org with the policy off surfaces here instead of as missing data later:

```bash
python3 -I -B <skill>/scripts/github_copilot_setup.py check-org <github-org>
```

It calls `GET /orgs/<github-org>/copilot/metrics/reports/organization-28-day/latest`
through `gh api`:

- **Enabled** (HTTP 200 or 204) → this org will report. Keep it for `verify`.
- **Not found** (HTTP 404) → the policy is off. Tell the user this org will send
  no data until an owner enables it: org Settings → Copilot → Policies →
  Features → **"Copilot usage metrics"** → Enabled (enterprise-managed orgs need
  the enterprise policy **"Enabled everywhere"**).
- **Token lacks permission** (HTTP 403 on an owned org) → the `gh` token lacks
  the Copilot metrics scope; relay the `gh auth refresh` command it prints and
  re-run.
- **Any other failure** → relay it; it is a caveat, not a hard stop.

These are warnings, not blocks: continue to Phase 2 as long as at least one org
is enabled (or an enterprise name was given). If none is, ask whether to connect
anyway. (`--json` gives `org`, `status` and `verdict`.)

## Phase 2: Perform the OAuth flow and validate

### Mint and open the GitHub authorize URL

```bash
python3 -I -B <skill>/scripts/github_copilot_setup.py authorize --account-name "<LOGIN>" --open
```

- Replace `<skill>` with this skill's directory and `<LOGIN>` with the GitHub
  login from Phase 1. Add `--continue` only when the user chose **(2)
  continue** in Phase 1, `--enterprise-name` if one was given, and
  `--tags <key:value>` once per tag the user chose. The command
  resolves the site (Phase 0), mints the installation on the site's UI host
  (see *Why the mint bypasses `pup api`* below), then opens the authorize URL
  the Datadog API returns **complete**; add `--json` to capture `site`, `url`,
  `account_name` and `existing_accounts` in the transcript.
- **The account name is frozen at mint time** — it cannot be learned from the
  consent outcome.
- **The GitHub user who clicks Authorize must be `<LOGIN>`.** The connection
  collects for whoever approves in the browser; if the browser is signed into a
  different GitHub user, the account name will not match what it collects.
- Headless: see Phase 0's **Browser**. If `authorize --open` exits 3, the browser didn't open: give the user the URL printed after
  `open the URL manually:`.

The URL lands the user directly on **GitHub's Authorize screen** for the
Datadog Copilot app. Never complete the consent on their behalf — but **do not
wait for them to report back either**: start the poll below as soon as the
consent screen is open and let it notice the consent. Repeat runs may
auto-approve with no click.

Caveats to hand to the user:

- The `state` in the minted authorize URL is valid about **30 minutes** — but
  the account only appears after the consent, so open it promptly
  anyway.
- The finish validates the state's Datadog **user and org** against the browser
  session that completes the consent: whoever finishes on GitHub must be the
  same Datadog user, on the same org, as the `pup` session that minted — an
  install finished from a different user or org is rejected. Multi-org users:
  make sure the browser is on that same org.
- If GitHub shows "The redirect_uri is not associated with this application",
  something is wrong on Datadog's side — this flow always redirects to Datadog's
  own callback. Do not retry; tell the user to contact Datadog support
  (<https://docs.datadoghq.com/help/>) with the time of the attempt and their Datadog
  site.

#### Why the mint bypasses `pup api`

This is the one call that does not ride `pup api`, and deliberately so. The
minted `state` token records the **host the mint was sent to**, and every
post-consent browser leg is driven by that recorded host — the redirect bounces
to `https://<that host>/api/ui/oauth-integrations/github-copilot/install/finish`
and on success to `https://<that host>/integrations?...&oauthAuthenticationStatus=success`.
Mint on `api.<site>` and the browser lands on the API host, where the user's
Datadog browser session cannot authenticate — the flow ends in "unauthorized"
right after the GitHub account is picked. Minting from the UI host reproduces
exactly what the integration tile's Authorize button does. (All the *other*
calls stay on `pup api`, which only ever attaches the session credential to the
`api.<site>` host.) The script derives the UI host — `app.<site>` for a bare
domain, the site itself for regional or vanity hosts (e.g. `us3.datadoghq.com`)
— so do not build it yourself.

### If the mint fails: read the error before re-routing

`authorize` appends the guidance below to the error, so relay that text rather
than inventing a route. When classifying by hand:

- **Existing-accounts refusal** (only when `--continue` was not passed) → go
  back to Phase 1's ask; the customer decides.
- **Permission / `403` / `Forbidden` / `manage_integrations`** — the user's
  Datadog session is not allowed to start the connection. Tell them they need
  **Integrations Manage** (`manage_integrations`) on their Datadog org: an org
  admin grants it through RBAC, and it is on the default Datadog Standard
  Role. The related **Integrations Read** (`integrations_read`) is view-only
  and is **not** sufficient. Give the user the Roles page of **their own**
  Datadog site — `https://<UI host>/organization-settings/roles` (e.g.
  `app.datadoghq.com` for US1, `app.datadoghq.eu` for EU, `us3.datadoghq.com`
  for US3) — and the reference
  <https://docs.datadoghq.com/account_management/rbac/permissions/>. Once an
  admin grants the permission, re-run this step.
- **`401` / not authenticated** — the pup token itself is missing, stale, or
  org-unbound; re-authenticate (`pup auth login`, see Phase 0) and re-run.

### Poll until the consent lands (no user confirmation)

```bash
python3 -I -B <skill>/scripts/github_copilot_setup.py wait --account-name "<LOGIN>" --interval 5 --timeout 300
```

Start this **as a background process immediately after the consent screen is
open** (background it with the host's process manager or a shell `&`), then
continue when it reports the account — the poll is the confirmation, so never
ask the user to report back that they clicked Authorize, and never block on a
"done". Always pass `--account-name` with the login from Phase 1: the wait exits
0 as soon as an account with that name is listed, **even if the consent landed
before the poll started**, and ignores unrelated accounts. That is safe because
`authorize` refuses a name that is already connected. `--json` emits
`site` and `new_accounts`. Exit 2 means it timed out: re-check this phase's
caveats and re-run `authorize --open` and `wait`. Pass the same
`--site`/`--pup`/pup-session `--org <session>` as the rest of the flow.

### Hand off

Tell the user: **GitHub Copilot is connected** — name the account that
landed. Copilot usage metrics (`github_copilot.usage.*`) start flowing.

**If the metrics never appear** (GitHub-side checks, in order):

1. Re-run `check-org <github-org>` for each org and act on its verdict as in
   Phase 1's *Is the Copilot usage metrics policy on?*. The policy is an org
   (or enterprise) policy; `github.com/settings/...` pages never show it.
2. The GitHub user who approved the consent must **own the orgs or enterprise**
   (Phase 1) — it collects only what that user can read — and those orgs must not
   block the app via OAuth App access restrictions.
3. Enterprise users: licenses managed at the enterprise level need the
   enterprise name on the account (Phase 1's `--enterprise-name`).

Cleanup, when asked:
`python3 -I -B <skill>/scripts/github_copilot_setup.py delete-account <id>`
removes one account (it re-lists afterwards and confirms the removal);
`accounts` shows what exists first.

## Getting the Most Out of Your Integration

Once `wait` reports the account, confirm that the orgs' metrics arrive without blocking
the conversation.

If Phase 1 found no enabled org and no enterprise name was given, there is nothing
to check: skip the background check and tell the user the account is connected,
but metrics arrival cannot be verified until an org has the Copilot usage metrics
policy on (or an enterprise name is set). Otherwise:

1. Start a background subagent with this task. Pass one `--github-org` for each
   org Phase 1 found enabled and `--enterprise-name` if one was given. The
   subagent runs in a fresh shell, so give it the **same Datadog context setup
   used** — the same `DD_SITE=<site>` prefix or `--site`, and the same `--pup`
   and pup session `--org <session>` — and include them in its command; otherwise `verify` falls back
   to the default site and could check the wrong one:

   > Run `DD_SITE=<site> python3 -I -B <skill>/scripts/github_copilot_setup.py verify --github-org "<ORG1>" --github-org "<ORG2>" [--pup <path>] [--org <session>]`
   > (drop the `DD_SITE=` prefix and the bracketed flags that setup did not use)
   > with a command timeout of at least 16 minutes. It polls Datadog for up to
   > 15 minutes and prints JSON. Do not retry it or change anything. Report back
   > its `status` (`succeeded`, `partial`, `failed`, or `error`), `arrived`,
   > `pending`, `links`, `message`, and any `error` object.

2. Tell the user, in these words:

   > "Your GitHub Copilot integration is set up! I'm now checking in the
   > background that your Copilot metrics are arriving in Datadog. This can take
   > up to 15 minutes, and I'll let you know as soon as they show up. Feel free to
   > keep working in the meantime."

3. When the subagent reports, relay the result:

- **`succeeded`: celebrate.** Give a short, enthusiastic message such as "Your github
  Copilot metrics now show up in Datadog!", the `links`
  (one Metrics Explorer link per org that arrived), and two or three
  one-sentence next steps, for example: open the **GitHub Copilot Overview**
  dashboard to see seat usage and adoption; set a monitor on
  `github_copilot.organizations.billing.seats.*` to catch seat pressure; compare
  daily active users across teams. Only describe what the script confirmed.
- **`partial`:** celebrate the orgs in `arrived` the same way, then add one
  sentence naming the `pending` orgs and that they can take longer.
- **`failed`:** stay encouraging. Copilot reports usage daily, so the first data
  can take a while; suggest checking `github_copilot.*` metrics later, and the
  **If the metrics never appear** checks above if they still don't show up.
- **`error`:** report the reason. For `datadog_read_not_permitted`, ask before
  running `pup auth login --extra-scopes metrics_read,timeseries_query`.

If no subagent tool is available, run the command yourself and report the same
way. The script looks for a Copilot seat-count point
(`github_copilot.organizations.billing.seats.total`, tagged `org_name:<github-org>`, or
the enterprise one tagged `enterprise_name:<enterprise>`) **stamped after it
started**. Datadog records a new seat count about every 10 minutes, so data
left over from an earlier install never counts.
