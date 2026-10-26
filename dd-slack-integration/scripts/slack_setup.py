#!/usr/bin/env python3
"""dd-slack-integration helper.

Four subcommands, one script:

- `authorize`  resolve the Datadog site from `pup auth status` and mint the Slack
               OAuth authorize URL through the Datadog API for a commercial
               (slack.com) workspace, opening it so the user completes the grant.
               The minted host is verified to be Slack before the URL is opened.
- `wait`       poll the Slack integration until the OAuth connect lands (a new
               account appears), so nobody has to report back that they clicked
               Allow; times out with exit 2.
- `accounts`    check the Slack integration is configured and report its account
               name(s) and channels (GET /api/v1/integration/slack).
- `add-channel` POST the user-chosen channel to the Slack integration account
               (POST /api/v1/integration/slack/configuration/accounts/{account}/channels).

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
from urllib.parse import quote, urlsplit

INTEGRATION_BASE = "/api/v1/integration/slack"
# Datadog API route that mints the Slack OAuth authorize URL for a workspace install.
AUTHORIZE_PATH = "/auth/integration/slack/install/authorize-url"

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


def resolve_site(args):
    """--site wins (lowercased); otherwise derive from pup."""
    if args.site:
        return args.site.strip().lower()
    return site_from_pup(args.pup, args.org)


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


def api_json(proc, what):
    """Parse a `pup api` result: JSON body on stdout; API errors become SlackError.

    A permission-shaped failure carries the RBAC guidance, whichever call
    produced it — a 403 on the OAuth mint and a 403 on a channel write are the
    same missing `manage_integrations` grant."""
    out = (proc.stdout or "") + (proc.stderr or "")
    start = out.find("{")
    if start >= 0:
        try:
            payload, _ = json.JSONDecoder().raw_decode(out, start)
        except json.JSONDecodeError:
            payload = None
        if isinstance(payload, dict):
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


def fetch_integration(pup, site, org=None):
    proc = pup_api(pup, site, "GET", INTEGRATION_BASE, org=org)
    return parse_integration(api_json(proc, "listing the Slack integration"))


def _wait_timeout_error(account, timeout, site):
    target = f"Slack account {account!r}" if account else "a new Slack account"
    where = f" on {site}" if site else ""
    return SlackError(
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


def cmd_add_channel(args):
    site = resolve_site(args)
    channel = normalize_channel(args.channel)
    account = args.account
    if not account:
        accounts, _ = fetch_integration(args.pup, site, args.org)
        if not accounts:
            raise SlackError(
                "no Slack account is configured — complete the Slack OAuth grant first"
            )
        if len(accounts) > 1:
            raise SlackError(
                "multiple Slack accounts are configured: "
                + ", ".join(accounts)
                + " — pass --account <name> to choose one"
            )
        account = accounts[0]

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


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Slack + Datadog: OAuth authorize URL and channel setup."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    def common(p):
        p.add_argument("--site", help="Datadog site; default: derive from `pup auth status`")
        p.add_argument("--pup", default="pup", help="pup binary used for auth and API calls")
        p.add_argument(
            "--org",
            help="named pup org session; passed to `pup auth status` and `pup api`",
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

    add = sub.add_parser("add-channel", help="POST a channel to the Slack integration account")
    add.add_argument("channel", help="Slack channel, e.g. '#ops' (a missing '#' is added)")
    add.add_argument("--account", help="Slack account name; default: the single configured one")
    common(add)

    args = parser.parse_args(argv)
    try:
        if args.command == "authorize":
            return cmd_authorize(args)
        if args.command == "wait":
            return cmd_wait(args)
        if args.command == "accounts":
            return cmd_accounts(args)
        return cmd_add_channel(args)
    except SlackError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
