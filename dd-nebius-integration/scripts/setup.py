#!/usr/bin/env python3
"""Zero-argument Datadog Nebius integration setup."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import shutil
import sys
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from _common import ProtectedFiles, SetupError, sanitize_text  # noqa: E402
from _datadog import (  # noqa: E402
    _build_payload,
    _find_verified_account,
    _list_accounts,
    _register_account,
    _validate_pup,
)
from _nebius import (  # noqa: E402
    _create_service_account,
    _ensure_admin_membership,
    _generate_authorized_key,
    _issue_static_key,
    _list_service_accounts,
    _profile_identity,
)

STREAMS = {
    "default",
    "nebius-cloud-applications",
    "nebius-cloud-compute",
    "nebius-cloud-kubernetes",
    "nebius-cloud-mlflow",
    "nebius-cloud-postgresql",
}


def emit(value: dict[str, Any]) -> None:
    print(json.dumps(value, indent=2, sort_keys=True))


def _binary_available(binary: str) -> bool:
    if os.path.sep in binary:
        path = Path(binary).expanduser()
        return path.is_file() and os.access(path, os.X_OK)
    return shutil.which(binary) is not None


def _one_year_from(now: dt.datetime | None = None) -> str:
    current = (now or dt.datetime.now(dt.timezone.utc)).astimezone(dt.timezone.utc)
    try:
        expires = current.replace(year=current.year + 1)
    except ValueError:
        expires = current.replace(year=current.year + 1, day=28)
    return expires.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _parse_streams(value: str | None) -> list[str]:
    if value is None:
        return sorted(STREAMS)
    selected = sorted({item.strip() for item in value.split(",") if item.strip()})
    unknown = sorted(set(selected) - STREAMS)
    if not selected or unknown:
        raise SetupError(
            "invalid_streams",
            (
                "Select at least one supported stream."
                if not unknown
                else f"Unknown streams: {', '.join(unknown)}."
            ),
            recoverable=False,
            details={"supported_streams": sorted(STREAMS)},
        )
    return selected


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")


def _account_name(tenant_id: str, project_id: str) -> str:
    identity = f"{tenant_id}:{project_id}"
    readable = f"nebius-{_slug(tenant_id)}-{_slug(project_id)}"
    if len(readable) <= 80:
        return readable
    digest = hashlib.sha256(identity.encode()).hexdigest()[:16]
    return f"{readable[:63].rstrip('-')}-{digest}"


def _discover_config(
    binary: str,
    *,
    now: dt.datetime | None = None,
    streams: list[str] | None = None,
    service_account_name: str = "datadog-integration",
    account_name: str | None = None,
) -> dict[str, Any]:
    service_account_name = service_account_name.strip()
    if not service_account_name:
        raise SetupError(
            "invalid_service_account_name",
            "Service-account name cannot be empty.",
            recoverable=False,
        )
    if account_name is not None and not account_name.strip():
        raise SetupError(
            "invalid_account_name",
            "Datadog integration account name cannot be empty.",
            recoverable=False,
        )
    identity = _profile_identity()
    accounts = _list_service_accounts(binary, identity["project_id"])
    named_accounts = [
        account
        for account in accounts
        if account.get("name") == service_account_name
    ]
    if len(named_accounts) > 1:
        raise SetupError(
            "duplicate_service_accounts",
            f"Multiple visible service accounts are named {service_account_name}.",
            recoverable=False,
            details={"service_account_ids": [item.get("id") for item in named_accounts]},
        )
    service_account_id = None
    create_service_account = not named_accounts
    ensure_admin_membership = False
    warnings: list[dict[str, str]] = []
    if named_accounts:
        service_account_id = named_accounts[0]["id"]
        project_id = named_accounts[0]["project_id"]
        ensure_admin_membership = True
    else:
        project_id = identity["project_id"]

    selected_streams = list(streams or sorted(STREAMS))
    if "default" not in selected_streams:
        warnings.append(
            {
                "code": "default_stream_excluded",
                "message": (
                    "The default stream is excluded, so uncategorized logs "
                    "will not be collected."
                ),
            }
        )
    expiration = _one_year_from(now)
    return {
        "tenant_id": identity["tenant_id"],
        "project_id": project_id,
        "service_account_id": service_account_id,
        "service_account_name": service_account_name,
        "create_service_account": create_service_account,
        "ensure_admin_membership": ensure_admin_membership,
        "account_name": (
            account_name.strip()
            if account_name is not None
            else _account_name(identity["tenant_id"], project_id)
        ),
        "streams": selected_streams,
        "authorized_expires_at": expiration,
        "static_expires_at": expiration,
        "warnings": warnings,
    }


def _existing_account(
    accounts: list[dict[str, Any]], config: dict[str, Any]
) -> dict[str, Any] | None:
    matches = [
        account
        for account in accounts
        if account.get("tenant_id") == config["tenant_id"]
    ]
    if len(matches) > 1:
        raise SetupError(
            "duplicate_datadog_accounts",
            "Multiple Datadog accounts use this Nebius tenant.",
            recoverable=False,
            details={"account_ids": [item.get("id") for item in matches]},
        )
    if not matches:
        return None
    if (
        not config.get("service_account_id")
        or matches[0].get("service_account_id") != config["service_account_id"]
    ):
        raise SetupError(
            "datadog_tenant_already_registered",
            (
                "A Datadog account already uses this Nebius tenant with a "
                "different service account."
            ),
            recoverable=False,
            details={"existing_account": matches[0]},
        )
    if _find_verified_account(matches, config):
        return matches[0]
    raise SetupError(
        "datadog_account_mismatch",
        "A Datadog account exists for this identity with mismatched settings.",
        recoverable=False,
        details={"existing_account": matches[0]},
    )


def run_setup(
    *,
    now: dt.datetime | None = None,
    streams: list[str] | None = None,
    service_account_name: str = "datadog-integration",
    account_name: str | None = None,
    org: str | None = None,
) -> dict[str, Any]:
    binary = "nebius"
    if not _binary_available(binary):
        raise SetupError(
            "nebius_cli_missing",
            "Install the Nebius CLI from https://docs.nebius.com/cli/install.",
            recoverable=False,
        )
    pup_identity = _validate_pup(org=org)
    config = _discover_config(
        binary,
        now=now,
        streams=streams,
        service_account_name=service_account_name,
        account_name=account_name,
    )
    accounts = _list_accounts(org=org)
    existing = _existing_account(accounts, config)

    if not config["create_service_account"]:
        if config["ensure_admin_membership"]:
            _ensure_admin_membership(config, binary)

    files: ProtectedFiles | None = None
    try:
        if config["create_service_account"]:
            _create_service_account(config, binary)
            existing = _existing_account(_list_accounts(org=org), config)
        if existing:
            return {
                "status": "already_configured",
                "account": existing,
                "datadog_auth": {"provider": "pup", **pup_identity},
                "enabled_services": config["streams"],
                "local_secrets_deleted": True,
                "warnings": config.get("warnings", []),
            }
        files = ProtectedFiles()
        _generate_authorized_key(config, binary, files)
        _issue_static_key(config, binary, files)
        summary = _build_payload(config, files)
        account = _register_account(config, files, org=org)
        files.cleanup()
        return {
            "status": "complete",
            "account": account,
            "datadog_auth": {"provider": "pup", **pup_identity},
            "registration": summary,
            "credential_expirations": {
                "authorized_key": config.get("authorized_expires_at"),
                "static_key": config.get("static_expires_at"),
            },
            "local_secrets_deleted": True,
            "log_query": "source:nebius-cloud*",
            "warnings": config.get("warnings", []),
        }
    except Exception as error:
        if files is not None:
            files.cleanup()
        if isinstance(error, SetupError):
            raise
        details = {
            "error_type": type(error).__name__,
            "message": sanitize_text(str(error)) or "",
        }
        raise SetupError(
            "unexpected_setup_error",
            "Setup failed; temporary credential material was deleted.",
            details=details,
        ) from error


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Set up the Datadog Nebius Cloud integration."
    )
    parser.add_argument(
        "--streams",
        help=(
            "Comma-separated supported log streams. "
            "Defaults to all six streams."
        ),
    )
    parser.add_argument(
        "--service-account-name",
        default="datadog-integration",
        help="Service account to reuse or create.",
    )
    parser.add_argument(
        "--account-name",
        help="Datadog integration display name. Defaults to a derived stable name.",
    )
    parser.add_argument(
        "--org",
        help="Optional named Pup organization session.",
    )
    args, unknown = parser.parse_known_args(
        list(sys.argv[1:] if argv is None else argv)
    )
    if unknown:
        raise SetupError(
            "unexpected_arguments",
            f"Unsupported arguments: {' '.join(unknown)}.",
            recoverable=False,
        )
    return args


def main(argv: list[str] | None = None) -> int:
    try:
        args = parse_args(argv)
        emit(
            run_setup(
                streams=_parse_streams(args.streams),
                service_account_name=args.service_account_name,
                account_name=args.account_name,
                org=args.org,
            )
        )
        return 0
    except SetupError as error:
        emit({"status": "error", "error": error.as_dict()})
        return 1


if __name__ == "__main__":
    sys.exit(main())
