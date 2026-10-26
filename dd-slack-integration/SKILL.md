---
name: dd-slack-integration
description: Connect a Slack workspace to Datadog and wire up a channel so the user can talk to Datadog with @Datadog in Slack and receive Datadog notifications there - checks for an existing Slack integration first, asks whether the workspace is Slack for Government (commercial Slack only), mints and opens the Slack OAuth authorize URL through the Datadog API, polls until the connect lands, then adds the chosen channel. Use when the user wants Slack alerts or notifications from Datadog, wants to message @Datadog in Slack, or asks to connect or configure the Datadog Slack integration.
metadata:
  version: "1.0.0"
  author: datadog-labs
  repository: https://github.com/datadog-labs/agent-skills
  tags: datadog,slack,integration,oauth,notifications,onboarding
  alwaysApply: "false"
---

# Datadog Slack integration

Connect Slack to Datadog in three user-driven moves: the **workspace type** they confirm,
the **OAuth grant** on that Slack workspace, then the **channel** they pick, added through
the Datadog API. Once a channel is connected, the user can talk to Datadog directly in
Slack by mentioning **@Datadog**, and Datadog notifications can target that channel. This
flow handles **commercial Slack workspaces** (slack.com) only.

The flow starts with a pre-check (Phase 1): if the org already has a Slack integration, the
skill reports exactly what is configured and asks whether to stop, to connect another
channel to that existing account (skipping the OAuth mint entirely), or to set up a new
Slack workspace. Phase 1 then asks a gate question: a **Slack for Government** (govcloud)
workspace is not something this flow sets up — those users are pointed at the Datadog UI
and the flow stops.

The workspace choice, the OAuth grant and the channel choice are all the user's — this skill
never authorizes
on their behalf, never picks a channel for them, and never writes credentials. All
Datadog API calls go through `pup api`, which authenticates with the user's own session;
that session needs the **Integrations Manage** (`manage_integrations`) permission, which
is what authorizes connecting the workspace (see **Important Notes** when it is
missing). Nothing here waits on the user to report back: the connect is detected by a
background poll.

`scripts/slack_setup.py` owns site resolution, authorize-URL minting, account discovery,
and the API calls — do not reproduce that logic yourself. The API reference is
<https://docs.datadoghq.com/api/latest/slack-integration/>.

## Phase 0: Preflight

**`pup` and the site.** This flow drives the Datadog API through `pup api`, and the site is
derived from the user's pup authentication, so the authorize URL and the API calls hit the
same Datadog environment they are logged into:

```bash
pup auth status
```

- `authenticated: true` → use the reported `site`; continue.
- `authenticated: false` → **do not declare the user unauthenticated yet.** pup reports the
  session for the site in `DD_SITE` (default `datadoghq.com`) or the named `--org` session —
  a user logged into another Datadog site (e.g. `datadoghq.eu`) shows as unauthenticated
  under the default. Ask which site/org they authenticated for, then re-run with
  `DD_SITE=<site>`. If they authenticated a named (org-keyed) pup session instead, pass
  `--org <name>` to the script's commands — it threads the flag through both `pup auth
  status` and the `pup api` calls. Still `false` → **stop** and ask them to run
  `DD_SITE=<site> pup auth login` (it needs a browser). Never guess a site.
- `pup` is missing or returns non-JSON → the `pup` on `PATH` may not be the Datadog CLI
  (e.g. a same-named proto tool shadowing it). Point the script at the real binary with
  `--pup <path-to-pup>`; if the user knows their site, `--site <site>` bypasses pup for
  the authorize URL, but the mint and the API steps still need a working `pup api`.

**Python.** The flow runs `scripts/slack_setup.py`, so confirm `python3` is on `PATH`
before starting:

```bash
command -v python3 || echo "MISSING python3"
```

**Browser.** The OAuth grant needs a browser logged into both Slack and the Datadog app.
If there is no browser (a headless host), Phase 2 drops `--open` and prints the URL for
the user to open where they are logged in.

**Permission precheck.** Connecting the workspace needs the **Integrations Manage**
(`manage_integrations`) permission in the user's Datadog org — an org admin grants it
through RBAC, and it is on the default Datadog Standard Role. A session without it fails
the mint with a `403`; **Important Notes** carries the full error mapping and the RBAC
reference.

## Phase 1: Determine Scope

Two questions come before anything is minted: whether the org already has a Slack workspace
connected, and whether the workspace they want is commercial Slack.

### Is a Slack workspace already connected?

Before asking anything about the workspace, find out whether the org already has a Slack
integration — the user may not need this flow at all. `accounts` is a read-only GET:

```bash
python3 -I -B <skill>/scripts/slack_setup.py accounts
```

Pass the same `--pup`/`--site`/`--org` context as the rest of the flow (Phase 0).

- **Exit 2 / "no Slack account is configured"** → nothing is connected yet (a fresh org).
  Continue to the govcloud question below.
- **Exit 0 with account name(s)** → report **exactly what was discovered** before asking
  anything else. Name every account and how many channels each one has — the plain output
  lists both, and `--json` gives the full `accounts`/`channels` lists when the channel list
  is truncated. For example:

  > We discovered you already have these Slack integration(s) configured:
  > **Main_Account** (3 channels).

  Then ask a three-way question with the host's native selector:

  > Would you like to: (1) stop, (2) connect a new channel to this account, or (3) set up a
  > new Slack workspace?

  - **(1) stop** → end the flow here; their existing integration already covers them. Do not
    modify it, and do not add channels to it in this flow.
  - **(2) connect a new channel to this account** → the workspace is already connected, so
    **skip the mint and the OAuth grant entirely** and go straight to channel setup: ask
    which Slack channel they want (the same question as Phase 2's channel step), then run
    the `add-channel` flow against the discovered account. If discovery found **several**
    accounts, ask which one first — list them and pass the user's pick as `--account <name>`
    — rather than letting `add-channel` hit its ambiguous-account refusal.
  - **(3) set up a new Slack workspace** → continue with the existing flow: the govcloud
    question below, then the Phase 2 OAuth flow.

### Is this a Slack for Government workspace?

Before minting anything, ask the user — with the host's native selector — whether the Slack
workspace they want to connect is **Slack for Government** (govcloud). Never guess: a
commercial org can hold a Slack for Government workspace and vice versa.

- **Yes (Slack for Government)** → this automated flow supports **commercial Slack
  workspaces only**. Tell the user to set up the Slack for Government integration from the
  Datadog UI instead (the Datadog app → **Integrations** → **Slack**), and **stop here**:
  do not mint an authorize URL and do not continue with the rest of this flow.
- **No (a commercial workspace, slack.com)** → continue to Phase 2.

## Phase 2: Perform the OAuth flow and validate

### Mint and open the Slack OAuth authorize URL

```bash
python3 -I -B <skill>/scripts/slack_setup.py authorize --open
```

- Replace `<skill>` with this skill's directory. The command resolves the site (Phase 0),
  mints a fresh commercial authorize URL from the Datadog API
  (`GET https://api.<site>/auth/integration/slack/install/authorize-url` through `pup
  api`), and opens it; add `--json` to capture `site`, `url` and `authorize_url_host` in
  the transcript.
- If Phase 0 needed a `DD_SITE=<site>` prefix, pass `--site <site>` explicitly rather than
  relying on the environment carrying through. If it needed `--org <name>` (an org-keyed
  pup session), pass the same `--org <name>` to every script command in this phase.
- Headless (no browser): drop `--open`, print the returned URL, and ask the user to open
  it where they are logged into both Slack and the Datadog app.

The URL lands the user directly on the **Slack workspace consent screen** (slack.com), with
the same redirect back to `/auth/integration/slack/install/complete`. Never complete the
grant on their behalf — but **do not wait for them to report back either**: start the poll
below as soon as the consent screen is open and let it notice the connect.

The minted URL is verified before it is ever opened: `authorize` refuses to open (or print)
a URL whose host is not Slack, so nothing but a genuine Slack consent screen reaches the
browser.

Two caveats to hand to the user:

- The browser that finishes the grant must have a **Datadog app session** for the
  resolved site. Slack redirects back to
  `/auth/integration/slack/install/complete`, and that callback binds the workspace to
  the org from the logged-in session — a browser logged out of Datadog cannot finish the
  connect.
- The minted URL's `state` is a **CSRF nonce that expires after about five minutes**. Open
  it promptly; if it expires, re-run this step for a fresh URL instead of reusing the old
  one.

### Poll until the connect lands (no user confirmation)

```bash
python3 -I -B <skill>/scripts/slack_setup.py wait --interval 10 --timeout 300
```

Start this **as a background process immediately after the consent screen is open**
(background it with the host's process manager or a shell `&`), then continue when it
reports the account. It snapshots the configured accounts on its first call and exits 0
printing the new account name(s) as soon as the connect lands — the poll is the
confirmation, so never ask the user to report back that they clicked Allow, and never
block on a "done". It prints nothing per poll; `--json` emits `site`, `accounts` and
`account`. Exit 2 means it timed out with no new account: re-check the mint caveats above
(Datadog app session in that browser, URL not expired) and re-run this step. Pass the same
`--site`/`--pup`/`--org` as the rest of the flow; `--account <name>` waits for one specific
account name and ignores unrelated new accounts.

`accounts` remains the one-shot check — use it when the connect predates this run, or to
re-check by hand after a timeout:

```bash
python3 -I -B <skill>/scripts/slack_setup.py accounts
```

- Exits 0 and prints the **account name(s)** (e.g. `Main_Account`) and configured channels
  → OAuth is done; continue to the channel step below. This is the same account name `wait`
  reported.
- Exits 2 with "no Slack account is configured" → the connect hasn't landed yet; run the
  `wait` poll rather than asking the user for a status.
- The account name comes from the integration itself, never guessed: it is what the
  channel API path needs, and it is not always `main`.

### Ask which Slack channel

The channel is the user's choice — ask with the host's native selector or free text, and
wait for the answer. Suggest something like `#ops`, `#alerts`, or an existing channel
their team watches. A missing leading `#` is added automatically; the user can name any
channel in the connected workspace.

### Add the channel and verify

```bash
python3 -I -B <skill>/scripts/slack_setup.py add-channel '#ops'
```

- One configured account → it is used automatically; several → the command refuses and
  lists them; pass `--account <name>` (the name the poll reported) to choose.
- This POSTs `{"name": "#ops"}` to
  `https://api.<site>/api/v1/integration/slack/configuration/accounts/<account>/channels`
  through `pup api`, then re-lists the channels and reports whether the new one shows up.
- Exit 0 with `listed: true` → done. Anything else → report the error plainly (the script
  surfaces the API's own `errors` array); do not retry silently or claim success.

## Getting the Most Out of Your Integration

Once a channel is connected, the user can **talk to Datadog directly in Slack by mentioning
@Datadog** in that channel — asking questions and running commands without leaving Slack.
The same channel can be targeted from Datadog monitors and notifications, so alerts land
where the team already works. For more on what the connected channel unlocks, see the
Datadog Slack integration docs: <https://docs.datadoghq.com/integrations/slack/>.

If they want more channels, repeat the channel and add-channel steps above; if they want
monitors or notifications wired to the channel, that is the monitors/notifications setup,
not this skill.

## Important Notes

`authorize` appends the guidance below to the API's own `errors` array, so relay that text
rather than inventing a route. When classifying by hand:

- **Permission / `403` / `Forbidden` / `manage_integrations`** — the user's Datadog session
  is not allowed to authorize the Slack workspace connect. Tell them they need
  **Integrations Manage** (`manage_integrations`, "Install, uninstall, and configure
  integrations") in your Datadog org: an org admin grants it through RBAC, and it is on
  the default Datadog Standard Role. The related **Integrations Read** (`integrations_read`) is
  view-only and is **not** sufficient. Reference:
  <https://docs.datadoghq.com/account_management/rbac/permissions/>. Once an admin grants
  the permission (re-login if the session was cached), re-run the mint for a fresh URL.
- **`401` / not authenticated** — the pup token itself is missing or stale; re-authenticate
  (`pup auth login`, see Phase 0 for the site/org session) and re-run the mint.
- **No mint route at all** (a `404` where
  `/auth/integration/slack/install/authorize-url` is not deployed on this site) — this
  Datadog site does not expose the authorize-url route. Surface the error plainly: this
  skill has no fallback route, so report it rather than routing the user elsewhere.
