#!/usr/bin/env python3
"""dd-github-copilot-onboarding helper.

Seven subcommands, one script:

- `accounts`       list the connected GitHub Copilot accounts
                    (GET /api/ui/oauth-integrations/github-copilot/accounts).
- `orgs`           list the GitHub orgs the authenticated `gh` user *owns*
                    (role `admin`, state `active`) — read-only, no Datadog
                    credentials; used to suggest the account name before minting.
- `check-org`      probe an org's Copilot usage metrics policy (read-only):
                    GET /orgs/{org}/copilot/metrics/reports/organization-28-day/latest
                    through `gh api`. 200/204 = policy enabled and the caller may
                    read metrics (only owners can, so it also confirms ownership);
                    403 = not authorized; 404 = policy off or the org name is wrong.
- `authorize`      check for existing accounts first — when any are connected
                    the command refuses and lists them unless `--continue` is
                    passed, so the customer decides whether to add another —
                    then mint the installation and print the GitHub authorize
                    URL the Datadog API returns, and open it.
- `wait`           poll the accounts until the consent lands (a new account
                    appears); times out with exit 2.
- `delete-account` remove a connected account by id (cleanup).
- `verify`         poll Datadog (up to 15 minutes) until a Copilot seat-count
                    point newer than the start of the wait arrives for each
                    `org_name:<org>` (or `enterprise_name:<enterprise>`); prints JSON.

The account listing, the wait and the delete ride `pup api` (`--no-agent`, so
output is the raw payload), which supplies authentication from the user's
session. The mint is the one deliberate exception — it POSTs directly to the
site's **UI host** (`app.<site>`, or the site itself for regional/vanity hosts)
with the session's OAuth token read from `pup auth token`. It cannot ride
`pup api`: the minted state token records the host the mint was sent to, and
every post-consent browser step goes back to that host (`/install/redirect` and
the final `/integrations?...` page both do), so the user's browser must finish
the consent on the UI host
where their Datadog browser session lives — exactly what the integration tile's
own Authorize button does (it mints from the browser on the UI host). `pup api`
only attaches credentials to the configured API host (`api.<site>`), so an
absolute-URL mint on the UI host would go out unauthenticated.

The token is held in memory only: never printed, logged, or placed on any
argv — it becomes the mint's `Authorization: Bearer` header (sent with the
stdlib `urllib`) and is dropped right after. The mint response carries the
authorize URL complete (`data.attributes.authUrl`), so the OAuth endpoint and
scope set stay server-side and follow the integration config; this script never
assembles the URL.
"""

import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser

INTEGRATION_BASE = "/api/ui/oauth-integrations/github-copilot"
ACCOUNTS_PATH = INTEGRATION_BASE + "/accounts"
# Same endpoint the integration tile's Authorize button calls: it answers with a
# complete authorize URL, so the OAuth endpoint and scope set stay server-side.
MINT_PATH = INTEGRATION_BASE + "/install/generate-auth-url"

# Timeout for the UI-host mint POST and for reading the session token: one
# request each, so 30s covers a slow network without leaving the user staring
# at a hung command.
MINT_TIMEOUT = 30.0
# `pup auth status` and `gh api` reads are local or single small requests;
# 30s (15s for the one-field login lookup) bounds a hung CLI without failing
# a slow but working one.
CLI_TIMEOUT = 30
GH_LOGIN_TIMEOUT = 15

# The account name is frozen at mint time, so say plainly who may connect one.
OWNERSHIP_PREREQUISITE = (
    "Confirm that the authenticated user is an owner of the organizations or "
    "enterprise you want to fetch data from."
)

# GitHub REST endpoint listing the authenticated user's org memberships.
GH_ORG_MEMBERSHIPS = "/user/memberships/orgs"

# GitHub Copilot metrics report probe. Docs: "REST API endpoints for Copilot metrics",
# https://docs.github.com/en/rest/copilot/copilot-metrics?apiVersion=2026-03-10.
# The latest organization 28-day report is owner-gated: 200 (payload) or 204 (no content)
# means the policy is enabled *and* the caller may read metrics — only org owners, or
# holders of the "View Organization Copilot Metrics" custom-role permission, can — so it
# doubles as an ownership confirmation. 403 is an authorization/ownership failure; 404
# means the policy is off or the org name is wrong. Needs the `read:org` scope (part of
# gh's default login scopes; a minimally-scoped token can false-403).
COPILOT_METRICS_REPORT = "/orgs/{org}/copilot/metrics/reports/organization-28-day/latest"

# Where an org owner turns the policy on (current docs naming — the old "Copilot
# Metrics API" beta name is stale).
COPILOT_METRICS_ENABLE_HINT = (
    "enable it in GitHub: org Settings → Copilot → Policies → Features → "
    "\"Copilot usage metrics\" → Enabled (org owners only); for an enterprise-managed org "
    "the enterprise's \"Copilot usage metrics\" policy must be \"Enabled everywhere\""
)


# Datadog RBAC permission that authorizes installing/connecting an integration.
# Integrations Read (`integrations_read`) is view-only and is NOT sufficient.
PERMISSION_MARKERS = ("403", "forbidden", "permission", "manage_integrations")
PERMISSION_HELP = (
    "This action requires the Integrations Manage (manage_integrations) permission on the Datadog org; "
    "an org admin grants it through RBAC — "
    "https://docs.datadoghq.com/account_management/rbac/permissions/"
)

# --- verify: wait for Copilot metrics to arrive (mirrors CoreWeave's verify_data.py)

# Datadog collects Copilot data about every 10 minutes; 15 leaves room for a
# new connection's first collection and ingestion delay.
VERIFY_MAX_WAIT = 900
VERIFY_INTERVAL = 30  # spots a new seat point within 30s; 30 cheap reads over the 15-minute wait
VERIFY_LOOKUP_RETRY_DELAYS = (5, 10)  # short backoff so a transient read error is not reported as missing data
# Copilot metrics are tagged with the GitHub org (or enterprise) they describe,
# not the Datadog account name.
ORG_TAG = "org_name"
ENTERPRISE_TAG = "enterprise_name"
# Seat counts are stamped with the time Datadog collected them, on every
# collection (usage metrics are stamped with the previous day), so a seat point
# newer than the start of the
# wait proves this connection is collecting — not data from an earlier install.
SEAT_METRICS = {
    ORG_TAG: "github_copilot.organizations.billing.seats.total",
    ENTERPRISE_TAG: "github_copilot.enterprises.billing.seats.total",
}
READ_SCOPE_HINT = "Run `pup auth login --extra-scopes metrics_read,timeseries_query`."



class CopilotError(Exception):
    """User-actionable failure: bad input, unusable pup state, or an API error."""


class PupTimeout(CopilotError):
    """`pup` did not answer before its timeout."""


def permission_error(text):
    """True when an error message looks like a missing-permission failure.

    Datadog answers a missing `manage_integrations` with 403/Forbidden, and the
    message occasionally names the permission instead — match all of those."""
    low = str(text).lower()
    return any(marker in low for marker in PERMISSION_MARKERS)


def with_permission_help(message):
    """Append the RBAC guidance to a permission-shaped error message."""
    if permission_error(message):
        return f"{message} — {PERMISSION_HELP}"
    return message


def normalize_site(site):
    """Lowercase a Datadog site and reject anything that is not a bare hostname."""
    site = (site or "").strip().lower().strip(".")
    if not site or "://" in site or "/" in site or " " in site:
        raise CopilotError(f"invalid Datadog site: {site!r}")
    return site


def site_from_pup(pup="pup", org=None):
    """Derive the authenticated Datadog site from `pup auth status`.

    `org` selects a named (org-keyed) pup session; without it pup reports the
    session for `DD_SITE` (default `datadoghq.com`)."""
    cmd = [pup, "auth", "status"]
    if org:
        cmd += ["--org", org]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=CLI_TIMEOUT)
    except FileNotFoundError:
        raise CopilotError(f"pup not found ({pup!r}) — install the Datadog pup CLI or pass --site")
    except (subprocess.TimeoutExpired, OSError) as e:
        raise CopilotError(f"could not run `pup auth status`: {e}")
    out = (proc.stdout or "") + (proc.stderr or "")
    start = out.find("{")  # tolerate non-JSON preamble (e.g. keychain warnings)
    if start < 0:
        raise CopilotError(
            "`pup auth status` did not return JSON — is 'pup' the Datadog CLI? "
            f"Output began: {out.strip()[:120]!r}"
        )
    try:
        # raw_decode parses the first balanced JSON object, ignoring any trailing
        # non-JSON noise (e.g. a stderr warning appended after stdout's JSON).
        status, _ = json.JSONDecoder().raw_decode(out, start)
    except json.JSONDecodeError:
        raise CopilotError(f"could not parse `pup auth status` output as JSON: {out.strip()[:120]!r}")
    if not isinstance(status, dict) or "site" not in status:
        raise CopilotError(
            "`pup auth status` returned JSON but not a Datadog auth status — is 'pup' the "
            f"Datadog CLI? Output began: {out.strip()[:120]!r}"
        )
    if not status.get("authenticated"):
        raise CopilotError("pup is not authenticated — run `pup auth login`, then retry")
    site = str(status.get("site") or "").strip().lower()
    if not site:
        raise CopilotError("`pup auth status` reported no site")
    return site


# The GitHub Copilot integration is not available on Datadog for Government.
GOV_SITE_SUFFIX = "ddog-gov.com"


def resolve_site(args):
    """--site wins (lowercased); otherwise derive from pup. Refuses gov sites."""
    site = normalize_site(args.site) if args.site else site_from_pup(args.pup, args.org)
    if site == GOV_SITE_SUFFIX or site.endswith("." + GOV_SITE_SUFFIX):
        raise CopilotError(
            f"the GitHub Copilot integration is not available on Datadog for "
            f"Government ({site}) yet"
        )
    return site


def pup_api(pup, site, method, path, org=None, auto_approve=False, timeout=60):
    """One authenticated Datadog API call through `pup api` (raw payload, no envelope).

    `org` (a global pup flag, valid after the subcommand) selects the same named
    org-keyed session `site_from_pup` resolves against. Only the `site`'s API
    host is authenticated; `DD_SITE` in the env is what selects it."""
    cmd = [pup, "api", "--no-agent"]
    if org:
        cmd += ["--org", org]
    cmd += ["--method", method, path]
    if auto_approve:
        cmd += ["--yes"]
    env = dict(os.environ, DD_SITE=site)
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=env)
    except FileNotFoundError:
        raise CopilotError(f"pup not found ({pup!r}) — install the Datadog pup CLI")
    except subprocess.TimeoutExpired as e:
        raise PupTimeout(f"could not run `pup api {method} {path}`: {e}")
    except OSError as e:
        raise CopilotError(f"could not run `pup api {method} {path}`: {e}")


def first_json_object(text):
    """The first JSON object in `text`, or None.

    Tolerates non-JSON preamble and trailing noise (keychain warnings, an HTML
    error page appended after the body) by decoding from the first `{`."""
    start = text.find("{")
    if start < 0:
        return None
    try:
        payload, _ = json.JSONDecoder().raw_decode(text, start)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def api_json(proc, what, allow_empty=False):
    """Parse a `pup api` result: JSON body on stdout; API errors become CopilotError.

    A permission-shaped failure carries the RBAC guidance, whichever call
    produced it — a 403 on the mint and a 403 on a delete are the same missing
    `manage_integrations` grant. `allow_empty` accepts a bodyless 2xx (e.g. the
    delete answers 204 with no payload); stdout is what is checked, so a
    harmless stderr warning (e.g. a keychain notice) does not turn a successful
    empty delete into a failure."""
    out = (proc.stdout or "") + (proc.stderr or "")
    if allow_empty and proc.returncode == 0 and not (proc.stdout or "").strip():
        return {}
    payload = first_json_object(out)
    if payload is not None:
        errors = payload.get("errors")
        if errors:
            # pup reports the status on stderr ("Error: HTTP 403 ..."); keep it so
            # a bare "Not authorized" body still gets the RBAC guidance.
            status = re.search(r"\bHTTP (\d{3})\b", out)
            code = f" (HTTP {status.group(1)})" if status else ""
            raise CopilotError(
                with_permission_help(f"{what} failed{code}: {', '.join(map(str, errors))}")
            )
        if proc.returncode == 0:
            return payload
    raise CopilotError(
        with_permission_help(
            f"{what} failed (pup exit {proc.returncode}): {out.strip()[:200]!r}"
        )
    )


def parse_accounts(payload):
    """Connected accounts from the accounts list: [{account_id, account_name}]."""
    attrs = (payload.get("data") or {}).get("attributes") or {}
    out = []
    for entry in attrs.get("accounts") or []:
        account_id = str(entry.get("accountId") or "").strip()
        name = str(entry.get("accountName") or "").strip()
        if account_id:
            out.append({"account_id": account_id, "account_name": name})
    return sorted(out, key=lambda a: (a["account_name"], a["account_id"]))


def fetch_accounts(pup, site, org=None):
    proc = pup_api(pup, site, "GET", ACCOUNTS_PATH, org=org)
    return parse_accounts(api_json(proc, "listing the GitHub Copilot accounts"))


def parse_json_stream(text):
    """Every JSON value in a stream of concatenated documents.

    `gh api --paginate` emits one document per page; a top-level array endpoint
    therefore yields several arrays back to back, which is not valid JSON on its
    own — decode them one at a time and stop at the first non-JSON byte."""
    values, idx, decoder = [], 0, json.JSONDecoder()
    while idx < len(text):
        while idx < len(text) and text[idx] in " \t\r\n":
            idx += 1
        if idx >= len(text):
            break
        try:
            value, idx = decoder.raw_decode(text, idx)
        except json.JSONDecodeError:
            break
        values.append(value)
    return values


def parse_gh_org_memberships(entries):
    """Owner (`admin`, `active`) orgs from `/user/memberships/orgs`.

    GitHub also reports `member` and `pending` memberships; only an org the
    authenticated user actually owns can be named as the account, so the rest
    are dropped."""
    orgs = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        if entry.get("role") != "admin" or entry.get("state") != "active":
            continue
        login = str((entry.get("organization") or {}).get("login") or "").strip()
        if login:
            orgs.append({"login": login, "role": "admin"})
    return sorted(orgs, key=lambda o: o["login"])


def fetch_admin_orgs(gh="gh"):
    """`(orgs, error)`: the GitHub orgs the authenticated gh user owns.

    Read-only and Datadog-credential-free. Never fatal — a missing or
    unauthenticated `gh`, or an API error, comes back as `([], reason)` so the
    caller can note it and let the user type a name instead."""
    try:
        proc = subprocess.run([gh, "api", "--paginate", GH_ORG_MEMBERSHIPS],
                              capture_output=True, text=True, timeout=CLI_TIMEOUT)
    except FileNotFoundError:
        return [], f"the GitHub CLI was not found ({gh!r})"
    except (subprocess.TimeoutExpired, OSError) as e:
        return [], f"could not run `gh api`: {e}"
    if proc.returncode != 0:
        detail = ((proc.stderr or proc.stdout or "").strip().splitlines() or [""])[0]
        return [], (f"`gh api {GH_ORG_MEMBERSHIPS}` failed (exit {proc.returncode})"
                    + (f": {detail}" if detail else ""))
    entries = []
    for value in parse_json_stream(proc.stdout or ""):
        entries.extend(value if isinstance(value, list) else [value])
    return parse_gh_org_memberships(entries), None


def gh_login(gh="gh"):
    """The authenticated GitHub login, or '' when it cannot be read.

    Reported alongside the orgs so an empty result can name the account that was
    queried — the org may be owned by a different, also-logged-in GitHub user."""
    try:
        proc = subprocess.run([gh, "api", "user", "-q", ".login"],
                              capture_output=True, text=True, timeout=GH_LOGIN_TIMEOUT)
    except (subprocess.TimeoutExpired, OSError):
        return ""
    if proc.returncode != 0:
        return ""
    return (proc.stdout or "").strip()


def gh_api_status(path, gh="gh"):
    """`(status, body, error)`: `gh api --include <path>` plus the HTTP status.

    `gh api` exits non-zero on 4xx/5xx, so the status comes from the response's
    status line that `--include` prints, falling back to the `(HTTP <code>)` gh
    writes into its error text. Never raises: an unusable `gh` comes back as an
    error string."""
    try:
        proc = subprocess.run([gh, "api", "--include", path],
                              capture_output=True, text=True, timeout=CLI_TIMEOUT)
    except FileNotFoundError:
        return None, "", f"the GitHub CLI was not found ({gh!r})"
    except (subprocess.TimeoutExpired, OSError) as e:
        return None, "", f"could not run `gh api`: {e}"
    body = proc.stdout or ""
    status = None
    for line in body.splitlines():
        if line.startswith("HTTP/"):
            parts = line.split()
            if len(parts) > 1 and parts[1].isdigit():
                status = int(parts[1])
            break
    if status is None:
        match = re.search(r"\bHTTP (\d{3})\b", proc.stderr or "")
        status = int(match.group(1)) if match else None
    if status is None:
        detail = ((proc.stderr or body).strip().splitlines() or [""])[0]
        return None, body, (f"`gh api {path}` gave no HTTP status (exit {proc.returncode})"
                            + (f": {detail}" if detail else ""))
    return status, body, None


def check_org_metrics(org, gh="gh"):
    """`(verdict, status, detail)`: probe an org's Copilot usage metrics policy.

    Verdicts: `enabled` (200/204 — policy on and the caller may read metrics, so an
    owner or a holder of the metrics permission), `not-owner` (403 on an org the
    user does not own), `token-permissions` (403 on an org the user *does* own —
    the gh token lacks the scope, ownership is fine), `disabled` (404 — policy
    off, or the org name is wrong), `error` (anything else, including an
    unusable `gh`). A 403 alone cannot tell the two apart, so ownership comes
    from the same org discovery as `orgs`; when that is unavailable the 403
    stays `not-owner` with the token caveat in `detail`."""
    status, _body, error = gh_api_status(COPILOT_METRICS_REPORT.format(org=org), gh)
    if error:
        return "error", None, error
    if status in (200, 204):
        return "enabled", status, ""
    if status == 403:
        owned, discovery_error = fetch_admin_orgs(gh)
        if discovery_error:
            return "not-owner", status, (
                "could not confirm ownership — if you do own this org, the gh token may lack "
                "the Copilot metrics scope instead")
        if org.casefold() in {o["login"].casefold() for o in owned}:
            return "token-permissions", status, ""
        return "not-owner", status, ""
    if status == 404:
        return "disabled", status, ""
    return "error", status, f"unexpected HTTP {status} from the metrics report endpoint"


def cmd_check_org(args):
    """Report whether an org's Copilot usage metrics policy is on (read-only).

    200/204 proves both that the policy is enabled and that the caller is authorized
    (an owner, or a holder of the "View Organization Copilot Metrics" permission), so
    it also answers the ownership question for that org; 403 is the ownership guard;
    404 is the policy-off / wrong-org case worth resolving before a mint is burned."""
    org = (args.organization or "").strip()
    if not org:
        raise CopilotError("a GitHub organization login is required")
    verdict, status, detail = check_org_metrics(org, args.gh)
    message = {
        "enabled": (
            f"Copilot usage metrics policy is ENABLED on {org} (HTTP {status}) — the caller "
            "may read metrics, which only org owners (or holders of the 'View Organization "
            "Copilot Metrics' permission) can"
        ),
        "not-owner": (
            f"NOT AUTHORIZED on {org} (HTTP 403) — the authenticated gh user is not an owner "
            f"of {org}, so the connect will not complete; pick an org you own, or `gh auth "
            "switch` to the owning account and re-run `orgs`"
            + (f" ({detail})" if detail else "")
        ),
        "token-permissions": (
            f"TOKEN LACKS PERMISSION on {org} (HTTP 403) — you own {org}, but GitHub refused "
            "the metrics report to this gh token; grant the scope with `gh auth refresh -h "
            "github.com -s read:org` (or, for a fine-grained token, the "
            "'Copilot metrics' organization permission) and re-run `check-org`"
        ),
        "disabled": (
            f"no metrics report for {org} (HTTP 404) — the policy is off, or the org name is "
            f"wrong. Double-check the org name, then {COPILOT_METRICS_ENABLE_HINT}"
        ),
        "error": detail,
    }[verdict]
    if args.json:
        print(json.dumps({"org": org, "status": status, "verdict": verdict,
                          "message": message}, indent=2))
    elif verdict == "enabled":
        print(message)
    else:
        print(f"error: {message}", file=sys.stderr)
    return 0 if verdict == "enabled" else 2


def ui_host_for(site):
    """Browser-facing UI host for a Datadog site.

    Bare domains (`datadoghq.com`, `datadoghq.eu`) serve the UI on `app.<site>`;
    regional sites (`us3.datadoghq.com`, `ap1.datadoghq.com`, ...) and vanity
    hosts serve it on the site itself. No site list to keep current: a new
    region follows the same rule. The mint must land on this host, the one the
    integration tile mints from and the user's browser session lives on."""
    return site if site.count(".") >= 2 else "app." + site


def pup_auth_token(pup="pup", site=None, org=None):
    """Read the pup session's OAuth access token — in memory only.

    `pup auth token` prints exactly the raw access token on stdout (`--org`
    selects a named session, and the site comes from `DD_SITE` — the command
    takes no `--site`), refreshing it as needed. The token is returned to the
    caller and dropped after the mint: it never reaches an argv, a log, or this
    script's output."""
    cmd = [pup, "auth", "token"]
    if org:
        cmd += ["--org", org]
    env = dict(os.environ, DD_SITE=site) if site else dict(os.environ)
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=MINT_TIMEOUT, env=env)
    except FileNotFoundError:
        raise CopilotError(f"pup not found ({pup!r}) — install the Datadog pup CLI")
    except (subprocess.TimeoutExpired, OSError) as e:
        raise CopilotError(f"could not run `pup auth token`: {e}")
    token = (proc.stdout or "").strip()
    if proc.returncode != 0 or not token:
        raise CopilotError(
            "could not read a session token from `pup auth token` — run `pup auth login` "
            "(see the skill's preflight) and retry"
        )
    return token


def mint_request(ui_host, token, body):
    """The mint POST, built as a stdlib `Request` against the UI host.

    The token rides as the `Authorization` header only — never an argv, never a
    log line — which is why this uses `urllib` rather than shelling out. It is an
    unredirected header, so urllib never forwards it if the mint answers with a
    redirect to another location."""
    request = urllib.request.Request(
        f"https://{ui_host}{MINT_PATH}",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    request.add_unredirected_header("Authorization", f"Bearer {token}")
    return request


def http_post(request, timeout=MINT_TIMEOUT):
    """POST `request`; return `(status, body_text)`.

    Error statuses (401/403/...) come back as their status rather than raising,
    so the caller maps them like `api_json` does for `pup api`."""
    try:
        try:
            with urllib.request.urlopen(request, timeout=timeout) as resp:
                return resp.getcode(), resp.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "replace") if getattr(e, "fp", None) else ""
            return e.code, body
    except urllib.error.URLError as e:
        raise CopilotError(f"could not reach the Datadog UI host: {e.reason}")
    except TimeoutError:
        # A stall while reading the body raises a bare TimeoutError, not URLError.
        raise CopilotError("timed out reading the Datadog UI response — retry the mint")


def _mint_error(status, text):
    """Map a non-2xx mint response to an actionable error.

    A permission-shaped body (403) carries the RBAC guidance; a 401 means the
    pup session token itself was rejected, so it routes to re-authentication."""
    payload = first_json_object(text)
    detail = ", ".join(map(str, payload.get("errors", []))) if payload else ""
    if not detail:
        detail = text.strip()[:200] or "empty response"
    if status == 401:
        return CopilotError(
            "minting the GitHub Copilot installation failed: 401 unauthorized — the pup session "
            "token was rejected; run `pup auth login` and retry"
        )
    return CopilotError(
        with_permission_help(
            f"minting the GitHub Copilot installation failed (HTTP {status}): {detail}"
        )
    )


def minted_auth_url(status, text):
    """The authorize URL from a mint response, or a CopilotError."""
    if not 200 <= status < 300:
        raise _mint_error(status, text)
    payload = first_json_object(text)
    if payload is None:
        raise CopilotError(
            "minting the GitHub Copilot installation failed: unusable response "
            f"{text.strip()[:200]!r}"
        )
    attrs = (payload.get("data") or {}).get("attributes") or {}
    auth_url = str(attrs.get("authUrl") or "").strip()
    if not auth_url:
        raise CopilotError("the mint returned no authorize URL")
    return auth_url


def authorize_url(pup, site, org=None, account_name="", enterprise_name=None, tags=()):
    """Mint the installation and return the GitHub authorize URL.

    POSTs `/install/generate-auth-url` to the site's **UI host** with the
    session's bearer token — the very call the integration tile's Authorize
    button makes — so the Datadog API answers with a complete, ready-to-open
    authorize URL (`data.attributes.authUrl`) and the state token binds the host
    the browser will finish the consent on (see the module docstring for why
    this is the one call that does not ride `pup api`). The call also creates the
    placeholder account, and `account_name` is frozen into it at mint time;
    `enterprise_name` covers Copilot licenses managed at the GitHub Enterprise
    Cloud level. `tags` become the account's tags: the integration reads them from
    `settings.tags` as one comma-separated string when the consent completes."""
    settings = {"enterpriseName": enterprise_name} if enterprise_name else {}
    if tags:
        settings["tags"] = ",".join(tags)
    body = {"data": {"type": "generateAuthUrl",
                    "attributes": {"name": account_name, "settings": settings}}}
    token = pup_auth_token(pup, site=site, org=org)
    try:
        status, text = http_post(mint_request(ui_host_for(site), token, body))
    finally:
        token = None  # held in memory only; never printed or logged
    return minted_auth_url(status, text)


def named_accounts(accounts, account_name):
    """Accounts whose name is `account_name` (case-insensitive, like GitHub logins)."""
    want = (account_name or "").strip().lower()
    return [a for a in accounts if want and a["account_name"].lower() == want]


def _wait_timeout_error(timeout, site, account_name):
    target = f"account {account_name!r}" if account_name else "a new account"
    message = (
        f"timed out after {timeout:g}s waiting for {target} to appear on {site} — the GitHub "
        "consent has not landed. Check that the browser completed GitHub's Authorize screen "
        "(the state is valid ~30 minutes but the account only appears after the click), that "
        "the Datadog session's org is the one this mint bound, and that GitHub did not reject "
        "the redirect (the app's registered callback must cover this site); then re-run "
        "`authorize --open` and `wait`"
    )
    return CopilotError(message)


def wait_for_account(fetch, account_name=None, interval=5.0, timeout=300.0, site=None):
    """Poll `fetch` (returning account dicts) until the connect lands.

    With `account_name` the loop returns as soon as an account with that name
    is listed — including on the first call, so a consent that landed before
    the poll started still counts (`authorize` refuses a name that is already
    connected, so the match is this run's connect); unrelated new accounts keep
    it waiting. Without a name, the account set is snapshotted on the first
    call and any new account completes it. Raises CopilotError
    on timeout. Prints nothing: the caller reports once. `fetch` and the clock
    are injectable so tests drive faked sequences."""
    if interval <= 0:
        raise CopilotError(f"--interval must be greater than zero (got {interval:g})")
    if timeout <= 0:
        raise CopilotError(f"--timeout must be greater than zero (got {timeout:g})")
    clock, sleep = time.monotonic, time.sleep
    deadline = clock() + timeout
    accounts = list(fetch())
    baseline = {a["account_id"] for a in accounts}
    while True:
        if account_name:
            match = named_accounts(accounts, account_name)
            if match:
                return match
        remaining = deadline - clock()
        if remaining <= 0:
            raise _wait_timeout_error(timeout, site, account_name)
        sleep(min(interval, remaining))
        accounts = list(fetch())
        if not account_name:
            new = [a for a in accounts if a["account_id"] not in baseline]
            if new:
                return new


def cmd_orgs(args):
    """Report the GitHub orgs the authenticated gh user owns (read-only).

    An empty result is a note (exit 0), not an error; an unusable gh (missing, not
    logged in, or an API error) prints an error and exits 2, and the caller then
    asks the user for the login and orgs instead."""
    orgs, error = fetch_admin_orgs(args.gh)
    if error:
        print(f"error: could not discover GitHub orgs — {error}. {OWNERSHIP_PREREQUISITE}",
              file=sys.stderr)
        return 2
    login = gh_login(args.gh)
    if args.json:
        print(json.dumps({"gh_login": login or None, "orgs": orgs}, indent=2))
        return 0
    print(f"gh user: {login or '(unknown)'}")
    for o in orgs:
        print(o["login"])
    if not orgs:
        print(
            f"note: no GitHub org with an owner (admin) role and active membership was found "
            f"for the authenticated gh user ({login or 'unknown'}) — the account may only be a "
            f"member, or the org may be owned by a different GitHub account (run `gh auth "
            f"switch` and re-run this command). {OWNERSHIP_PREREQUISITE}",
            file=sys.stderr,
        )
    return 0


def cmd_accounts(args):
    site = resolve_site(args)
    accounts = fetch_accounts(args.pup, site, args.org)
    if not accounts:
        print(
            "error: no GitHub Copilot account is connected on this org — run the `authorize` "
            "subcommand with --open to start the flow",
            file=sys.stderr,
        )
        return 2
    if args.json:
        print(json.dumps({"site": site, "accounts": accounts}, indent=2))
    else:
        print(f"site:     {site}")
        for a in accounts:
            print(f"  {a['account_name'] or '(unnamed)'} ({a['account_id']})")
    return 0


def normalize_tags(values):
    """Account tags as given, trimmed; refuse ones the comma-separated setting would split."""
    tags = [value.strip() for value in values or ()]
    bad = [tag for tag in tags if not tag or any(c.isspace() or c == "," for c in tag)]
    if bad:
        raise CopilotError(
            f"invalid tag(s) {bad!r}: tags must be non-empty and contain no whitespace or commas"
        )
    return tags


def cmd_authorize(args):
    tags = normalize_tags(args.tags)
    site = resolve_site(args)

    # The customer decides whether to add another account when the integration
    # already exists: refuse and list what is connected unless --continue.
    existing = fetch_accounts(args.pup, site, args.org)
    if existing and not args.continue_setup:
        raise CopilotError(
            "GitHub Copilot accounts are already connected on this org: "
            + ", ".join(a["account_name"] or a["account_id"] for a in existing)
            + " — confirm with the customer whether they want to add another, "
            "then re-run with --continue"
        )

    # The connection is a GitHub user's grant (it collects every org that user
    # owns), so it is named after the GitHub login by default.
    account_name = args.account_name
    if account_name is None:
        account_name = gh_login(args.gh)
        if account_name:
            print(f"account name (frozen at mint time): {account_name}", file=sys.stderr)

    # Reconnecting a GitHub user that already has an account would duplicate its
    # metrics, and `wait --account-name` treats any account with the name as
    # this run's connect — so a connected name is refused even with --continue.
    clash = named_accounts(existing, account_name)
    if clash:
        raise CopilotError(
            f"a GitHub Copilot account named {clash[0]['account_name']!r} is already connected "
            f"({clash[0]['account_id']}) — switch GitHub user (`gh auth switch`), or remove it with "
            f"`delete-account {clash[0]['account_id']}` first to reconnect it"
        )

    url = authorize_url(args.pup, site, args.org, account_name=account_name,
                        enterprise_name=args.enterprise_name, tags=tags)
    opened = False
    if args.open:
        opened = webbrowser.open(url)
        if not opened:
            print(f"error: could not open a browser — open the URL manually: {url}", file=sys.stderr)
            return 3
    if args.json:
        print(json.dumps({
            "site": site,
            "url": url,
            "account_name": account_name,
            "existing_accounts": [a["account_name"] or a["account_id"] for a in existing],
            "opened_browser": opened,
        }, indent=2))
    else:
        print(url)
    return 0


def cmd_wait(args):
    """Block until the consent shows up as a new account, then report it once.

    Meant to be started as a background process right after the authorize URL
    is opened, so the flow proceeds on the connect itself instead of on the
    user saying they clicked Authorize."""
    site = resolve_site(args)
    landed = wait_for_account(
        lambda: fetch_accounts(args.pup, site, args.org),
        account_name=args.account_name,
        interval=args.interval,
        timeout=args.timeout,
        site=site,
    )
    if args.json:
        print(json.dumps({"site": site, "new_accounts": landed}, indent=2))
    else:
        print("github copilot connected: "
              + ", ".join(a["account_name"] or a["account_id"] for a in landed))
    return 0


def cmd_delete_account(args):
    site = resolve_site(args)
    proc = pup_api(args.pup, site, "DELETE", f"{INTEGRATION_BASE}/account/{args.account_id}",
                   org=args.org, auto_approve=True)
    api_json(proc, f"deleting account {args.account_id}", allow_empty=True)
    remaining = fetch_accounts(args.pup, site, args.org)
    gone = all(a["account_id"] != args.account_id for a in remaining)
    if args.json:
        print(json.dumps({
            "site": site,
            "account_id": args.account_id,
            "remaining": [a["account_name"] or a["account_id"] for a in remaining],
            "deleted": gone,
        }, indent=2))
    else:
        print(f"deleted {args.account_id!r} on {site} "
              f"(remaining: {', '.join(a['account_name'] or a['account_id'] for a in remaining) or 'none'})")
    return 0 if gone else 2


class VerifyError(CopilotError):
    def __init__(self, code, message, **details):
        super().__init__(message)
        self.code = code
        self.details = details


class _Transient(Exception):
    """A poll attempt failed in a way the next attempt may not."""


def verify_get(pup, site, path, org=None, timeout=60):
    """GET through pup for the verify poll: 401/403 is fatal, other failures transient."""
    try:
        proc = pup_api(pup, site, "GET", path, org=org, timeout=max(timeout, 1))
    except PupTimeout as e:
        raise _Transient(str(e))
    if proc.returncode != 0:
        status = re.search(r"\bHTTP (\d{3})\b", proc.stderr or "")
        status = int(status.group(1)) if status else None
        if status in (401, 403):
            raise VerifyError(
                "datadog_read_not_permitted", "pup's Datadog session cannot read metrics",
                http_status=status, remediation=READ_SCOPE_HINT,
            )
        raise _Transient((proc.stderr or "pup request failed").strip()[:200])
    payload = first_json_object(proc.stdout or "")
    if payload is None:
        raise _Transient("pup returned a non-JSON response")
    return payload


def _tag_value(value):
    """Approximate Datadog tag normalization."""
    normalized = re.sub(r"[^a-z0-9_\-./:]", "_", value.lower())
    return re.sub(r"_+", "_", normalized).strip("_")


def latest_seat_point(pup, site, org, scope, since, now, timeout):
    """Timestamp (epoch seconds) of the newest seat point for `scope` at or after
    `since`, or None."""
    metric = SEAT_METRICS[scope.split(":", 1)[0]]
    query = urllib.parse.urlencode(
        {"from": int(since), "to": int(now) + 60, "query": f"max:{metric}{{{scope}}}"})
    # Public metrics query API:
    # https://docs.datadoghq.com/api/latest/metrics/#query-timeseries-points
    payload = verify_get(pup, site, f"/api/v1/query?{query}", org, timeout)
    stamps = [point[0] / 1000 for series in payload.get("series") or []
              for point in series.get("pointlist") or []
              if isinstance(point, list) and len(point) == 2 and point[1] is not None]
    fresh = [t for t in stamps if t >= since]
    return max(fresh) if fresh else None


def verify_scopes(github_orgs=(), enterprise_name=None):
    scopes = [f"{ORG_TAG}:{_tag_value(o)}" for o in github_orgs if _tag_value(o or "")]
    if enterprise_name and _tag_value(enterprise_name):
        scopes.append(f"{ENTERPRISE_TAG}:{_tag_value(enterprise_name)}")
    return list(dict.fromkeys(scopes))


def verify(pup, site, github_orgs=(), enterprise_name=None, org=None,
           max_wait=VERIFY_MAX_WAIT, interval=VERIFY_INTERVAL, clock=time.monotonic,
           wall_clock=time.time, sleep=time.sleep):
    scopes = verify_scopes(github_orgs, enterprise_name)
    if not scopes:
        raise VerifyError("github_org_unknown",
                          "pass the GitHub org(s) as --github-org (or --enterprise-name)")
    start = clock()
    deadline = start + min(max_wait, VERIFY_MAX_WAIT)

    def remaining():
        return max(deadline - clock(), 0)

    # Retry transient lookup failures (e.g. 429/503) within the same deadline.
    delays = iter(VERIFY_LOOKUP_RETRY_DELAYS)
    while True:
        try:
            accounts = parse_accounts(verify_get(pup, site, ACCOUNTS_PATH, org, remaining()))
            break
        except _Transient as e:
            wait = next(delays, interval)
            if remaining() <= wait:
                raise VerifyError("account_lookup_failed",
                                  "could not list the GitHub Copilot accounts", error=str(e))
            sleep(wait)
    if not accounts:
        raise VerifyError("account_not_found", "no GitHub Copilot account is connected")

    since = wall_clock()
    checks = {scope: {"seen": False, "metric": SEAT_METRICS[scope.split(":", 1)[0]],
                      "last_point": None} for scope in scopes}
    attempts, last_error = 0, None
    while True:
        attempts += 1
        for scope, check in checks.items():
            if check["seen"]:
                continue
            try:
                stamp = latest_seat_point(pup, site, org, scope, since, wall_clock(), remaining())
            except _Transient as e:
                last_error = str(e)
                continue
            if stamp is not None:
                check.update(seen=True, last_point=time.strftime(
                    "%Y-%m-%dT%H:%M:%SZ", time.gmtime(stamp)))
        pending = [s for s, c in checks.items() if not c["seen"]]
        # Poll through the deadline: collections are ~600s apart, so a point that
        # lands in the last interval must still be caught.
        if not pending or remaining() <= 0:
            break
        sleep(min(interval, remaining()))

    arrived = [s for s, c in checks.items() if c["seen"]]
    if not pending:
        status = "succeeded"
        message = "Datadog is receiving new GitHub Copilot data for " + ", ".join(arrived) + "."
    elif arrived:
        status = "partial"
        message = (f"Datadog is receiving new GitHub Copilot data for {', '.join(arrived)}, but "
                   f"not yet for {', '.join(pending)}; check again later.")
    else:
        status = "failed"
        message = ("No new GitHub Copilot data arrived within the wait (Datadog collects it "
                   "about every 10 minutes, and a new connection can take longer); check "
                   "again later before treating this as broken.")
    result = {
        "status": status,
        "site": site,
        "since": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(since)),
        "checks": checks,
        "arrived": arrived,
        "pending": pending,
        "attempts": attempts,
        "elapsed_seconds": round(clock() - start),
        "max_wait_seconds": round(deadline - start),
        "message": message,
    }
    if arrived:
        host = ui_host_for(site)
        result["links"] = {
            scope: f"https://{host}/metric/explorer?" + urllib.parse.urlencode(
                {"exp_metric": checks[scope]["metric"], "exp_scope": scope})
            for scope in arrived
        }
    if pending and last_error:
        result["last_error"] = last_error
    return result


def cmd_verify(args):
    try:
        site = resolve_site(args)
        result = verify(args.pup, site, args.github_org or (), args.enterprise_name, args.org,
                        max_wait=args.timeout)
    except VerifyError as e:
        error = {"code": e.code, "message": str(e), "details": e.details}
    except CopilotError as e:
        error = {"code": "setup_error", "message": str(e)}
    else:
        print(json.dumps(result, indent=2))
        return 0 if result["status"] == "succeeded" else 2
    print(json.dumps({"status": "error", "error": error}, indent=2))
    return 2


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="GitHub Copilot + Datadog: OAuth authorize URL and account setup."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    def common(p):
        p.add_argument("--site", help="Datadog site; default: derive from `pup auth status`")
        p.add_argument("--pup", default="pup", help="pup binary used for auth and API calls")
        p.add_argument(
            "--org",
            help="named pup org session (e.g. staging); passed to `pup auth status` and `pup api`",
        )
        p.add_argument("--json", action="store_true", help="emit machine-readable JSON")

    orgs = sub.add_parser(
        "orgs", help="list the GitHub orgs the authenticated gh user owns (read-only)"
    )
    orgs.add_argument("--gh", default="gh", help="path to the GitHub CLI to query")
    orgs.add_argument("--json", action="store_true", help="emit machine-readable JSON")

    check = sub.add_parser(
        "check-org", help="probe an org's Copilot usage metrics policy through gh (read-only)"
    )
    check.add_argument("organization", help="GitHub organization login to probe")
    check.add_argument("--gh", default="gh", help="path to the GitHub CLI to query")
    check.add_argument("--json", action="store_true", help="emit machine-readable JSON")

    accounts = sub.add_parser("accounts", help="list connected GitHub Copilot accounts")
    common(accounts)

    authz = sub.add_parser(
        "authorize", help="mint the installation and return the GitHub authorize URL "
                          "(guards on existing accounts)"
    )
    authz.add_argument("--open", action="store_true", help="open the authorize URL in the user's browser")
    authz.add_argument(
        "--continue", dest="continue_setup", action="store_true",
        help="proceed even when accounts are already connected (the customer confirmed they "
             "want to add another)",
    )
    authz.add_argument(
        "--account-name",
        help="name for the new account (frozen at mint time). Default (best-effort, via the gh "
             "CLI): the authenticated GitHub login, else the account is named by its creation time",
    )
    authz.add_argument(
        "--enterprise-name",
        help="GitHub enterprise name, when Copilot licenses are managed at the GitHub "
             "Enterprise Cloud level",
    )
    authz.add_argument(
        "--tags", action="append", default=[], metavar="TAG",
        help="tag for the new account, such as team:dev-tools; repeat for more",
    )
    authz.add_argument("--gh", default="gh", help="path to the GitHub CLI used for the name default")
    common(authz)

    wait = sub.add_parser(
        "wait",
        help="poll until the GitHub consent lands (start it in the background)",
    )
    wait.add_argument(
        "--account-name", help="wait for this account name (ignore other new accounts)"
    )
    wait.add_argument(
        "--interval", type=float, default=5.0, help="seconds between polls (default: 5)"
    )
    wait.add_argument(
        "--timeout", type=float, default=300.0, help="give up after this many seconds (default: 300)"
    )
    common(wait)

    delete = sub.add_parser("delete-account", help="remove a connected account by id")
    delete.add_argument("account_id", help="account id as listed by `accounts`")
    common(delete)

    ver = sub.add_parser(
        "verify", help="poll up to 15 minutes for the orgs' github_copilot.* metrics "
                       "(always prints JSON)"
    )
    ver.add_argument(
        "--github-org", action="append",
        help="GitHub org whose metrics to look for (the org_name tag); repeat for each org",
    )
    ver.add_argument(
        "--enterprise-name", help="GitHub enterprise to also look for (the enterprise_name tag)",
    )
    ver.add_argument(
        "--timeout", type=float, default=VERIFY_MAX_WAIT,
        help=f"give up after this many seconds (default and max: {VERIFY_MAX_WAIT})",
    )
    common(ver)

    args = parser.parse_args(argv)
    try:
        if args.command == "check-org":
            return cmd_check_org(args)
        if args.command == "orgs":
            return cmd_orgs(args)
        if args.command == "accounts":
            return cmd_accounts(args)
        if args.command == "authorize":
            return cmd_authorize(args)
        if args.command == "wait":
            return cmd_wait(args)
        if args.command == "verify":
            return cmd_verify(args)
        return cmd_delete_account(args)
    except CopilotError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
