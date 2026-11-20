#!/bin/sh
# dd-env.sh — load and validate Datadog credentials for this skill. Source it; do not execute it:
#
#   . <SKILL_DIR>/scripts/dd-env.sh || exit 1    # from the user's project root, where .env lives
#
# The `|| exit 1` matters: a failure here only returns from this file, so without it the rest of the
# caller's block (terraform apply included) would still run.
#
# Precedence: shell environment > .env.local > .env (the first value found wins).
# Exports DD_SITE, DD_API_KEY, DD_APP_KEY, and APP_BASE.
# DD_SITE defaults to datadoghq.com only when it is unset.
# Aborts loudly if either key is missing — without ever printing a key value.
#
# The loader is eval-free on purpose: pre-bash-guard hooks in this repo block eval.
#
# Credential validation:
#   Both DD_API_KEY and DD_APP_KEY are verified against the Datadog API unless
#   DD_SKIP_VALIDATE is non-empty (set it to 1 for offline/test use).

for f in .env.local .env; do
  [ -f "$f" ] || continue
  # tr -d '\r': a .env saved with CRLF line endings would otherwise leave a \r on every value.
  [ -z "${DD_SITE:-}" ]    && DD_SITE=$(grep -E '^DD_SITE=' "$f" | head -1 | cut -d= -f2- | tr -d '\r' | sed 's/^["'\'']//;s/["'\'']$//')
  [ -z "${DD_API_KEY:-}" ] && DD_API_KEY=$(grep -E '^DD_API_KEY=' "$f" | head -1 | cut -d= -f2- | tr -d '\r' | sed 's/^["'\'']//;s/["'\'']$//')
  [ -z "${DD_APP_KEY:-}" ] && DD_APP_KEY=$(grep -E '^DD_APP_KEY=' "$f" | head -1 | cut -d= -f2- | tr -d '\r' | sed 's/^["'\'']//;s/["'\'']$//')
done

: "${DD_SITE:=datadoghq.com}"
: "${DD_API_KEY:?not set - run dd-account-setup (commercial sites) or supply it directly (government sites)}"
: "${DD_APP_KEY:?not set - run dd-account-setup (commercial sites) or supply it directly (government sites)}"

export DD_SITE DD_API_KEY DD_APP_KEY

# Compute APP_BASE from DD_SITE
case "$DD_SITE" in
  datadoghq.com)
    APP_BASE="https://app.datadoghq.com" ;;
  datadoghq.eu)
    APP_BASE="https://app.datadoghq.eu" ;;
  ddog-gov.com)
    APP_BASE="https://app.ddog-gov.com" ;;
  us3.datadoghq.com|us5.datadoghq.com|ap1.datadoghq.com|ap2.datadoghq.com|uk1.datadoghq.com|us2.ddog-gov.com)
    APP_BASE="https://${DD_SITE}" ;;
  *)
    printf '✗ dd-env.sh: unknown DD_SITE: %s\n' "$DD_SITE" >&2
    return 1
    ;;
esac
export APP_BASE

# Validate credentials unless DD_SKIP_VALIDATE is non-empty
if [ -z "${DD_SKIP_VALIDATE:-}" ]; then
  _vsc=$(printf 'DD-API-KEY: %s\nDD-APPLICATION-KEY: %s\n' "$DD_API_KEY" "$DD_APP_KEY" | \
    curl -sS --max-time 20 -o /dev/null -w '%{http_code}' -H @- \
    "https://api.${DD_SITE}/api/v1/validate")
  _usc=$(printf 'DD-API-KEY: %s\nDD-APPLICATION-KEY: %s\n' "$DD_API_KEY" "$DD_APP_KEY" | \
    curl -sS --max-time 20 -o /dev/null -w '%{http_code}' -H @- \
    "https://api.${DD_SITE}/api/v2/current_user")

  if [ "$_vsc" != "200" ] || [ "$_usc" != "200" ]; then
    if [ "$_vsc" = "403" ]; then
      _diag="API key invalid or wrong region"
    elif [ "$_vsc" = "200" ] && [ "$_usc" = "403" ]; then
      _diag="app key invalid or wrong region"
    else
      _diag="unexpected status"
    fi
    printf '✗ dd-env.sh: validate=%s current_user=%s — %s\n' "$_vsc" "$_usc" "$_diag" >&2
    unset _vsc _usc _diag
    return 1
  fi

  printf '✓ dd-env.sh: validated on %s\n' "$DD_SITE" >&2
  unset _vsc _usc
fi
