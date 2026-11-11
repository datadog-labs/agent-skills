#!/usr/bin/env python3
"""dd-slack-integration helper.

Seven subcommands, one script:

- `authorize`  resolve the Datadog site from `pup auth status` and mint the Slack
               OAuth authorize URL through the Datadog API for a commercial
               (slack.com) workspace, opening it so the user completes the grant.
               The minted host is verified to be Slack before the URL is opened.
- `audit-logs`  mint the separate Slack audit-logs OAuth authorize URL (the
               `auditlogs:read` user scope) — an optional follow-up for
               Enterprise Grid/Select orgs after the workspace connect.
- `wait`       poll the Slack integration until the OAuth connect lands (a new
               account appears), so nobody has to report back that they clicked
               Allow; times out with exit 2.
- `accounts`    check the Slack integration is configured and report its account
               name(s) and channels (GET /api/v1/integration/slack).
- `suggest-channels` suggest up to three channels from the connected workspace for
               the user to pick from (optionally matching `--query`); read-only.
- `add-channel` POST the user-chosen channel to the Slack integration account
               (POST /api/v1/integration/slack/configuration/accounts/{account}/channels).
- `test-notification` POST a Datadog test message to a channel on the configured
               account (POST /api/v2/integration/send-test-notification/slack-{account}-{channel})
               so the user sees the wiring work end to end.

Exit codes: 0 success; 1 error (pup missing or unauthenticated, bad input, an
API error such as a 403); 2 an expected "not there" outcome — `accounts` found
no Slack account, `wait` timed out, or `add-channel` posted but the channel is
not listed; 3 the browser could not be opened.

All Datadog API calls go through `pup api` (`--no-agent`, so output is the raw
payload), which supplies authentication from the user's session. The authorize
mint passes a full `https://api.<site>/...` endpoint so pup skips its `/api` path
prefix and sends credentials to the allowlisted API host — the mint route
(`/auth/integration/slack/...`) has no `/api` prefix, so passing it as a bare
absolute path would 404 at `api.<site>/api/auth/...`.
"""

import argparse
import json
import os
import subprocess
import sys
import time
import webbrowser
from pathlib import Path
from urllib.parse import parse_qs, quote, urlsplit

INTEGRATION_BASE = "/api/v1/integration/slack"
# The error GET INTEGRATION_BASE returns on an org with no Slack integration yet.
INTEGRATION_NOT_INSTALLED = "slack not found"
# Datadog API route that mints the Slack OAuth authorize URL for a workspace install.
AUTHORIZE_PATH = "/auth/integration/slack/install/authorize-url"
# Datadog API route that mints the Slack audit-logs OAuth authorize URL (a
# separate grant with the `auditlogs:read` user scope; enterprise orgs only).
AUDIT_LOGS_PATH = "/api/ui/slack/auth/audit-logs/authorize-url"
# Datadog routes that list the connected Slack workspaces and a workspace's channels
# (the same lists Datadog's web app uses; the channel list is kept in sync with Slack).
WORKSPACES_PATH = "/api/ui/slack/workspaces"
CHANNELS_PATH = "/api/ui/slack/channels"
# Name parts that suggest a channel for alerts, most likely first; used to rank
# suggestions when the user has not typed anything yet.
ALERT_CHANNEL_HINTS = (
    "alerts", "alert", "alerting", "monitoring", "monitors", "monitor", "datadog",
    "ops", "oncall", "on-call", "sre", "devops", "infra", "incidents",
)
# Datadog API route that sends a test notification to a Slack channel; the handle
# is "slack-{account}-{channel}" with the channel's leading '#' stripped.
TEST_NOTIFICATION_BASE = "/api/v2/integration/send-test-notification"

# The mint returns a URL on the commercial Slack host; the host is verified
# before the URL is opened so the user is never sent somewhere unintended.
SLACK_OAUTH_HOST = "slack.com"

# Datadog RBAC permission that authorizes installing/connecting an integration.
# Integrations Read (`integrations_read`) is view-only and is NOT sufficient.
PERMISSION_MARKERS = ("403", "forbidden", "permission", "manage_integrations")
PERMISSION_HELP = (
    "you may need the Integrations Manage (manage_integrations) permission in your Datadog org; "
    "an org admin grants it through RBAC — "
    "https://docs.datadoghq.com/account_management/rbac/permissions/"
)


class SlackError(Exception):
    """User-actionable failure: bad input, unusable pup state, or an API error."""


class WaitTimeout(SlackError):
    """`wait` gave up with no new account: an expected outcome (exit 2), not an error."""


def permission_error(text):
    """True when an error message looks like a missing-permission failure.

    the Datadog API answers a missing `manage_integrations` with 403/Forbidden, and the
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
        raise SlackError(f"invalid Datadog site: {site!r}")
    return site


def api_host(site):
    """Map a Datadog site to its API host (e.g. datadoghq.com -> api.datadoghq.com).

    `api.<site>` is the host pup is allowed to attach credentials to; the
    `app.<site>` hosts are not, so API calls must use this form."""
    return f"api.{normalize_site(site)}"


def site_from_pup(pup="pup", org=None):
    """Derive the authenticated Datadog site from `pup auth status`.

    `org` selects a named (org-keyed) pup session; without it pup reports the
    session for `DD_SITE` (default `datadoghq.com`)."""
    cmd = [pup, "auth", "status"]
    if org:
        cmd += ["--org", org]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    except FileNotFoundError:
        raise SlackError(f"pup not found ({pup!r}) — install the Datadog pup CLI or pass --site")
    except (subprocess.TimeoutExpired, OSError) as e:
        raise SlackError(f"could not run `pup auth status`: {e}")
    out = (proc.stdout or "") + (proc.stderr or "")
    start = out.find("{")  # tolerate non-JSON preamble (e.g. keychain warnings)
    if start < 0:
        raise SlackError(
            "`pup auth status` did not return JSON — is 'pup' the Datadog CLI? "
            f"Output began: {out.strip()[:120]!r}"
        )
    try:
        # raw_decode parses the first balanced JSON object, ignoring any trailing
        # non-JSON noise (e.g. a stderr warning appended after stdout's JSON).
        status, _ = json.JSONDecoder().raw_decode(out, start)
    except json.JSONDecodeError:
        raise SlackError(f"could not parse `pup auth status` output as JSON: {out.strip()[:120]!r}")
    if not isinstance(status, dict) or "site" not in status:
        raise SlackError(
            "`pup auth status` returned JSON but not a Datadog auth status — is 'pup' the "
            f"Datadog CLI? Output began: {out.strip()[:120]!r}"
        )
    if not status.get("authenticated"):
        raise SlackError("pup is not authenticated — run `pup auth login`, then retry")
    site = str(status.get("site") or "").strip().lower()
    if not site:
        raise SlackError("`pup auth status` reported no site")
    return site


# Datadog for Government is out of scope for the agentic onboarding skills.
GOV_SITE = "ddog-gov.com"


def resolve_site(args):
    """--site wins (validated, lowercased); otherwise derive from pup. Refuses gov sites."""
    site = normalize_site(args.site) if args.site else site_from_pup(args.pup, args.org)
    if site == GOV_SITE or site.endswith("." + GOV_SITE):
        raise SlackError(
            f"this onboarding skill does not support Datadog for Government ({site}); "
            "set up the integration from the Datadog app instead"
        )
    return site


def pup_api(pup, site, method, path, fields=None, org=None):
    """One authenticated Datadog API call through `pup api` (raw payload, no envelope).

    `org` (a global pup flag, valid after the subcommand) selects the same named
    org-keyed session `site_from_pup` resolves against."""
    cmd = [pup, "api", "--no-agent"]
    if org:
        cmd += ["--org", org]
    cmd += ["--method", method, path]
    for key, value in (fields or {}).items():
        cmd += ["--raw-field", f"{key}={value}"]
    env = dict(os.environ, DD_SITE=site)
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=60, env=env)
    except FileNotFoundError:
        raise SlackError(f"pup not found ({pup!r}) — install the Datadog pup CLI")
    except (subprocess.TimeoutExpired, OSError) as e:
        raise SlackError(f"could not run `pup api {method} {path}`: {e}")


def decode_payload(proc):
    """The first JSON object in a `pup api` result's output, or None."""
    out = (proc.stdout or "") + (proc.stderr or "")
    start = out.find("{")
    if start < 0:
        return None
    try:
        payload, _ = json.JSONDecoder().raw_decode(out, start)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def api_json(proc, what):
    """Parse a `pup api` result: JSON body on stdout; API errors become SlackError.

    A permission-shaped failure carries the RBAC guidance, whichever call
    produced it — a 403 on the OAuth mint and a 403 on a channel write are the
    same missing `manage_integrations` grant."""
    out = (proc.stdout or "") + (proc.stderr or "")
    payload = decode_payload(proc)
    if payload is not None:
        errors = payload.get("errors")
        if errors:
            raise SlackError(
                with_permission_help(f"{what} failed: {', '.join(map(str, errors))}")
            )
        if proc.returncode == 0:
            return payload
    raise SlackError(
        with_permission_help(
            f"{what} failed (pup exit {proc.returncode}): {out.strip()[:200]!r}"
        )
    )


def authorize_url_host(url):
    """Host of a minted authorize URL (empty string when it has none)."""
    return (urlsplit(url).hostname or "").lower()


def check_authorize_url_host(url):
    """Fail when the minted authorize URL does not point at Slack.

    Anything that is not the expected host is not something to put in the user's
    browser, so the URL is verified before it is returned."""
    host = authorize_url_host(url)
    if host == SLACK_OAUTH_HOST:
        return host
    raise SlackError(
        f"minted authorize URL host {host or '<none>'!r} is not {SLACK_OAUTH_HOST} — "
        "not opening it; re-run `authorize` and report the returned URL"
    )


def normalize_channel(channel):
    """Slack channel as Datadog expects it: leading '#', surrounding whitespace stripped."""
    channel = (channel or "").strip()
    if not channel:
        raise SlackError("channel name is empty — pass e.g. '#ops'")
    return channel if channel.startswith("#") else f"#{channel}"


def parse_integration(payload):
    """(account names in order, channels) from GET /api/v1/integration/slack.

    The raw payload has no `accounts` key: account names appear on each
    `channels` entry and each `service_hooks` entry, so collect from both."""
    accounts, seen = [], set()
    for key in ("channels", "service_hooks"):
        for entry in payload.get(key) or []:
            name = str(entry.get("account") or "").strip()
            if name and name not in seen:
                accounts.append(name)
                seen.add(name)
    channels = []
    for entry in payload.get("channels") or []:
        name = str(entry.get("channel_name") or "").strip()
        account = str(entry.get("account") or "").strip()
        if name:
            channels.append({"account": account, "channel": name})
    return accounts, channels


def integration_not_installed(proc):
    """True when the GET answered that the org has no Slack integration at all.

    A fresh org returns `{"errors": ["slack not found"]}` rather than an empty
    payload; that is "nothing connected yet", not a failure. Only that exact
    error matches, so e.g. a `Resource not found` (route missing) still raises."""
    payload = decode_payload(proc) or {}
    errors = [str(e).strip().lower() for e in payload.get("errors") or []]
    return errors == [INTEGRATION_NOT_INSTALLED]


def fetch_integration(pup, site, org=None):
    proc = pup_api(pup, site, "GET", INTEGRATION_BASE, org=org)
    if integration_not_installed(proc):
        return [], []
    return parse_integration(api_json(proc, "listing the Slack integration"))


def _wait_timeout_error(account, timeout, site):
    target = f"Slack account {account!r}" if account else "a new Slack account"
    where = f" on {site}" if site else ""
    return WaitTimeout(
        f"timed out after {timeout:g}s waiting for {target} to appear{where} — the Slack "
        "OAuth grant has not landed. Check that the browser finished the Slack consent "
        "screen with a Datadog app session for this site (and that --site/--org match the "
        "org the grant bound to), re-run `authorize --open` for a fresh URL, then re-run "
        "`wait`"
    )


def wait_for_account(fetch_accounts, account=None, interval=10.0, timeout=300.0, site=None):
    """Poll `fetch_accounts` until the Slack OAuth connect lands.

    The account set is snapshotted on the first call and the loop returns as soon
    as it changes — or, when `account` is given, as soon as that name is present
    (unrelated new accounts keep it waiting). Returns the new account name(s);
    raises SlackError on timeout. Prints nothing: the caller reports once.

    `fetch_accounts` is injectable so tests can drive a faked pup_api sequence.
    `time.monotonic`/`time.sleep` are looked up per call, so patching them on the
    `time` module fakes the clock too."""
    if interval <= 0:
        raise SlackError(f"--interval must be greater than zero (got {interval:g})")
    if timeout <= 0:
        raise SlackError(f"--timeout must be greater than zero (got {timeout:g})")
    clock, sleep = time.monotonic, time.sleep
    deadline = clock() + timeout
    baseline = list(fetch_accounts())
    if account and account in baseline:
        return [account]  # already connected: the connect predates this poll
    while True:
        remaining = deadline - clock()
        if remaining <= 0:
            raise _wait_timeout_error(account, timeout, site)
        sleep(min(interval, remaining))
        names = list(fetch_accounts())
        if account:
            if account in names:
                return [account]
        else:
            new = [name for name in names if name not in baseline]
            if new:
                return new


def authorize_url(pup, site, org=None):
    """Mint the Slack OAuth authorize URL through the Datadog API (`pup api`).

    The endpoint is a full URL so pup drops its `/api` path prefix. The returned
    URL points at the commercial Slack host (slack.com) and its `state` is a
    short-lived (about five minutes) CSRF nonce bound to the org and user. The
    minted host is verified before the URL is returned; a permission-shaped
    failure carries the RBAC guidance from `api_json`, and every other failure
    surfaces as-is (a 401 means the pup token is missing or stale, a 404 means
    this Datadog site does not expose the authorize-url route)."""
    endpoint = f"https://{api_host(site)}{AUTHORIZE_PATH}"
    proc = pup_api(pup, site, "GET", endpoint, org=org)
    payload = api_json(proc, "minting the Slack authorize URL")
    url = str(payload.get("authorizationUrl") or "").strip()
    if not url:
        raise SlackError(
            "minting the Slack authorize URL returned no `authorizationUrl`"
        )
    check_authorize_url_host(url)
    return url


def cmd_authorize(args):
    site = resolve_site(args)
    url = authorize_url(args.pup, site, args.org)
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
            "authorize_url_host": authorize_url_host(url),
            "opened_browser": opened,
        }, indent=2))
    else:
        print(url)
    return 0


def audit_logs_authorize_url(pup, site, org=None):
    """Mint the Slack audit-logs OAuth authorize URL through the Datadog API.

    A follow-up to the workspace connect for Enterprise Grid/Select orgs: this
    is a separate OAuth grant asking for the `auditlogs:read` user scope. The
    route lives under `/api/ui` (already prefixed), so the endpoint passes as a
    bare absolute path. The returned URL's host is verified to be Slack and its
    `user_scope` must carry `auditlogs:read` before the URL is handed back."""
    endpoint = f"{AUDIT_LOGS_PATH}?workspace_environment=commercial"
    proc = pup_api(pup, site, "GET", endpoint, org=org)
    payload = api_json(proc, "minting the Slack audit-logs authorize URL")
    url = str(payload.get("authorizationUrl") or "").strip()
    if not url:
        raise SlackError("minting the Slack audit-logs authorize URL returned no `authorizationUrl`")
    check_authorize_url_host(url)
    scope = (parse_qs(urlsplit(url).query).get("user_scope") or [""])[0]
    if "auditlogs:read" not in scope:
        raise SlackError(
            "minted audit-logs authorize URL lacks the `auditlogs:read` user scope "
            f"(got {scope!r}) — do not open it"
        )
    return url


def cmd_audit_logs(args):
    site = resolve_site(args)
    url = audit_logs_authorize_url(args.pup, site, args.org)
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
            "authorize_url_host": authorize_url_host(url),
            "opened_browser": opened,
        }, indent=2))
    else:
        print(url)
    return 0


def cmd_wait(args):
    """Block until the OAuth connect shows up, then report it once.

    Meant to be started as a background process right after the authorize URL is
    opened, so the flow proceeds on the connect itself instead of on the user
    saying they clicked Allow."""
    site = resolve_site(args)

    def accounts():
        return fetch_integration(args.pup, site, args.org)[0]

    landed = wait_for_account(
        accounts,
        account=args.account,
        interval=args.interval,
        timeout=args.timeout,
        site=site,
    )
    if args.json:
        print(json.dumps(
            {"site": site, "accounts": landed, "account": args.account}, indent=2,
        ))
    else:
        print(f"slack connected: {', '.join(landed)}")
    return 0


def cmd_accounts(args):
    site = resolve_site(args)
    accounts, channels = fetch_integration(args.pup, site, args.org)
    if not accounts:
        print(
            "error: no Slack account is configured on this org — complete the Slack OAuth "
            "grant first (run the `authorize` subcommand with --open)",
            file=sys.stderr,
        )
        return 2
    if args.json:
        print(json.dumps({"site": site, "accounts": accounts, "channels": channels}, indent=2))
    else:
        print(f"site:     {site}")
        print(f"accounts: {', '.join(accounts)}")
        shown, rest = channels[:10], len(channels) - 10
        for ch in shown:
            print(f"  {ch['account']}: {ch['channel']}")
        if rest > 0:
            print(f"  … (+{rest} more channels — use --json for the full list)")
    return 0


def resolve_account(pup, site, org, explicit):
    """The Slack account to act on: the explicitly passed one, else the single
    configured one. Refuses to guess among several — the caller must ask the user."""
    if explicit:
        return explicit
    accounts, _ = fetch_integration(pup, site, org)
    if not accounts:
        raise SlackError("no Slack account is configured — complete the Slack OAuth grant first")
    if len(accounts) > 1:
        raise SlackError(
            "multiple Slack accounts are configured: "
            + ", ".join(accounts)
            + " — pass --account <name> to choose one"
        )
    return accounts[0]


def _name_key(name):
    """Account and workspace names compared loosely: account names replace the
    workspace name's spaces (and other punctuation) with underscores."""
    return "".join(c if c.isalnum() else "_" for c in (name or "").strip().lower())


def workspace_for_account(workspaces, account):
    """The connected Slack workspace an integration account belongs to: the only
    one, else the one whose name matches the account's."""
    if len(workspaces) == 1:
        return workspaces[0]
    matches = [w for w in workspaces if _name_key(w["name"]) == _name_key(account)]
    if len(matches) == 1:
        return matches[0]
    raise SlackError(
        f"could not tell which Slack workspace account {account!r} belongs to "
        f"(connected: {', '.join(w['name'] for w in workspaces) or 'none'})"
    )


def fetch_workspaces(pup, site, org=None):
    payload = api_json(pup_api(pup, site, "GET", WORKSPACES_PATH, org=org),
                       "listing the connected Slack workspaces")
    return [
        {"id": str(item.get("id") or ""), "name": str((item.get("attributes") or {}).get("name") or "")}
        for item in payload.get("data") or []
        if item.get("id")
    ]


def fetch_channel_names(pup, site, team_id, org=None):
    proc = pup_api(pup, site, "GET", CHANNELS_PATH, fields={"filter[team_id]": team_id}, org=org)
    payload = api_json(proc, "listing the workspace's Slack channels")
    names = (str((item.get("attributes") or {}).get("name") or "").strip()
             for item in payload.get("data") or [])
    return [name for name in names if name]


def rank_channels(names, query=None, exclude=(), limit=3):
    """Up to `limit` channel names (with '#'), best match first.

    With a query: exact name, then names starting with it, then names with a part
    (split on - _ .) starting with it, then any name containing it. Without one:
    names with a part from ALERT_CHANNEL_HINTS, earlier hints first. Either way,
    names with digits (per-incident or dated channels) come after the rest, then
    names with fewer parts, then shorter, then alphabetical. Channels in `exclude`
    are skipped."""
    skip = {name.lstrip("#").lower() for name in exclude}
    q = (query or "").strip().lstrip("#").lower()
    scored = []
    for name in set(names):
        lowered = name.lower()
        if lowered in skip:
            continue
        parts = lowered.replace("_", "-").replace(".", "-").split("-")
        if q:
            if lowered == q:
                score = 0
            elif lowered.startswith(q):
                score = 1
            elif any(part.startswith(q) for part in parts):
                score = 2
            elif q in lowered:
                score = 3
            else:
                continue
        else:
            hits = [i for i, hint in enumerate(ALERT_CHANNEL_HINTS)
                    if hint in parts or lowered == hint]
            if not hits:
                continue
            score = min(hits)
        has_digits = any(c.isdigit() for c in lowered)
        scored.append((has_digits, score, len(parts), len(name), lowered, name))
    return [f"#{name}" for *_, name in sorted(scored)[:max(limit, 0)]]


def cmd_suggest_channels(args):
    site = resolve_site(args)
    account = resolve_account(args.pup, site, args.org, args.account)
    workspace = workspace_for_account(fetch_workspaces(args.pup, site, args.org), account)
    names = fetch_channel_names(args.pup, site, workspace["id"], args.org)
    _, configured = fetch_integration(args.pup, site, args.org)
    already = [c["channel"] for c in configured if c["account"] == account]
    suggestions = rank_channels(names, args.query, exclude=already, limit=args.limit)
    if args.json:
        print(json.dumps({
            "site": site,
            "account": account,
            "workspace": workspace,
            "query": args.query,
            "suggestions": suggestions,
            "channels_in_workspace": len(names),
        }, indent=2))
    elif suggestions:
        for name in suggestions:
            print(name)
    else:
        print("no matching channels — ask the user for the channel name", file=sys.stderr)
    return 0


def cmd_add_channel(args):
    site = resolve_site(args)
    channel = normalize_channel(args.channel)
    account = resolve_account(args.pup, site, args.org, args.account)

    path = f"{INTEGRATION_BASE}/configuration/accounts/{quote(account)}/channels"
    proc = pup_api(args.pup, site, "POST", path, fields={"name": channel}, org=args.org)
    created = api_json(proc, f"adding {channel} to Slack account {account!r}")

    # Confirm the channel is now listed on *this* account before claiming success:
    # matching the name alone lets a same-named channel on another configured
    # account satisfy the check.
    _, channels_after = fetch_integration(args.pup, site, args.org)
    listed = any(
        c["channel"] == channel and c["account"] == account for c in channels_after
    )

    if args.json:
        print(json.dumps({
            "site": site,
            "account": account,
            "channel": channel,
            "created": created,
            "listed": listed,
        }, indent=2))
    else:
        print(f"added {channel} to Slack account {account!r} on {site} (listed: {listed})")
    return 0 if listed else 2


def send_test_notification(pup, site, account, channel, org=None):
    """POST a Datadog test notification to a channel (204 No Content on success).

    The route answers 204 with an empty body when the message is sent, so an
    empty successful response is success; error bodies are JSON and go through
    the same mapping as every other call (permission failures carry the RBAC
    guidance). Only stdout carries the response body — pup prints benign
    warnings (e.g. the keychain notice) on stderr for every invocation, so an
    exit-0 call with an empty stdout is a successful empty body regardless of
    whatever stderr noise accompanied it."""
    handle = f"slack-{quote(account)}-{quote(channel[1:])}"  # channel is '#name'
    proc = pup_api(pup, site, "POST", f"{TEST_NOTIFICATION_BASE}/{handle}", org=org)
    if proc.returncode == 0 and not (proc.stdout or "").strip():
        return
    api_json(proc, f"sending a test notification to {channel}")


def cmd_test_notification(args):
    site = resolve_site(args)
    channel = normalize_channel(args.channel)
    account = resolve_account(args.pup, site, args.org, args.account)
    send_test_notification(args.pup, site, account, channel, org=args.org)
    if args.json:
        print(json.dumps({
            "site": site,
            "account": account,
            "channel": channel,
            "sent": True,
        }, indent=2))
    else:
        print(
            f"test notification sent to {channel} on Slack account {account!r} — "
            "check the channel for the message"
        )
    return 0


class _Parser(argparse.ArgumentParser):
    """argparse exits 2 on a usage error; exit 1 so 2 keeps its one meaning.
    add_subparsers defaults parser_class to this class, so subcommands use it too."""

    def error(self, message):
        self.print_usage(sys.stderr)
        self.exit(1, f"{self.prog}: error: {message}\n")


def main(argv=None):
    parser = _Parser(
        description="Slack + Datadog: OAuth authorize URL and channel setup."
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

    authz = sub.add_parser(
        "authorize", help="mint the Slack OAuth authorize URL through the Datadog API"
    )
    authz.add_argument(
        "--open", action="store_true", help="open the authorize URL in the user's browser"
    )
    common(authz)

    wait = sub.add_parser(
        "wait",
        help="poll until the Slack OAuth connect lands (start it in the background)",
    )
    wait.add_argument(
        "--account", help="wait for this specific Slack account name (ignore other new accounts)"
    )
    wait.add_argument(
        "--interval", type=float, default=10.0, help="seconds between polls (default: 10)"
    )
    wait.add_argument(
        "--timeout", type=float, default=300.0, help="give up after this many seconds (default: 300)"
    )
    common(wait)

    accounts = sub.add_parser("accounts", help="list configured Slack accounts and channels")
    common(accounts)

    suggest = sub.add_parser(
        "suggest-channels", help="suggest channels from the connected Slack workspace (read-only)"
    )
    suggest.add_argument("--query", help="part of a channel name the user typed, e.g. 'ops'")
    suggest.add_argument("--account", help="Slack account name; default: the single configured one")
    suggest.add_argument(
        "--limit", type=int, default=3, help="how many suggestions to return (default: 3)"
    )
    common(suggest)

    add = sub.add_parser("add-channel", help="POST a channel to the Slack integration account")
    add.add_argument("channel", help="Slack channel, e.g. '#ops' (a missing '#' is added)")
    add.add_argument("--account", help="Slack account name; default: the single configured one")
    common(add)

    test = sub.add_parser(
        "test-notification", help="POST a Datadog test message to a Slack channel"
    )
    test.add_argument("channel", help="Slack channel, e.g. '#ops' (a missing '#' is added)")
    test.add_argument("--account", help="Slack account name; default: the single configured one")
    common(test)

    audit = sub.add_parser(
        "audit-logs",
        help="mint the Slack audit-logs OAuth authorize URL (enterprise follow-up)",
    )
    audit.add_argument(
        "--open", action="store_true", help="open the authorize URL in the user's browser"
    )
    common(audit)

    args = parser.parse_args(argv)
    try:
        if args.command == "authorize":
            return cmd_authorize(args)
        if args.command == "wait":
            return cmd_wait(args)
        if args.command == "accounts":
            return cmd_accounts(args)
        if args.command == "test-notification":
            return cmd_test_notification(args)
        if args.command == "audit-logs":
            return cmd_audit_logs(args)
        if args.command == "suggest-channels":
            return cmd_suggest_channels(args)
        return cmd_add_channel(args)
    except WaitTimeout as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    except SlackError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
