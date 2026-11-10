#!/usr/bin/env python3
"""Set up the Datadog Nebius Cloud integration.

Usage: setup.py [--streams IDS] [--service-account-name NAME] [--account-name NAME]
Prints one JSON object.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import shutil
import signal
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
    SUSPENDED_TENANT_STATES,
    _create_service_account,
    _ensure_admin_membership,
    _generate_authorized_key,
    _issue_static_key,
    _list_service_accounts,
    _profile_identity,
    _tenant_suspension_state,
)

STREAMS = {
    "default",
    "nebius-cloud-applications",
    "nebius-cloud-compute",
    "nebius-cloud-kubernetes",
    "nebius-cloud-mlflow",
    "nebius-cloud-postgresql",
}

DEFAULT_SERVICE_ACCOUNT_NAME = "datadog-integration"


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


def _normalize_tags(values: list[str]) -> list[str]:
    tags = sorted({value.strip() for value in values})
    if any(not tag or any(character.isspace() for character in tag) for tag in tags):
        raise SetupError(
            "invalid_account_tags",
            "Account tags must be non-empty and contain no whitespace.",
            recoverable=False,
        )
    return tags


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")


def _account_name(tenant_id: str, project_id: str) -> str:
    identity = f"{tenant_id}:{project_id}"
    readable = f"nebius-{_slug(tenant_id)}-{_slug(project_id)}"
    # Keep derived display names at most 80 characters. A longer name keeps a
    # 63-character readable prefix plus "-" and a 16-hex-digit hash of the
    # identity (63 + 1 + 16 = 80), so truncated names stay unique and stable.
    if len(readable) <= 80:
        return readable
    digest = hashlib.sha256(identity.encode()).hexdigest()[:16]
    return f"{readable[:63].rstrip('-')}-{digest}"


def _discover_config(
    binary: str,
    *,
    now: dt.datetime | None = None,
    streams: list[str] | None = None,
    service_account_name: str = DEFAULT_SERVICE_ACCOUNT_NAME,
    account_tags: list[str] | None = None,
) -> dict[str, Any]:
    service_account_name = service_account_name.strip()
    if not service_account_name:
        raise SetupError(
            "invalid_service_account_name",
            "Service-account name cannot be empty.",
            recoverable=False,
        )
    identity = _profile_identity()
    suspension_state = _tenant_suspension_state(binary, identity["tenant_id"])
    if suspension_state in SUSPENDED_TENANT_STATES:
        raise SetupError(
            "tenant_suspended",
            (
                f"This Nebius tenancy is not active (suspension state: "
                f"{suspension_state}). Onboarding a suspended tenancy is not "
                "supported; resume it in Nebius and rerun setup."
            ),
            recoverable=False,
            details={
                "tenant_id": identity["tenant_id"],
                "suspension_state": suspension_state,
            },
        )
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
    if named_accounts:
        service_account_id = named_accounts[0]["id"]
        project_id = named_accounts[0]["project_id"]
        ensure_admin_membership = True
    else:
        project_id = identity["project_id"]

    selected_streams = list(streams or sorted(STREAMS))
    warnings: list[dict[str, str]] = []
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
        "account_name": _account_name(identity["tenant_id"], project_id),
        "streams": selected_streams,
        "account_tags": list(account_tags or []),
        "resources_enabled": True,
        "service_quota_enabled": True,
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
            "Multiple Datadog Nebius accounts use this Nebius tenant.",
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
                "A Datadog Nebius account already uses this Nebius tenant "
                "with a different service account."
            ),
            recoverable=False,
            details={"existing_account": matches[0]},
        )
    if _find_verified_account(matches, config):
        return matches[0]
    raise SetupError(
        "datadog_account_mismatch",
        "A Datadog Nebius account exists for this tenant with mismatched settings.",
        recoverable=False,
        details={"existing_account": matches[0]},
    )


def run_setup(
    *,
    now: dt.datetime | None = None,
    streams: list[str] | None = None,
    service_account_name: str = DEFAULT_SERVICE_ACCOUNT_NAME,
    account_tags: list[str] | None = None,
) -> dict[str, Any]:
    binary = "nebius"
    if not _binary_available(binary):
        raise SetupError(
            "nebius_cli_missing",
            "Install the Nebius CLI from https://docs.nebius.com/cli/install.",
            recoverable=False,
            details={
                "supported_platforms": ["macOS", "Ubuntu"],
                "install_command": (
                    "curl -sSL https://artifacts.nebius.cloud/cli/install.sh | bash"
                ),
                "after_install": (
                    "Restart the terminal or run `exec -l $SHELL`, then verify "
                    "with `nebius version`."
                ),
                "authenticate_command": "nebius profile create",
            },
        )
    pup_identity = _validate_pup()
    config = _discover_config(
        binary,
        now=now,
        streams=streams,
        service_account_name=service_account_name,
        account_tags=account_tags,
    )
    accounts = _list_accounts()
    existing = _existing_account(accounts, config)

    if not config["create_service_account"]:
        if config["ensure_admin_membership"]:
            _ensure_admin_membership(config, binary)

    files: ProtectedFiles | None = None
    try:
        if config["create_service_account"]:
            _create_service_account(config, binary)
            existing = _existing_account(_list_accounts(), config)
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
        account = _register_account(config, files)
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
    finally:
        # Runs on success, failure, Ctrl-C (KeyboardInterrupt) and SIGTERM
        # (see _exit_on_sigterm), so no credential file outlives the run.
        if files is not None:
            files.cleanup()


def _accounts_status() -> dict[str, Any]:
    """Read-only report behind --list-accounts: no Nebius access, nothing is written."""
    identity = _validate_pup()
    return {
        "status": "accounts",
        "site": identity.get("site"),
        "org": identity.get("org"),
        "accounts": _list_accounts(),
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Set up the Datadog Nebius Cloud integration."
    )
    parser.add_argument(
        "--list-accounts",
        action="store_true",
        help="Read-only: report the existing Datadog Nebius Cloud accounts and "
             "exit, without touching the Nebius side or writing anything.",
    )
    parser.add_argument(
        "--streams",
        help=(
            "Comma-separated supported log streams. "
            "Defaults to all six streams."
        ),
    )
    parser.add_argument(
        "--account-tags",
        action="append",
        default=[],
        metavar="TAG",
        help="Datadog account tag, such as team:infra; repeat for more.",
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


def _exit_on_sigterm(_signum: int, _frame: Any) -> None:
    # Raising unwinds through run_setup's finally, which deletes local secrets.
    raise SystemExit(128 + signal.SIGTERM)


def main(argv: list[str] | None = None) -> int:
    previous_sigterm = signal.signal(signal.SIGTERM, _exit_on_sigterm)
    try:
        args = parse_args(argv)
        if args.list_accounts:
            emit(_accounts_status())
            return 0
        emit(
            run_setup(
                streams=_parse_streams(args.streams),
                account_tags=_normalize_tags(args.account_tags),
            )
        )
        return 0
    except SetupError as error:
        emit({"status": "error", "error": error.as_dict()})
        return 1
    finally:
        signal.signal(signal.SIGTERM, previous_sigterm)


if __name__ == "__main__":
    sys.exit(main())
