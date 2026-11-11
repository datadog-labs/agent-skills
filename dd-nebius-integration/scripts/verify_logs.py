#!/usr/bin/env python3
"""Poll Datadog until logs from a configured Nebius account arrive."""

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

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from _common import SetupError, sanitize_text  # noqa: E402
from _datadog import ACCOUNTS_PATH, _account_summary, _pup_base, _refuse_gov_site  # noqa: E402

MAX_WAIT_SECONDS = 420
POLL_INTERVAL_SECONDS = 30
LOOKUP_RETRY_DELAYS_SECONDS = (5, 10)
LOGS_SOURCE_QUERY = "source:nebius-cloud*"
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
                        "integrations_read,logs_read_data`."
                    ),
                },
            )
        raise _TransientError(sanitize_text(stderr) or "Pup request failed.")
    stdout = result.stdout.decode(errors="replace")
    try:
        return json.loads(stdout) if stdout.strip() else {}
    except json.JSONDecodeError as error:
        raise _TransientError("Pup returned a non-JSON response.") from error


def _tag_value(value: str) -> str:
    # Approximates Datadog tag normalization for the account_display_name tag,
    # which ECI adds to every Nebius log for the account.
    normalized = re.sub(r"[^a-z0-9_\-./:]", "_", value.lower())
    return re.sub(r"_+", "_", normalized).strip("_")


def _logs_query(account_name: str) -> str:
    return f'{LOGS_SOURCE_QUERY} account_display_name:"{_tag_value(account_name)}"'


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


def _logs_link(site: str, account_name: str) -> str:
    host = site if site.count(".") >= 2 else f"app.{site}"
    return f"https://{host}/logs?" + urllib.parse.urlencode(
        {"query": _logs_query(account_name)}
    )


def _search_logs(
    account_name: str,
    *,
    timeout: float,
) -> list[dict[str, Any]]:
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
    return [event for event in events if isinstance(event, dict)]


def _find_account(
    account_name: str,
    *,
    timeout: float,
) -> dict[str, Any]:
    # A transient failure propagates as _TransientError so verify() can retry it.
    accounts = _account_summary(_pup_request("GET", ACCOUNTS_PATH, timeout=timeout))
    matches =[account for account in accounts if account.get("name") == account_name]
    if not matches:
        raise SetupError(
            "account_not_found",
            f"No Datadog Nebius account is named {account_name}.",
            recoverable=False,
        )
    if len(matches) > 1:
        raise SetupError(
            "duplicate_datadog_accounts",
            f"Multiple Datadog Nebius accounts are named {account_name}.",
            recoverable=False,
            details={"account_ids": [item.get("id") for item in matches]},
        )
    return matches[0]


def verify(
    *,
    account_name: str,
    max_wait: float = MAX_WAIT_SECONDS,
    interval: float = POLL_INTERVAL_SECONDS,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    start = clock()
    deadline = start + min(max_wait, MAX_WAIT_SECONDS)

    def remaining() -> float:
        return max(deadline - clock(), 0)

    # Refuse Government sites before any Datadog API call.
    site = _datadog_site(timeout=min(remaining(), 10))
    _refuse_gov_site(site or "")

    name = account_name.strip()
    # Retry transient lookup failures (for example 429 or 503) within the same
    # deadline as polling, backing off quickly so a brief blip costs little.
    lookup_delays = iter(LOOKUP_RETRY_DELAYS_SECONDS)
    while True:
        try:
            account = _find_account(name, timeout=remaining())
            break
        except _TransientError as error:
            wait = next(lookup_delays, interval)
            if remaining() <= wait:
                raise SetupError(
                    "account_lookup_failed",
                    "Could not list Datadog Nebius accounts.",
                    details={"message": str(error)},
                ) from error
            sleep(wait)
    attempts = 0
    last_error: str | None = None
    events: list[dict[str, Any]] = []
    while True:
        attempts += 1
        try:
            events = _search_logs(name, timeout=remaining())
        except _TransientError as error:
            last_error = str(error)
        if events or remaining() <= interval:
            break
        sleep(interval)

    succeeded = bool(events)
    result: dict[str, Any] = {
        "status": "succeeded" if succeeded else "failed",
        "account": account,
        "query": _logs_query(name),
        "attempts": attempts,
        "elapsed_seconds": round(clock() - start),
        "max_wait_seconds": round(deadline - start),
        "message": (
            "Datadog is receiving Nebius logs for this account."
            if succeeded
            else (
                "No Nebius logs arrived within the wait. Collection can take "
                "longer to start; check again in ~1 hour before treating this as broken."
            )
        ),
    }
    if succeeded:
        attributes = events[0].get("attributes", {})
        if isinstance(attributes, dict):
            tags = attributes.get("tags", [])
            result["sample"] = {
                "timestamp": attributes.get("timestamp"),
                "source": next(
                    (
                        tag.split(":", 1)[1]
                        for tag in tags if isinstance(tags, list)
                        if isinstance(tag, str) and tag.startswith("source:")
                    ),
                    None,
                ),
            }
        if site:
            result["links"] = {"logs": _logs_link(site, name)}
    elif last_error:
        result["last_error"] = last_error
    return result


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Poll Datadog until logs from a Nebius account arrive."
    )
    parser.add_argument("--account-name", required=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(list(sys.argv[1:] if argv is None else argv))
    try:
        result = verify(account_name=args.account_name)
    except SetupError as error:
        print(json.dumps({"status": "error", "error": error.as_dict()}, indent=2, sort_keys=True))
        return 1
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["status"] == "succeeded" else 1


if __name__ == "__main__":
    sys.exit(main())
