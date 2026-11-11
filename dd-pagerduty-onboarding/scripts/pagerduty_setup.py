#!/usr/bin/env python3
"""dd-pagerduty-onboarding helper.

Five subcommands, one script:

- `services`       list the connected PagerDuty services
                    (GET /api/v1/integration/pagerduty).
- `authorize`      check for existing services first — when any are connected
                    the command refuses and lists them unless `--continue` is
                    passed, so the user decides whether to set up new
                    services — then mint the PagerDuty install URL and open it.
- `wait`           poll the services list until the install lands (a new
                    service appears), so nobody has to report back that they
                    clicked install; times out with exit 2.
- `delete-service` remove a connected service by name, confirmed by a re-list.
- `test-page`      page a connected service end to end: create a throwaway monitor
                    that notifies it, wait for the alert (the page) and the
                    recovery (the resolve), then delete the monitor. A downtime
                    that mutes the alert is reported as not paged.

All `/api/` calls go through `pup api` (`--no-agent`, so output is the raw
payload), which supplies authentication from the user's session.

The authorize mint is different: the Datadog API answers it with a 302 to
`app.pagerduty.com`, and `pup api` follows redirects — the Location header
(the actual install URL) is only visible to a raw HTTP client. So the mint is
read with urllib on the allowlisted `api.<site>` host, with a bearer captured
from `pup auth token` that lives in process memory only — never in argv, on
disk, or in output.

Exit codes: 0 success; 1 error (bad input, pup/auth failure, API error);
2 an expected "not yet" outcome — `services` found nothing connected, `wait`
timed out, or `test-page` did not complete every step; 3 `authorize --open`
could not open a browser.
"""

import argparse
import json
import os
import secrets
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from urllib.parse import quote

INTEGRATION_BASE = "/api/v1/integration/pagerduty"
# Datadog route that mints the PagerDuty install URL; served on the api host.
AUTHORIZE_PATH = "/auth/pagerduty/add-services/begin"
MONITOR_BASE = "/api/v1/monitor"
# Tag on every throwaway `test-page` monitor, so a leftover one is easy to find.
TEST_MONITOR_TAG = "dd-pagerduty-onboarding:test"
# Upper bound (seconds) on waiting for the test monitor to recover during
# cleanup after an error or interrupt, before deleting it anyway.
CLEANUP_RECOVERY_WAIT = 120.0

# Datadog RBAC permission that authorizes installing/connecting an integration.
# Integrations Read (`integrations_read`) is view-only and is NOT sufficient.
PERMISSION_MARKERS = ("403", "forbidden", "permission", "manage_integrations")
PERMISSION_HELP = (
    "you may need the Integrations Manage (manage_integrations) permission on the Datadog org; "
    "an org admin grants it through RBAC — "
    "https://docs.datadoghq.com/account_management/rbac/permissions/"
)


EXIT_ERROR = 1
EXIT_NOT_YET = 2
EXIT_NO_BROWSER = 3


class PagerDutyError(Exception):
    """User-actionable failure: bad input, unusable pup state, or an API error."""


class WaitTimeout(PagerDutyError):
    """`wait` gave up with no new service: an expected outcome, not an error (exit 2)."""


class _Parser(argparse.ArgumentParser):
    """argparse exits 2 on a usage error; exit 1 so 2 keeps its one meaning."""

    def error(self, message):
        self.print_usage(sys.stderr)
        self.exit(EXIT_ERROR, f"{self.prog}: error: {message}\n")


def permission_error(text):
    """True when an error message looks like a missing-permission failure.

    Datadog answers a missing `manage_integrations` with 403/Forbidden, and
    the message occasionally names the permission instead — match all of those."""
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
        raise PagerDutyError(f"invalid Datadog site: {site!r}")
    return site


def api_host(site):
    """Map a Datadog site to its API host (e.g. datadoghq.com -> api.datadoghq.com)."""
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
        raise PagerDutyError(f"pup not found ({pup!r}) — install the Datadog pup CLI")
    except (subprocess.TimeoutExpired, OSError) as e:
        raise PagerDutyError(f"could not run `pup auth status`: {e}")
    out = (proc.stdout or "") + (proc.stderr or "")
    start = out.find("{")  # tolerate non-JSON preamble (e.g. keychain warnings)
    if start < 0:
        raise PagerDutyError(
            "`pup auth status` did not return JSON — is 'pup' the Datadog CLI? "
            f"Output began: {out.strip()[:120]!r}"
        )
    try:
        # raw_decode parses the first balanced JSON object, ignoring any trailing
        # non-JSON noise (e.g. a stderr warning appended after stdout's JSON).
        status, _ = json.JSONDecoder().raw_decode(out, start)
    except json.JSONDecodeError:
        raise PagerDutyError(f"could not parse `pup auth status` output as JSON: {out.strip()[:120]!r}")
    if not isinstance(status, dict) or "site" not in status:
        raise PagerDutyError(
            "`pup auth status` returned JSON but not a Datadog auth status — is 'pup' the "
            f"Datadog CLI? Output began: {out.strip()[:120]!r}"
        )
    if not status.get("authenticated"):
        raise PagerDutyError("pup is not authenticated — run `pup auth login`, then retry")
    site = str(status.get("site") or "").strip().lower()
    if not site:
        raise PagerDutyError("`pup auth status` reported no site")
    return site


# Datadog for Government is out of scope for the agentic onboarding skills.
GOV_SITE = "ddog-gov.com"


def resolve_site(args):
    """The Datadog site of the user's pup session (`DD_SITE` selects it).

    There is no --site override: the site always comes from the session that
    makes the API calls, so the mint and the API never disagree. Refuses gov sites."""
    site = site_from_pup(args.pup, args.org)
    if site == GOV_SITE or site.endswith("." + GOV_SITE):
        raise PagerDutyError(
            f"this onboarding skill does not support Datadog for Government ({site}); "
            "set up the integration from the Datadog app instead"
        )
    return site


def pup_api(pup, site, method, path, fields=None, org=None, auto_approve=False, body=None):
    """One authenticated Datadog API call through `pup api` (raw payload, no envelope).

    `org` (a global pup flag, valid after the subcommand) selects the same named
    org-keyed session `site_from_pup` resolves against. `body` is a JSON request
    body sent on stdin, for payloads `fields` cannot express (nested objects)."""
    cmd = [pup, "api", "--no-agent"]
    if org:
        cmd += ["--org", org]
    cmd += ["--method", method, path]
    if auto_approve:
        cmd += ["--yes"]
    for key, value in (fields or {}).items():
        cmd += ["--raw-field", f"{key}={value}"]
    stdin = None
    if body is not None:
        cmd += ["--input", "-"]
        stdin = json.dumps(body)
    env = dict(os.environ, DD_SITE=site)
    try:
        return subprocess.run(
            cmd, capture_output=True, text=True, timeout=60, env=env, input=stdin,
        )
    except FileNotFoundError:
        raise PagerDutyError(f"pup not found ({pup!r}) — install the Datadog pup CLI")
    except (subprocess.TimeoutExpired, OSError) as e:
        raise PagerDutyError(f"could not run `pup api {method} {path}`: {e}")


def api_json(proc, what):
    """Parse a `pup api` result: JSON body on stdout; API errors become PagerDutyError.

    A permission-shaped failure carries the RBAC guidance, whichever call
    produced it — a 403 on the OAuth mint and a 403 on a service delete are the
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
                raise PagerDutyError(
                    with_permission_help(f"{what} failed: {', '.join(map(str, errors))}")
                )
            if proc.returncode == 0:
                return payload
    raise PagerDutyError(
        with_permission_help(
            f"{what} failed (pup exit {proc.returncode}): {out.strip()[:200]!r}"
        )
    )


def parse_services(payload):
    """Connected service names from GET /api/v1/integration/pagerduty."""
    return sorted(
        str(entry.get("service_name")).strip()
        for entry in payload.get("services") or []
        if str(entry.get("service_name") or "").strip()
    )


def api_ok(proc, what):
    """Raise unless a `pup api` call succeeded; an empty body (e.g. 204) is success."""
    out = (proc.stdout or "") + (proc.stderr or "")
    if proc.returncode == 0 and not out.strip():
        return
    if "{" in out:
        api_json(proc, what)
        return
    if proc.returncode != 0:
        raise PagerDutyError(
            with_permission_help(f"{what} failed (pup exit {proc.returncode}): {out.strip()[:200]!r}")
        )


def fetch_services(pup, site, org=None):
    proc = pup_api(pup, site, "GET", INTEGRATION_BASE, org=org)
    return parse_services(api_json(proc, "listing the PagerDuty integration"))


def pup_token(pup, site, org=None):
    """Capture the org-bound bearer for the one raw mint call.

    Obscuration contract: the token is read from `pup auth token` into process
    memory and used only in the urllib Authorization header — never in argv,
    on disk, or in output. On failure only the exit code and output length are
    reported, never the content."""
    cmd = [pup, "auth", "token"]
    if org:
        cmd += ["--org", org]
    env = dict(os.environ, DD_SITE=site)
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60, env=env)
    except FileNotFoundError:
        raise PagerDutyError(f"pup not found ({pup!r}) — install the Datadog pup CLI")
    except (subprocess.TimeoutExpired, OSError) as e:
        raise PagerDutyError(f"could not run `pup auth token`: {e}")
    if proc.returncode != 0:
        raise PagerDutyError(
            with_permission_help(
                f"`pup auth token` failed (exit {proc.returncode}; stdout {len(proc.stdout or '')}B, "
                f"stderr {len(proc.stderr or '')}B — content withheld) — the session is missing, "
                f"expired, or org-unbound; run `DD_SITE={site} pup auth login` and retry"
            )
        )
    token = proc.stdout.strip()
    if not token or len(token.split()) > 1:
        raise PagerDutyError("`pup auth token` output was not a single bare token — check your pup version")
    return token


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
        raise PagerDutyError(f"minting the PagerDuty install URL failed: {getattr(e, 'reason', e)}")


def mint_url(pup, site, org=None, token=None, http=None):
    """Mint the PagerDuty install URL.

    The Datadog API answers GET /auth/pagerduty/add-services/begin with a 302 whose
    Location is the `app.pagerduty.com/install/integration` URL carrying a
    short-lived (~5 minutes) org+user-bound state. `pup api` follows redirects,
    so this one call uses a raw client; the bearer comes from `pup auth token`
    (injectable `token`/`http` keep the network out of tests). A 307 to the
    login page means the site does not accept token authn on the
    route yet; 401/403 carry the RBAC guidance."""
    if token is None:
        token = pup_token(pup, site, org)
    fetch = http or http_redirect
    status, location = fetch(f"https://{api_host(site)}{AUTHORIZE_PATH}", token)
    if status == 307 and location and "/account/login" in location:
        raise PagerDutyError(
            "the mint redirected to login — this site does not accept token authn "
            "on this route yet; use the PagerDuty integration tile in the Datadog app instead"
        )
    if status != 302 or not location:
        if status in (401, 403):
            raise PagerDutyError(with_permission_help(
                f"the mint answered HTTP {status} (token org-unbound, expired, or ManageIntegrations missing)"))
        raise PagerDutyError(f"the mint answered HTTP {status} (expected 302)")
    return location


def _wait_timeout_error(timeout, site):
    return WaitTimeout(
        f"timed out after {timeout:g}s waiting for a new PagerDuty service on {site} — the install "
        "has not landed. Run `services` first: a service that was already connected, or that landed "
        "before the poll started, is not reported as new. Otherwise check that the browser finished "
        "PagerDuty's install screen with a Datadog app session for this site, re-run "
        "`authorize --open` for a fresh URL, then re-run `wait`"
    )


def wait_for_service(fetch, interval=5.0, timeout=300.0, site=None):
    """Poll `fetch` (returning service names) until a new one appears.

    The service set is snapshotted on the first call and the loop returns as
    soon as it grows. Raises WaitTimeout on timeout. Prints nothing: the
    caller reports once. `fetch` and the clock are injectable so tests can
    drive faked sequences."""
    if interval <= 0:
        raise PagerDutyError(f"--interval must be greater than zero (got {interval:g})")
    if timeout <= 0:
        raise PagerDutyError(f"--timeout must be greater than zero (got {timeout:g})")
    clock, sleep = time.monotonic, time.sleep
    deadline = clock() + timeout
    baseline = list(fetch())
    while True:
        remaining = deadline - clock()
        if remaining <= 0:
            raise _wait_timeout_error(timeout, site)
        sleep(min(interval, remaining))
        names = list(fetch())
        new = [name for name in names if name not in baseline]
        if new:
            return new


def test_monitor(service, nonce, firing):
    """A throwaway log monitor that pages `service` without needing any data.

    It counts logs matching a nonce that never occurs, so the count is always 0:
    `>= 0` holds (the monitor alerts and pages) and `> 0` does not (it recovers,
    which resolves the PagerDuty incident). No logs or events are required, so
    it works on a brand-new org; the session needs `monitors_write`.

    The empty count is evaluated as 0 only under the legacy `notify_no_data:
    false`. Do not set `on_missing_data` (not even to "default"): verified live,
    it leaves the monitor in No Data and it never fires."""
    query = (
        f'logs("dd-pagerduty-onboarding-test-{nonce}").index("*").rollup("count").last("5m") '
        + (">= 0" if firing else "> 0")
    )
    return {
        "name": f"[Datadog onboarding] PagerDuty test page ({nonce})",
        "type": "log alert",
        "query": query,
        "message": (
            "Test page from the Datadog PagerDuty onboarding — safe to ignore: it resolves and "
            f"the monitor is deleted automatically. @pagerduty-{service}"
        ),
        "tags": [TEST_MONITOR_TAG],
        "options": {"thresholds": {"critical": 0}, "notify_no_data": False},
    }


def wait_for_state(fetch_state, want, interval=10.0, timeout=300.0):
    """Poll `fetch_state` until it returns `want`; True on success, False on timeout.

    The clock is looked up per call (as in `wait_for_service`) so tests can fake it."""
    clock, sleep = time.monotonic, time.sleep
    deadline = clock() + timeout
    while True:
        if fetch_state() == want:
            return True
        remaining = deadline - clock()
        if remaining <= 0:
            return False
        sleep(min(interval, remaining))


def cleanup_hint(monitor_id):
    """How to remove a test monitor the script could not confirm it deleted."""
    return (
        f"test monitor {monitor_id} was not deleted — remove it with "
        f"`pup monitors delete {monitor_id}` (tag {TEST_MONITOR_TAG})"
    )


def manual_resolve_hint(service):
    """What to tell the user when the test alert's recovery was never confirmed."""
    return (
        f"the test page's recovery was not confirmed, so the PagerDuty incident on "
        f"{service!r} may still be open — resolve it manually in PagerDuty"
    )


def is_muted(monitor):
    """True when notifications for this monitor are suppressed.

    An active downtime covering it (`matching_downtimes`, present when read with
    `with_downtimes=true`) or a monitor-level mute (`options.silenced`) still lets
    the state go Alert -> OK, but no notification is sent — so no page."""
    silenced = (monitor.get("options") or {}).get("silenced")
    return bool(monitor.get("matching_downtimes") or silenced)


def delete_monitor(pup, site, path, org=None):
    """Delete a monitor; True only once a re-read 404s ("Monitor not found").

    Never raises: it runs during cleanup, where a pup failure must not replace
    the error that triggered the cleanup."""
    try:
        pup_api(pup, site, "DELETE", path, org=org, auto_approve=True)
        gone = pup_api(pup, site, "GET", path, org=org)
    except PagerDutyError:
        return False
    return gone.returncode != 0 and "not found" in (
        (gone.stdout or "") + (gone.stderr or "")
    ).lower()


def page_test(pup, site, service, org=None, interval=10.0, timeout=300.0, nonce=None):
    """Page `service` through a throwaway monitor, then resolve and delete it.

    Returns {monitor_id, alerted, muted, paged, resolved, deleted}. `alerted`
    is the monitor reaching Alert; `paged` is that alert actually notifying —
    false when a downtime or mute suppressed it (`muted`). The script sees only
    Datadog's side, so `paged` means Datadog sent the notification.

    The monitor is deleted whatever happens after it is created — a timeout,
    API error, or interrupt never leaves it behind silently — and deleting only
    after the recovery means the PagerDuty incident is resolved, not left open.
    When an error or interrupt hits after the alert, cleanup first tries to
    recover the monitor (bounded by CLEANUP_RECOVERY_WAIT) before deleting it.
    Whatever the user must act on — an unconfirmed recovery (resolve the
    incident by hand) or an unconfirmed delete — travels with the error."""
    if interval <= 0:
        raise PagerDutyError(f"--interval must be greater than zero (got {interval:g})")
    if timeout <= 0:
        raise PagerDutyError(f"--timeout must be greater than zero (got {timeout:g})")
    connected = fetch_services(pup, site, org)
    if service not in connected:
        raise PagerDutyError(
            f"PagerDuty service {service!r} is not connected on {site} (connected: "
            f"{', '.join(connected) or 'none'}) — pass a name as listed by `services`"
        )
    nonce = nonce or secrets.token_hex(4)
    created = api_json(
        pup_api(pup, site, "POST", MONITOR_BASE, org=org,
                body=test_monitor(service, nonce, firing=True)),
        "creating the test monitor",
    )
    monitor_id = created.get("id")
    if monitor_id is None:
        raise PagerDutyError("creating the test monitor returned no monitor id")
    path = f"{MONITOR_BASE}/{monitor_id}"
    result = {
        "monitor_id": monitor_id, "alerted": False, "muted": False,
        "paged": False, "resolved": False, "deleted": False,
    }

    def state():
        monitor = api_json(
            pup_api(pup, site, "GET", f"{path}?with_downtimes=true", org=org),
            "reading the test monitor",
        )
        # Sticky: a downtime seen at any point while the test runs may have
        # swallowed the alert's notification.
        result["muted"] = result["muted"] or is_muted(monitor)
        return monitor.get("overall_state")

    recover_sent = False

    def recover():
        nonlocal recover_sent
        api_json(
            pup_api(pup, site, "PUT", path, org=org,
                    body=test_monitor(service, nonce, firing=False)),
            "updating the test monitor to recover",
        )
        recover_sent = True

    try:
        result["alerted"] = wait_for_state(state, "Alert", interval, timeout)
        if result["alerted"]:
            recover()
            result["resolved"] = wait_for_state(state, "OK", interval, timeout)
    except BaseException as e:
        # Cleanup must outlive any failure (including Ctrl-C). Deleting an alerting
        # monitor sends no recovery, so once it has paged, recover it first: that
        # is what resolves the PagerDuty incident. The attempt is bounded and never
        # raises — a second Ctrl-C skips it, but the delete still runs.
        if result["alerted"] and not result["resolved"]:
            try:
                if not recover_sent:
                    recover()
                result["resolved"] = wait_for_state(
                    state, "OK", interval, min(timeout, CLEANUP_RECOVERY_WAIT),
                )
            except BaseException:
                pass
        notes = []
        if result["alerted"] and not result["resolved"]:
            notes.append(manual_resolve_hint(service))
        if not delete_monitor(pup, site, path, org):
            notes.append(cleanup_hint(monitor_id))
        # No result is returned on this path, so anything the user must act on
        # travels with the error.
        if notes and isinstance(e, PagerDutyError):
            raise PagerDutyError(f"{e} — and {'; '.join(notes)}") from e
        for note in notes:
            print(f"error: {note}", file=sys.stderr)
        raise
    result["deleted"] = delete_monitor(pup, site, path, org)
    result["paged"] = result["alerted"] and not result["muted"]
    return result


def cmd_services(args):
    site = resolve_site(args)
    services = fetch_services(args.pup, site, args.org)
    if not services:
        print(
            "error: no PagerDuty service is connected on this org — run the `authorize` "
            "subcommand with --open to start the flow",
            file=sys.stderr,
        )
        return EXIT_NOT_YET
    if args.json:
        print(json.dumps({"site": site, "services": services}, indent=2))
    else:
        print(f"site:     {site}")
        print(f"services: {', '.join(services)}")
    return 0


def cmd_authorize(args):
    site = resolve_site(args)

    # The user decides whether to add more services when the integration
    # already exists: refuse and list what is connected unless --continue.
    existing = fetch_services(args.pup, site, args.org)
    if existing and not args.continue_setup:
        raise PagerDutyError(
            "PagerDuty services are already connected on this org: "
            + ", ".join(existing)
            + " — confirm with the user whether they want to set up new services, "
            "then re-run with --continue"
        )

    url = mint_url(args.pup, site, args.org)
    opened = False
    if args.open:
        opened = webbrowser.open(url)
        if not opened:
            print(f"error: could not open a browser — open the URL manually: {url}", file=sys.stderr)
            return EXIT_NO_BROWSER
    if args.json:
        print(json.dumps({
            "site": site,
            "url": url,
            "existing_services": existing,
            "opened_browser": opened,
        }, indent=2))
    else:
        print(url)
    return 0


def cmd_wait(args):
    """Block until the install shows up as a new service, then report it once.

    Meant to be started as a background process right after the install URL is
    opened, so the flow proceeds on the connect itself instead of on the user
    saying they clicked install."""
    site = resolve_site(args)
    landed = wait_for_service(
        lambda: fetch_services(args.pup, site, args.org),
        interval=args.interval,
        timeout=args.timeout,
        site=site,
    )
    if args.json:
        print(json.dumps({"site": site, "new_services": landed}, indent=2))
    else:
        print(f"pagerduty connected: {', '.join(landed)}")
    return 0


def cmd_delete_service(args):
    site = resolve_site(args)
    path = f"{INTEGRATION_BASE}/configuration/services/{quote(args.service, safe='')}"
    # A failed DELETE (403, 404, ...) raises with the API's error — and the RBAC
    # guidance on a 403 — instead of falling through to the re-list.
    api_ok(
        pup_api(args.pup, site, "DELETE", path, org=args.org, auto_approve=True),
        f"deleting the PagerDuty service {args.service!r}",
    )

    # Confirm the service is gone before claiming success.
    remaining = fetch_services(args.pup, site, args.org)
    deleted = args.service not in remaining
    if args.json:
        print(json.dumps({
            "site": site,
            "service": args.service,
            "remaining": remaining,
            "deleted": deleted,
        }, indent=2))
    elif deleted:
        print(f"deleted {args.service!r} on {site} (remaining: {', '.join(remaining) or 'none'})")
    if not deleted:
        print(f"error: {args.service!r} is still connected on {site}", file=sys.stderr)
        return EXIT_ERROR
    return 0


def cmd_test_page(args):
    site = resolve_site(args)
    result = page_test(
        args.pup, site, args.service, org=args.org,
        interval=args.interval, timeout=args.timeout,
    )
    ok = result["paged"] and result["resolved"] and result["deleted"]
    if args.json:
        print(json.dumps({"site": site, "service": args.service, **result}, indent=2))
    else:
        def mark(done):
            return "yes" if done else "NO"
        print(
            f"test page to {args.service!r} on {site} (monitor {result['monitor_id']}): "
            f"paged {mark(result['paged'])}, resolved {mark(result['resolved'])}, "
            f"monitor deleted {mark(result['deleted'])}"
        )
    if result["muted"]:
        print(
            "error: a downtime or mute covered the test monitor, so its alert sent no "
            "notification — PagerDuty was not paged; end the downtime and re-run",
            file=sys.stderr,
        )
    if not result["deleted"]:
        print(f"error: {cleanup_hint(result['monitor_id'])}", file=sys.stderr)
    return 0 if ok else EXIT_NOT_YET


def main(argv=None):
    parser = _Parser(
        description="PagerDuty + Datadog: OAuth install URL and service setup."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    def common(p):
        p.add_argument("--pup", default="pup", help="pup binary used for auth and API calls")
        # Hidden: the flow uses the default pup session only (see SKILL.md).
        p.add_argument("--org", help=argparse.SUPPRESS)
        p.add_argument("--json", action="store_true", help="emit machine-readable JSON")

    services = sub.add_parser("services", help="list connected PagerDuty services")
    common(services)

    authz = sub.add_parser(
        "authorize", help="mint the PagerDuty install URL (guards on existing services)"
    )
    authz.add_argument("--open", action="store_true", help="open the install URL in the user's browser")
    authz.add_argument(
        "--continue", dest="continue_setup", action="store_true",
        help="proceed even when services are already connected (the user confirmed they "
             "want to set up new ones)",
    )
    common(authz)

    wait = sub.add_parser(
        "wait",
        help="poll until the PagerDuty install lands (start it in the background)",
    )
    wait.add_argument(
        "--interval", type=float, default=5.0, help="seconds between polls (default: 5)"
    )
    wait.add_argument(
        "--timeout", type=float, default=300.0, help="give up after this many seconds (default: 300)"
    )
    common(wait)

    delete = sub.add_parser("delete-service", help="remove a connected service by name")
    delete.add_argument("service", help="PagerDuty service name as listed by `services`")
    common(delete)

    test_page = sub.add_parser(
        "test-page",
        help="page a connected service through a throwaway monitor, then resolve and delete it",
    )
    test_page.add_argument("service", help="PagerDuty service name as listed by `services`")
    test_page.add_argument(
        "--interval", type=float, default=10.0, help="seconds between state checks (default: 10)"
    )
    test_page.add_argument(
        "--timeout", type=float, default=300.0,
        help="give up on each of the alert and the recovery after this many seconds (default: 300)",
    )
    common(test_page)

    args = parser.parse_args(argv)
    try:
        if args.command == "services":
            return cmd_services(args)
        if args.command == "authorize":
            return cmd_authorize(args)
        if args.command == "wait":
            return cmd_wait(args)
        if args.command == "test-page":
            return cmd_test_page(args)
        return cmd_delete_service(args)
    except WaitTimeout as e:
        print(f"error: {e}", file=sys.stderr)
        return EXIT_NOT_YET
    except PagerDutyError as e:
        print(f"error: {e}", file=sys.stderr)
        return EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main())
