#!/usr/bin/env bash
# account-setup.sh — deterministically provision DD_API_KEY + DD_APP_KEY for a Datadog org.
#
# The decision tree lives HERE, not in the agent:
#   1. Both keys already present and validate against the site → done (idempotent).
#   2. pup installed AND new enough ('auth login' supports --site/--extra-scopes — an old
#      pup falls through to 3) → ONE browser login (pup, with key scopes) → pup creates
#      BOTH keys. A single login source cannot org-mismatch itself.
#   3. Otherwise → hand-rolled OAuth (PKCE + local callback) whose scope list INCLUDES the
#      api+app key scopes, so the token creates both keys directly.
#
# Secrets never print, never enter argv, and land only in the env file (0600, atomic write).
# Hook-compatible cleanup: single-file rm only, no recursive rm, no shell-eval loaders.
#
# Usage: account-setup.sh [--site SITE] [--env-file PATH] [--key-name NAME] [--fresh | --no-browser]
#   --site        Datadog site (default: DD_SITE from the shell, then the env files below;
#                 else datadoghq.com; validated against the allowed list)
#   --env-file    target file (default: .env.local if present, else .env); keys are read per
#                 variable from the shell env, then this file, then .env.local, then .env
#   --key-name    base name for created keys (default: dd-account-setup-<date>-<pid>; app-key
#                 names are unique per user, so the suffix keeps same-day re-runs from 409ing)
#   --fresh       browser sign-in + new keys even when a valid pair exists ("Sign in" /
#                 "Create a new account" — the existing pair is not used)
#   --no-browser  headless: validate the existing pair only; missing/invalid → exit 7, no sign-in
#   --regions     print the region table (DD_SITE + app base URL per region) and exit —
#                 the picker for "which region?" is built from THIS output, not from a doc
#
# Exit codes: 0 ok · 1 usage · 2 deps missing · 3 keys present but invalid ·
#             4 pup path failed · 5 oauth path failed · 6 env file is git-tracked/not ignorable ·
#             7 no valid pair and --no-browser forbids a sign-in
set -euo pipefail

log()  { printf '▸ %s\n' "$*"; }
ok()   { printf '✓ %s\n' "$*"; }
fail() { printf '✗ %s\n' "$*"; }

# One table drives both --regions and --site validation.
REGIONS='US1 - East (Virginia)|datadoghq.com|https://app.datadoghq.com
US3 - West (Oregon)|us3.datadoghq.com|https://us3.datadoghq.com
US5 - Central (Ohio)|us5.datadoghq.com|https://us5.datadoghq.com
EU1 - Europe (Frankfurt)|datadoghq.eu|https://app.datadoghq.eu
AP1 - Japan (Tokyo)|ap1.datadoghq.com|https://ap1.datadoghq.com
AP2 - Australia (Sydney)|ap2.datadoghq.com|https://ap2.datadoghq.com
UK1 - United Kingdom|uk1.datadoghq.com|https://uk1.datadoghq.com'
print_regions() {
  local region site app_base
  printf '%-24s %-19s %s\n' "REGION" "DD_SITE" "APP_BASE"
  while IFS='|' read -r region site app_base; do
    printf '%-24s %-19s %s\n' "$region" "$site" "$app_base"
  done <<< "$REGIONS"
}
site_is_valid() {
  local region site app_base
  while IFS='|' read -r region site app_base; do
    [ "$site" != "$SITE" ] || return 0
  done <<< "$REGIONS"
  return 1
}
# Point the shell at the confirmed site rather than unsetting it: a validate-only run saves
# no site, so with DD_SITE unset environment-first loaders would fall back to US1.
warn_shell_site() {
  [ -z "${DD_SITE:-}" ] || [ "$DD_SITE" = "$SITE" ] \
    || log "the calling shell still exports DD_SITE=${DD_SITE}, which differs from ${SITE} — run 'export DD_SITE=${SITE}' before the next step (don't just unset it: with no saved site, environment-first loaders fall back to US1)"
}

SITE="datadoghq.com"
SITE_EXPLICIT=0
ENV_FILE=""
FRESH=0
NO_BROWSER=0
# App-key names are unique per user — the PID suffix keeps same-day re-runs from 409ing.
KEY_NAME="dd-account-setup-$(date +%Y%m%d)-$$"
while [ $# -gt 0 ]; do
  case "$1" in
    --site)      SITE="${2:?}"; SITE_EXPLICIT=1; shift 2 ;;
    --env-file)  ENV_FILE="${2:?}"; shift 2 ;;
    --key-name)  KEY_NAME="${2:?}"; shift 2 ;;
    --fresh)      FRESH=1; shift ;;
    --no-browser) NO_BROWSER=1; shift ;;
    --regions)   print_regions; exit 0 ;;
    -h|--help)   sed -n '2,30p' "$0"; exit 0 ;;
    *) fail "unknown argument: $1"; exit 1 ;;
  esac
done
if [ "$FRESH" = 1 ] && [ "$NO_BROWSER" = 1 ]; then
  fail "--fresh needs a browser sign-in — it cannot be combined with --no-browser"; exit 1
fi

# --- env file selection + parsing (no shell-eval; quote-stripping like the skill's loader) -
# ENV_FILE is where keys are WRITTEN; reads fall back per variable through the same
# precedence the downstream loader uses: shell env > ENV_FILE > .env.local > .env.
if [ -z "$ENV_FILE" ]; then
  if [ -f .env.local ]; then ENV_FILE=.env.local; else ENV_FILE=.env; fi
fi
file_value() { # file_value VAR FILE — VAR's value in FILE (empty when unset or no file)
  [ -f "$2" ] || return 0
  grep -E "^$1=" "$2" 2>/dev/null | head -1 | cut -d= -f2- | sed 's/^["'\'']//;s/["'\'']$//' || true
}
env_value() { # env_value VAR — shell env first, then ENV_FILE, .env.local, .env
  k="$1"
  if [ -n "${!k:-}" ]; then printf '%s' "${!k}"; return 0; fi
  for f in "$ENV_FILE" .env.local .env; do
    v=$(file_value "$k" "$f")
    if [ -n "$v" ]; then printf '%s' "$v"; return 0; fi
  done
  return 0
}

# Region resolution: explicit --site wins; then DD_SITE from the shell env or a saved env
# file (already a decision — no confirmation needed); then a terminal prompt (empty answer =
# default US1); else the default with a notice. The default is never silently treated as chosen.
SITE_CONFIRMED=0
if [ "$SITE_EXPLICIT" = 0 ]; then
  SITE_SRC=""
  if [ -n "${DD_SITE:-}" ]; then
    SITE="$DD_SITE"; SITE_SRC="the environment"
  else
    for f in "$ENV_FILE" .env.local .env; do
      v=$(file_value DD_SITE "$f")
      if [ -n "$v" ]; then SITE="$v"; SITE_SRC="$f"; break; fi
    done
  fi
  if [ -n "$SITE_SRC" ]; then
    SITE_CONFIRMED=1
    log "using DD_SITE=${SITE} from ${SITE_SRC}"
  elif [ "$NO_BROWSER" = 0 ] && [ -t 0 ] && [ -t 1 ]; then
    prompt_tries=0
    while :; do
      printf 'Datadog site [datadoghq.com — default US1; answer "regions" to list them]: '
      read -r answer || answer=""
      case "$answer" in
        "") break ;;                                                    # empty = accept the default
        regions|--regions|list|ls|\?|help) print_regions; prompt_tries=$((prompt_tries+1)); [ "$prompt_tries" -ge 5 ] && break; continue ;;
        *) SITE="$answer"; break ;;
      esac
    done
    SITE_CONFIRMED=1
  fi
fi

site_is_valid || { fail "DD_SITE '$SITE' is not a recognized Datadog site — run with --regions to list them, then re-run with --site <one of those>"; exit 1; }
[ "$SITE_EXPLICIT" = 1 ] || [ "$SITE_CONFIRMED" = 1 ] || log "no --site and no terminal — defaulting to ${SITE} (pass --site, or see --regions)"

# --- progress checklist — printed by the script, not documented elsewhere ----------------
# Line 1 reflects how the region was resolved; line 2 reflects the credential source the
# script detected (set during detection below); the agent relays these lines verbatim.
region_line() {
  if [ "$SITE_EXPLICIT" = 1 ] || [ "$SITE_CONFIRMED" = 1 ]; then
    printf -- '- ● 1. Confirm region / site (%s)\n' "$SITE"
  else
    printf -- '- ⊘ 1. Region / site — defaulted to %s (pass --site to change)\n' "$SITE"
  fi
}
checklist() { # checklist MARK3 [MARK4]
  region_line
  printf -- '- %s 2. Credential source — %s\n' "${M2:-◔}" "${CRED_SOURCE:-not detected yet}"
  printf -- '- %s 3. Create api + app keys (optional)\n' "$1"
  printf -- '- %s 4. Ready — hand off\n' "${2:-○}"
}
checklist ◔
SKIP3=0   # set when a valid pair already existed and nothing was created
KEYS_SAVED=0
API_CREATED=0
APP_CREATED=0
trap 'code=$?
  if [ "$KEYS_SAVED" = 0 ]; then
    [ "$API_CREATED" = 0 ] || log "API key named ${KEY_NAME} was created on ${SITE} but not saved — remove it from Organization Settings > API Keys if unused"
    [ "$APP_CREATED" = 0 ] || log "Application key named ${KEY_NAME} was created on ${SITE} but not saved — remove it from Organization Settings > Application Keys if unused"
  fi
  [ -n "${TMP:-}" ] && { rm -f "$TMP/api.key" 2>/dev/null; rm -f "$TMP/app.key" 2>/dev/null; rm -f "$TMP/pup-api.json" 2>/dev/null; rm -f "$TMP/pup-app.json" 2>/dev/null; rm -f "$TMP/pup-api.err" 2>/dev/null; rm -f "$TMP/pup-app.err" 2>/dev/null; rm -f "$TMP/api-body.json" 2>/dev/null; rm -f "$TMP/app-body.json" 2>/dev/null; rmdir "$TMP" 2>/dev/null || true; }
  if [ "$code" = 0 ]; then
    if [ "$SKIP3" = 1 ]; then checklist ⊘ ◔; else checklist ● ◔; fi
  else
    checklist ✖
  fi
  exit "$code"' EXIT

command -v curl >/dev/null 2>&1    || { fail "curl is required"; exit 2; }
command -v python3 >/dev/null 2>&1 || { fail "python3 is required"; exit 2; }
umask 077   # every temp file below can hold secrets (pup JSON, OAuth state/token) — 0600 regardless of ambient umask

SHELL_KEYS=0; [ -z "${DD_API_KEY:-}${DD_APP_KEY:-}" ] || SHELL_KEYS=1   # for the hand-off warning
DD_API_KEY=$(env_value DD_API_KEY || true)
DD_APP_KEY=$(env_value DD_APP_KEY || true)

# --- validate an existing pair --------------------------------------------------------
validate_pair() { # sets V_API/V_APP to HTTP codes
  V_API=$(printf 'DD-API-KEY: %s\n' "$DD_API_KEY" \
    | curl -sS --max-time 20 -o /dev/null -w '%{http_code}' -H @- "https://api.${SITE}/api/v1/validate" || echo 000)
  V_APP=$(printf 'DD-API-KEY: %s\nDD-APPLICATION-KEY: %s\n' "$DD_API_KEY" "$DD_APP_KEY" \
    | curl -sS --max-time 20 -o /dev/null -w '%{http_code}' -H @- "https://api.${SITE}/api/v2/current_user" || echo 000)
  [ "$V_API" = "200" ] && [ "$V_APP" = "200" ]
}

if [ "$FRESH" = 1 ]; then
  M2=●; CRED_SOURCE="fresh login (--fresh — any existing pair is replaced)"
  log "--fresh — not using any existing pair; provisioning fresh (browser login)"
elif [ -n "$DD_API_KEY" ] && [ -n "$DD_APP_KEY" ]; then
  log "detected an existing key pair (shell env or env file) — validating against ${SITE}"
  if validate_pair; then
    M2=●; CRED_SOURCE="existing pair (api …${DD_API_KEY: -4})"; SKIP3=1
    ok "keys already valid on ${SITE} (api …${DD_API_KEY: -4}, app …${DD_APP_KEY: -4}) — nothing to create"
    warn_shell_site
    exit 0
  fi
  if [ "$NO_BROWSER" = 1 ]; then
    M2=✖; CRED_SOURCE="existing pair invalid on ${SITE} (--no-browser — no sign-in)"
    fail "existing keys invalid (validate HTTP ${V_API}, current_user HTTP ${V_APP}) and --no-browser forbids a sign-in — fix DD_API_KEY/DD_APP_KEY/DD_SITE"
    exit 7
  fi
  M2=●; CRED_SOURCE="fresh login (existing pair invalid on ${SITE})"
  fail "existing keys invalid (validate HTTP ${V_API}, current_user HTTP ${V_APP}) — provisioning fresh"
else
  if [ "$NO_BROWSER" = 1 ]; then
    M2=✖; CRED_SOURCE="no existing pair (--no-browser — no sign-in)"
    fail "no DD_API_KEY/DD_APP_KEY in shell env, ${ENV_FILE}, .env.local or .env, and --no-browser forbids a sign-in — provide the pair first"
    exit 7
  fi
  M2=●; CRED_SOURCE="fresh login (no existing pair in shell env, ${ENV_FILE}, .env.local or .env)"
  log "no existing DD_API_KEY/DD_APP_KEY in shell env, ${ENV_FILE}, .env.local or .env — provisioning fresh (browser login)"
fi

# --- git guard — only provisioning can write files -----------------------------------
# Normalize absolute paths and verify the exclusion before creating any keys.
ENV_DIR=$(dirname "$ENV_FILE")
if GIT_ROOT=$(git -C "$ENV_DIR" rev-parse --show-toplevel 2>/dev/null); then
  REL=$(python3 -c 'import os, sys; d, b = os.path.split(sys.argv[1]); print(os.path.relpath(os.path.join(os.path.realpath(d or "."), b), os.path.realpath(sys.argv[2])))' "$ENV_FILE" "$GIT_ROOT")
  if git -C "$GIT_ROOT" ls-files --error-unmatch -- "$REL" >/dev/null 2>&1; then
    fail "$ENV_FILE is git-tracked — untrack it or use --env-file"
    exit 6
  fi
  git -C "$GIT_ROOT" check-ignore -q -- "$REL" 2>/dev/null || printf '\n# Datadog local credentials\n%s\n' "$REL" >> "$GIT_ROOT/.gitignore"
  git -C "$GIT_ROOT" check-ignore -q -- "$REL" 2>/dev/null \
    || { fail "git still does not ignore $ENV_FILE — add it to $GIT_ROOT/.gitignore yourself (nothing written)"; exit 6; }
else   # pre-ignore beside the file for a later git init
  grep -qxF -- "$(basename "$ENV_FILE")" "$ENV_DIR/.gitignore" 2>/dev/null \
    || printf '\n# Datadog local credentials\n%s\n' "$(basename "$ENV_FILE")" >> "$ENV_DIR/.gitignore"
fi

# --- write keys into the env file atomically (values passed via files, never argv) ----
TMP=$(mktemp -d "${TMPDIR:-/tmp}/dd-keys.XXXXXXXXXX") || exit 9   # cleaned by the EXIT trap above

write_env() { # write_env — reads $TMP/api.key + $TMP/app.key
  DD_SITE="$SITE" TMPKEYS="$TMP" ENVF="$ENV_FILE" python3 - <<'PY' || return 1
import os, sys, tempfile
tmp, envf = os.environ['TMPKEYS'], os.environ['ENVF']
api = open(os.path.join(tmp, 'api.key')).read().strip()
app = open(os.path.join(tmp, 'app.key')).read().strip()
lines = [l for l in (open(envf).read().splitlines() if os.path.exists(envf) else [])
         if not l.startswith(('DD_SITE=', 'DD_API_KEY=', 'DD_APP_KEY='))]
lines += ['DD_SITE=' + os.environ['DD_SITE'], 'DD_API_KEY=' + api, 'DD_APP_KEY=' + app]
fd, staged = tempfile.mkstemp(prefix='.env.', dir=os.path.dirname(envf) or '.')
try:
    with os.fdopen(fd, 'w') as f:
        f.write('\n'.join(lines) + '\n')
    os.replace(staged, envf)
finally:
    if os.path.exists(staged):
        os.unlink(staged)
os.chmod(envf, 0o600)
PY
  ok "keys written to ${ENV_FILE} (api …$(cat "$TMP/api.key" | tail -c 5), app …$(cat "$TMP/app.key" | tail -c 5))"
}

# --- path 1: pup (only when new enough to drive it) ------------------------------------
# Feature-detect instead of trusting presence: an old pup (e.g. 1.24.x) has no --site /
# --extra-scopes on 'auth login', and its login cannot grant the key scopes this flow
# needs — using it would fail mid-flow. An old pup falls through to the script's own
# PKCE OAuth, exactly as if pup were not installed.
PUP_OK=0
if command -v pup >/dev/null 2>&1; then
  h=$(pup auth login --help 2>&1 || true)
  case "$h" in
    *--extra-scopes*--site*|*--site*--extra-scopes*) PUP_OK=1 ;;
    *) log "pup is installed but its CLI is too old for this flow ('auth login' lacks --site/--extra-scopes) — using the script's own PKCE OAuth instead" ;;
  esac
fi
if [ "$PUP_OK" = 1 ]; then
  log "pup detected — using it (one browser login, both keys, one org by construction)"
  log "pup auth login (browser) — pick the org you want the keys in"
  pup auth login --site "$SITE" \
    --extra-scopes api_keys_read,api_keys_write,org_app_keys_read,org_app_keys_write,user_app_keys \
    || { fail "pup login failed"; exit 4; }
  # NB: pup's api-keys/app-keys create do not take --site (site comes from $DD_SITE,
  # set inline below — the session from `pup auth login --site` pins it too). Invoked bare
  # (--name only): older pup builds reject -o/--no-envelope, and JSON is the default output
  # in both — the {status,data} envelope and the raw body share the data.attributes.key path.
  if DD_SITE="$SITE" pup api-keys create --name "$KEY_NAME" \
       > "$TMP/pup-api.json" 2>"$TMP/pup-api.err" \
     && { API_CREATED=1; } \
     && DD_SITE="$SITE" pup app-keys create --name "$KEY_NAME" \
       > "$TMP/pup-app.json" 2>"$TMP/pup-app.err"; then
    APP_CREATED=1
    python3 - "$TMP/pup-api.json" "$TMP/pup-app.json" "$TMP/api.key" "$TMP/app.key" <<'PY' || { fail "could not parse pup output"; exit 4; }
import json, sys
api = json.load(open(sys.argv[1]))['data']['attributes']['key']
app = json.load(open(sys.argv[2]))['data']['attributes']['key']
open(sys.argv[3], 'w').write(api)
open(sys.argv[4], 'w').write(app)
PY
    rm -f "$TMP/pup-api.json"
    rm -f "$TMP/pup-app.json"
    rm -f "$TMP/pup-api.err"
    rm -f "$TMP/pup-app.err"
  else
    fail "pup key creation failed:"; grep -v Warning "$TMP"/pup-*.err 2>/dev/null | head -5 || true
    exit 4
  fi

# --- path 2: hand-rolled OAuth with api+app key scopes in the scope list -------------
else
  command -v openssl >/dev/null 2>&1 || { fail "openssl is required for the OAuth path (or install pup)"; exit 2; }
  log "no pup — hand-rolled OAuth (api+app key scopes included so the token creates both keys)"

  SF="${TMPDIR:-/tmp}/dd-oauth-$(id -u).state"; CB="${TMPDIR:-/tmp}/dd-oauth-$(id -u).cb"
  TF="${TMPDIR:-/tmp}/dd-oauth-$(id -u).token"
  rm -f "$CB"
  CID="32e4e079-11ce-49d6-ae37-6cd2c8937354"   # Datadog OAuth public client (PKCE; travels in the authorize URL)
  b64u() { openssl base64 -A | tr '+/' '-_' | tr -d '='; }
  VER=$(openssl rand 32 | b64u)
  CHAL=$(printf %s "$VER" | openssl dgst -sha256 -binary | b64u)
  ST=$(uuidgen 2>/dev/null || openssl rand -hex 16)
  PORT=$(python3 - <<'PORTPY'
import socket
def grab(p):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", p)); return s.getsockname()[1]
    except OSError:
        return 0
    finally:
        s.close()
print(grab(8080) or grab(0) or 8080)
PORTPY
)
  ( umask 077; printf 'ver=%s\nst=%s\nport=%s\n' "$VER" "$ST" "$PORT" > "$SF" )
  URL="https://dd.${SITE}/oauth2/v1/authorize?client_id=${CID}&redirect_uri=http%3A%2F%2Flocalhost%3A${PORT}%2Fcallback&response_type=code&code_challenge=${CHAL}&code_challenge_method=S256&state=${ST}"
  # key scopes FIRST so both key types can be created; then the original provisioning scopes
  SCOPES='api_keys_read api_keys_write org_app_keys_read org_app_keys_write user_app_keys rum_apps_write incident_read rum_apps_read logs_read_data apm_read metrics_read hosts_read'
  { command -v open >/dev/null && open "$URL"; } 2>/dev/null || { command -v xdg-open >/dev/null && xdg-open "$URL"; } 2>/dev/null || true
  printf 'Open this URL to sign in (a browser should have opened):\n%s\n' "$URL"
  CB_PORT="$PORT" CBFILE="$CB" python3 - <<'PY'
import http.server, urllib.parse, os
os.umask(0o077)
port = int(os.environ.get("CB_PORT", "8080"))
class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        open(os.environ["CBFILE"], "w").write(urllib.parse.urlparse(self.path).query)
        self.send_response(200); self.send_header("Content-Type", "text/html; charset=utf-8"); self.end_headers()
        self.wfile.write(b"<!doctype html><meta charset=utf-8><title>Datadog sign-in</title>"
                         b"<h1>Signed in.</h1><p>You can close this tab and return to the terminal.</p>")
    def log_message(self, *a): pass
try:
    s = http.server.HTTPServer(("127.0.0.1", port), H); s.timeout = 180; s.handle_request()
except OSError:
    pass
PY
  [ -s "$CB" ] || { fail "no OAuth callback captured (180s timeout) — over SSH, forward localhost:${PORT} from the browser machine, or install a current pup and re-run; alternatively create keys in Organization Settings > API Keys / Application Keys and save them in ${ENV_FILE}, then run --no-browser"; exit 5; }

  CODE=$(tr '&' '\n' < "$CB" | sed -n 's/^code=//p' | head -1)
  CST=$(tr '&' '\n' < "$CB" | sed -n 's/^state=//p' | head -1)
  [ -n "$CODE" ] && [ "$CST" = "$ST" ] || { fail "bad code or state mismatch — re-run"; exit 5; }
  RESP=$(curl -s -X POST "https://api.${SITE}/oauth2/v1/token" \
    --data-urlencode "client_id=$CID" --data-urlencode "redirect_uri=http://localhost:$PORT/callback" \
    --data-urlencode 'grant_type=authorization_code' --data-urlencode "code=$CODE" \
    --data-urlencode "scope=$SCOPES" --data-urlencode "code_verifier=$VER") || RESP=""
  TOK=$(printf '%s' "$RESP" | grep -oE '"access_token"[[:space:]]*:[[:space:]]*"[^"]*"' | head -1 | cut -d'"' -f4) || TOK=""
  [ -n "$TOK" ] || { fail "token exchange failed (code is single-use — re-run for a fresh one)"; exit 5; }
  ( umask 077; printf %s "$TOK" > "$TF" )
  rm -f "$CB"
  rm -f "$SF"
  ok "signed in via OAuth (token …${TOK: -4} → $TF)"

  # headers-on-stdin so the token never enters argv
  dd_api() { # dd_api METHOD PATH [BODY_FILE]
    m="$1"; p="$2"; b="${3:-}"
    if [ -n "$b" ]; then
      printf 'Authorization: Bearer %s\n' "$(cat "$TF")" | curl -sg --max-time 30 -w '\n%{http_code}' -X "$m" -H @- \
        -H 'Content-Type: application/json' --data-binary "@$b" "https://api.${SITE}${p}"
    else
      printf 'Authorization: Bearer %s\n' "$(cat "$TF")" | curl -sg --max-time 30 -w '\n%{http_code}' -X "$m" -H @- \
        "https://api.${SITE}${p}"
    fi
  }
  extract() { grep -oE "\"$1\"[[:space:]]*:[[:space:]]*\"[^\"]*\"" /dev/stdin | head -1 | cut -d'"' -f4; }

  # A failed retrieval reports WHY, as the verdicts SKILL.md's failure table keys on: a re-run
  # fixes NETWORK/TRANSIENT; EMPTY_ORG / LIST_DENIED / SECRET_DENIED need pup or the UI.
  create_key() { # create_key KIND BODYFILE OUTFILE — POST create; falls back to retrieve-most-recent
    kind="$1" body="$2" out="$3" lcode=- gcode=- kid=""
    # Application keys must belong to the signed-in user, including on fallback.
    key_path="/api/v2/${kind}"
    [ "$kind" != application_keys ] || key_path="/api/v2/current_user/application_keys"
    resp=$(dd_api POST "$key_path" "$body"); code=$(printf '%s' "$resp" | tail -1)
    if [ "$code" = 201 ] || [ "$code" = 200 ]; then
      if [ "$kind" = api_keys ]; then API_CREATED=1; else APP_CREATED=1; fi
    fi
    sec=$(printf '%s' "$resp" | sed '$d' | extract key)
    if [ -z "$sec" ]; then
      log "create ${kind} returned HTTP ${code} — falling back to retrieving the most recent one"
      lresp=$(dd_api GET "${key_path}?page[size]=1&sort=-created_at"); lcode=$(printf '%s' "$lresp" | tail -1)
      kid=$(printf '%s' "$lresp" | sed '$d' | grep -oE '"id"[[:space:]]*:[[:space:]]*"[a-f0-9-]{36}"' | head -1 | cut -d'"' -f4)
      if [ "$lcode" = "200" ] && [ -n "$kid" ]; then
        gresp=$(dd_api GET "${key_path}/${kid}"); gcode=$(printf '%s' "$gresp" | tail -1)
        sec=$(printf '%s' "$gresp" | sed '$d' | extract key)
      fi
      [ -z "$sec" ] || ok "create denied → retrieved the most recent ${kind} (HTTP ${gcode})"
    fi
    [ -n "$sec" ] && printf '%s' "$sec" > "$out" && return 0
    codes="create HTTP ${code}, list HTTP ${lcode}"; [ "$gcode" = - ] || codes="${codes}, reveal HTTP ${gcode}"
    verdict=""
    for c in "$code" "$lcode" "$gcode"; do
      case "$c" in
        -|2??|401|403|404) ;;
        000|"") verdict=NETWORK ;;
        *) [ "$verdict" = NETWORK ] || verdict=TRANSIENT ;;
      esac
    done
    if [ -z "$verdict" ]; then
      if [ "$lcode" != 200 ]; then verdict=LIST_DENIED
      elif [ -z "$kid" ]; then verdict=EMPTY_ORG
      else verdict=SECRET_DENIED; fi
    fi
    case "$verdict" in
      NETWORK)       fail "could not obtain ${kind}: NETWORK — no HTTP response (${codes}); a network or proxy problem, not a permission one — re-run" ;;
      TRANSIENT)     fail "could not obtain ${kind}: TRANSIENT — server-side status (${codes}), not a permission problem — re-run; if it persists, check the Datadog status page and connectivity to api.${SITE}" ;;
      EMPTY_ORG)     fail "could not obtain ${kind}: EMPTY_ORG — create denied and the org has none to retrieve (${codes}) — create one in the UI and add it to ${ENV_FILE} yourself" ;;
      LIST_DENIED)   fail "could not obtain ${kind}: LIST_DENIED — create and list both denied (${codes}): this role, or an org restriction on the OAuth client for that key type, can't manage ${kind} — install pup and re-run (a pup session can create both), or create the key in the UI and add it to ${ENV_FILE} yourself" ;;
      SECRET_DENIED) fail "could not obtain ${kind}: SECRET_DENIED — keys listed but the secret can't be revealed (${codes}) — create one in the UI and add it to ${ENV_FILE} yourself" ;;
    esac
    return 1
  }

  printf '{"data":{"type":"api_keys","attributes":{"name":"%s"}}}' "$KEY_NAME" > "$TMP/api-body.json"
  printf '{"data":{"type":"application_keys","attributes":{"name":"%s"}}}' "$KEY_NAME" > "$TMP/app-body.json"
  create_key api_keys         "$TMP/api-body.json" "$TMP/api.key" || exit 5   # the EXIT trap removes both bodies
  create_key application_keys "$TMP/app-body.json" "$TMP/app.key" || exit 5
  rm -f "$TMP/api-body.json"
  rm -f "$TMP/app-body.json"
fi

# --- final validation -----------------------------------------------------------------
# Validate staged keys before replacing any previously saved credentials.
# A just-created app key can 403 for a few seconds while its permission checks propagate —
# retry briefly before declaring failure.
DD_API_KEY=$(cat "$TMP/api.key")
DD_APP_KEY=$(cat "$TMP/app.key")
vi=0
until validate_pair; do
  vi=$((vi+1))
  if [ "$vi" -ge 6 ]; then
    fail "provisioned keys failed validation (validate HTTP ${V_API}, current_user HTTP ${V_APP}) — existing ${ENV_FILE} was left unchanged; check the org, site, and key permissions"
    exit 3
  fi
  log "validation not green yet (validate HTTP ${V_API}, current_user HTTP ${V_APP}) — retrying in 10s (${vi}/5)"
  sleep 10
done
write_env || { fail "could not write ${ENV_FILE}"; if [ "$PUP_OK" = 1 ]; then exit 4; else exit 5; fi; }
KEYS_SAVED=1
ok "pair validated on ${SITE} (HTTP 200/200) — api …${DD_API_KEY: -4}, app …${DD_APP_KEY: -4}"
[ "$SHELL_KEYS" = 0 ] || log "the calling shell still exports the previous DD_API_KEY/DD_APP_KEY — they override ${ENV_FILE} for anything that reads the environment first; unset them before the next step"
warn_shell_site
printf '🔑 keys are in %s · site %s · token (OAuth path only) at %s\n' "$ENV_FILE" "$SITE" "${TMPDIR:-/tmp}/dd-oauth-$(id -u).token"
exit 0
