# Step 3 — Authenticate (detail)

Backs **Step 3** in `SKILL.md`. Assumes Step 2 has pinned `DD_SITE`. Presents "The
choice", then runs the path the user picks: **Path A** (use a detected key), **Path B**
(OAuth sign-in), or **Path C** (create a new account, then Path B). Display/wording
rules are in `conventions.md`.

**Ask how to connect before using any credential — even when Step 1 detected env keys.** Present **The choice** below, then run the path the user picks: an ambient `DD_API_KEY` may belong to a different org or account than the user intends, and region/IP can't reveal which, so let them decide. (Headless/**Step H** is exempt — no TTY to ask, env keys only.)

## The choice — how to connect

Ask via the host's **native selector** (`AskUserQuestion`) — an up/down-navigable list, **not** a
letter/number the user types. Header: "Connect to Datadog". Options (A/B/C are your internal
path labels, not shown as keys to press):

> **How do you want to connect to Datadog?**
> - **Use my existing credentials** — `DD_API_KEY …<last4>` (from your shell env or `.env`/`.env.local`) → validate & use (**Path A**)  · _include this option only when Step 1 detected a key_
> - **Sign in** — I already have a Datadog account → browser OAuth (**Path B**)
> - **Create a new account** — automated in-terminal signup (default; browser signup is the fallback) → **Path C**
>
> _(Include the "existing credentials" option only if a key was detected. Not sure between Sign in / Create? Pick **Sign in** — it fails cleanly if there's no account, then offer Create.)_

Wait for the answer, then run the matching path. Re-offer the choice whenever a path dead-ends — OAuth finds no account (→ **C**), or a wrong-region key sent the user back to Step 2 first.

> ↳ **Checklist:** this choice is part of **3. Authenticate** — keep that item ◔ until a token or key is actually in hand.

## Path A — use the API key already detected (env or `.env`/`.env.local`)

Only when the user picked **A** at The choice. Validate the detected key — this also catches a **wrong region** (a key valid on US1 returns `403` on EU1):

```bash
# Fresh shell — reload DD_* (env > .env.local > .env) so a file-only key is available here too.
for f in .env.local .env; do [ -f "$f" ] || continue; for k in DD_SITE DD_API_KEY DD_APP_KEY; do eval "[ -n \"\${$k:-}\" ]" && continue; v=$(grep -E "^$k=" "$f" | head -1 | cut -d= -f2- | sed 's/^["'\'']//;s/["'\'']$//'); [ -n "$v" ] && export "$k=$v"; done; done
site="${DD_SITE:?set DD_SITE first}"; DDLOG="${TMPDIR:-/tmp}/dd-onboard-$(id -u).log"
vcode=$(curl -sg -o "$DDLOG" -w '%{http_code}' -H "DD-API-KEY: $DD_API_KEY" "https://api.${site}/api/v1/validate")
[ "$vcode" = 200 ] && echo "✓ key valid on $site (HTTP 200)" || echo "✗ key not valid on $site (HTTP $vcode) — likely wrong region; see: tail -n 30 \"$DDLOG\""
```

- `200` + `{"valid":true}` → the key is good for this region. If `DD_APP_KEY` is also set, sanity-check it **directly** — Path A has no OAuth token, so do **not** use Step 4's Bearer identity call. Use the app-key headers instead: `curl -s -o /dev/null -w '%{http_code}' -H "DD-API-KEY: $DD_API_KEY" -H "DD-APPLICATION-KEY: $DD_APP_KEY" "https://api.$site/api/v2/current_user"` — `200` means the app key is valid (a `403` means it's wrong or from another region). Then go to **Step 5**.
- `403` → the key is invalid, malformed, **or belongs to a different region** (Datadog returns `403` for all three). If the user believes it's valid, it's almost certainly the **wrong region** — show the error below and return to **Step 2**; otherwise re-offer **The choice** (sign in or create instead).

  > Your `DD_API_KEY` isn't valid for **`<DD_SITE>`**. Datadog API keys are region-specific — this one most likely belongs to a different region. Set `DD_SITE` to that region, or authenticate to create a key here.

> ↳ **Checklist (Path A):** on `200`, collapse 3–4 to a single **Validate existing key** ● and go to Step 5.

## Path B — Sign in with OAuth (browser, PKCE + state)

The user chose **B. Sign in** (this also runs after Path C creates an account). OAuth handles **no password from us** — the user authenticates on Datadog's own page. Done **inline, no bundled script**: PKCE via `openssl`, the redirect caught by a **one-shot local listener** (stdlib `python3` `http.server` on `localhost` — port 8080 if free, else the next free port, with the `redirect_uri` set to match), the token saved to a `0600` file. A **pasted-URL fallback** covers no-`python3`/timeout. Needs `bash`, `curl`, `openssl`, a browser (Windows: WSL/Git Bash); `python3` for the auto-callback (else paste).

**Step 1 — start sign-in + auto-catch the callback** (opens the browser, then a one-shot listener writes the `code`/`state` to a file and shows the browser a real "close this tab" page — no paste):
```bash
site="$DD_SITE"; sf="${TMPDIR:-/tmp}/dd-oauth-$(id -u).state"; cb="${TMPDIR:-/tmp}/dd-oauth-$(id -u).cb"; rm -f "$cb"
cid=32e4e079-11ce-49d6-ae37-6cd2c8937354   # Datadog OAuth public client (PKCE; travels in the authorize URL)
b64u(){ openssl base64 -A | tr '+/' '-_' | tr -d '='; }
ver=$(openssl rand 32 | b64u); chal=$(printf %s "$ver" | openssl dgst -sha256 -binary | b64u)
st=$(uuidgen 2>/dev/null || openssl rand -hex 16)
# Resolve any Python 3 interpreter FIRST — ONE process binds the callback port AND serves the listener, so the port is never handed between processes: no bind→close→rebind gap for another local process to steal, and the authorize URL always names the exact port we are listening on.
# Prefer `python3`, else a `python` that is v3 (conda/some Windows/Linux). Stdlib only (http.server/urllib/os/subprocess/shutil) — ANY 3.x works; no version pin.
PYBIN=$(command -v python3 2>/dev/null || true)
[ -z "$PYBIN" ] && command -v python >/dev/null 2>&1 && python -c 'import sys;sys.exit(0 if sys.version_info[0]==3 else 1)' 2>/dev/null && PYBIN=$(command -v python)
DDLOG="${TMPDIR:-/tmp}/dd-onboard-$(id -u).log"
rc=1
if [ -n "$PYBIN" ]; then
  echo "▸ waiting for the browser sign-in to complete…"
  # One process, no race: bind (prefer 8080, else an OS-assigned free port) and HOLD the socket, write ver/st/port to the statefile, open the browser, then serve exactly one request on that same held socket.
  CID="$cid" SITE="$site" CHAL="$chal" ST="$st" VER="$ver" SF="$sf" CBFILE="$cb" "$PYBIN" - <<'PY'
import http.server,urllib.parse,os,subprocess,shutil
os.umask(0o077)   # statefile (holds the PKCE verifier) + callback file (code/state) are 0600, like the sibling .token file
cb=os.environ["CBFILE"]
class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        open(cb,"w").write(urllib.parse.urlparse(self.path).query)
        self.send_response(200);self.send_header("Content-Type","text/html; charset=utf-8");self.end_headers()
        self.wfile.write(b"<!doctype html><meta charset=utf-8><title>Datadog sign-in</title>"
                         b"<h1>Signed in.</h1><p>You can close this tab and return to the terminal.</p>")
    def log_message(self,*a):pass
srv=None
for p in (8080,0):                       # prefer 8080; if busy, let the OS assign a free ephemeral port
    try: srv=http.server.HTTPServer(("127.0.0.1",p),H); break
    except OSError: srv=None
if srv is None: raise SystemExit(3)      # no free loopback port at all (very rare) — shell falls back to paste on :8080
port=srv.server_address[1]
# redirect_uri MUST match the bound port — persist ver/st/port for Step 2 before the browser can redirect
open(os.environ["SF"],"w").write("ver=%s\nst=%s\nport=%d\n"%(os.environ["VER"],os.environ["ST"],port))
url=("https://dd.%s/oauth2/v1/authorize?client_id=%s"
     "&redirect_uri=http%%3A%%2F%%2Flocalhost%%3A%d%%2Fcallback"
     "&response_type=code&code_challenge=%s&code_challenge_method=S256&state=%s"
     )%(os.environ["SITE"],os.environ["CID"],port,os.environ["CHAL"],os.environ["ST"])
opened=False
for o in ("open","xdg-open"):             # same openers as the no-python fallback — a GUI browser, not a terminal one
    if shutil.which(o):
        try: subprocess.Popen([o,url],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL); opened=True; break
        except Exception: opened=False
if not opened: print("Open this URL to sign in:\n%s"%url)
srv.timeout=180; srv.handle_request()    # one request (or 180s timeout), then exit
PY
  rc=$?
  port=$(sed -n 's/^port=//p' "$sf" 2>/dev/null); port=${port:-8080}
  echo "callback listener: using $("$PYBIN" -V 2>&1) at $PYBIN on port $port" >> "$DDLOG"  # interpreter detail → log, not screen (conventions.md)
fi
if [ "$rc" -ne 0 ]; then
  # No python3 (or it could not bind any port): fixed :8080 + manual paste in Step 2. Build the URL and open the browser here.
  port=8080
  ( umask 077; printf 'ver=%s\nst=%s\nport=%s\n' "$ver" "$st" "$port" > "$sf" )
  url="https://dd.$site/oauth2/v1/authorize?client_id=$cid&redirect_uri=http%3A%2F%2Flocalhost%3A${port}%2Fcallback&response_type=code&code_challenge=$chal&code_challenge_method=S256&state=$st"
  { command -v open >/dev/null && open "$url"; } 2>/dev/null || { command -v xdg-open >/dev/null && xdg-open "$url"; } 2>/dev/null || printf 'Open this URL:\n%s\n' "$url"
  echo "no auto-callback (no python3, or no free port) — after approving, copy the localhost:$port URL your browser shows (it will NOT load) and use the paste fallback in Step 2"
elif [ -s "$cb" ]; then
  echo "callback captured ✓ — run Step 2"
else
  echo "no callback captured (180s timeout, or the sign-in was not completed) — use the paste fallback in Step 2"
fi
```
With `python3`, the listener captures the redirect automatically — nothing to paste. **Fallback:** if it printed "no callback" / "python3 not found", the browser's `localhost` callback URL (`…/callback?...`, on the port named in Step 1) won't load (expected) — copy that **full address-bar URL** for Step 2.

**Step 2 — finish sign-in** (auto: reads the captured file; fallback: put the pasted URL in `PASTE_REDIRECT_URL`):
```bash
site="$DD_SITE"; sf="${TMPDIR:-/tmp}/dd-oauth-$(id -u).state"; cb="${TMPDIR:-/tmp}/dd-oauth-$(id -u).cb"; tf="${TMPDIR:-/tmp}/dd-oauth-$(id -u).token"
cid=32e4e079-11ce-49d6-ae37-6cd2c8937354   # Datadog OAuth public client (PKCE; travels in the authorize URL)
paste='PASTE_REDIRECT_URL'                                    # only used if the auto-capture file is absent
if [ -s "$cb" ]; then q=$(cat "$cb"); else q=${paste#*\?}; fi
code=$(printf %s "$q" | tr '&' '\n' | sed -n 's/^code=//p'  | head -1)
st=$(printf   %s "$q" | tr '&' '\n' | sed -n 's/^state=//p' | head -1)
ver=$(sed -n 's/^ver=//p' "$sf"); exp=$(sed -n 's/^st=//p' "$sf"); prt=$(sed -n 's/^port=//p' "$sf"); prt=${prt:-8080}   # callback port chosen in Step 1 — redirect_uri must match it
[ -n "$code" ] && [ "$st" = "$exp" ] || { echo 'bad code or state mismatch — re-run Step 1'; exit 1; }
scopes='api_keys_write rum_apps_write incident_read rum_apps_read logs_read_data apm_read metrics_read hosts_read'  # write scopes (api_keys_write, rum_apps_write) are for downstream provisioning the Bearer token performs later (e.g. a RUM app); reading keys here is role-based, not scope-gated (no api_keys_read needed)
resp=$(curl -s -X POST "https://api.$site/oauth2/v1/token" \
  --data-urlencode "client_id=$cid" --data-urlencode "redirect_uri=http://localhost:$prt/callback" \
  --data-urlencode 'grant_type=authorization_code' --data-urlencode "code=$code" \
  --data-urlencode "scope=$scopes" --data-urlencode "code_verifier=$ver")
tok=$(printf %s "$resp" | grep -oE '"access_token"[[:space:]]*:[[:space:]]*"[^"]*"' | head -1 | cut -d'"' -f4)
granted=$(printf %s "$resp" | grep -oE '"scope"[[:space:]]*:[[:space:]]*"[^"]*"' | head -1 | cut -d'"' -f4)
DDLOG="${TMPDIR:-/tmp}/dd-onboard-$(id -u).log"
[ -n "$tok" ] \
  && { ( umask 077; printf %s "$tok" > "$tf" ); rm -f "$sf" "$cb"; echo "Authenticated ✓ (token …${tok: -4}) → $tf"; \
       echo "granted scopes: ${granted:-<none returned>}" >>"$DDLOG"; echo "✓ scopes granted (see \$DDLOG for the full list)"; } \
  || { rm -f "$cb"; echo 'token exchange failed (the code is single-use — re-run Step 1 for a fresh one):'; printf %s "$resp" | grep -oE '"error[a-z_]*"[[:space:]]*:[[:space:]]*"[^"]*"'; }
```

Notes:
- **One-shot local listener (no `nc`), paste fallback.** `python3`'s stdlib `http.server` handles exactly one request on `localhost` (port 8080 if free, else a free port it selects) then exits — so the redirect is captured with no copy-paste and the browser sees a real page. Without `python3` (or on timeout) it falls back to the pasted URL. Either way the `code` is one-time and PKCE-bound (useless without the verifier in the `0600` statefile), so it's safe in chat; the token is written to a `0600` file and **never printed**. Step 4 reads it from `${TMPDIR:-/tmp}/dd-oauth-$(id -u).token`.
- If sign-in shows **no account yet**, go to **Path C** to create one, then sign in (log in with the email + generated password from `.env`).

> ↳ **Checklist:** tick **3. Authenticate** only after `Authenticated ✓` (token in the file), then mark **4** ◔.

## Path C — Create a new account (automated, in the terminal)

**Default.** The skill creates the org over HTTP with a **generated** password (no masked prompt, no typed secret). Steps run as inline commands — no bundled script. **Browser trial signup is the fallback** (`<base-url>/signup`) if these endpoints are unavailable or the shell lacks `curl`/`openssl`.

**C1 — collect + confirm.** Read git defaults, then show Name / Email / Company and let the user edit any field before submitting (email defaults from git, but it's just a default):
```bash
echo "Name:    $(git config --get user.name 2>/dev/null || echo '<none>')"
echo "Email:   $(git config --get user.email 2>/dev/null || echo '<none>')"
echo "Company: <ask the user>"
```
**Ask for these as a single plain free-text reply — do NOT use `AskUserQuestion` / a native selector here.** These are free-text values, not an enumerable choice; a selector can't edit an email, and forcing one makes the user "decline" the whole question just to change one field. Show the three defaults and say, e.g.: *"Reply to change any of these, or say 'ok' to accept — Name: … / Email: … / Company: …"*. (Native selectors are for the connect-method and region choices only.) Wait for the user to confirm or correct all three. There is **no password field** — it's generated next.

If the git email looks like a corporate/work address (e.g. `@datadoghq.com`) and this is a trial, you may suggest a `+alias` (`name+test@…`) so it stays distinct — but let the user decide.

**C2 — generate the password → `.env`** (off-context: the value is written straight to the file, never echoed; rule: ≥8 chars, ≥1 number, ≥1 lowercase — this makes a strong 26-char one):
```bash
envf=".env"
git ls-files --error-unmatch "$envf" >/dev/null 2>&1 && { echo "✗ $envf is git-tracked — use .env.local or untrack it first"; exit 1; }
git check-ignore -q "$envf" 2>/dev/null || printf '\n# Datadog local credentials\n.env\n' >> .gitignore
pw="$(LC_ALL=C tr -dc 'A-Za-z0-9' </dev/urandom | head -c 24)"
pw="${pw}$(LC_ALL=C tr -dc 'a-z' </dev/urandom | head -c 1)$(LC_ALL=C tr -dc '0-9' </dev/urandom | head -c 1)"
( umask 077; printf 'DD_SIGNUP_PASSWORD=%s\n' "$pw" >> "$envf" ); chmod 600 "$envf" 2>/dev/null   # umask only guards NEW files; force 0600 in case .env pre-existed 0644
echo "Generated password saved to $envf (DD_SIGNUP_PASSWORD, …${pw: -4}) — read it there; it's never shown in chat."
```

**C3 — create the account** (reads the password back from `.env`; it's piped to `curl` via the `printf` builtin so it never lands on argv/stdout; put the confirmed values in `EMAIL`/`NAME`/`COMPANY`):
```bash
site="$DD_SITE"; envf=".env"; jar="${TMPDIR:-/tmp}/dd-signup-$(id -u).jar"; jf="${TMPDIR:-/tmp}/dd-signup-$(id -u).jwt"; cmk="${TMPDIR:-/tmp}/dd-signup-$(id -u).created"   # cmk: run-scoped "this run created the account" marker (cleaned up with the other dd-signup.* files at handoff)
case "$site" in datadoghq.com|datadoghq.eu) base="https://app.$site";; *) base="https://$site";; esac
EMAIL='<confirmed email>'; NAME='<confirmed name>'; COMPANY='<confirmed company>'
esc(){ local s=$1; s=${s//\\/\\\\}; s=${s//\"/\\\"}; printf %s "$s"; }        # escape name/company for JSON
pw=$(grep '^DD_SIGNUP_PASSWORD=' "$envf" | tail -1 | cut -d= -f2-)             # newest entry = the password C2 just generated (C2 appends); generated -> alnum, no escaping
csrf=$(curl -s -c "$jar" -H 'Accept: application/vnd.api+json' "$base/api/ui/signup?csrf=true" | grep -oE '"token"[[:space:]]*:[[:space:]]*"[^"]*"' | head -1 | cut -d'"' -f4)
chmod 600 "$jar" 2>/dev/null   # jar holds the signup session cookie/JWT — curl -c honors ambient umask, so force 0600
[ -n "$csrf" ] || { echo "signup unavailable (no CSRF token) — retry, or use $base/signup"; exit 1; }
body='{"data":{"type":"password_signup","attributes":{"email":"'"$(esc "$EMAIL")"'","name":"'"$(esc "$NAME")"'","company":"'"$(esc "$COMPANY")"'","password":"'"$pw"'","shortSignup":false,"metadata":{"sessionId":"'"$(uuidgen 2>/dev/null || openssl rand -hex 16)"'","referrer":"skill","signup_source":"skill"},"datadogVariant":"standard"}}}'
resp=$(printf '%s' "$body" | curl -s -c "$jar" -b "$jar" -X POST "$base/api/ui/signup" -H 'Content-Type: application/vnd.api+json' -H 'Accept: application/vnd.api+json' -H "x-csrf-token: $csrf" --data-binary @-)
jwt=$(printf '%s' "$resp" | grep -oE '"jwt"[[:space:]]*:[[:space:]]*"[^"]*"' | head -1 | cut -d'"' -f4)
[ -n "$jwt" ] && { ( umask 077; printf %s "$jwt" > "$jf" ); : > "$cmk"; echo "Account created — a verification code was emailed to $EMAIL."; } \
  || { echo "signup rejected:"; printf '%s' "$resp" | grep -oE '"detail"[[:space:]]*:[[:space:]]*"[^"]*"' | cut -d'"' -f4; }
```
By signing up the user agrees to Datadog's **Master Subscription Agreement** (`/legal/msa/`), **Privacy Policy** (`/legal/privacy/`), and **Cookie Policy** (`/legal/cookies/`) — mention this before submitting. A `detail` line means Datadog rejected an input (email already registered, password policy) — surface it and retry.

**C4 — verify the emailed 8-digit code** (put it in `CODE`; `resend` = re-run C3's CSRF fetch then `POST $base/api/ui/signup/resend-code`):
```bash
site="$DD_SITE"; jar="${TMPDIR:-/tmp}/dd-signup-$(id -u).jar"; jf="${TMPDIR:-/tmp}/dd-signup-$(id -u).jwt"
case "$site" in datadoghq.com|datadoghq.eu) base="https://app.$site";; *) base="https://$site";; esac
CODE='<8-digit code from the user>'; jwt=$(cat "$jf")
csrf=$(curl -s -c "$jar" -b "$jar" -H 'Accept: application/vnd.api+json' "$base/api/ui/signup?csrf=true" | grep -oE '"token"[[:space:]]*:[[:space:]]*"[^"]*"' | head -1 | cut -d'"' -f4)
printf '%s\tFALSE\t/\tTRUE\t0\tdd_sev\t%s\n' "${base#https://}" "$jwt" >> "$jar"   # dd_sev is an explicit cookie
loc=$(curl -s -o /dev/null -D - -b "$jar" -X POST "$base/signup/process/verify?mobile=false" \
  -H 'Content-Type: application/x-www-form-urlencoded' -H "Origin: $base" -H "Referer: $base/signup/process/verify" \
  --data-urlencode "verification_token=$CODE" --data-urlencode "_authentication_token=$csrf" --data-urlencode "signup_source=skill" \
  | grep -i '^location:' | head -1 | tr -d '\r')
case "$loc" in
  *error=1*) echo "too many attempts — wait a minute, then retry" ;;
  *error=2*) echo "invalid code — re-enter it" ;;
  *error=3*) echo "unexpected error — try again" ;;
  *) echo "✓ account created and verified"; rm -f "$jf" ;;
esac
```
Then **run Path B** to sign in (the user logs in with their email + the generated password from `.env`) → **Step 4** for the API key.

> ↳ **Checklist (Path C):** tick **a** once the account is created (C3) and **b** once the code is verified (C4); **c** sign in runs via Path B. Then tick **3. Authenticate**, mark **4** ◔.
