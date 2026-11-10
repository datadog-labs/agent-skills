#!/usr/bin/env python3
"""Poll Datadog until usage metrics from a newly configured OpenAI account arrive.

Datadog tags every OpenAI metric with the Datadog account it came from
(`account_id:<id>`), and a new account has no history, so any `openai.*` metric
carrying this account's id proves this connection is collecting."""

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
    SetupError,
    _ArgumentParser,
    _account_summary,
    _existing_account,
    _pup_base,
    _sanitize_text,
    emit,
)

# Leave room for a new account's first data to be collected and ingested.
MAX_WAIT_SECONDS = 900
POLL_INTERVAL_SECONDS = 30
LOOKUP_RETRY_DELAYS_SECONDS = (5, 10)
# Usage points are stamped with when the usage happened, and the first data
# collected can include earlier usage, so look back a day; the account tag keeps
# other accounts' data out.
LOOKBACK_SECONDS = 24 * 3600
METRIC_PREFIX = "openai."
# The metric the Metrics Explorer link opens when it reported, as the one most
# users recognize; otherwise the first metric found.
LINK_METRIC = "openai.completions.input_tokens"
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
                    "remediation": "Run `pup auth login --extra-scopes metrics_read`.",
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


def _account_scope(account_id: str) -> str:
    return f"account_id:{account_id}"


def _links(site: str, account_id: str, sample: list[str], ccm_enabled: bool) -> dict[str, str]:
    host = _app_host(site)
    links = {}
    if sample:
        metric = LINK_METRIC if LINK_METRIC in sample else sample[0]
        links["metrics"] = f"https://{host}/metric/explorer?" + urllib.parse.urlencode(
            {"exp_metric": metric, "exp_scope": _account_scope(account_id)}
        )
    if ccm_enabled:
        links["costs"] = f"https://{host}/cost"
    return links


def _metrics_seen(account_id: str, *, now: float, timeout: float) -> list[str]:
    query = urllib.parse.urlencode(
        {"from": int(now - LOOKBACK_SECONDS), "tag_filter": _account_scope(account_id)}
    )
    response = _pup_request("GET", f"/api/v1/metrics?{query}", timeout=timeout)
    metrics = response.get("metrics", []) if isinstance(response, dict) else []
    return sorted(
        name for name in metrics if isinstance(name, str) and name.startswith(METRIC_PREFIX)
    )


def verify(
    *,
    account_name: str,
    max_wait: float = MAX_WAIT_SECONDS,
    interval: float = POLL_INTERVAL_SECONDS,
    clock: Callable[[], float] = time.monotonic,
    wall_clock: Callable[[], float] = time.time,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    start = clock()
    deadline = start + min(max_wait, MAX_WAIT_SECONDS)

    def remaining() -> float:
        return max(deadline - clock(), 0)

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
                    "Could not list Datadog OpenAI accounts.",
                    details={"message": str(error)},
                ) from error
            sleep(wait)
    account = _existing_account(accounts, account_name=account_name.strip())
    if not account:
        raise SetupError(
            "account_not_found",
            f"No Datadog OpenAI account is named {account_name}.",
            recoverable=False,
        )
    account_id = str(account["id"])
    site = _datadog_site(timeout=min(remaining(), 10))

    attempts = 0
    sample: list[str] = []
    last_error: str | None = None
    while True:
        attempts += 1
        try:
            sample = _metrics_seen(account_id, now=wall_clock(), timeout=remaining())
        except _TransientError as error:
            last_error = str(error)
        # Poll through the deadline so data landing in the last interval
        # is still caught.
        if sample or remaining() <= 0:
            break
        sleep(min(interval, remaining()))

    checks = {
        "metrics": {
            "seen": bool(sample),
            "sample": sample[:5],
            "query": f"{METRIC_PREFIX}* tagged {_account_scope(account_id)}",
        }
    }
    if sample:
        status = "succeeded"
        message = "Datadog is receiving OpenAI usage metrics from this account."
    else:
        status = "failed"
        message = (
            "No OpenAI usage metrics from this account arrived within the wait. "
            "Metrics only exist for API usage, OpenAI reports usage with a delay, "
            "and the first data can take longer to appear; check again later before "
            "treating this as broken."
        )
    result: dict[str, Any] = {
        "status": status,
        "account": account,
        "checks": checks,
        "attempts": attempts,
        "elapsed_seconds": round(clock() - start),
        "max_wait_seconds": round(deadline - start),
        "message": message,
        # Cost data lands in Cloud Cost Management within about 24 hours, so
        # this wait does not check it.
        "ccm_enabled": account["ccm_enabled"],
    }
    if site:
        links = _links(site, account_id, sample, account["ccm_enabled"])
        if links:
            result["links"] = links
    if not sample and last_error:
        result["last_error"] = last_error
    return result


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = _ArgumentParser(
        description="Poll Datadog until OpenAI usage metrics arrive."
    )
    parser.add_argument("--account-name", required=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    try:
        args = parse_args(list(sys.argv[1:] if argv is None else argv))
        result = verify(account_name=args.account_name)
    except SetupError as error:
        emit({"status": "error", "error": error.as_dict()})
        return 1
    emit(result)
    return 0 if result["status"] == "succeeded" else 1


if __name__ == "__main__":
    sys.exit(main())
