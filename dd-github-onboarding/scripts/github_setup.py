#!/usr/bin/env python3
"""dd-github-onboarding helper.

Nine subcommands, one script:

- `accounts`  list the connected GitHub accounts and the user's GitHub App
              installations with their link statuses (read-only).
- `authorize` check for existing accounts first — when any are connected the
              command refuses and lists them unless `--continue` is passed,
              so the user decides whether to connect another GitHub —
              then open GitHub's install screen for the Datadog GitHub App.
- `wait`      poll the installations until one becomes linkable that was not
              so when the poll started, link it when exactly one does
              (several -> actionable error naming `link`), then poll until
              the new account appears; times out with exit 2. Linking is part
              of landing the connect — it is the step the integration tile's
              creation modal performs.
- `link`      link one installation by id (the manual pick for the ambiguous
              case, or to recover one left by an earlier install).
- `unlink`    unlink an installation by id (cleanup).
- `verify`    prove what the connect unlocked, read-only: the repository sync
              finished, Datadog can read code (Source Code Integration, the
              same check Datadog's own setup checks use), and which
              repositories it can read and comment on pull requests in.
- `catalog-pr` show the connect in action: open a pull request in one of the
              user's repositories that adds a `service.datadog.yaml`, through
              the user's own `gh` login (`--dry-run` shows the file first).
- `catalog-wait` poll the Software Catalog until that service appears (after
              the user merges the PR), then link to it — or, with `--absent`,
              until it is gone.
- `catalog-cleanup` before unlinking: find the account's repositories that
              hold demo files through the Software Catalog (entries tagged
              `source:dd-github-onboarding`), and open a pull request in each
              that removes them, so the catalog drops them while it can
              still see the change.

All `/api/` calls go through `pup api` (`--no-agent`, so output is the raw
payload), which supplies authentication from the user's session.

The install URL is minted first: GET /api/ui/integration/github/install
answers a 302 to GitHub's install screen carrying a short-lived (~5 minutes)
org+user-bound `state`. With that state, GitHub's post-install redirect
completes Datadog's callback and lands on the GitHub configuration page;
without it the callback has no flow to finish and shows an error page (the
install itself still lands, and `wait` links it). `pup api` follows
redirects, so the mint is read with urllib on the allowlisted `api.<site>`
host, with a bearer captured from `pup auth token` that lives in process
memory only — never in argv, on disk, or in output. When the mint is not
available, `authorize` falls back to the public install screen,
`https://github.com/apps/<slug>/installations/new`, built from the site's app
slug.

`catalog-pr` writes to GitHub as the user, through `gh api` (the GitHub REST
API with the user's own `gh` login): it creates a branch, commits one file
with the contents API, and opens a pull request — never touching the default
branch, never merging. Which GitHub account it acts as is decided, not
guessed: without `--gh-user`, it checks every `gh` account logged into
github.com (read-only) and uses the one that can push — or, when several can,
lists them for the user to choose. Each account's token is read from
`gh auth token --user` into process memory and passed to `gh` in its
environment only.
"""

import argparse
import base64
import json
import os
import re
import secrets
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from urllib.parse import parse_qs, quote, urlsplit

ACCOUNTS_PATH = "/api/v2/source-code/github/accounts"
INSTALLATIONS_PATH = "/api/v2/source-code/github/public-app/installations"
# Datadog route that mints the state-bearing GitHub App install URL (a 302).
MINT_PATH = "/api/ui/integration/github/install"
# Where Datadog's post-install callback lands. The mint's default opens the
# tile's creation modal, which links the installation itself and would race
# `wait`'s link; the plain configuration page shows the connected accounts.
INSTALL_REDIRECT_PATH = "/integrations/github/configuration"
AVAILABLE_ACCOUNTS_PATH = "/api/v2/source-code/github/available-accounts-with-permissions"
# Permissions `verify` checks: reading code powers code snippets in stack
# traces (Source Code Integration); writing pull requests powers PR comments.
CODE_READ = {"contents": "read"}
PR_WRITE = {"pull_requests": "write"}
SYNC_DONE = "done"
CATALOG_ENTITY_PATH = "/api/v2/catalog/entity"
# The file `catalog-pr` adds: the Software Catalog scans every readable
# repository for it and syncs it in (Datadog docs, Software Catalog setup).
CATALOG_FILE = "service.datadog.yaml"
# Every file name the Software Catalog syncs from a repository, at any path.
CATALOG_FILE_NAMES = ("service.datadog.yaml", "entity.datadog.yaml")
CATALOG_BRANCH = "datadog/software-catalog"
CLEANUP_BRANCH = "datadog/software-catalog-removal"
CATALOG_TAG = "source:dd-github-onboarding"

# GitHub App slug per Datadog site, for the fallback when the mint is
# unavailable: GitHub's public install screen at
# https://github.com/apps/<slug>/installations/new. Verified against each
# app's github.com/apps/<slug> page.
SITE_APP_SLUGS = {
    "datadoghq.com": "datadog",
    "us3.datadoghq.com": "datadog-us3",
    "us5.datadoghq.com": "datadog-us5",
    "ap1.datadoghq.com": "datadog-ap1",
    "datadoghq.eu": "datadog-eu",
    "ddog-gov.com": "datadog-gov",
    "datad0g.com": "datadog-staging",
}

LINKABLE = "NOT_LINKED_LINKABLE"
# The installations list answers 422 with one of these `failure` codes until
# the user has connected their GitHub login (the install's OAuth step): no
# installation is visible yet, which is not an error for this flow.
NO_INSTALLATIONS_YET = ("USER_NOT_CONNECTED", "NO_INSTALLATION_FOR_USER")
# Polling reads that fail like this are retried until the deadline.
TRANSIENT = re.compile(r"HTTP (429|5\d\d)|could not run|timed out", re.I)

# Datadog RBAC permission that authorizes connecting the integration.
# Integrations Read (`integrations_read`) is view-only and is NOT sufficient.
PERMISSION_MARKERS = ("403", "forbidden", "permission", "manage_integrations")
PERMISSION_HELP = (
    "you may need the Integrations Manage (manage_integrations) permission on the Datadog org; "
    "an org admin grants it through RBAC — "
    "https://docs.datadoghq.com/account_management/rbac/permissions/"
)

PAGE_LIMIT = 100
# `verify` names at most this many repositories per check; the totals come
# from the API, so nothing needs every repository.
REPO_SAMPLE = 10
# Upper bound on pages read from one list, so a misbehaving `next_offset`
# cannot loop forever (100 pages x 100 = 10,000 items).
MAX_PAGES = 100


class GitHubError(Exception):
    """User-actionable failure: bad input, unusable pup state, or an API error."""


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
        raise GitHubError(f"invalid Datadog site: {site!r}")
    return site


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
        raise GitHubError(f"pup not found ({pup!r}) — {PUP_MISSING}, or pass --site")
    except (subprocess.TimeoutExpired, OSError) as e:
        raise GitHubError(f"could not run `pup auth status`: {e}")
    out = (proc.stdout or "") + (proc.stderr or "")
    start = out.find("{")  # tolerate non-JSON preamble (e.g. keychain warnings)
    if start < 0:
        raise GitHubError(
            "`pup auth status` did not return JSON — is 'pup' the Datadog CLI? "
            f"Output began: {out.strip()[:120]!r}"
        )
    try:
        # raw_decode parses the first balanced JSON object, ignoring any trailing
        # non-JSON noise (e.g. a stderr warning appended after stdout's JSON).
        status, _ = json.JSONDecoder().raw_decode(out, start)
    except json.JSONDecodeError:
        raise GitHubError(f"could not parse `pup auth status` output as JSON: {out.strip()[:120]!r}")
    if not isinstance(status, dict) or "site" not in status:
        raise GitHubError(
            "`pup auth status` returned JSON but not a Datadog auth status — is 'pup' the "
            f"Datadog CLI? Output began: {out.strip()[:120]!r}"
        )
    if not status.get("authenticated"):
        raise GitHubError("pup is not authenticated — run `pup auth login`, then retry")
    site = str(status.get("site") or "").strip().lower()
    if not site:
        raise GitHubError("`pup auth status` reported no site")
    return site


def api_host(site):
    """Map a Datadog site to its API host (e.g. datadoghq.com -> api.datadoghq.com)."""
    return f"api.{normalize_site(site)}"


def app_host(site):
    """The Datadog app host for a site: subdomained sites (us5.datadoghq.com)
    are the host themselves, others get `app.` (app.datadoghq.com) — the same
    rule the Nebius and CoreWeave verify scripts use for their links."""
    site = normalize_site(site)
    return site if site.count(".") >= 2 else f"app.{site}"


def resolve_site(args):
    """--site wins (lowercased); otherwise derive from pup."""
    if args.site:
        return normalize_site(args.site)
    return site_from_pup(args.pup, args.org)


def pup_api(pup, site, method, path, fields=None, org=None, auto_approve=False, body=None):
    """One authenticated Datadog API call through `pup api` (raw payload, no envelope).

    `org` (a global pup flag, valid after the subcommand) selects the same named
    org-keyed session `site_from_pup` resolves against. `body` is a JSON body sent
    verbatim via `--input -` (for nested payloads the flat `--raw-field` form
    cannot express)."""
    cmd = [pup, "api", "--no-agent"]
    if org:
        cmd += ["--org", org]
    cmd += ["--method", method, path]
    if auto_approve:
        cmd += ["--yes"]
    if body is not None:
        cmd += ["--input", "-"]
    for key, value in (fields or {}).items():
        cmd += ["--raw-field", f"{key}={value}"]
    env = dict(os.environ, DD_SITE=site)
    try:
        return subprocess.run(cmd, input=json.dumps(body) if body is not None else None,
                              capture_output=True, text=True, timeout=60, env=env)
    except FileNotFoundError:
        raise GitHubError(f"pup not found ({pup!r}) — {PUP_MISSING}")
    except (subprocess.TimeoutExpired, OSError) as e:
        raise GitHubError(f"could not run `pup api {method} {path}`: {e}")


def api_json(proc, what, allow_empty=False):
    """Parse a `pup api` result: JSON body on stdout; API errors become GitHubError.

    A permission-shaped failure carries the RBAC guidance, whichever call
    produced it — a 403 on the accounts list and a 403 on a link are the same
    missing `manage_integrations` grant. `allow_empty` accepts a bodyless 2xx
    (e.g. the link POST answers 201 with no payload): the payload is stdout,
    so stderr noise (pup's keychain warning on hosts without a Secret
    Service) does not turn a success into a failure."""
    out = (proc.stdout or "") + (proc.stderr or "")
    if allow_empty and proc.returncode == 0 and not (proc.stdout or "").strip():
        return {}
    start = out.find("{")
    if start >= 0:
        try:
            payload, _ = json.JSONDecoder().raw_decode(out, start)
        except json.JSONDecodeError:
            payload = None
        if isinstance(payload, dict):
            errors = payload.get("errors")
            if errors:
                detail = ", ".join(map(str, errors))
                status = re.search(r"HTTP \d{3}\b", out)
                if status:
                    detail = f"{status.group(0)}: {detail}"
                raise GitHubError(with_permission_help(f"{what} failed: {detail}"))
            if proc.returncode == 0:
                return payload
    raise GitHubError(
        with_permission_help(
            f"{what} failed (pup exit {proc.returncode}): {out.strip()[:200]!r}"
        )
    )


def parse_accounts(payload):
    """Connected accounts from GET /api/v2/source-code/github/accounts."""
    out = []
    for entry in payload.get("data") or []:
        attrs = entry.get("attributes") or {}
        name = str(attrs.get("account_name") or "").strip()
        if name:
            out.append({
                "id": entry.get("id"),
                "account_name": name,
                "repository_count": attrs.get("repository_count"),
            })
    return sorted(out, key=lambda a: a["account_name"])


def parse_installations(payload):
    """{installation_id: {name, status, suspended_at}} from the installations list."""
    out = {}
    for entry in payload.get("data") or []:
        attrs = entry.get("attributes") or {}
        try:
            installation_id = int(entry["id"])
        except (KeyError, TypeError, ValueError):
            continue
        out[installation_id] = {
            "name": attrs.get("github_account_name"),
            "status": attrs.get("link_status"),
            "suspended_at": attrs.get("suspended_at"),
        }
    return out


def fetch_all(pup, site, path, what, fields=None, org=None):
    """Every item of a paged Datadog list -> {"data": [...]}.

    Follows `meta.page.next_offset` until it is null; a response without
    paging metadata is a single page."""
    data, offset = [], 0
    for _ in range(MAX_PAGES):
        page_fields = dict(fields or {}, **{"page[limit]": str(PAGE_LIMIT),
                                            "page[offset]": str(offset)})
        payload = api_json(pup_api(pup, site, "GET", path, fields=page_fields, org=org), what)
        data.extend(payload.get("data") or [])
        nxt = ((payload.get("meta") or {}).get("page") or {}).get("next_offset")
        if not isinstance(nxt, int) or nxt <= offset:
            return {"data": data}
        offset = nxt
    raise GitHubError(f"{what} returned more than {MAX_PAGES} pages — stopping")


def fetch_accounts(pup, site, org=None):
    return parse_accounts(fetch_all(pup, site, ACCOUNTS_PATH, "listing the GitHub accounts", org=org))


def fetch_installations(pup, site, org=None):
    proc = pup_api(pup, site, "GET", INSTALLATIONS_PATH, org=org)
    failure = re.search(r'"failure"\s*:\s*"([A-Z_]+)"', (proc.stdout or "") + (proc.stderr or ""))
    if proc.returncode != 0 and failure and failure.group(1) in NO_INSTALLATIONS_YET:
        return {}
    return parse_installations(api_json(proc, "listing the GitHub App installations"))


def link_installation(pup, site, installation_id, org=None):
    """POST the link for one installation (what the tile's creation modal does)."""
    body = {"data": {"type": "link_public_app_installation_to_org_request",
                     "attributes": {"installation_id": int(installation_id)}}}
    proc = pup_api(pup, site, "POST", INSTALLATIONS_PATH, org=org,
                   auto_approve=True, body=body)
    api_json(proc, f"linking installation {installation_id}", allow_empty=True)


def install_url(site, app_slug=None):
    """GitHub's install screen for the Datadog GitHub App of `site`.

    The screen is public — GitHub sends a not-logged-in visitor through login
    and back to it — so the URL carries no Datadog-issued state. The slug
    comes from the site map; `app_slug` overrides it for a site that is not
    in the map (the slug is the github.com/apps/<slug> page of the site's
    Datadog app)."""
    site = normalize_site(site)
    slug = app_slug or SITE_APP_SLUGS.get(site)
    if not slug:
        raise GitHubError(
            f"no Datadog GitHub App slug is known for site {site!r} — pass "
            "--app-slug <slug> (the slug from the github.com/apps/<slug> page "
            "of this site's Datadog app), or connect from the GitHub integration tile "
            "in Datadog; do not guess a slug"
        )
    return f"https://github.com/apps/{slug}/installations/new"


PUP_MISSING = "install the Datadog pup CLI (https://github.com/DataDog/pup) and run `pup auth login`"
GH_MISSING = "install the GitHub CLI and run `gh auth login`"


def run_cli(cmd, missing, input=None, env=None, timeout=60):
    """Run one CLI call, turning a missing binary or a hung call into GitHubError."""
    try:
        return subprocess.run(cmd, input=input, capture_output=True, text=True,
                              timeout=timeout, env=env)
    except FileNotFoundError:
        raise GitHubError(f"{cmd[0]} not found ({cmd[0]!r}) — {missing}")
    except (subprocess.TimeoutExpired, OSError) as e:
        raise GitHubError(f"could not run `{' '.join(cmd[:3])}`: {e}")


def cli_token(cmd, missing, env=None, hint=""):
    """Read one bare token printed by a CLI (`pup auth token`, `gh auth token`).

    Obscuration contract: the token lives in process memory only — used in an
    Authorization header or a child's environment, never in argv, on disk, or
    in output. On failure only the exit code and output sizes are reported."""
    proc = run_cli(cmd, missing, env=env)
    token = (proc.stdout or "").strip()
    if proc.returncode != 0:
        raise GitHubError(
            f"`{' '.join(cmd)}` failed (exit {proc.returncode}; stdout {len(proc.stdout or '')}B, "
            f"stderr {len(proc.stderr or '')}B — content withheld){hint}"
        )
    if not token or len(token.split()) > 1:
        raise GitHubError(f"`{' '.join(cmd)}` output was not a single bare token")
    return token


def pup_token(pup, site, org=None):
    """The org-bound bearer for the one raw mint call (see `cli_token`)."""
    cmd = [pup, "auth", "token"] + (["--org", org] if org else [])
    return cli_token(cmd, PUP_MISSING, env=dict(os.environ, DD_SITE=site))


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Surface 302s instead of following them: the Location header is the payload."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)


def http_redirect(url, token):
    """One raw GET returning (status, location-or-None) without following the redirect."""
    req = urllib.request.Request(url, headers={"Authorization": "Bearer " + token})
    try:
        with _OPENER.open(req, timeout=20) as r:
            return r.status, None
    except urllib.error.HTTPError as e:
        location = e.headers.get("Location") if e.headers else None
        return e.code, location
    except OSError as e:
        raise GitHubError(f"minting the GitHub App install URL failed: {getattr(e, 'reason', e)}")


def minted_install_url(pup, site, org=None, token=None, http=None):
    """Mint the state-bearing GitHub App install URL through the Datadog API.

    Returns the `https://github.com/apps/<slug>/installations/new?state=...`
    Location of the mint's 302; the callback then lands on
    INSTALL_REDIRECT_PATH. Raises GitHubError when the mint is unavailable —
    a 307 to the login page (the site does not accept token authn on the
    route), 401/403/404, or a Location that is not GitHub's install screen
    with a state — so the caller can fall back to the public install screen.
    `token`/`http` are injectable to keep the network out of tests."""
    if token is None:
        token = pup_token(pup, site, org)
    fetch = http or http_redirect
    status, location = fetch(
        f"https://{api_host(site)}{MINT_PATH}?redirect_path={quote(INSTALL_REDIRECT_PATH, safe='')}",
        token,
    )
    if status == 307 and location and "/account/login" in location:
        raise GitHubError("the mint redirected to login (no token authn on this route for this site)")
    if status != 302 or not location:
        raise GitHubError(f"the mint answered HTTP {status} (expected 302)")
    u = urlsplit(location)
    if (u.scheme, (u.hostname or "").lower()) != ("https", "github.com") \
            or not u.path.endswith("/installations/new") or not parse_qs(u.query).get("state"):
        raise GitHubError("the mint did not return GitHub's install screen with a state")
    return location


def check_poll_args(interval, timeout):
    if interval <= 0:
        raise GitHubError(f"--interval must be greater than zero (got {interval:g})")
    if timeout <= 0:
        raise GitHubError(f"--timeout must be greater than zero (got {timeout:g})")


def poll(fetch, done, interval, timeout):
    """Call `fetch()` until `done(value)` or the deadline; returns the last value.

    The clock is looked up per call, so patching `time.monotonic`/`time.sleep`
    fakes it in tests."""
    check_poll_args(interval, timeout)
    clock, sleep = time.monotonic, time.sleep
    deadline = clock() + timeout
    while True:
        value = fetch()
        remaining = deadline - clock()
        if done(value) or remaining <= 0:
            return value
        sleep(min(interval, remaining))


def _wait_timeout_error(timeout, site):
    return GitHubError(
        f"timed out after {timeout:g}s waiting for the GitHub connect on {site} — the install has "
        "not landed. Check that the browser installed the Datadog GitHub App on a GitHub org the "
        "user owns (an installation the user cannot link answers NOT_LINKED_MISSING_PERMISSIONS). "
        "An install that completed before this poll started is not linked here — list the "
        "installations with `accounts` and run `link <installation_id>` by hand; otherwise redo "
        "the install with `authorize --open` and a fresh `wait`"
    )


def wait_for_connect(fetch_installations, link, fetch_accounts,
                     interval=5.0, timeout=300.0, site=None):
    """Drive the post-install landing: poll installations until one that was
    not linkable at the start of the poll becomes linkable, link it when
    exactly one does (several -> actionable error naming `link`), then poll
    accounts until the new one appears.

    The poll is the detector: only installations that become linkable while
    it runs are linked. A linkable installation that predates the poll is not
    this install — it is often a stale record from an earlier uninstall — and
    an install completed before the poll started is linked by hand with
    `link` instead. The account announced is the linked installation's own. Transient read failures
    (5xx, 429, timeouts) are retried until the deadline. Returns
    (installation_id, installation_name, [new account dicts]). Raises
    GitHubError on timeout. Prints nothing: the caller reports once. All
    fetchers and the clock are injectable so tests drive faked sequences."""
    check_poll_args(interval, timeout)
    clock, sleep = time.monotonic, time.sleep
    deadline = clock() + timeout

    def pause():
        remaining = deadline - clock()
        if remaining <= 0:
            raise _wait_timeout_error(timeout, site)
        sleep(min(interval, remaining))

    def read(fetch):
        """`fetch()`, retried through transient failures until the deadline."""
        while True:
            try:
                return fetch()
            except GitHubError as e:
                if not TRANSIENT.search(str(e)):
                    raise
            pause()

    # Installations first: one that turns linkable during the accounts read is
    # then still seen by the poll, instead of landing in the baseline and being
    # ignored for the whole run.
    baseline_linkable = {i for i, v in read(fetch_installations).items()
                         if v["status"] == LINKABLE}
    baseline_accounts = {a["account_name"] for a in read(fetch_accounts)}
    while True:
        pause()
        installs = read(fetch_installations)
        linkable = {i: v for i, v in installs.items()
                    if v["status"] == LINKABLE and i not in baseline_linkable}
        if not linkable:
            continue
        if len(linkable) > 1:
            names = ", ".join(f"{v['name']} ({i})" for i, v in sorted(linkable.items()))
            raise GitHubError(
                "several installations became linkable while polling: " + names
                + " — pick one and run `link <installation_id>`"
            )
        installation_id, info = next(iter(linkable.items()))
        try:
            link(installation_id)
        except GitHubError as e:
            # A re-run of `wait` would baseline this installation and ignore it.
            raise GitHubError(
                f"{e} — link it by hand with `link {installation_id}`, then run `accounts` to confirm"
            ) from e
        while True:
            pause()
            # Only the linked installation's own account counts — not another
            # account someone connected in the meantime.
            new = [a for a in read(fetch_accounts)
                   if a["account_name"] not in baseline_accounts
                   and a["account_name"].casefold() == str(info["name"]).casefold()]
            if new:
                return installation_id, info["name"], new


def _print_accounts(site, accounts, installs, as_json):
    if as_json:
        print(json.dumps({
            "site": site,
            "accounts": accounts,
            "installations": {str(k): v for k, v in installs.items()},
        }, indent=2))
        return
    print(f"site:     {site}")
    if accounts:
        print("accounts: " + ", ".join(
            f"{a['account_name']} ({a['repository_count']} repos)" for a in accounts))
    else:
        print("accounts: (none connected)")
    for installation_id, info in sorted(installs.items()):
        print(f"  installation {installation_id}: {info['name']} — {info['status']}"
              + (" (suspended)" if info["suspended_at"] else ""))


def cmd_accounts(args):
    site = resolve_site(args)
    accounts = fetch_accounts(args.pup, site, args.org)
    installs = fetch_installations(args.pup, site, args.org)
    if installs or accounts:
        _print_accounts(site, accounts, installs, args.json)
    if not accounts:
        # Not connected, even when installations exist (they may only need `link`).
        print(
            "error: no GitHub account is connected on this org — "
            + ("link an installation listed above with `link <installation_id>`, or "
               if installs else "")
            + "run the `authorize` subcommand with --open to start the flow",
            file=sys.stderr,
        )
        return 2
    return 0


def cmd_authorize(args):
    site = resolve_site(args)

    # The user decides whether to connect another GitHub when the
    # integration already exists: refuse and list what is connected unless
    # --continue.
    existing = fetch_accounts(args.pup, site, args.org)
    if existing and not args.continue_setup:
        raise GitHubError(
            "GitHub accounts are already connected on this org: "
            + ", ".join(a["account_name"] for a in existing)
            + " — confirm with the user whether they want to connect another GitHub, "
            "then re-run with --continue"
        )

    # Prefer the minted URL: its state lets Datadog's post-install callback
    # finish cleanly. Fall back to the public install screen when the mint is
    # unavailable — the install still lands and `wait` links it, but the
    # browser shows an error page after Install.
    mint_error = None
    try:
        url = minted_install_url(args.pup, site, args.org)
    except GitHubError as e:
        mint_error = str(e)
        url = install_url(site, args.app_slug)
        print(
            f"note: {mint_error} — opening the public install screen instead; after Install, "
            "GitHub's redirect shows a Datadog error page that is safe to ignore",
            file=sys.stderr,
        )
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
            "minted": mint_error is None,
            "mint_error": mint_error,
            "existing_accounts": [a["account_name"] for a in existing],
            "opened_browser": opened,
        }, indent=2))
    else:
        print(url)
    return 0


def cmd_wait(args):
    """Drive the post-install landing until the new account appears, then report once.

    Meant to be started as a background process right after the install URL is
    opened, so the flow proceeds on the connect itself instead of on the user
    saying they clicked install."""
    site = resolve_site(args)
    installation_id, name, new = wait_for_connect(
        lambda: fetch_installations(args.pup, site, args.org),
        lambda i: link_installation(args.pup, site, i, args.org),
        lambda: fetch_accounts(args.pup, site, args.org),
        interval=args.interval,
        timeout=args.timeout,
        site=site,
    )
    if args.json:
        print(json.dumps({
            "site": site,
            "installation_id": installation_id,
            "github_account_name": name,
            "new_accounts": new,
        }, indent=2))
    else:
        print(f"github connected: {name} (installation {installation_id}) — "
              + ", ".join(a["account_name"] for a in new))
    return 0


def cmd_link(args):
    site = resolve_site(args)
    link_installation(args.pup, site, args.installation_id, args.org)
    installs = fetch_installations(args.pup, site, args.org)
    linked = installs.get(args.installation_id, {}).get("status") == "LINKED_TO_THIS_ORG"
    accounts = fetch_accounts(args.pup, site, args.org)
    if args.json:
        print(json.dumps({
            "site": site,
            "installation_id": args.installation_id,
            "linked": linked,
            "accounts": [a["account_name"] for a in accounts],
        }, indent=2))
    else:
        print(f"linked installation {args.installation_id}: {linked} "
              f"(accounts: {', '.join(a['account_name'] for a in accounts) or 'none'})")
    return 0 if linked else 2


def cmd_unlink(args):
    site = resolve_site(args)
    proc = pup_api(args.pup, site, "DELETE", f"{INSTALLATIONS_PATH}/{args.installation_id}",
                   org=args.org, auto_approve=True)
    api_json(proc, f"unlinking installation {args.installation_id}", allow_empty=True)
    installs = fetch_installations(args.pup, site, args.org)
    gone = args.installation_id not in installs or \
        installs[args.installation_id]["status"] != "LINKED_TO_THIS_ORG"
    if args.json:
        print(json.dumps({
            "site": site,
            "installation_id": args.installation_id,
            "unlinked": gone,
        }, indent=2))
    else:
        print(f"unlinked installation {args.installation_id}: {gone}")
    return 0 if gone else 2


def fetch_sync_status(pup, site, account_id, org=None):
    path = f"{ACCOUNTS_PATH}/{quote(account_id, safe='=')}/repository-sync/status"
    payload = api_json(pup_api(pup, site, "GET", path, org=org), "reading the repository sync status")
    return ((payload.get("data") or {}).get("attributes") or {}).get("status")


def fetch_repositories(pup, site, account_id, permissions, org=None):
    """The account's repositories that grant Datadog `permissions` ->
    {names, total}: one page of up to REPO_SAMPLE names plus the API's total."""
    path = f"{ACCOUNTS_PATH}/{quote(account_id, safe='=')}/repositories"
    fields = {"permissions": json.dumps(permissions), "page[limit]": str(REPO_SAMPLE)}
    payload = api_json(pup_api(pup, site, "GET", path, fields=fields, org=org),
                       "listing the account's repositories")
    names = sorted(str((r.get("attributes") or {}).get("name"))
                   for r in payload.get("data") or [] if (r.get("attributes") or {}).get("name"))
    total = ((payload.get("meta") or {}).get("page") or {}).get("total")
    return {"names": names, "total": total if isinstance(total, int) else len(names)}


def code_read_accounts(pup, site, org=None):
    """Account names Datadog can read code from — the signal Datadog's own
    setup checks use to mark Source Code Integration as connected."""
    body = {"data": {"type": "available_accounts_with_permissions_request",
                     "attributes": {"permissions": CODE_READ}}}
    payload = api_json(pup_api(pup, site, "POST", AVAILABLE_ACCOUNTS_PATH, org=org, body=body),
                       "checking which accounts Datadog can read code from")
    attrs = (payload.get("data") or {}).get("attributes") or {}
    return {str(a.get("account_name")) for a in attrs.get("available_accounts") or []}


def verify_account(fetch_sync, fetch_repos, fetch_code_accounts, account_name,
                   interval=5.0, timeout=120.0):
    """Prove what a connected account unlocked, read-only.

    Waits (bounded) for the repository sync to finish, then reports whether
    Datadog can read the account's code, and which repositories it can read
    (code snippets in stack traces) and comment on pull requests in.
    `verified` needs the sync done, code read access, and at least one
    readable repository. Repositories are a sample of up to REPO_SAMPLE names
    plus the totals. Fetchers and the clock are injectable for tests."""
    sync = poll(fetch_sync, lambda status: status == SYNC_DONE, interval, timeout)
    code, pr = fetch_repos(CODE_READ), fetch_repos(PR_WRITE)
    source_code = account_name in fetch_code_accounts()
    return {
        "sync_status": sync,
        "source_code_integration": source_code,
        "code_repositories": code["names"],
        "code_repository_count": code["total"],
        "pr_comment_repositories": pr["names"],
        "pr_comment_repository_count": pr["total"],
        "verified": sync == SYNC_DONE and source_code and code["total"] > 0,
    }


def pick_account(accounts, name=None):
    """The connected GitHub account to act on: `name`, else the only one."""
    if name:
        matches = [a for a in accounts if a["account_name"] == name]
        if not matches:
            raise GitHubError(
                f"GitHub account {name!r} is not connected (connected: "
                f"{', '.join(a['account_name'] for a in accounts) or 'none'})"
            )
        return matches[0]
    if len(accounts) == 1:
        return accounts[0]
    if not accounts:
        raise GitHubError("no GitHub account is connected")
    raise GitHubError(
        "several GitHub accounts are connected: "
        + ", ".join(a["account_name"] for a in accounts)
        + " — pass --account <name>"
    )


def cmd_verify(args):
    site = resolve_site(args)
    account = pick_account(fetch_accounts(args.pup, site, args.org), args.account)
    result = verify_account(
        lambda: fetch_sync_status(args.pup, site, account["id"], args.org),
        lambda perms: fetch_repositories(args.pup, site, account["id"], perms, args.org),
        lambda: code_read_accounts(args.pup, site, args.org),
        account["account_name"],
        interval=args.interval,
        timeout=args.timeout,
    )
    if args.json:
        print(json.dumps({"site": site, "account": account["account_name"], **result}, indent=2))
    else:
        def names(repos):
            return ", ".join(repos) or "none"
        print(f"github account {account['account_name']!r} on {site}: "
              f"sync {result['sync_status']}, "
              f"source code integration {'yes' if result['source_code_integration'] else 'NO'}")
        def more(listed, total):
            return f" (+{total - len(listed)} more)" if total > len(listed) else ""
        print(f"  code readable in: {names(result['code_repositories'])}"
              + more(result["code_repositories"], result["code_repository_count"]))
        print(f"  pull request comments in: {names(result['pr_comment_repositories'])}"
              + more(result["pr_comment_repositories"], result["pr_comment_repository_count"]))
    return 0 if result["verified"] else 2


def catalog_service_name(repository):
    """A Software Catalog entity name for a repository: lowercase, [a-z0-9._-]."""
    name = re.sub(r"[^a-z0-9._-]+", "-", repository.lower()).strip("-._")
    if not name:
        raise GitHubError(f"cannot derive a Software Catalog name from {repository!r} — pass --service-name")
    return name


def catalog_definition(owner, repository, service_name):
    """The `service.datadog.yaml` `catalog-pr` adds (v3, the format the
    Software Catalog syncs from GitHub): the service, a link back to the
    repository, and a tag marking where it came from."""
    # Names are quoted: repositories like `123`, `true` or `null` would
    # otherwise load as a number, boolean or null instead of a string.
    q = json.dumps
    return (
        "apiVersion: v3\n"
        "kind: service\n"
        "metadata:\n"
        f"  name: {q(service_name)}\n"
        f"  displayName: {q(repository)}\n"
        "  description: Added by the Datadog GitHub onboarding to show the Software Catalog\n"
        "    syncing from GitHub. Edit it to describe the service, or delete it.\n"
        "  links:\n"
        f"    - name: {q(repository)}\n"
        "      type: repo\n"
        "      provider: github\n"
        f"      url: {q(f'https://github.com/{owner}/{repository}')}\n"
        "  tags:\n"
        f"    - {CATALOG_TAG}\n"
    )


def gh_user_token(gh, user):
    """Token of one `gh` account, passed to `gh` via its environment (see `cli_token`)."""
    return cli_token([gh, "auth", "token", "--hostname", "github.com", "--user", user], GH_MISSING,
                     hint=" — is that account logged in? `gh auth status` lists them")


def gh_api(gh, method, path, body=None, token=None):
    """One GitHub REST call through `gh api` -> (status, payload).

    `gh` authenticates with the user's own login (or `token`, set as GH_TOKEN
    in its environment). A failed call exits non-zero with GitHub's JSON error
    on stdout; its `status` is returned so callers can tell 404 from 403."""
    # Pinned to github.com: a GH_HOST set for GitHub Enterprise must not redirect these.
    cmd = [gh, "api", "--hostname", "github.com", "-X", method, path] + (
        ["--input", "-"] if body is not None else [])
    env = dict(os.environ, GH_TOKEN=token) if token else dict(os.environ)
    proc = run_cli(cmd, GH_MISSING, input=json.dumps(body) if body is not None else None, env=env)
    try:
        payload = json.loads(proc.stdout) if (proc.stdout or "").strip() else {}
    except json.JSONDecodeError:
        payload = {}
    if proc.returncode == 0:
        return 200, payload
    try:
        status = int((payload or {}).get("status") or 0)
    except (TypeError, ValueError):
        status = 0
    return status or 1, payload


def gh_accounts(gh):
    """Accounts logged into github.com in `gh`: [{login, active}], from
    `gh auth status --json hosts` (no tokens in that output)."""
    proc = run_cli([gh, "auth", "status", "--json", "hosts", "--hostname", "github.com"],
                   GH_MISSING)
    try:
        hosts = json.loads(proc.stdout or "{}").get("hosts") or {}
    except json.JSONDecodeError:
        raise GitHubError("`gh auth status --json hosts` did not return JSON — check your gh version")
    return [
        {"login": a["login"], "active": bool(a.get("active"))}
        for a in hosts.get("github.com") or []
        if a.get("login") and a.get("state") == "success"
    ]


def repo_access(call, owner, repository):
    """One account's access to a repository -> (access, repository info):
    access is "push", "read", or "none" (cannot see it)."""
    status, info = call("GET", f"repos/{owner}/{repository}", None)
    if status != 200:
        return "none", {}
    return ("push" if (info.get("permissions") or {}).get("push") else "read"), info


def select_gh_account(accounts, probe):
    """Decide which `gh` account `catalog-pr` acts as.

    `probe(login)` -> "push" | "read" | "none". Returns (login, checked):
    `login` is the only account that can push, or None when several can (the
    user must choose); `checked` lists every account with its access. Raises
    GitHubError when none can push or none is logged in."""
    if not accounts:
        raise GitHubError("no account is logged into github.com in gh — run `gh auth login`")
    checked = [dict(a, access=probe(a["login"])) for a in accounts]
    pushers = [a["login"] for a in checked if a["access"] == "push"]
    if not pushers:
        raise GitHubError(
            "no gh account can push to this repository ("
            + ", ".join(f"{a['login']}: {a['access']}" for a in checked)
            + ") — log in to an account with write access (`gh auth login`)"
        )
    return (pushers[0] if len(pushers) == 1 else None), checked


def raise_read_error(status, payload, what):
    """A GitHub read that is neither found (200) nor absent (404) — 403, 429,
    5xx — is an error, never "the file is not there"."""
    raise GitHubError(f"{what} failed: {(payload or {}).get('message') or f'HTTP {status}'}")


def catalog_files(call, repo_path, base):
    """Software Catalog definition files on `base`, at any depth -> (paths, complete).

    One recursive tree read. When GitHub truncates it (very large
    repositories), keeps the matches it did return, adds both names at the
    root, and reports `complete` False: nested files may be missing."""
    status, tree = call("GET", f"{repo_path}/git/trees/{quote(base, safe='')}?recursive=1", None)
    found = set()
    if status == 200:
        found = {entry["path"] for entry in tree.get("tree") or []
                 if entry.get("type") == "blob"
                 and entry.get("path", "").rsplit("/", 1)[-1] in CATALOG_FILE_NAMES}
        if not tree.get("truncated"):
            return sorted(found), True
    elif status != 404:
        raise_read_error(status, tree, f"reading the file tree of {repo_path}")
    for name in CATALOG_FILE_NAMES:
        status, payload = call("GET", f"{repo_path}/contents/{name}?ref={quote(base, safe='')}", None)
        if status == 200:
            found.add(name)
        elif status != 404:
            raise_read_error(status, payload, f"checking {name} in {repo_path}")
    return sorted(found), False


def new_branch(call, owner, repository, prefix):
    """A fresh branch name for a new PR: `prefix-<6 hex>`, unique per run.

    Never reuses or deletes an existing branch, so nothing anyone pushed is
    lost. Refuses while a PR from the same family (`prefix`, optionally with a
    suffix) is still open — a new one would duplicate it."""
    ours = re.compile(rf"{re.escape(prefix)}(-[0-9a-f]+)?")
    for page in range(1, MAX_PAGES + 1):
        status, prs = call(
            "GET", f"repos/{owner}/{repository}/pulls?state=open&per_page=100&page={page}", None)
        if status != 200 or not isinstance(prs, list):
            raise GitHubError(f"could not list the open pull requests in {owner}/{repository}")
        for pr in prs:
            ref = (pr.get("head") or {}).get("ref") or ""
            if ours.fullmatch(ref):
                raise GitHubError(
                    f"a pull request from {ref} is still open in {owner}/{repository} — "
                    f"{pr.get('html_url')}; merge or close it first"
                )
        if len(prs) < 100:
            return f"{prefix}-{secrets.token_hex(3)}"
    raise GitHubError(f"{owner}/{repository} has too many open pull requests to check")


def open_catalog_pr(call, owner, repository, service_name, dry_run=False,
                    account="this gh account"):
    """Open the Software Catalog demo pull request; `call(method, path, body)`
    -> (status, payload) is `gh_api` (injectable for tests).

    Refuses before writing anything when the account cannot see or push to
    the repository, when the repository already has a catalog file (either
    name, at any path), or while an earlier demo PR is still open. Uses a new
    branch (`new_branch`), never an existing one. With `dry_run`, stops after
    those checks. Otherwise creates the branch from the default branch,
    commits the file there, and opens the PR — never touching the default
    branch, never merging."""
    repo_path = f"repos/{owner}/{repository}"

    def ok(status, payload, what):
        if status != 200 and status != 201:
            message = (payload or {}).get("message") or f"HTTP {status}"
            raise GitHubError(f"{what} failed: {message}")
        return payload

    access, info = repo_access(call, owner, repository)
    if access != "push":
        raise GitHubError(
            f"{account} cannot {'see' if access == 'none' else 'push to'} {owner}/{repository} — "
            "re-run without --gh-user to pick the logged-in account that can push"
        )
    base = info.get("default_branch") or "main"
    existing, complete = catalog_files(call, repo_path, base)
    if existing:
        raise GitHubError(
            f"{owner}/{repository} already has Software Catalog definitions on {base} ("
            + ", ".join(existing[:5]) + (", …" if len(existing) > 5 else "")
            + ") — the catalog syncs those as is; not adding another"
        )
    if not complete:
        raise GitHubError(
            f"{owner}/{repository} is too large to check for existing Software Catalog "
            "definitions (GitHub truncated its file tree) — not adding one that might "
            "duplicate an existing definition"
        )
    branch = new_branch(call, owner, repository, CATALOG_BRANCH)
    plan = {"repository": f"{owner}/{repository}", "base_branch": base,
            "branch": branch, "file": CATALOG_FILE, "service_name": service_name,
            "definition": catalog_definition(owner, repository, service_name)}
    if dry_run:
        return dict(plan, pr_url=None, pr_number=None)

    ref = ok(*call("GET", f"{repo_path}/git/ref/heads/{quote(base, safe='')}", None),
             f"reading branch {base}")
    sha = (ref.get("object") or {}).get("sha")
    if not sha:
        raise GitHubError(f"could not read the head commit of {base}")
    ok(*call("POST", f"{repo_path}/git/refs", {"ref": f"refs/heads/{branch}", "sha": sha}),
       f"creating branch {branch}")
    ok(*call("PUT", f"{repo_path}/contents/{CATALOG_FILE}", {
        "message": f"Add {CATALOG_FILE} for the Datadog Software Catalog",
        "content": base64.b64encode(plan["definition"].encode()).decode(),
        "branch": branch,
    }), f"committing {CATALOG_FILE}")
    pr = ok(*call("POST", f"{repo_path}/pulls", {
        "title": "Add this repository to the Datadog Software Catalog",
        "head": branch,
        "base": base,
        "body": (
            f"Adds `{CATALOG_FILE}` so Datadog's Software Catalog lists this repository as the "
            f"`{service_name}` service, synced through the Datadog GitHub integration.\n\n"
            "Opened by the Datadog GitHub onboarding at your request. Merge it to see the service "
            "appear in the Software Catalog; edit the file to describe the service, or close "
            "this pull request to skip it."
        ),
    }), "opening the pull request")
    return dict(plan, pr_url=pr.get("html_url"), pr_number=pr.get("number"))


def gh_account_for(args, owner, repository):
    """The `gh` account to act as on one repository -> (login, token, checked).

    `--gh-user` wins (checked is None). Otherwise the only logged-in account
    that can push; login and token are None when several can (the user
    chooses), with `checked` listing every account's access."""
    if args.gh_user:
        return args.gh_user, gh_user_token(args.gh, args.gh_user), None
    tokens = {}

    def probe(account):
        tokens[account] = gh_user_token(args.gh, account)
        return repo_access(
            lambda method, path, body: gh_api(args.gh, method, path, body, tokens[account]),
            owner, repository)[0]

    login, checked = select_gh_account(gh_accounts(args.gh), probe)
    return login, tokens.get(login), checked


def cmd_catalog_pr(args):
    owner, _, repository = (args.repo or "").partition("/")
    if not owner or not repository or "/" in repository:
        raise GitHubError(f"--repo must be <owner>/<name> (got {args.repo!r})")
    service_name = args.service_name or catalog_service_name(repository)

    # The name must be new to the catalog, so `catalog-wait` can only find the
    # entry this PR creates.
    site = resolve_site(args)
    taken = catalog_lookup(args.pup, site, service_name, args.org)
    if taken:
        raise GitHubError(
            f"the Software Catalog already has an entry named {service_name!r}"
            + (f" (defined in {taken['github_url']})" if taken.get("github_url") else "")
            + " — pass --service-name <another name>"
        )

    # The account: --gh-user, else the only logged-in account that can push.
    # Several that can -> the user chooses (the dry run lists them).
    login, token, checked = gh_account_for(args, owner, repository)
    if login is None:
        choices = ", ".join(a["login"] for a in checked if a["access"] == "push")
        if not args.dry_run:
            raise GitHubError(
                f"several gh accounts can push to {owner}/{repository} ({choices}) — "
                "ask the user which to use and pass --gh-user <login>"
            )
        if args.json:
            print(json.dumps({"repository": f"{owner}/{repository}", "gh_user": None,
                              "needs_choice": True, "accounts": checked,
                              "dry_run": True}, indent=2))
        else:
            print(f"several gh accounts can push to {owner}/{repository}: {choices} — "
                  "choose one and re-run with --gh-user <login>")
        return 0

    result = open_catalog_pr(
        lambda method, path, body: gh_api(args.gh, method, path, body, token),
        owner, repository, service_name, dry_run=args.dry_run, account=login,
    )
    result = dict(result, gh_user=login, needs_choice=False, accounts=checked)
    if args.json:
        print(json.dumps(dict(result, dry_run=args.dry_run), indent=2))
    elif args.dry_run:
        print(f"would open a pull request in {result['repository']} as {login}: "
              f"{result['branch']} -> {result['base_branch']}, adding {result['file']}:\n")
        print(result["definition"])
    else:
        print(f"opened {result['pr_url']} as {login} — merge it to add {service_name!r} "
              "to the Software Catalog")
    return 0


def demo_files(call, repo_path, base):
    """The Software Catalog files on `base` that `catalog-pr` added ->
    [{path, sha, service_name}]: only those tagged CATALOG_TAG, so the user's
    own definitions are never touched."""
    found = []
    # Demo files are only ever created at the root, which the scan always
    # covers — a truncated tree cannot hide one.
    for path in catalog_files(call, repo_path, base)[0]:
        status, entry = call("GET", f"{repo_path}/contents/{path}?ref={quote(base, safe='')}", None)
        if status == 404:
            continue  # removed since the tree was read
        if status != 200:
            raise_read_error(status, entry, f"reading {path} in {repo_path}")
        try:
            text = base64.b64decode(entry.get("content") or "").decode()
        except (ValueError, UnicodeDecodeError):
            continue
        if not re.search(rf"^\s*-\s*{re.escape(CATALOG_TAG)}\s*$", text, re.M):
            continue
        # Quoted (current) or bare (files written before names were quoted).
        name = re.search(r'^  name:\s*"?([^"\s]+)"?\s*$', text, re.M)
        found.append({"path": path, "sha": entry.get("sha"),
                      "service_name": name.group(1) if name else None})
    return found


def open_cleanup_pr(call, owner, repository, dry_run=False, account="this gh account"):
    """Open the pull request that removes `catalog-pr`'s demo files from one
    repository; `call` is `gh_api` (injectable for tests).

    Returns {repository, base_branch, branch, demo_files, pr_url, pr_number};
    no PR (pr_url None) when there are no demo files or with `dry_run`.
    Refuses like `open_catalog_pr` when the account cannot push or a removal
    PR is still open; uses a new branch (`new_branch`). Never touches the
    default branch, never merges."""
    repo_path = f"repos/{owner}/{repository}"
    access, info = repo_access(call, owner, repository)
    if access != "push":
        raise GitHubError(
            f"{account} cannot {'see' if access == 'none' else 'push to'} {owner}/{repository}")
    base = info.get("default_branch") or "main"
    files = demo_files(call, repo_path, base)
    result = {"repository": f"{owner}/{repository}", "base_branch": base,
              "branch": None, "demo_files": [
                  {"path": f["path"], "service_name": f["service_name"]} for f in files],
              "pr_url": None, "pr_number": None}
    if not files:
        return result
    branch = result["branch"] = new_branch(call, owner, repository, CLEANUP_BRANCH)
    if dry_run:
        return result

    def ok(status, payload, what):
        if status not in (200, 201, 204):
            raise GitHubError(f"{what} failed: {(payload or {}).get('message') or f'HTTP {status}'}")
        return payload

    ref = ok(*call("GET", f"{repo_path}/git/ref/heads/{quote(base, safe='')}", None),
             f"reading branch {base}")
    ok(*call("POST", f"{repo_path}/git/refs",
             {"ref": f"refs/heads/{branch}", "sha": (ref.get("object") or {}).get("sha")}),
       f"creating branch {branch}")
    for f in files:
        ok(*call("DELETE", f"{repo_path}/contents/{f['path']}", {
            "message": f"Remove {f['path']} added by the Datadog GitHub onboarding",
            "sha": f["sha"], "branch": branch,
        }), f"removing {f['path']}")
    names = ", ".join(f"`{f['service_name']}`" for f in files if f["service_name"])
    pr = ok(*call("POST", f"{repo_path}/pulls", {
        "title": "Remove the Datadog Software Catalog demo",
        "head": branch,
        "base": base,
        "body": (
            "Removes the `service.datadog.yaml` the Datadog GitHub onboarding added"
            + (f" ({names})" if names else "") + ", so the Software Catalog drops it before "
            "the GitHub integration is removed. Merge it before uninstalling — once the "
            "integration is gone, Datadog can no longer see the change."
        ),
    }), "opening the pull request")
    return dict(result, pr_url=pr.get("html_url"), pr_number=pr.get("number"))


DEMO_FILE_URL = re.compile(r"^https://github\.com/([^/]+)/([^/]+)/blob/[^/]+/(.+)$")


def catalog_demo_repositories(pup, site, owner, org=None):
    """Repositories of `owner` holding a demo file, found through the
    Software Catalog rather than by scanning every repository.

    Pages through the catalog (its tag filters are ignored, verified live),
    keeps entries tagged CATALOG_TAG, and reads each one's repository from
    the file it syncs from (`meta.htmlURL`)."""
    repos, offset = set(), 0
    for _ in range(MAX_PAGES):
        fields = {"page[limit]": str(PAGE_LIMIT), "page[offset]": str(offset)}
        payload = api_json(pup_api(pup, site, "GET", CATALOG_ENTITY_PATH, fields=fields, org=org),
                           "listing the Software Catalog")
        entities = payload.get("data") or []
        for entity in entities:
            if CATALOG_TAG not in ((entity.get("attributes") or {}).get("tags") or []):
                continue
            match = DEMO_FILE_URL.match((entity.get("meta") or {}).get("htmlURL") or "")
            if match and match.group(1).lower() == owner.lower():
                repos.add(match.group(2))
        if len(entities) < PAGE_LIMIT:
            return sorted(repos)
        offset += PAGE_LIMIT
    raise GitHubError(f"the Software Catalog returned more than {MAX_PAGES} pages — stopping")


def cmd_catalog_cleanup(args):
    site = resolve_site(args)
    account = pick_account(fetch_accounts(args.pup, site, args.org), args.account)
    owner = account["account_name"]
    # Only the repositories the catalog has demo entries from; each file is
    # still confirmed (path and tag) on GitHub before anything is removed.
    repos = catalog_demo_repositories(args.pup, site, owner, args.org)
    results = []
    for repository in repos:
        # One repository failing (no push access, an open PR) never stops the others.
        try:
            login, token, checked = gh_account_for(args, owner, repository)
            if login is None:
                results.append({"repository": f"{owner}/{repository}", "needs_choice": True,
                                "accounts": checked})
                continue
            result = open_cleanup_pr(
                lambda method, path, body: gh_api(args.gh, method, path, body, token),
                owner, repository, dry_run=args.dry_run, account=login)
            results.append(dict(result, gh_user=login))
        except GitHubError as e:
            results.append({"repository": f"{owner}/{repository}", "error": str(e)})
    services = sorted({f["service_name"] for r in results for f in r.get("demo_files") or []
                       if f["service_name"]})
    blocked = [r for r in results if r.get("error") or r.get("needs_choice")]
    if args.json:
        print(json.dumps({"site": site, "account": owner, "dry_run": args.dry_run,
                          "repositories": results, "service_names": services}, indent=2))
    else:
        for r in results:
            if r.get("error"):
                print(f"{r['repository']}: skipped — {r['error']}")
            elif r.get("needs_choice"):
                print(f"{r['repository']}: several gh accounts can push — pass --gh-user <login>")
            elif not r["demo_files"]:
                print(f"{r['repository']}: no demo files")
            else:
                paths = ", ".join(f["path"] for f in r["demo_files"])
                print(f"{r['repository']}: " + (
                    f"would remove {paths} as {r['gh_user']}" if args.dry_run
                    else f"opened {r['pr_url']} removing {paths}"))
    return 2 if blocked else 0


def catalog_entity(payload, service_name):
    """The catalog entry named `service_name` from an entity list, or None.

    Returns {id, name, kind, github_url}: `github_url` is the definition file
    on GitHub (`meta.htmlURL`) for an entry synced from a repository."""
    for entity in payload.get("data") or []:
        attrs = entity.get("attributes") or {}
        if (attrs.get("name") or entity.get("name")) == service_name:
            meta = entity.get("meta") or {}
            return {
                "id": entity.get("id"),
                "name": attrs.get("name"),
                "kind": attrs.get("kind"),
                "github_url": meta.get("htmlURL"),
            }
    return None


def wait_for_catalog(fetch, service_name, interval=30.0, timeout=3600.0, absent=False):
    """Poll `fetch()` (a Software Catalog entity list filtered by name) until
    `service_name` appears — or, with `absent`, is gone. Returns its
    `catalog_entity` as last seen: None means it is not in the catalog."""
    return poll(lambda: catalog_entity(fetch(), service_name),
                lambda entity: (entity is None) == absent, interval, timeout)


def fetch_catalog(pup, site, service_name, org=None):
    """The Software Catalog entity list filtered by name (read-only)."""
    proc = pup_api(pup, site, "GET", CATALOG_ENTITY_PATH,
                   fields={"filter[name]": service_name}, org=org)
    return api_json(proc, "reading the Software Catalog")


def catalog_lookup(pup, site, service_name, org=None):
    """The catalog entry named `service_name`, or None."""
    return catalog_entity(fetch_catalog(pup, site, service_name, org), service_name)


def cmd_catalog_wait(args):
    site = resolve_site(args)
    entity = wait_for_catalog(lambda: fetch_catalog(args.pup, site, args.service_name, args.org),
                              args.service_name, args.interval, args.timeout, args.absent)
    found = entity is not None
    if args.absent:
        if args.json:
            print(json.dumps({"site": site, "service_name": args.service_name,
                              "in_catalog": found}, indent=2))
        elif not found:
            print(f"{args.service_name!r} is no longer in the Software Catalog on {site}")
        else:
            print(
                f"error: {args.service_name!r} is still in the Software Catalog after "
                f"{args.timeout:g}s — check the removal pull request is merged into the default "
                "branch while the GitHub integration is still installed",
                file=sys.stderr,
            )
        return 2 if found else 0
    # The Software Catalog's home page; the API returns no Datadog page URL for
    # one entry, so the entry itself is identified by name, id and its file.
    link = f"https://{app_host(site)}/software"
    if args.json:
        print(json.dumps({"site": site, "service_name": args.service_name,
                          "in_catalog": found, "link": link, "entity": entity}, indent=2))
    elif found:
        print(f"{args.service_name!r} is in the Software Catalog on {site} — {link}"
              + (f" (defined in {entity['github_url']})" if entity.get("github_url") else ""))
    else:
        print(
            f"error: {args.service_name!r} did not appear in the Software Catalog within "
            f"{args.timeout:g}s — check the pull request is merged into the default branch, then "
            "look for sync errors in Event Management with `source:software_catalog status:error`",
            file=sys.stderr,
        )
    return 0 if found else 2


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="GitHub + Datadog: GitHub App install URL and account setup."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    def common(p):
        p.add_argument("--site", help="Datadog site; default: derive from `pup auth status`")
        p.add_argument("--pup", default="pup", help="pup binary used for auth and API calls")
        p.add_argument(
            "--org",
            help="named pup org session (e.g. staging); passed to `pup auth status`, `pup auth token` and `pup api`",
        )
        p.add_argument("--json", action="store_true", help="emit machine-readable JSON")

    accounts = sub.add_parser("accounts", help="list GitHub accounts and app installations")
    common(accounts)

    authz = sub.add_parser(
        "authorize", help="open GitHub's install screen for the Datadog GitHub App (guards on existing accounts)"
    )
    authz.add_argument("--open", action="store_true", help="open the install URL in the user's browser")
    authz.add_argument(
        "--app-slug",
        help="GitHub App slug for this site's Datadog app (default: derived from the site)",
    )
    authz.add_argument(
        "--continue", dest="continue_setup", action="store_true",
        help="proceed even when accounts are already connected (the user confirmed they "
             "want to connect another GitHub)",
    )
    common(authz)

    wait = sub.add_parser(
        "wait",
        help="poll until the GitHub App install lands and link the installation (background it)",
    )
    wait.add_argument(
        "--interval", type=float, default=5.0, help="seconds between polls (default: 5)"
    )
    wait.add_argument(
        "--timeout", type=float, default=300.0, help="give up after this many seconds (default: 300)"
    )
    common(wait)

    link = sub.add_parser("link", help="link one installation by id")
    link.add_argument("installation_id", type=int, help="installation id as listed by `accounts`")
    common(link)

    unlink = sub.add_parser("unlink", help="unlink an installation by id (cleanup)")
    unlink.add_argument("installation_id", type=int, help="installation id as listed by `accounts`")
    common(unlink)

    verify = sub.add_parser(
        "verify",
        help="prove what the connect unlocked (sync, code access, PR comments), read-only",
    )
    verify.add_argument("--account", help="GitHub account to verify (default: the only one)")
    verify.add_argument(
        "--interval", type=float, default=5.0, help="seconds between sync checks (default: 5)"
    )
    verify.add_argument(
        "--timeout", type=float, default=120.0,
        help="give up waiting for the repository sync after this many seconds (default: 120)",
    )
    common(verify)

    catalog_pr = sub.add_parser(
        "catalog-pr",
        help="open a PR adding service.datadog.yaml (Software Catalog demo) via the user's gh login",
    )
    catalog_pr.add_argument("--repo", required=True, help="repository as <owner>/<name>")
    catalog_pr.add_argument("--service-name",
                            help="Software Catalog name (default: derived from the repository)")
    catalog_pr.add_argument(
        "--gh-user",
        help="gh account to act as (default: the only logged-in account that can push)")
    catalog_pr.add_argument("--gh", default="gh", help="gh binary (default: gh)")
    catalog_pr.add_argument("--dry-run", action="store_true",
                            help="run the checks and print the file; write nothing")
    common(catalog_pr)

    catalog_wait = sub.add_parser(
        "catalog-wait",
        help="poll the Software Catalog until the service appears (background it)",
    )
    catalog_wait.add_argument("--service-name", required=True,
                              help="the name `catalog-pr` (or `catalog-cleanup`) reported")
    catalog_wait.add_argument("--absent", action="store_true",
                              help="wait until the service is gone (after a removal PR merges)")
    # The sync itself takes seconds; the wait is for a person to review and
    # merge the pull request, so the default allows an hour.
    catalog_wait.add_argument("--interval", type=float, default=30.0,
                              help="seconds between checks (default: 30)")
    catalog_wait.add_argument("--timeout", type=float, default=3600.0,
                              help="give up after this many seconds (default: 3600, one hour)")
    common(catalog_wait)

    catalog_cleanup = sub.add_parser(
        "catalog-cleanup",
        help="before unlinking: open PRs removing catalog-pr's demo files (tagged only)",
    )
    catalog_cleanup.add_argument("--account", help="GitHub account (default: the only one)")
    catalog_cleanup.add_argument("--gh-user",
                                 help="gh account to act as (default: the one that can push)")
    catalog_cleanup.add_argument("--gh", default="gh", help="gh binary (default: gh)")
    catalog_cleanup.add_argument("--dry-run", action="store_true",
                                 help="list the demo files per repository; write nothing")
    common(catalog_cleanup)

    args = parser.parse_args(argv)
    try:
        if args.command == "accounts":
            return cmd_accounts(args)
        if args.command == "authorize":
            return cmd_authorize(args)
        if args.command == "wait":
            return cmd_wait(args)
        if args.command == "link":
            return cmd_link(args)
        if args.command == "verify":
            return cmd_verify(args)
        if args.command == "catalog-pr":
            return cmd_catalog_pr(args)
        if args.command == "catalog-wait":
            return cmd_catalog_wait(args)
        if args.command == "catalog-cleanup":
            return cmd_catalog_cleanup(args)
        return cmd_unlink(args)
    except GitHubError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
