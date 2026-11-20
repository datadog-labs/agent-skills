#!/usr/bin/env python3
"""Poll Datadog until usage data arrives for a newly configured Anthropic account.

Only data that lands *after* the install counts. Anthropic usage points are
stamped with the minute the usage happened and arrive late (the first data
collected also covers ~3 hours of earlier usage), so their timestamps cannot tell
new data from old.
Instead setup records a baseline just before it creates the account — the
org's usage total over a window starting 3 hours back — and this script
succeeds only when the total over that same window grows past it. Without a
baseline from setup, it takes its own when it starts and says so."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
import urllib.parse
from typing import Any, Callable

from setup import (
    ACCOUNTS_PATH,
    USAGE_LOOKBACK_SECONDS,
    USAGE_METRIC,
    usage_query,
    usage_total,
    SetupError,
    _ArgumentParser,
    _account_summary,
    _existing_account,
    _pup_base,
    _refuse_gov_site,
    _sanitize_text,
    emit,
)

# Leave room for a new account's first data to be collected and ingested.
MAX_WAIT_SECONDS = 900
POLL_INTERVAL_SECONDS = 30
LOOKUP_RETRY_DELAYS_SECONDS = (5, 10)
PERMISSION_STATUSES = {401, 403}


class _TransientError(Exception):
    """A poll attempt failed in a way that may succeed on the next attempt."""


def _pup_request(method: str, path: str, *, timeout: float) -> Any:
    # Polling is read-only and must fit the overall wait, so each request is
    # bounded by the time remaining.
    command = _pup_base("api")
    command.extend(["--method", method, path])
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            timeout=max(timeout, 1),
            check=False,
        )
    except subprocess.TimeoutExpired as error:
        raise _TransientError("Pup did not answer before the poll deadline.") from error
    except OSError as error:
        raise SetupError(
            "pup_transport_error",
            "Pup could not be started.",
            recoverable=False,
            details={"error_type": type(error).__name__},
        ) from error
    stderr = result.stderr.decode(errors="replace")
    if result.returncode != 0:
        status = re.search(r"\bHTTP (\d{3})\b", stderr)
        http_status = int(status.group(1)) if status else None
        if http_status in PERMISSION_STATUSES:
            raise SetupError(
                "datadog_read_not_permitted",
                "Pup's Datadog session cannot read this data.",
                recoverable=False,
                details={
                    "http_status": http_status,
                    "remediation": (
                        "Run `pup auth login --extra-scopes metrics_read,timeseries_query`."
                    ),
                },
            )
        raise _TransientError(_sanitize_text(stderr) or "Pup request failed.")
    stdout = result.stdout.decode(errors="replace")
    try:
        return json.loads(stdout) if stdout.strip() else {}
    except json.JSONDecodeError as error:
        raise _TransientError("Pup returned a non-JSON response.") from error


def _datadog_site(*, timeout: float) -> str | None:
    """Return Pup's Datadog site, or None; links are a convenience, not a check."""
    command = ["pup", "auth", "status", "--no-envelope", "--output", "json"]
    try:
        result = subprocess.run(
            command, capture_output=True, timeout=max(timeout, 1), check=False
        )
        status = json.loads(result.stdout or b"{}") if result.returncode == 0 else {}
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError):
        return None
    site = status.get("site") if isinstance(status, dict) else None
    return site.strip().lower() if isinstance(site, str) and site.strip() else None


def _app_host(site: str) -> str:
    return site if site.count(".") >= 2 else f"app.{site}"


def _org_scope(org_id: str) -> str:
    return f"org_id:{org_id}"


def _links(site: str, org_id: str, arrived: bool, ccm_enabled: bool) -> dict[str, str]:
    host = _app_host(site)
    links = {}
    if arrived:
        links["metrics"] = f"https://{host}/metric/explorer?" + urllib.parse.urlencode(
            {"exp_metric": USAGE_METRIC, "exp_scope": _org_scope(org_id)}
        )
    if ccm_enabled:
        links["costs"] = f"https://{host}/cost"
    return links


def _total(org_id: str, *, since: int, now: float, timeout: float) -> float:
    return usage_total(
        org_id,
        since=since,
        now=int(now),
        run=lambda method, path: _pup_request(method, path, timeout=timeout),
    )


def verify(
    *,
    account_name: str,
    anthropic_org_id: str,
    plan: str = "platform",
    baseline_since: int | None = None,
    baseline_total: float | None = None,
    max_wait: float = MAX_WAIT_SECONDS,
    interval: float = POLL_INTERVAL_SECONDS,
    clock: Callable[[], float] = time.monotonic,
    wall_clock: Callable[[], float] = time.time,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    org_id = anthropic_org_id.strip()
    if not org_id:
        raise SetupError(
            "anthropic_org_id_missing",
            "Pass the Anthropic organization id that setup reported.",
            recoverable=False,
        )
    start = clock()
    deadline = start + min(max_wait, MAX_WAIT_SECONDS)

    def remaining() -> float:
        return max(deadline - clock(), 0)

    # Refuse Government sites before any Datadog API call.
    site = _datadog_site(timeout=min(remaining(), 10))
    _refuse_gov_site(site or "")

    # Retry transient lookup failures (for example 429 or 503) within the same
    # deadline as polling, backing off quickly so a brief blip costs little.
    lookup_delays = iter(LOOKUP_RETRY_DELAYS_SECONDS)
    while True:
        try:
            accounts = _account_summary(
                _pup_request("GET", ACCOUNTS_PATH, timeout=remaining())
            )
            break
        except _TransientError as error:
            wait = next(lookup_delays, interval)
            if remaining() <= wait:
                raise SetupError(
                    "account_lookup_failed",
                    "Could not list Datadog Anthropic accounts.",
                    details={"message": str(error)},
                ) from error
            sleep(wait)
    account = _existing_account(accounts, account_name=account_name.strip())
    if not account:
        raise SetupError(
            "account_not_found",
            f"No Datadog Anthropic account is named {account_name}.",
            recoverable=False,
        )
    other_accounts = len(accounts) - 1

    # Setup's baseline was taken just before the account existed; without it,
    # the first successful query here stands in, which is ambiguous if the
    # first data already landed.
    from_setup = baseline_since is not None and baseline_total is not None
    since = int(baseline_since) if from_setup else int(wall_clock()) - USAGE_LOOKBACK_SECONDS
    baseline: float | None = float(baseline_total) if from_setup else None
    attempts = 0
    current = None
    last_error: str | None = None
    while True:
        attempts += 1
        try:
            current = _total(org_id, since=since, now=wall_clock(), timeout=remaining())
        except _TransientError as error:
            last_error = str(error)
        else:
            if baseline is None:
                baseline = current
        arrived = current is not None and baseline is not None and current > baseline + 1e-9
        # Poll through the deadline so data landing in the last interval
        # is still caught.
        if arrived or remaining() <= 0:
            break
        sleep(min(interval, remaining()))

    already_reporting = bool(baseline)
    checks = {
        "metrics": {
            "seen": arrived,
            "metric": USAGE_METRIC,
            "query": usage_query(org_id),
            "total_at_install": baseline,
            "total_now": current,
        }
    }
    if arrived:
        status = "succeeded"
        message = (
            "Datadog received new Anthropic usage data for this organization "
            "after the integration was installed."
        )
    else:
        status = "failed"
        if plan == "enterprise":
            reason = (
                "Enterprise (Analytics) usage is emitted in 1-hour buckets once the "
                "hour has settled, typically 1-3 hours later"
            )
        else:
            reason = (
                "Anthropic reports usage with a delay (often ~30 minutes), and "
                "only API usage produces data"
            )
        message = (
            f"No new Anthropic usage data arrived within the wait. {reason}; "
            "check again later before treating this as broken."
        )
    result: dict[str, Any] = {
        "status": status,
        "account": account,
        "anthropic_org_id": org_id,
        "plan": plan,
        "checks": checks,
        "attempts": attempts,
        "elapsed_seconds": round(clock() - start),
        "max_wait_seconds": round(deadline - start),
        "message": message,
        # Cost data lands in Cloud Cost Management within about 24 hours, so
        # this wait does not check it.
        "ccm_enabled": account["ccm_enabled"],
    }
    # Usage data for this Anthropic org was already in Datadog at install: an
    # earlier install, or another account here collecting the same org. Its
    # new points would also count, since the metrics do not name the account.
    if already_reporting:
        result["already_reporting_at_install"] = True
        result["other_anthropic_accounts"] = other_accounts
    if not from_setup:
        result["baseline_taken_by_verify"] = True
    if site:
        links = _links(site, org_id, arrived, account["ccm_enabled"])
        if links:
            result["links"] = links
    if not arrived and last_error:
        result["last_error"] = last_error
    return result


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = _ArgumentParser(
        description="Poll Datadog until Anthropic usage metrics arrive."
    )
    parser.add_argument("--account-name", required=True)
    parser.add_argument("--anthropic-org-id", required=True)
    parser.add_argument("--plan", choices=["enterprise", "platform"], default="platform")
    parser.add_argument("--baseline-since", type=int, help="usage_baseline.since from setup")
    parser.add_argument("--baseline-total", type=float, help="usage_baseline.total from setup")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    try:
        args = parse_args(list(sys.argv[1:] if argv is None else argv))
        result = verify(
            account_name=args.account_name,
            anthropic_org_id=args.anthropic_org_id,
            plan=args.plan,
            baseline_since=args.baseline_since,
            baseline_total=args.baseline_total,
        )
    except SetupError as error:
        emit({"status": "error", "error": error.as_dict()})
        return 1
    emit(result)
    return 0 if result["status"] == "succeeded" else 1


if __name__ == "__main__":
    sys.exit(main())
