---
name: dd-pagerduty-onboarding
description: Connects PagerDuty to Datadog so Datadog monitors can trigger and resolve incidents on the user's PagerDuty services. Checks which PagerDuty services are already connected first and asks whether to continue; the user picks the services to connect on PagerDuty's own install screen, the skill confirms they landed, and it can send a test page that opens and resolves a real incident. Use when the user wants PagerDuty alerts/notifications from Datadog, or asks to connect or configure the Datadog PagerDuty integration.
metadata:
  version: "1.0.0"
  author: datadog-labs
  repository: https://github.com/datadog-labs/agent-skills
  tags: datadog,pagerduty,integration,oauth,notifications,incidents
  alwaysApply: "false"
  tools: pup,python3
---

# Datadog PagerDuty integration

Connect PagerDuty to Datadog in two user-driven moves: the **install** on PagerDuty's
install screen (the user picks which PagerDuty service(s) to connect), and Datadog writing
the integration keys as services when PagerDuty redirects back. Once a service is
connected, Datadog can trigger and resolve incidents on it.

The install is the user's — this skill never installs on their behalf and never writes
credentials. All `/api/` calls go through `pup api` with the user's own session; the
install-URL mint is the one call made outside `pup api`, and the script handles it (its
bearer stays in process memory only — never in argv, on disk, or in output).

`scripts/pagerduty_setup.py` owns site resolution, install-URL minting, service discovery,
and the API calls — do not reproduce that logic yourself. The API reference is
<https://docs.datadoghq.com/api/latest/pagerduty-integration/>.

This is a hands-on setup: run the commands yourself rather than handing the user a list,
keep them in the loop, and let the user's answer decide whether an existing integration is
extended.

**Script exit codes** (every subcommand): `0` success · `1` error — relay the `error:`
line · `2` an expected "not yet" outcome (`services`: nothing connected; `wait`: timed out;
`test-page`: a step did not happen) · `3` `authorize --open` could not open a browser.

Checklist:

- [ ] 0. `pup`, `python3` present; site confirmed; `pup auth status` authenticated on the right org
- [ ] 1. `services` → none connected, or the user chose "continue"
- [ ] 2. `authorize [--continue] --open` → install screen open
- [ ] 3. `wait` started in the background immediately → new service reported
- [ ] 4. Test page: asked; sent or skipped → result reported
- [ ] 5. Final message sent

## Phase 0: Preflight

**Datadog for Government is not supported.** If `pup auth status` reports a
`ddog-gov.com` site, stop and tell the user to set up the integration from the
Datadog app instead. The script refuses Government sites too.

**Tools.** `pup` and `python3` are required; a browser is needed to finish PagerDuty's
install screen:

```bash
command -v pup     >/dev/null 2>&1 && echo "pup: available"     || echo "MISSING pup"
command -v python3 >/dev/null 2>&1 && echo "python3: available" || echo "MISSING python3 - the setup script needs it"
```

If `pup` is missing, ask before installing it:
`brew tap datadog-labs/pack && brew install datadog-labs/pack/pup` (macOS or Linux with
Homebrew), `curl -fsSL https://raw.githubusercontent.com/DataDog/pup/main/install.sh | sh`,
or `npm install -g @datadog/pup` (Windows or anywhere with npm); see
<https://github.com/DataDog/pup>. Then run `DD_SITE=<site> pup auth login`.

If `python3` is missing or the user declines installing `pup`, stop and point them to the
PagerDuty integration tile in the Datadog app to connect it manually.

**Session and site.** The flow runs on the user's **default** pup session: the Datadog org
is whichever one that session's OAuth token belongs to, and the site comes from
`DD_SITE` (the script has no site flag; it reads the site from that session). Use no
named (`--org`) sessions — one default session means every command, including any `pup`
command handed back to the user, hits the same org.

```bash
DD_SITE=<site> pup auth status
```

- Ask which Datadog site they use if it is not already known (e.g. `datadoghq.com`,
  `datadoghq.eu`, `us3.datadoghq.com`, `us5.datadoghq.com`, `ap1.datadoghq.com`, or staging
  `datad0g.com`). Never guess a site.
- `authenticated: true` → confirm with the user, with the host's native selector, that
  this session is logged into the Datadog org they want to connect PagerDuty to. Yes →
  continue. No → have them log in again (next bullet) and pick the right org.
- `authenticated: false`, or the wrong org → **stop** and ask them to run
  `DD_SITE=<site> pup auth login` (it needs a browser) and **pick the org they want on
  Datadog's login screen** — that makes it the default session's org. Warn them first if
  they were already logged in: this replaces their existing default pup session.
- **Prefix every command in this flow with `DD_SITE=<site>`** (each shell command starts
  fresh, so it does not carry over on its own). The script resolves the site from
  `pup auth status`, which reads it.
- `pup` returns non-JSON → the `pup` on `PATH` may not be the Datadog CLI (e.g. a
  same-named proto tool shadowing it). Point the script at the real binary with
  `--pup <path-to-pup>`, and pass it to every script command.

**Permissions.** The pup session needs the **Integrations Manage** (`manage_integrations`)
permission on the Datadog org — it is what authorizes the connect. It sits on the default
Datadog Standard Role; an org admin grants it through RBAC. The related **Integrations
Read** (`integrations_read`) is view-only and is **not** sufficient. `test-page` also needs
**Monitors Write** (`monitors_write`), because it creates a throwaway monitor; without it,
skip the test page and have the user confirm the connect in PagerDuty. Reference:
<https://docs.datadoghq.com/account_management/rbac/permissions/>.

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

Before minting anything, find out whether the org already has a PagerDuty integration —
the user may not want this flow at all. `services` is a read-only GET:

```bash
DD_SITE=<site> python3 -I -B <skill>/scripts/pagerduty_setup.py services
```

Replace `<skill>` with this skill's directory, here and in every later command.

- **Exit 2** → nothing is connected yet (a fresh org). Continue to Phase 2 as normal.
- **Exit 1** → an error (e.g. not authenticated, or a `403`); relay it and fix it before
  continuing — never treat it as "nothing connected".
- **Exit 0 with service names** → report **exactly what was discovered** before asking
  anything else. Name every service — the plain output lists them, and `--json` gives the
  full list. For example:

  > We discovered these PagerDuty services are already connected: **ops-pages**,
  > **customer-incidents**.

  Then ask, with the host's native selector:

  > Would you like to: (1) stop — the existing services already cover you, or (2) continue
  > and set up new services?

  - **(1) stop** → end the flow here; do not modify the existing services.
  - **(2) continue** → run Phase 2 with `--continue` (the script refuses the mint without
    it when services exist, so the user's answer is what unlocks it).

**Which services to integrate** is the user's choice, made on PagerDuty's install screen
in Phase 2: they pick and connect the service(s) there. Do not try to select services for
them.

## Phase 2: Run the install flow, add services, and validate

### Mint and open the PagerDuty install URL

```bash
DD_SITE=<site> python3 -I -B <skill>/scripts/pagerduty_setup.py authorize --continue --open
```

- Drop `--continue` when Phase 1 found nothing connected (it is only needed to proceed
  past the existing-services guard). The command resolves the site (Phase 0), mints a
  fresh install URL, and opens it.
- Headless (no browser): drop `--open`, print the returned URL, and ask the user to open
  it where they are logged into both PagerDuty and the Datadog app.
- **Exit 3** → no browser opened. Give the user the URL from stderr, and start `wait`
  anyway.
- The URL carries a short-lived, org- and user-bound `state`: hand it only to the user,
  and never save, share, or reuse it.

The URL lands the user directly on **PagerDuty's install screen for the Datadog app**:
they log in to PagerDuty, pick the service(s) to connect, and install. Start the poll
below as soon as the install screen is open and let it notice the install.

Two caveats to hand to the user:

- **The install needs a PagerDuty account** that can install the Datadog app and connect
  its services (typically a PagerDuty admin or account owner). Without one, the flow stops
  at the install screen — surface that plainly rather than retrying.
- The minted URL's `state` expires after about **five minutes**. Open it promptly; if it
  expires, re-run this step for a fresh URL instead of reusing the old one. The browser
  that finishes the install must also have a **Datadog app session** for the resolved site:
  PagerDuty redirects back to Datadog, which writes the integration keys with that
  session's org.

### If the mint fails: read the error before re-routing

`authorize` appends the guidance below to the error, so relay that text rather than
inventing a route. When classifying by hand:

- **Existing-services refusal** (only when `--continue` was not passed) → go back to
  Phase 1's ask; the user decides.
- **Permission / `403` / `Forbidden` / `manage_integrations`** → the session lacks
  Integrations Manage; see Permissions in Phase 0. Once an admin grants it, re-run this
  step for a fresh URL.
- **`401` / not authenticated** — the pup token itself is missing, stale, or org-unbound;
  re-authenticate (`DD_SITE=<site> pup auth login`, see Phase 0) and re-run.
- **Login redirect** — the site does not accept token authn on the mint route
  yet (it 307s to `/account/login`). Fallback: the **PagerDuty integration tile** in the
  Datadog app — the user opens the tile and clicks "Connect to PagerDuty", which runs the
  same vendor flow with their browser session. Surface this plainly.

### Poll until the install lands (no user confirmation)

```bash
DD_SITE=<site> python3 -I -B <skill>/scripts/pagerduty_setup.py wait --interval 5 --timeout 300
```

Start this **as a background process immediately after the install screen is open**
(background it with the host's process manager or a shell `&`), then continue when it
reports the new service(s). It snapshots the connected services on its first call and
exits 0 printing the new service name(s) as soon as PagerDuty's redirect lands — the poll
is the confirmation, so never ask the user to report back that they clicked install, and
never block on a "done". It prints nothing per poll; `--json` emits `site` and
`new_services`.

- **Exit 2 (timed out)** → run `services` before retrying. If the service the user picked
  is listed, the install landed — before the poll started, or as a re-install of an
  already-connected service, which `wait` does not report as new — so report it as
  connected. Otherwise re-check the caveats above (PagerDuty account with install rights,
  Datadog app session in that browser, URL not expired) and re-run the mint and `wait`.
- **Exit 1** → an error; relay it.

## Getting the Most Out of Your Integration

**Send a test page to the service just connected.** This verifies the wiring end to end
— a real PagerDuty incident opens on the service, then resolves on its own:

```bash
DD_SITE=<site> python3 -I -B <skill>/scripts/pagerduty_setup.py test-page '<service>'
```

- **Ask first, with the host's native selector** (e.g. AskUserQuestion), and wait for
  the answer — it **pages whoever is on call** for that service. Name the service in the
  question, e.g. "Send a test page to `<service>`? It opens a real PagerDuty incident
  (it resolves on its own), so whoever is on call gets paged." with the options **Yes,
  send a test page** / **No, skip it**. If several services were connected, ask which
  one to page instead (plus a skip option). Never run it without a yes; on no, skip
  ahead to the final message. Use the name exactly as the poll or `services` reported it.
- It creates a throwaway event monitor (tagged `dd-pagerduty-onboarding:test`) whose message is
  `@pagerduty-<service>`, waits for it to alert — that is the page — then flips it so it
  recovers, which resolves the PagerDuty incident, and deletes the monitor. The monitor
  needs no data, so this works on a brand-new org. It usually takes a few minutes (each
  step gives up after `--timeout`, default 300s); start it in the background and report
  when it exits.
- Exit 0 means Datadog sent the page, it resolved, and the monitor is gone — tell the
  user to check PagerDuty for the opened-then-resolved incident (the script sees only
  Datadog's side, so PagerDuty is the final confirmation). Exit 2 reports which step did
  not happen (`paged`/`resolved`/`deleted`); report that plainly. Exit 1 is an error
  (e.g. a `403` without `monitors_write`); relay it. **`muted: true`** means
  an active downtime (or a mute) covered the test monitor: it alerted (`alerted: true`)
  but sent no notification, so PagerDuty was **not** paged — tell the user to end the
  downtime and re-run. The monitor is deleted even when a step fails or the run is
  interrupted; if the output says it was not, relay the `pup monitors delete` command it
  prints.
- **If it paged but did not resolve**, the PagerDuty incident may still be open: deleting
  an alerting monitor sends no recovery. After an error or interrupt the script first
  tries to recover the monitor before deleting it, and says "resolve it manually in
  PagerDuty" when that recovery was not confirmed. Whenever you see that message — or a
  run that reports `paged` but not `resolved` — tell the user plainly to resolve the
  incident on `<service>` in PagerDuty so it does not escalate.

Only celebrate a verified test page (exit 0) — never after exit 1 or 2, including a muted
run. Wiring monitors or notifications to the connected services is the
monitors/notifications setup, not this skill.

## Final message

End the run with these lines, in this order:

```markdown
**PagerDuty connected:** <service names from `wait` or `services`>
**Test page:** sent and resolved 🎉 | skipped | failed at <paged|resolved|deleted> — <next step>
**Next:** route monitors with `@pagerduty-<service>`; see https://docs.datadoghq.com/integrations/pagerduty/
```

## Cleanup

When asked: `DD_SITE=<site> python3 -I -B <skill>/scripts/pagerduty_setup.py delete-service <name>`
removes one service. It exits 0 only after a re-list confirms the service is gone; a failed
delete or a service still listed exits 1 with the error. `services` shows what exists first.
