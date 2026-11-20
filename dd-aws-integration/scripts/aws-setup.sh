#!/usr/bin/env bash
# aws-setup.sh — establish usable AWS credentials for a target account, via `aws login`.
#
# The decision tree lives HERE, not in the agent — zero agent questions:
#   1. An explicit profile (--profile / AWS_PROFILE / AWS_DEFAULT_PROFILE) that verifies via
#      `sts get-caller-identity` → use it. Refresh an expired console-login profile with
#      `aws login --profile <NAME>`; reject other invalid explicit profiles.
#   2. Ambient credentials that verify (env keys, the default profile in ~/.aws/credentials,
#      instance role, credential_process) → use them.
#   3. Otherwise → `aws login --region <REGION>` (browser sign-in, non-interactive; credentials
#      land in the `default` profile; account/role are discovered from the session). Python 3
#      bounds browser sign-in to 60 seconds without GNU timeout: when the callback cannot arrive
#      (browser on another device, or a sign-in detour through an expired session), the script tells the user to
#      sign in to the AWS console in that browser and re-run — the session cookie makes the
#      retry's callback land.
#   There is no SSO path. `aws login` requires AWS CLI >= 2.32.0; an older CLI fails with an
#   upgrade message instead of falling back to `aws configure sso`.
#
# Usage: aws-setup.sh [--profile NAME] [--account ACCOUNT_ID] [--region REGION]
#   --account  expected 12-digit account; the final identity must match it (exit 7 otherwise)
#   --region   region for `aws login` (default us-east-1); persisted to the effective profile
#              when it has none, so Terraform's AWS provider finds one in any session.
#              Credentials from environment variables leave ~/.aws/config alone.
#              Monitoring regions are decided by the integration, not here.
#   AWS_LOGIN_TIMEOUT  seconds to wait for browser sign-in (default 60)
#
# Exit codes: 0 ok · 1 usage · 2 no credentials obtainable · 5 aws CLI missing or older than
#             2.32.0, or python3 missing for browser sign-in · 6 `aws login` timed out —
#             sign in to the console and re-run ·
#             7 identity/account mismatch — re-run with --profile for the target account
set -euo pipefail

log()  { printf '▸ %s\n' "$*"; }
ok()   { printf '✓ %s\n' "$*"; }
fail() { printf '✗ %s\n' "$*"; }

usage() {
  sed -n '2,/^set -euo pipefail$/p' "$0" | sed '$d' | sed 's/^# \{0,1\}//'
}

PROFILE="${AWS_PROFILE:-${AWS_DEFAULT_PROFILE:-}}"
ACCOUNT=""
REGION="us-east-1"
LOGIN_TIMEOUT="${AWS_LOGIN_TIMEOUT:-60}"
case "$LOGIN_TIMEOUT" in ''|*[!0-9]*) fail "AWS_LOGIN_TIMEOUT must be a number of seconds"; exit 1 ;; esac
while [ $# -gt 0 ]; do
  case "$1" in
    --profile) PROFILE="${2:?}"; shift 2 ;;
    --account) ACCOUNT="${2:?}"; shift 2 ;;
    --region)  REGION="${2:?}"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) fail "unknown argument: $1"; exit 1 ;;
  esac
done

command -v aws >/dev/null 2>&1 || { fail "aws CLI is required — https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html"; exit 5; }

identity() { # identity — prints JSON from sts get-caller-identity, or fails quietly
  # Leave AWS_PROFILE UNSET for the ambient check: an empty-but-set AWS_PROFILE makes the CLI
  # treat "" as a profile name (ProfileNotFound) instead of falling back to ambient credentials.
  if [ -n "$PROFILE" ]; then
    AWS_PROFILE="$PROFILE" aws sts get-caller-identity --output json --query '{account:Account,arn:Arn,user:UserId}' 2>/dev/null
  else
    aws sts get-caller-identity --output json --query '{account:Account,arn:Arn,user:UserId}' 2>/dev/null
  fi
}

configure_region() { # configure_region — persist REGION on the effective profile if it has none
  local have
  if [ -n "$PROFILE" ]; then
    have=$(aws configure get region --profile "$PROFILE" 2>/dev/null || true)
    [ -n "$have" ] || aws configure set region "$REGION" --profile "$PROFILE" >/dev/null
  elif [ -n "${AWS_ACCESS_KEY_ID:-}" ]; then
    # Environment-variable credentials: the [default] profile is not what is in use, so leave
    # ~/.aws/config alone. Terraform needs the region exported alongside the keys instead.
    [ -n "${AWS_REGION:-${AWS_DEFAULT_REGION:-}}" ] \
      || log "credentials come from environment variables — also export AWS_REGION=$REGION wherever Terraform runs"
  else
    have=$(aws configure get region 2>/dev/null || true)
    [ -n "$have" ] || aws configure set region "$REGION" >/dev/null
  fi
}

verify() { # verify — final identity check; prints compact JSON; enforces --account
  local id
  id=$(identity) || { fail "no usable AWS credentials for profile '${PROFILE:-<ambient>}'"; return 2; }
  local got_account got_arn
  got_account=$(printf '%s' "$id" | python3 -c 'import json,sys; print(json.load(sys.stdin)["account"])' 2>/dev/null \
    || printf '%s' "$id" | grep -oE '"account"[^,]*' | grep -oE '[0-9]{12}')
  got_arn=$(printf '%s' "$id" | grep -oE '"arn"[[:space:]]*:[[:space:]]*"[^"]*"' | head -1 | cut -d'"' -f4)
  if [ -n "$ACCOUNT" ] && [ "$got_account" != "$ACCOUNT" ]; then
    fail "AWS identity is account $got_account but the target is $ACCOUNT — wrong profile/credentials"
    printf 'If another profile signs in to %s, re-run with --profile <NAME> (list them: aws configure list-profiles)\n' "$ACCOUNT"
    return 7
  fi
  # Terraform's AWS provider needs a region in any later session, so leave one on the CLI
  # rather than relying on an AWS_REGION export in this shell.
  configure_region
  ok "AWS credentials ready${PROFILE:+ (profile $PROFILE)} — account ${got_account}"
  printf '{"account":"%s","arn":"%s","profile":"%s"}\n' "$got_account" "${got_arn:-}" "${PROFILE:-ambient}"
}

aws_login_supported() { # aws_login_supported — `aws login` ships in AWS CLI >= 2.32.0
  local ver major minor
  ver=$(aws --version 2>/dev/null | sed -n 's|^aws-cli/\([0-9][0-9.]*\).*|\1|p')
  [ -n "$ver" ] || return 1
  major=${ver%%.*}
  minor=${ver#*.}
  minor=${minor%%.*}
  [ "$major" -gt 2 ] 2>/dev/null && return 0
  [ "$major" -eq 2 ] && [ "$minor" -ge 32 ]
}

if [ -n "$PROFILE" ]; then
  export AWS_PROFILE="$PROFILE"
else
  unset AWS_PROFILE
fi

# --- case 1: explicit profile --------------------------------------------------------
if [ -n "$PROFILE" ]; then
  if id=$(identity); then
    verify; exit "$?"
  fi
  if [ -z "$(aws configure get login_session --profile "$PROFILE" 2>/dev/null || true)" ]; then
    fail "profile '$PROFILE' has no working credentials — fix its credentials, or re-run without --profile, AWS_PROFILE and AWS_DEFAULT_PROFILE to sign in with 'aws login'"
    exit 2
  fi
# --- case 2: ambient credentials ------------------------------------------------------
elif id=$(identity); then
  verify; exit "$?"
fi

# --- case 3: browser sign-in via `aws login` ------------------------------------------
PROFILE="${PROFILE:-default}"
export AWS_PROFILE="$PROFILE"
log "no usable credentials — browser sign-in via 'aws login --profile $PROFILE --region $REGION'"
if ! aws_login_supported; then
  fail "an older AWS CLI is installed — 'aws login' requires AWS CLI >= 2.32.0 (SSO is not a fallback)"
  fail "upgrade it: https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html"
  exit 5
fi
command -v python3 >/dev/null 2>&1 || { fail "python3 is required to bound browser sign-in to $LOGIN_TIMEOUT seconds"; exit 5; }

# 60-second timeout (room for a password plus MFA; AWS_LOGIN_TIMEOUT overrides it): the OAuth
# callback can silently never arrive — e.g. when the browser
# runs on another device (SSH/remote-desktop setups), or when the sign-in detours through
# a re-login ("session expired") and the redirect is lost. Without a timeout, the script
# would hang forever on a callback that will never come. Python's standard library works
# on macOS as well as Linux, preserves terminal stdin, and kills/reaps the CLI on timeout.
if python3 -c '
import subprocess, sys
try:
    result = subprocess.run(sys.argv[2:], timeout=int(sys.argv[1]))
except subprocess.TimeoutExpired:
    sys.exit(124)
except KeyboardInterrupt:
    sys.exit(130)
sys.exit(result.returncode)
' "$LOGIN_TIMEOUT" aws login --profile "$PROFILE" --region "$REGION"; then
  verify; exit "$?"
else
  rc=$?
fi

if [ "$rc" -eq 124 ]; then
  fail "'aws login' did not finish within $LOGIN_TIMEOUT seconds"
  printf 'Sign in to your AWS account in this browser: https://console.aws.amazon.com\n'
  printf 'Then re-run: %q --profile %q --region %q' "$0" "$PROFILE" "$REGION"
  if [ -n "$ACCOUNT" ]; then printf ' --account %q' "$ACCOUNT"; fi
  printf '\n'
  printf 'The browser session cookie makes the retry OAuth callback land without another prompt.\n'
  exit 6
fi

fail "'aws login' failed (exit $rc) — no credentials were written"
exit 2
