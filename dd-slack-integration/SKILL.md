---
name: dd-slack-integration
description: Connects a commercial Slack workspace to Datadog and adds a channel to it, so the user can receive Datadog alerts and notifications in Slack and talk to Datadog by mentioning @Datadog. Checks whether a Slack workspace is already connected first, and can add a channel to an existing connection without reconnecting the workspace. Slack for Government (govcloud) workspaces are pointed to the Datadog UI instead, since this flow covers commercial workspaces only. Use when the user wants Slack alerts/notifications from Datadog, wants to message @Datadog in Slack, or asks to connect or configure the Datadog Slack integration. Not for creating monitors or routing specific alerts to the channel.
metadata:
  version: "1.0.0"
  author: datadog-labs
  repository: https://github.com/datadog-labs/agent-skills
  tags: datadog,slack,integration,oauth,notifications
  alwaysApply: "false"
  tools: pup,python3
---

# Datadog Slack integration

Connect Slack to Datadog in three user-driven moves: the **workspace type** they confirm,
the **OAuth grant** on that Slack workspace, then the **channel** they pick, added through
the Datadog API. Once a channel is connected, the user can talk to Datadog directly in
Slack by mentioning **@Datadog**, and Datadog notifications can target that channel. This
flow handles **commercial Slack workspaces** (slack.com) only.

The workspace choice, the OAuth grant and the channel choice are all the user's — this skill
never authorizes on their behalf, never picks a channel for them, and never writes
credentials. All Datadog API calls go through `pup api`, which authenticates with the
user's own session (a `403` means a missing permission; see **Important Notes**). Nothing
here waits on the user to report back: the connect is detected by a background poll.

`scripts/slack_setup.py` owns site resolution, authorize-URL minting, account discovery,
and the API calls — do not reproduce that logic yourself. In the commands below, replace
`<skill>` with this skill's directory. The API reference is
<https://docs.datadoghq.com/api/latest/slack-integration/>. Its exit codes: **0** success,
**1** an error to report (pup missing or unauthenticated, a 403, bad input), **2** an
expected "not there yet" (no account, `wait` timed out, channel not listed), **3** no
browser.

**Account** = the name Datadog gives one connected Slack workspace (e.g. `Main_Account`);
it is what `--account` takes. "Account" means only that here.

Copy and tick off:

- [ ] 1. Preflight: `pup auth status`, `command -v python3`
- [ ] 2. `accounts`: existing account? → stop / add a channel (go to 7) / new workspace
- [ ] 3. Slack for Government? → yes: point to the UI and stop
- [ ] 4. Enterprise Grid/Select and org owner? → ask about audit logs
- [ ] 5. `authorize --open`
- [ ] 6. `wait` in the background until the account appears
- [ ] 7. Ask for the channel, then `add-channel` → `listed: true`
- [ ] 8. `test-notification` → exit 0
- [ ] 9. (If chosen in 4) `audit-logs --open`
- [ ] 10. Final report

## Phase 0: Preflight

**Datadog for Government is not supported.** If `pup auth status` reports a
`ddog-gov.com` site, stop and tell the user to set up the integration from the
Datadog app instead. The script refuses Government sites too.

**`pup` and the site.** This flow drives the Datadog API through `pup api`, and the site is
derived from the user's pup authentication, so the authorize URL and the API calls hit the
same Datadog environment they are logged into:

```bash
pup auth status
```

- `authenticated: true` → use the reported `site`; continue.
- `authenticated: false` → **do not declare the user unauthenticated yet.** pup reports the
  session for the site in `DD_SITE` (default `datadoghq.com`) or the named `--org` session —
  a user logged into another site (e.g. staging `datad0g.com`) shows as unauthenticated
  under the default. Ask which site/org they authenticated for, then re-run with
  `DD_SITE=<site>`. If they authenticated a named (org-keyed) pup session instead, pass
  `--org <name>` to the script's commands — it threads the flag through both `pup auth
  status` and the `pup api` calls. Still `false` → **stop** and ask them to run
  `DD_SITE=<site> pup auth login` (it needs a browser). Never guess a site.
- `pup` is missing (the script exits 1 with "pup not found") → ask before installing it:
  `brew tap datadog-labs/pack && brew install datadog-labs/pack/pup` (macOS or Linux
  with Homebrew),
  `curl -fsSL https://raw.githubusercontent.com/DataDog/pup/main/install.sh | sh`, or
  `npm install -g @datadog/pup` (Windows or anywhere with npm); see
  <https://github.com/DataDog/pup>. Then run `pup auth login`.
- `pup` returns non-JSON → the `pup` on `PATH` may not be the Datadog CLI
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

Three questions come before anything is minted: whether a Slack account already exists,
whether the workspace is Slack for Government, and whether it is an Enterprise org that
wants audit logs.

### 2. Is a Slack account already connected?

Before asking anything about the workspace, find out whether the org already has a Slack
account — the user may not need this flow at all. `accounts` is a read-only GET:

```bash
python3 -I -B <skill>/scripts/slack_setup.py accounts
```

Pass the same `--pup`/`--site`/`--org` context as the rest of the flow (Phase 0).

- **Exit 2 / "no Slack account is configured"** → nothing is connected yet (a fresh org).
  Continue to the govcloud question below.
- **Exit 1** → an error, not a fresh org: report it and fix it (Phase 0, Important Notes)
  before continuing.
- **Exit 0 with account name(s)** → report **exactly what was discovered** before asking
  anything else. Name every account and how many channels each one has — the plain output
  lists both, and `--json` gives the full `accounts`/`channels` lists when the channel list
  is truncated. For example:

  > We discovered you already have these Slack account(s) configured:
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
    — rather than letting `add-channel` hit its ambiguous-account refusal. Option (2) skips
    the govcloud and Enterprise questions; if the user later wants audit logs, ask the
    Enterprise questions, then run `audit-logs`.
  - **(3) set up a new Slack workspace** → continue with the existing flow: the govcloud
    question below, then the Phase 2 OAuth flow.

  If the user wants to re-authorize a workspace that `accounts` already lists, `wait`
  cannot detect it, because the account name does not change. After the consent screen,
  ask the user once whether Slack showed success, then confirm with `accounts` and
  `add-channel`.

### 3. Is this a Slack for Government workspace?

Before minting anything, ask the user — with the host's native selector — whether the Slack
workspace they want to connect is **Slack for Government** (govcloud). Never guess: a
commercial org can hold a Slack for Government workspace and vice versa.

- **Yes (Slack for Government)** → this automated flow supports **commercial Slack
  workspaces only**. Tell the user to set up the Slack for Government integration from the
  Datadog UI instead (the Datadog app → **Integrations** → **Slack**), and **stop here**:
  do not mint an authorize URL and do not continue with the rest of this flow.
- **No (a commercial workspace, slack.com)** → continue to Phase 2.

### 4. Is this an Enterprise Grid or Select org — and do you want audit logs?

Ask two questions (host's native selector or free text), and never guess either:

1. Is the Slack workspace being connected part of an **Enterprise Grid or
   Enterprise Select** org?
2. Is the user the **Slack organization owner**?

Audit-log streaming is optional, available only for Enterprise Grid/Select orgs, and
the audit-logs app is installed by the organization owner — see the public docs:
<https://docs.datadoghq.com/integrations/slack/?tab=datadogforslack#enterprise-grid-audit-logs>.

- **Not an Enterprise org, or the user is not the organization owner** → tell them
  audit logs are not part of this setup and continue with the
  standard flow. Do not ask again.
- **Enterprise org and the user is the organization owner** → ask whether they want to
  enable audit logs. If **yes**, remember it as a follow-up action after the workspace
  connect and channel setup — Phase 2's final step mints the separate audit-logs OAuth
  URL. If **no**, continue with the standard flow.

## Phase 2: Perform the OAuth flow and validate

### 5. Mint and open the Slack OAuth authorize URL

```bash
python3 -I -B <skill>/scripts/slack_setup.py authorize --open
```

- The command resolves the site (Phase 0), mints a fresh commercial authorize URL from
  the Datadog API (`GET https://api.<site>/auth/integration/slack/install/authorize-url`
  through `pup api`), and opens it; add `--json` to capture `site`, `url` and `authorize_url_host` in
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

Two caveats to hand to the user:

- The browser that finishes the grant must have a **Datadog app session** for the
  resolved site. Slack redirects back to
  `/auth/integration/slack/install/complete`, and that callback binds the workspace to
  the org from the logged-in session — a browser logged out of Datadog cannot finish the
  connect.
- The minted URL's `state` is a **CSRF nonce that expires after about five minutes**. Open
  it promptly; if it expires, re-run this step for a fresh URL instead of reusing the old
  one.

### 6. Poll until the connect lands (no user confirmation)

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
(Datadog app session in that browser, URL not expired) and re-run this step. Exit 1 is an
error (permission, auth, pup): report it instead of re-minting. Pass the same
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

### 7. Ask which Slack channel and add it

The channel is the user's choice — ask with the host's native selector or free text, and
wait for the answer. First get real suggestions from the connected workspace:

```bash
python3 -I -B <skill>/scripts/slack_setup.py suggest-channels --json
```

Offer its `suggestions` (up to three channels, best first, skipping ones already
connected) as the options, plus an Other response for any channel name. If the user types
part of a name, re-run with `--query "<what they typed>"` and offer those matches. Pass
`--account <name>` when several accounts are configured, as for `add-channel`. If the
command fails or returns no suggestions, ask for the channel name as free text, suggesting
something like `#ops`, `#alerts`, or an existing channel their team watches. A missing
leading `#` is added automatically; the user can name any channel in the connected
workspace. Datadog can post to a private channel only after the user runs
`/invite @Datadog` in it. Then add it:

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

### 8. Send a test notification

This verifies the wiring end to end — a Datadog message should appear in the channel
within moments:

```bash
python3 -I -B <skill>/scripts/slack_setup.py test-notification '#ops'
```

One configured account is used automatically; several → the command refuses and lists
them, so pass `--account <name>`. Exit 0 ("test notification sent") means the message was
sent — tell the user to check the channel. If it errors, report the message plainly (a 403 carries the RBAC
guidance from Important Notes); the message arriving in Slack is the real confirmation.

### 9. Audit logs (optional, enterprise follow-up)

If Phase 1 established an Enterprise Grid/Select org, the user is the organization
owner, and they chose to enable audit logs, run this after the channel setup:

```bash
python3 -I -B <skill>/scripts/slack_setup.py audit-logs --open
```

- This mints the **separate** audit-logs OAuth authorize URL — a different grant from
  the workspace connect, asking for the `auditlogs:read` user scope — and opens it so
  the user approves it in Slack. Anything other than a slack.com URL with
  `auditlogs:read` is an error: report it, and do not open it by hand.
- A **401 here when the workspace mint worked** means this Datadog site has not enabled
  token authentication on the audit-logs mint yet — surface that plainly; the user can
  complete the audit-logs connect from the Datadog UI in the meantime.
- This skill has no check that the audit-logs grant landed: once the URL is open, report
  audit logs as "requested, not verified".

### 10. Final report

```markdown
- Slack account: <account> (new | existing)
- Channel: <#channel> — listed: <true|false>
- Test notification: <sent | failed: <error>>
- Audit logs: <requested, not verified | skipped | not eligible>
- Next: use `@slack-<channel>` in a monitor message
```

Only when the account, channel and test notification lines are all successes, add a short
enthusiastic line, e.g. "🎉 Slack is connected to your Datadog org, and #ops is set up!"
After any failure, report the error instead.

**The user can now receive Datadog notifications in Slack.** In any monitor, use
**`@slack-<channel-name>`** in the notification message — for example `@slack-ops` in a
monitor's Say what's happening text sends that alert directly to the connected `#ops`
channel. With several Slack accounts, use `@slack-<account>-<channel-name>`. The same
channel can be targeted from other notifications too, so alerts land
where the team already works.

Once a channel is connected, the user can also **talk to Datadog directly in Slack by
mentioning @Datadog** in that channel — asking questions and running commands without
leaving Slack. For more on what the connected channel unlocks, see the Datadog Slack
integration docs: <https://docs.datadoghq.com/integrations/slack/>.

If they want more channels, repeat the channel and add-channel steps above; if they want
monitors or notifications wired to the channel, that is the monitors/notifications setup,
not this skill.

## Important Notes

`authorize` appends the guidance below to the API's own `errors` array, so relay that text
rather than inventing a route. When classifying by hand:

- **Permission / `403` / `Forbidden` / `manage_integrations`** — the user's Datadog session
  is not allowed to authorize the Slack workspace connect. Tell them they need
  **Integrations Manage** (`manage_integrations`, "Install, uninstall, and configure
  integrations") in their Datadog org: an org admin grants it through RBAC, and it is on
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
