#!/usr/bin/env python3
"""verify.py — post-apply checks for the Datadog AWS integration.

Subcommands:
  account   Confirm an AWS account id is registered in Datadog; print the entry.
  metrics   Poll the metrics API for the first datapoints from the registered account. The
            default poll (7 x 15s) stays inside a 2-minute agent tool timeout.

Reads DD_SITE / DD_API_KEY / DD_APP_KEY from the environment (source dd-env.sh
first). Keys are passed to curl on stdin so they never appear in argv.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time


def _die(msg: str, code: int = 1) -> None:
    print(f"✗ {msg}", file=sys.stderr)
    sys.exit(code)


def _validate_account(account: str) -> None:
    if not (account.isdigit() and len(account) == 12):
        _die(f"--account must be a 12-digit id, got {account!r}")


def _env() -> tuple[str, str, str]:
    site = os.environ.get("DD_SITE", "")
    api_key = os.environ.get("DD_API_KEY", "")
    app_key = os.environ.get("DD_APP_KEY", "")
    missing = [n for n, v in [("DD_SITE", site), ("DD_API_KEY", api_key), ("DD_APP_KEY", app_key)] if not v]
    if missing:
        _die(f"missing required environment variable(s): {', '.join(missing)} — source scripts/dd-env.sh first")
    return site, api_key, app_key


def _curl(url: str, api_key: str, app_key: str, *, extra_args: list | None = None) -> tuple[str, str]:
    """Run curl with the auth headers on stdin. Returns (body, http_status)."""
    cmd = ["curl", "-sS", "--max-time", "30", "-w", "\n%{http_code}", "-H", "@-", url]
    if extra_args:
        cmd[1:1] = extra_args  # insert after 'curl'
    result = subprocess.run(
        cmd,
        input=f"DD-API-KEY: {api_key}\nDD-APPLICATION-KEY: {app_key}\n",
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        _die(f"curl failed: {result.stderr.strip()}")
    body, _, status = result.stdout.rpartition("\n")
    return body, status.strip()


def cmd_account(args: argparse.Namespace) -> None:
    _validate_account(args.account)
    site, api_key, app_key = _env()

    url = f"https://api.{site}/api/v2/integration/aws/accounts"
    body, status = _curl(url, api_key, app_key)

    if status != "200":
        print(f"✗ Datadog API returned HTTP {status} — do NOT assume 'not connected':", file=sys.stderr)
        if body:
            print(body, file=sys.stderr)
        sys.exit(1)

    try:
        data = json.loads(body)
    except json.JSONDecodeError as exc:
        _die(f"Datadog API returned invalid JSON: {exc}")

    match = None
    for entry in data.get("data", []):
        if entry.get("attributes", {}).get("aws_account_id") == args.account:
            match = entry
            break

    if not match:
        print(f"✗ account {args.account} is NOT registered on {site}", file=sys.stderr)
        print("  the apply may have silently failed, or the registration was removed since", file=sys.stderr)
        sys.exit(1)

    print(json.dumps(match, indent=2))
    print(f"✓ account {args.account} registered on {site} (config_id={match.get('id')})", file=sys.stderr)


def cmd_metrics(args: argparse.Namespace) -> None:
    _validate_account(args.account)
    site, api_key, app_key = _env()

    url = f"https://api.{site}/api/v1/query"
    query = f"avg:{args.metric}{{aws_account:{args.account}}} by {{host}}"

    print(f"Polling {args.metric} for account {args.account} on {site} "
          f"(every {args.interval}s, up to {args.attempts} attempts)...", file=sys.stderr)

    for attempt in range(1, args.attempts + 1):
        now = int(time.time())
        extra = [
            "-G",
            "--data-urlencode", f"from={now - 900}",
            "--data-urlencode", f"to={now}",
            "--data-urlencode", f"query={query}",
        ]
        body, status = _curl(url, api_key, app_key, extra_args=extra)

        if status != "200":
            # Hard failure — do NOT keep retrying as if it were "still propagating".
            print(f"✗ metric query failed with HTTP {status} — that is NOT 'metrics still propagating':", file=sys.stderr)
            if body:
                print(body, file=sys.stderr)
            sys.exit(1)

        try:
            parsed = json.loads(body)
        except json.JSONDecodeError as exc:
            _die(f"metric API returned invalid JSON: {exc}")

        series = parsed.get("series") or []
        if series:
            elapsed = (attempt - 1) * args.interval
            print(f"✓ first datapoints arrived after {elapsed}s", file=sys.stderr)
            print(json.dumps(parsed, indent=2))
            return

        if attempt == args.attempts:
            app_base = os.environ.get("APP_BASE", f"https://{site}")
            print(f"✗ no datapoints after ~{(args.attempts - 1) * args.interval}s — nothing to chart yet.", file=sys.stderr)
            print("  This is normal right after a new integration: first AWS metrics usually take", file=sys.stderr)
            print("  10-15 minutes to arrive. Check back then (re-run this command).", file=sys.stderr)
            print(f"  Integration status: {app_base}/integrations/amazon-web-services", file=sys.stderr)
            print(f"  Metrics Explorer:   {app_base}/metric/explorer?exp_metric={args.metric}", file=sys.stderr)
            print("  Still empty after ~15 minutes: the account may have no EC2 instances, or the id is wrong.", file=sys.stderr)
            sys.exit(2)

        time.sleep(args.interval)


def main() -> None:
    parser = argparse.ArgumentParser(prog="verify.py", description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    acc = sub.add_parser("account", help="Confirm the AWS account is registered in Datadog")
    acc.add_argument("--account", required=True, help="12-digit AWS account id")

    met = sub.add_parser("metrics", help="Poll the metrics API for first datapoints")
    met.add_argument("--account", required=True, help="12-digit AWS account id")
    met.add_argument("--metric", default="aws.ec2.cpuutilization",
                     help="metric to probe (default: aws.ec2.cpuutilization)")
    met.add_argument("--attempts", type=int, default=7, help="max poll attempts (default 7)")
    met.add_argument("--interval", type=int, default=15, help="seconds between attempts (default 15)")

    args = parser.parse_args()
    if args.cmd == "account":
        cmd_account(args)
    elif args.cmd == "metrics":
        cmd_metrics(args)


if __name__ == "__main__":
    main()
