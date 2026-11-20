---
name: dd-account-setup
description: Ensure the user has an authenticated Datadog account with a valid DD_API_KEY on the right region before any Datadog setup or instrumentation. Detects existing DD_API_KEY / DD_APP_KEY / DD_SITE, validates them against the Datadog API, and fixes the common wrong-region 403. If no usable key exists, signs the user in (OAuth) or creates a new account, then obtains and validates a key. Use this whenever a user needs a Datadog account or API key, hits a 403 / wrong-region error, or is about to run any Datadog *-setup or instrumentation skill.
---

# Create / connect a Datadog account

This skill gets the user to a known-good state: **a valid API key + application key pair, on the
right region, validated against the Datadog API.** It is the "step 0" that setup/instrumentation
skills depend on — run it first, then hand off.

**`scripts/account-setup.sh` is the single source of truth for everything mechanical** —
credential detection (env > `.env.local` > `.env`), pair validation, browser sign-in (pup when
installed **and new enough** — an old pup CLI falls back to the script's own PKCE OAuth
automatically), key creation with retrieve-most-recent fallback (application keys are limited
to the signed-in user), validation of the new pair, and the atomic 0600 `.env` write.
Existing env-file contents are preserved if provisioning or validation fails. This SKILL.md carries **no
copy of that logic**: an inline copy is a second implementation that drifts — the `--site` drift
bug shipped exactly that way, in a reference that duplicated the script. If a step seems to need
inline bash the script already does, the answer is to fix or extend the script, not to duplicate it.

## Run it

```bash
scripts/account-setup.sh --site <DD_SITE> [--fresh | --no-browser]   # region from step 1, flag from step 2 / headless
```

Idempotent: a valid pair already in the environment/`.env` → exit 0, nothing created (unless
`--fresh`). Otherwise one browser approval provisions both keys; `--no-browser` never opens one.
Exit codes are documented in the script header.

## How to talk to the user

- For **questions** (the region, the connect method) use the host's **native selector** (Claude
  Code `AskUserQuestion`) — an up/down list, never "enter A / press 1".
- **Never put a secret in the conversation** — a key's name and last 4 characters are fine; the
  value never is. It lives in `.env` and nowhere else.
- **Relay the script's outcome minimally.** Post its **final checklist** and the one-line
  hand-off — nothing else. Do not replay the script's intermediate output, quote its status lines
  back, or wrap the result in commentary ("the flow ran clean, here's what you saw at each
  stage…"): the user saw the status lines live while it ran; the final checklist **is** the
  summary. The only checklist you render yourself is the baseline below, before step 1's question.
- Describe **outcomes** ("Signed in as X on US1"), not tool calls ("ran curl …").

Baseline to post when step 1 begins (the script takes over from its own checklist on entry):

```
**Datadog account setup**
- ◔ 1. Confirm region / site
- ○ 2. Credential source — existing pair or fresh login
- ○ 3. Create api + app keys (optional)
- ○ 4. Ready — hand off
```

## What stays with the agent — one question, one choice, one run, one hand-off

### 1. Confirm the region / site — the one question a customer must answer

The site drives every URL downstream (API host, key pages), so pin it before running the script.

**If `DD_SITE` is set** (env or `.env`): **skip this step entirely** — no `--regions` call, no
region question. The environment already carries the decision. Run the script without `--site`
(it reads `DD_SITE` from the shell, then `.env.local`, then `.env`, and validates the value against its own
allowed-site list, exiting with a clear error if it is unrecognized — if that error fires, relay
it and have the user fix `DD_SITE`). Post the baseline with step 1 already ● ("confirmed via
`DD_SITE`") and go straight to step 2's question.

**If `DD_SITE` is unset:** ask the user to pick a region — never silently commit one. Run
`scripts/account-setup.sh --regions` for the current region table (region, `DD_SITE`, app base
URL) and build the question's options from that output — not from memory or a doc. The same list
is what `--site` is validated against, so the picker can never offer a site the script would
reject. **Don't
   try to detect the user's location** (no IP-geolocation lookups or other third-party calls): the
   right region is where their *infrastructure* runs, which may not be where they are. Default to
   **US1** (`datadoghq.com`) if they have no preference. Tell the user, e.g.:
   > Which Datadog region should this account live in? Pick the one closest to where your infrastructure runs (e.g. your cloud region) — sending data to a far-away region adds latency and egress cost, and the region **can't be changed** after the account is created. Default: **US1 (Virginia), `datadoghq.com`**.

   Use the host's native selector with **US1 (default)** first. Wait for the user's reply before
   continuing; a plain acceptance ("ok", "default", "sure") means US1.

> ↳ Reprint the baseline with **1. Confirm region** ● once the user answers.

### 2. Ask "The choice" before touching any credential

An ambient `DD_API_KEY` may belong to a different org or account than the user intends, and the
region can't reveal which — so let the user decide rather than inferring it. Ask via the host's
native selector, header "Connect to Datadog":

> **How do you want to connect to Datadog?**
> - **Use my existing credentials** — `DD_API_KEY …<last4>` → validate & use · _include this option only if a pair was detected_
> - **Sign in** — I already have a Datadog account → browser sign-in
> - **Create a new account** — sign up in the browser

All three end the same way: **run the script**. The pick decides the flag:

- **Use my existing credentials** → no flag: the script validates the detected pair and uses it
  (a browser login happens only if that pair turns out invalid).
- **Sign in** → `--fresh`: browser sign-in and new keys even when the detected pair is valid —
  without the flag the script would keep that pair and never open the browser.
- **Create a new account** → the one case the script does not cover: have the user sign up at
  `<app-base>/signup` (the `APP_BASE` column of `--regions`) first, then run it with `--fresh`
  and sign in with the new credentials.

A detected pair is an option the user confirms, not a default to use silently. Headless is exempt
(no TTY to ask) — see the headless section. If the script's sign-in finds no account, send the
user to the signup page and re-offer the choice.

> ↳ Keep **2. The choice** ◔ until the user has picked.

### 3. Run the script and read its outcome

Run `scripts/account-setup.sh --site <DD_SITE>` (plus `--fresh` per step 2) and read what it prints — don't re-derive or
re-verify its steps with inline calls. It prints the checklist on entry and its final state on
exit (created keys: step 3 ●; a valid pair already existed: step 3 ⊘ skipped; any failure:
step 3 ✖); relay those lines as-is. Keys are **created only if no valid pair exists** — step 3
is optional by design. When a human runs the script directly without `--site`, it prompts for
the region on the terminal (empty answer = US1) and states the credential source it detected,
so it is self-contained outside an agent run.

When it fails, it prints the HTTP code and a one-line verdict. Read the verdict, don't guess a
blanket "403":

| Verdict (script log / HTTP) | Meaning | What to do |
|---|---|---|
| create denied → retrieved `200` | the org restricts OAuth minting by role; the script recovered | nothing — the pair is in `.env` |
| `EMPTY_ORG` (list `200`, zero keys) | the org has no keys and the token can't mint one | UI handoff below |
| `LIST_DENIED` (list `403`/`404`) | the signed-in user's role can't manage keys in this org (common on managed/enterprise orgs like `@datadoghq.com`) — or wrong region (the identity call would also `403`) — or, on the OAuth path, the org restricts the OAuth client for application keys (a pup session can still create them) | UI handoff below, or install `pup` and re-run, or an org admin grants key-management |
| `SECRET_DENIED` (list OK, reveal denied) | the role can list keys but not reveal secrets | UI handoff below |
| `TRANSIENT` (`5xx` or another non-`401/403/404` status) | server-side or transient — **not** a permission problem | re-run; if it persists, check the Datadog status page and connectivity to `api.<DD_SITE>`. Do **not** tell the user their access was denied |
| `NETWORK` (HTTP `000` — curl got no response) | network, proxy, or malformed URL — **not** a permission problem | re-run; check connectivity. Do **not** tell the user their key was denied |

**UI handoff** (no browser, or the script cannot run): have the user create the keys at
`<app-base>/organization-settings/api-keys` and `…/application-keys` → **+ New Key** (the
`APP_BASE` column of `--regions` for the confirmed site), and add the `DD_API_KEY=…` /
`DD_APP_KEY=…` lines to `.env` **in their own editor** — a key pasted into the conversation
lands in the transcript permanently. Same guards the script uses: `.env` not git-tracked, mode
`0600`.

### 4. Hand off

- End with the completed checklist and the hand-off line — nothing else.
- The script's final lines are the credential summary (site, key last-4, env-file location) —
  report them as-is; never print a key in full, and don't dump raw API responses on top of it.
- Credentials live in `.env`/`.env.local` (0600) — that file is the only place the secrets exist.
- If the script warns that the calling shell still exports the previous `DD_API_KEY`/`DD_APP_KEY`,
  `unset` them before the next step — they would override the new pair in the env file.
- If it warns about a different shell-exported `DD_SITE`, set it to the confirmed site
  (`export DD_SITE=<site>`) for the next step — don't just unset it: a run that only validated
  an existing pair saves no site, and environment-first loaders would then fall back to US1.
- **Keep the OAuth token** if the script took its OAuth path: it leaves
  `${TMPDIR:-/tmp}/dd-oauth-$(id -u).token` (0600) in place, and downstream instrumentation reuses
  that Bearer token to provision resources a plain API key can't create (notably a RUM
  application). The onboarding caller removes it once setup finishes; running instrumentation
  standalone, delete it when done.
- Hand back: "Account is ready. You can now run the setup / instrumentation step."

## Headless

Infer headless from the skill's invocation, not the environment — the trigger is the user
explicitly asking to run without a browser or interactive prompts ("I'm in CI, no browser"). Do
**not** infer headless merely from a missing TTY.

The script's fresh-login paths need a browser, so headless works only with a key pair already
provided (`DD_API_KEY` + `DD_APP_KEY` + `DD_SITE` in the environment or `.env`). Run
`scripts/account-setup.sh --no-browser`: it validates the pair, and if the pair is missing or
invalid it exits 7 at once. This mode never prompts for a region or changes `.env`/`.gitignore`;
valid existing credentials also leave those files untouched. Without the flag it would start a browser login and wait for the
callback. Relay that failure and stop; there is no sign-in or signup fallback without a browser.

## Golden rule

Never guess or fabricate an API key, app key, token, or site. Read what's in the environment,
validate it, and if it isn't there, authenticate — do not proceed on assumptions. When keys are
missing, do not reach for other ambient credential sources to keep moving (they can carry another
org's keys); run the script or ask the user.

## References

- `references/troubleshooting.md` — symptom→fix table plus design-decision and temp-state notes
