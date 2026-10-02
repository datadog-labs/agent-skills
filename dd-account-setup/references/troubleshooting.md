# Troubleshooting & reference

Backs the Troubleshooting pointer in `SKILL.md`. Diagnose a failed step here, then
return to the step that failed.

## Troubleshooting

| Problem | Fix |
|---------|-----|
| `403` from `/api/v1/validate` with a key you know is valid | Wrong region. The key belongs to a different `DD_SITE`. Set `DD_SITE` to the key's region (Step 2) and re-validate. |
| `403` from `current_user` but `/validate` is `200` | The **application key** is wrong or from another region, not the api key. |
| `DD_SITE` rejected as unrecognized | It must match the allowed list exactly (no `https://`, no trailing slash, no `app.` prefix). Use the `DD_SITE` column in `references/regions.md`. |
| IP detection times out / returns nothing | Expected on restricted networks. Falls back to US1; just confirm or override the region manually. |
| Headless run exits immediately | A non-interactive run needs `DD_API_KEY`, `DD_APP_KEY`, and `DD_SITE` all set. Neither OAuth nor signup works without a browser. |
| Browser shows "localhost … can't connect" after approving | The auto-capture listener isn't running (no `python3`, or it timed out after 180s). A busy port 8080 no longer blocks it — the listener auto-selects a free port and matches the `redirect_uri`. Copy the full address-bar URL and paste it into Path B Step 2's `PASTE_REDIRECT_URL`. |
| Auto-capture never returns / "no callback in 180s" | `python3` missing, or the browser never redirected. (A busy port 8080 is handled automatically — the listener picks a free port and matches the `redirect_uri`.) Re-run Path B Step 1; if it persists, use the paste fallback. |
| OAuth `state mismatch` (Path B Step 2) | The pasted URL's `state` didn't match the statefile (stale tab / CSRF, or Step 1 wasn't run). Re-run Path B Step 1 and paste the fresh redirect URL. |
| `bad code or state mismatch` | The pasted URL had no `code`, or the statefile was cleared. Re-run Path B Step 1, approve again, paste the new URL. |
| `openssl: command not found` (OAuth PKCE) | Install openssl (macOS/Linux usually ship it). On Windows, run under WSL or Git Bash. |
| Authenticated, but key creation returns `403` | The OAuth token / app key lacks `api_keys_write`, or is for another region. Create one at `organization-settings/api-keys` (you're already logged in) and paste it. |
| User pasted a key but validation still fails | Check for surrounding whitespace / quotes, and that it's an **API key** (32 hex) not an app key (40 hex). |
| Path C: `.env is git-tracked` | Refusing to write a secret to a tracked file. Use `.env.local` (or `git rm --cached .env` first), then re-run C2. |
| Path C: `signup unavailable (no CSRF token)` | Signup API unreachable or rate-limited. Retry shortly; if it persists, use the browser `<base>/signup` page. |
| Path C: signup rejected with a field `detail` | Datadog rejected an input (email already registered, or the generated password hit a policy). Surface the `detail`; for a policy miss, regenerate (C2) and retry. |
| Path C: verify returns `error=2` (invalid code) | Wrong 8-digit code. Re-enter it (C4), or resend a code. |

## Reference

- **No bundled scripts.** All auth/signup runs as small inline `bash` + `curl` commands (OAuth PKCE also uses `openssl`) in these files — nothing to install beyond a normal shell (Windows: WSL/Git Bash).
- **Design decisions:** generated password → `.env` (a new-account password is a credential the skill controls, so it's *generated* off-context rather than prompted — masking needs a real TTY the tool lacks), inline/no-scripts, OAuth redirect caught by a one-shot stdlib `python3 http.server` listener on `localhost` (port 8080 if free, else a free port it selects, with the `redirect_uri` matched; paste fallback when `python3` is absent or it times out), and the token→API-key step. On an **OAuth session** it only **retrieves** the most-recent existing key (minting a key with an OAuth token is server-blocked); an **empty org** or a role that can't list keys falls back to **manual UI paste**. Key **creation** (`POST /api/v2/api_keys`) happens only on the **API+APP-key** path, never on an OAuth token.
- Temp state (fixed paths): `${TMPDIR:-/tmp}/dd-signup-$(id -u).{jar,jwt}` and `${TMPDIR:-/tmp}/dd-oauth-$(id -u).state` are removed in Step 5; `${TMPDIR:-/tmp}/dd-oauth-$(id -u).token` (the Bearer token, `0600`) is **kept** as the downstream instrumentation handoff and removed by the onboarding caller when setup finishes (see Step 5).
