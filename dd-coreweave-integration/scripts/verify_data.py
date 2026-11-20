#!/usr/bin/env python3
"""Poll Datadog until data from a configured CoreWeave account arrives."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
import urllib.parse
from pathlib import Path
from typing import Any, Callable

# `python -I` drops the script's folder from sys.path; add it back for setup.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from setup import (  # noqa: E402
    ACCOUNTS_PATH,
    SetupError,
    _ArgumentParser,
    _account_summary,
    _existing_account,
    _pup_base,
    _refuse_gov_site,
    _sanitize_text,
    emit,
)

MAX_WAIT_SECONDS = 420
POLL_INTERVAL_SECONDS = 30
LOOKUP_RETRY_DELAYS_SECONDS = (5, 10)
LOOKBACK_SECONDS = 3600
LOGS_QUERY = "source:coreweave"
METRIC_PREFIX = "coreweave."
PERMISSION_STATUSES = {401, 403}


class _TransientError(Exception):
    """A poll attempt failed in a way that may succeed on the next attempt."""


def _pup_request(
    method: str,
    path: str,
    *,
    payload: dict[str, Any] | None = None,
    timeout: float,
) -> Any:
    # Unlike registration, polling is read-only and must fit the overall wait,
    # so each request is bounded by the time remaining.
    command = _pup_base("api")
    command.extend(["--method", method])
    data = None
    if payload is not None:
        command.extend(["--input", "-"])
        data = json.dumps(payload).encode()
    command.append(path)
    try:
        result = subprocess.run(
            command,
            input=data,
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
                        "Run `pup auth login --extra-scopes "
                        "metrics_read,logs_read_data`."
                    ),
                },
            )
        raise _TransientError(_sanitize_text(stderr) or "Pup request failed.")
    stdout = result.stdout.decode(errors="replace")
    try:
        return json.loads(stdout) if stdout.strip() else {}
    except json.JSONDecodeError as error:
        raise _TransientError("Pup returned a non-JSON response.") from error


def _tag_value(value: str) -> str:
    # Approximates Datadog tag normalization for the account_display_name tag,
    # which ECI adds to every CoreWeave metric and log for the account.
    normalized = re.sub(r"[^a-z0-9_\-./:]", "_", value.lower())
    return re.sub(r"_+", "_", normalized).strip("_")


def _logs_query(account_name: str) -> str:
    return f'{LOGS_QUERY} account_display_name:"{_tag_value(account_name)}"'


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


def _links(site: str, account_name: str, checks: dict[str, dict[str, Any]]) -> dict[str, str]:
    host = _app_host(site)
    links = {}
    metrics = checks.get("metrics", {}).get("sample")
    if metrics:
        links["metrics"] = f"https://{host}/metric/explorer?" + urllib.parse.urlencode(
            {
                "exp_metric": metrics[0],
                "exp_scope": f"account_display_name:{_tag_value(account_name)}",
            }
        )
    if checks.get("logs", {}).get("seen"):
        links["logs"] = f"https://{host}/logs?" + urllib.parse.urlencode(
            {"query": _logs_query(account_name)}
        )
    return links


def _metrics_seen(
    account_name: str,
    *,
    now: float,
    timeout: float,
) -> list[str]:
    query = urllib.parse.urlencode(
        {
            "from": int(now - LOOKBACK_SECONDS),
            "tag_filter": f"account_display_name:{_tag_value(account_name)}",
        }
    )
    response = _pup_request("GET", f"/api/v1/metrics?{query}", timeout=timeout)
    metrics = response.get("metrics", []) if isinstance(response, dict) else []
    return sorted(
        name for name in metrics if isinstance(name, str) and name.startswith(METRIC_PREFIX)
    )


def _logs_seen(
    account_name: str,
    *,
    timeout: float,
) -> list[str]:
    response = _pup_request(
        "POST",
        "/api/v2/logs/events/search",
        payload={
            "filter": {
                "query": _logs_query(account_name),
                "from": "now-1h",
                "to": "now",
            },
            "page": {"limit": 1},
            "sort": "-timestamp",
        },
        timeout=timeout,
    )
    events = response.get("data", []) if isinstance(response, dict) else []
    return [str(event.get("id")) for event in events if isinstance(event, dict)]


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
                    "Could not list Datadog CoreWeave accounts.",
                    details={"message": str(error)},
                ) from error
            sleep(wait)
    account = _existing_account(accounts, account_name=account_name.strip())
    if not account:
        raise SetupError(
            "account_not_found",
            f"No Datadog CoreWeave account is named {account_name}.",
            recoverable=False,
        )

    checks: dict[str, dict[str, Any]] = {}
    if account.get("metrics_config_enabled"):
        checks["metrics"] = {
            "seen": False,
            "query": f"{METRIC_PREFIX}* tagged account_display_name:{_tag_value(account_name)}",
        }
    if account.get("logs_config_enabled"):
        checks["logs"] = {"seen": False, "query": _logs_query(account_name)}
    if not checks:
        raise SetupError(
            "no_data_enabled",
            "The account has neither metrics nor logs collection enabled.",
            recoverable=False,
            details={"account": account},
        )

    attempts = 0
    last_error: str | None = None
    while True:
        attempts += 1
        for name, check in checks.items():
            if check["seen"]:
                continue
            try:
                if name == "metrics":
                    sample = _metrics_seen(
                        account_name, now=wall_clock(), timeout=remaining()
                    )
                else:
                    sample = _logs_seen(account_name, timeout=remaining())
            except _TransientError as error:
                last_error = str(error)
                continue
            if sample:
                check["seen"] = True
                check["sample"] = sample[:5]
        if all(check["seen"] for check in checks.values()):
            break
        if remaining() <= interval:
            break
        sleep(interval)

    arrived = [name for name, check in checks.items() if check["seen"]]
    pending = [name for name, check in checks.items() if not check["seen"]]
    if not pending:
        status = "succeeded"
        message = "Datadog is receiving CoreWeave data for this account."
    elif arrived:
        status = "partial"
        message = (
            f"Datadog is receiving CoreWeave {' and '.join(arrived)} for this "
            f"account, but {' and '.join(pending)} did not arrive within the wait. "
            "Collection can take longer to start; check again in ~1 hour."
        )
    else:
        status = "failed"
        message = (
            "No CoreWeave data arrived within the wait. Collection can take "
            "longer to start; check again in ~1 hour before treating this as broken. If this persists, contact support."
        )
    result: dict[str, Any] = {
        "status": status,
        "account": account,
        "checks": checks,
        "arrived": arrived,
        "pending": pending,
        "attempts": attempts,
        "elapsed_seconds": round(clock() - start),
        "max_wait_seconds": round(deadline - start),
        "message": message,
    }
    # Link whatever arrived, even when another enabled data type is still pending.
    if arrived and site:
        result["links"] = _links(site, account_name, checks)
    if pending and last_error:
        result["last_error"] = last_error
    return result


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = _ArgumentParser(
        description="Poll Datadog until CoreWeave metrics or logs arrive."
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
