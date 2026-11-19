# Troubleshooting & reference

Backs the Troubleshooting pointer in `SKILL.md`. Diagnose a failed step here, then
return to the step that failed.

## Troubleshooting

| Problem | Fix |
|---------|-----|
| `403` from `/api/v1/validate` with a key you know is valid | Wrong region. The key belongs to a different `DD_SITE`. Set `DD_SITE` to the key's region (step 1) and re-run the script. |
| `403` from `current_user` but `/validate` is `200` | The **application key** is wrong or from another region, not the api key. The script retries for a few seconds (permission propagation); if it persists, provision fresh. |
| `DD_SITE` rejected as unrecognized | It must match the allowed list exactly (no `https://`, no trailing slash, no `app.` prefix). Run the script with `--regions` and use a `DD_SITE` from that list. |
| Headless run (`--no-browser`) exits 7 | A non-interactive run needs a valid `DD_API_KEY` + `DD_APP_KEY` pair for `DD_SITE` (shell, `.env.local`, or `.env`). Neither sign-in nor signup works without a browser. |
| Script exits 6: "git still does not ignore …" | The env file's path could not be matched by a `.gitignore` entry (unusual characters in the name). Add it to the repo's `.gitignore` yourself, or pick a plain `--env-file`, and re-run — nothing was written. |
| Script's OAuth path: "no OAuth callback captured (180s timeout)" | The browser could not reach the local callback. Over SSH, forward the printed localhost port from the browser machine before retrying, use a current `pup`, or create keys through the UI handoff in `SKILL.md` step 3 and validate with `--no-browser`. |
| Provisioning failed after creating a key | The script prints the new key's name and site. Remove that unused key in Organization Settings → API Keys / Application Keys before retrying. Retrieved existing keys are not reported as newly created. |
| New pair fails validation | The existing env file is preserved; only a pair that validates is saved. Check the site, org and permissions, and clean up any unused new keys named in the failure output. |
| Script's OAuth path: "bad code or state mismatch" | Stale authorization (an old tab was re-approved). Re-run the script for a fresh URL and approve that one. |
| `openssl: command not found` (OAuth PKCE) | Install openssl (macOS/Linux usually ship it). On Windows, run under WSL or Git Bash. Or install `pup`, which takes the pup path instead. |
| Authenticated, but key creation returns `403` | The token/role lacks `api_keys_write`, or is for another region. The script falls back to retrieving the most-recent key; if that is also denied, use the UI handoff in `SKILL.md` step 3. |
| User needs a brand-new account | The script assumes an existing account or a working browser login. Sign up at `<app-base>/signup` (the `APP_BASE` column of `--regions`), then run the script with `--fresh` and sign in with the new credentials. |
| User pasted a key but validation still fails | Check for surrounding whitespace / quotes, and that it's an **API key** (32 hex) not an app key (40 hex). |

## Reference

- **The script is the implementation.** All detection, validation, sign-in (pup or PKCE OAuth),
  and key provisioning runs inside `scripts/account-setup.sh` — inline copies are forbidden
  because they drift. Account creation is the one thing it deliberately does not do: use the
  browser `<base-url>/signup` page, then run the script.
- **Design decisions:** the OAuth token carries the api+app key scopes so it can **create** both
  keys directly, falling back to retrieving the most-recent key when creation is denied
  (application-key creation, listing and retrieval are limited to the signed-in user; minting
  with a plain OAuth token is server-blocked on role-restricted orgs); an empty org or a role that
  can neither create nor list keys falls back to the **manual UI handoff** (`SKILL.md` step 3).
- Temp state (fixed paths): the script removes its own OAuth `state`/callback files;
  `${TMPDIR:-/tmp}/dd-oauth-$(id -u).token` (the Bearer token, `0600`) is **kept** as the
  downstream instrumentation handoff and removed by the onboarding caller when setup finishes.
