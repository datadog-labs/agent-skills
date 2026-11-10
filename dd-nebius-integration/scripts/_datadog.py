"""Datadog requests through Pup, duplicate checking, registration, and verification."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from typing import Any

from _common import ProtectedFiles, SetupError, sanitize_text, write_json

ACCOUNTS_PATH = "/api/v2/integration-interfaces/nebius-cloud/accounts"


def _pup_base(command: str) -> list[str]:
    return ["pup", command, "--no-envelope", "--output", "json"]


def _run_pup(
    command: list[str],
    *,
    payload: bytes | None = None,
) -> Any:
    # No client-side timeout: wait for Datadog to answer (or time out on the
    # server) or for the caller to kill the command. Abandoning a POST locally
    # would only make its outcome ambiguous.
    try:
        result = subprocess.run(
            command,
            input=payload,
            capture_output=True,
            check=False,
        )
    except OSError as error:
        raise SetupError(
            "pup_transport_error",
            "Pup could not complete the authenticated Datadog request.",
            details={
                "command": command[:2],
                "error_type": type(error).__name__,
                "message": sanitize_text(str(error)),
            },
        ) from error
    stdout = result.stdout.decode(errors="replace")
    stderr = result.stderr.decode(errors="replace")
    if result.returncode != 0:
        raise SetupError(
            "pup_request_failed",
            "Pup rejected or could not complete the Datadog request.",
            details={
                "command": command[:2],
                "exit_code": result.returncode,
                "stdout": sanitize_text(stdout),
                "stderr": sanitize_text(stderr),
            },
        )
    if not stdout.strip():
        return {}
    try:
        return json.loads(stdout)
    except json.JSONDecodeError as error:
        raise SetupError(
            "pup_response_invalid",
            "Pup returned a non-JSON Datadog response.",
            details={"stdout": sanitize_text(stdout)},
        ) from error


def _pup_api(
    method: str,
    path: str,
    *,
    payload: bytes | None = None,
) -> Any:
    command = _pup_base("api")
    command.extend(["--method", method])
    if payload is not None:
        command.extend(["--input", "-"])
    command.append(path)
    return _run_pup(command, payload=payload)


def _account_summary(value: Any) -> list[dict[str, Any]]:
    items = value.get("data", []) if isinstance(value, dict) else []
    result = []
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        attributes = item.get("attributes", {})
        settings = attributes.get("settings", {}) if isinstance(attributes, dict) else {}
        authentication = (
            attributes.get("authentication", {})
            if isinstance(attributes, dict)
            else {}
        )
        dataflows = attributes.get("dataflows", {}) if isinstance(attributes, dict) else {}
        logs = dataflows.get("nebius-cloud-logs", {})
        logs_settings = logs.get("settings", {}) if isinstance(logs, dict) else {}
        resources = dataflows.get("nebius-cloud-resources", {})
        resources = resources if isinstance(resources, dict) else {}
        service_quota = dataflows.get("nebius-cloud-service-quota-collection", {})
        service_quota = service_quota if isinstance(service_quota, dict) else {}
        result.append(
            {
                "id": item.get("id"),
                "name": attributes.get("name"),
                "tenant_id": settings.get("tenant_id"),
                "service_account_id": authentication.get("service_account_id"),
                "public_key_id": authentication.get("public_key_id"),
                "enabled_services": logs_settings.get("enabled_services"),
                "account_tags": sorted(settings.get("account_tags") or []),
                # Unset follows the integration defaults: both off.
                "resources_enabled": resources.get("enabled") is True,
                "service_quota_enabled": service_quota.get("enabled") is True,
            }
        )
    return result


def _list_accounts() -> list[dict[str, Any]]:
    return _account_summary(_pup_api("GET", ACCOUNTS_PATH))


def _find_account(
    accounts: list[dict[str, Any]],
    config: dict[str, Any],
    *,
    match_name: bool,
) -> dict[str, Any] | None:
    return next(
        (
            account
            for account in accounts
            if account["tenant_id"] == config["tenant_id"]
            and account["service_account_id"] == config["service_account_id"]
            and (not match_name or account["name"] == config["account_name"])
        ),
        None,
    )


def _find_verified_account(
    accounts: list[dict[str, Any]], config: dict[str, Any]
) -> dict[str, Any] | None:
    account = _find_account(accounts, config, match_name=True)
    if (
        not account
        or not account.get("id")
        or sorted(account.get("enabled_services") or [])
        != sorted(config["streams"])
        or (
            config.get("public_key_id")
            and account.get("public_key_id") != config["public_key_id"]
        )
        or account.get("account_tags", []) != sorted(config.get("account_tags") or [])
        or account.get("resources_enabled", False) != config.get("resources_enabled", False)
        or account.get("service_quota_enabled", False)
        != config.get("service_quota_enabled", False)
    ):
        return None
    return account


def _pup_version() -> tuple[int, int, int] | None:
    """Parse `pup --version` output such as "pup 2.4.3"; None when unreadable."""
    try:
        result = subprocess.run(
            ["pup", "--version"],
            capture_output=True,
            check=False,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    stdout = result.stdout.decode(errors="replace") if result.stdout else ""
    if result.returncode:
        return None
    match = re.search(r"(\d+)\.(\d+)(?:\.(\d+))?", stdout)
    if not match:
        return None
    return (
        int(match.group(1)),
        int(match.group(2)),
        int(match.group(3)) if match.group(3) else 0,
    )


def _validate_pup() -> dict[str, Any]:
    if shutil.which("pup") is None:
        raise SetupError(
            "pup_cli_missing",
            "Install Pup and authenticate with `pup auth login`.",
            recoverable=False,
        )
    version = _pup_version()
    if version is None or version[0] < 2:
        # The scripts rely on v2-only CLI flags such as --no-envelope; older
        # Pup versions fail on the first request with an opaque error.
        raise SetupError(
            "pup_cli_outdated",
            "Pup 2 or later is required; the installed Pup is too old.",
            recoverable=True,
            details={
                "installed_version": ".".join(str(part) for part in version)
                if version
                else None,
                "remediation": (
                    "Upgrade Pup from https://github.com/DataDog/pup/releases "
                    "(download the release archive, verify its checksum, and "
                    "install it, for example "
                    "`sudo install -m 0755 ./pup /usr/local/bin/pup`), then "
                    "rerun setup."
                ),
            },
        )
    status_command = [
        "pup",
        "auth",
        "status",
        "--no-envelope",
        "--output",
        "json",
    ]
    status = _run_pup(status_command)
    if not isinstance(status, dict) or not status.get("authenticated"):
        raise SetupError(
            "pup_not_authenticated",
            "Authenticate to Datadog with `pup auth login`.",
        )
    if status.get("token_type") == "bearer":
        scopes = {
            item for item in status.get("scopes", [])
            if isinstance(item, str)
        }
        required = {"integrations_read", "manage_integrations"}
        missing = sorted(required - scopes)
        if missing:
            raise SetupError(
                "pup_scopes_missing",
                "Pup's OAuth session lacks required integration permissions.",
                details={
                    "missing_scopes": missing,
                    "remediation": (
                        "Run `pup auth login --extra-scopes "
                        "integrations_read,manage_integrations`."
                    ),
                },
            )
    try:
        _pup_api("GET", "/api/v2/current_user")
    except SetupError as error:
        raise SetupError(
            "pup_authentication_failed",
            (
                "Pup could not authenticate to Datadog. Run `pup auth refresh`; "
                "if that fails, run `pup auth login`."
            ),
            details={"pup_error": error.as_dict()},
        ) from error
    return {
        "site": status.get("site"),
        "org": status.get("org"),
        "token_type": status.get("token_type"),
    }


def _build_payload(config: dict[str, Any], files: ProtectedFiles) -> dict[str, Any]:
    private_key = files.private_key.read_text(encoding="utf-8")
    static = json.loads(files.static_key.read_text(encoding="utf-8"))
    token = static.get("token") if isinstance(static, dict) else None
    if "PRIVATE KEY" not in private_key or not isinstance(token, str) or not token:
        raise SetupError(
            "credential_files_invalid",
            "Credential files failed local validation.",
        )
    payload = {
        "data": {
            "type": "integration-account",
            "attributes": {
                "name": config["account_name"],
                "authentication": {
                    "auth_type": "nebius_service_account",
                    "service_account_id": config["service_account_id"],
                    "public_key_id": config["public_key_id"],
                    "private_key": private_key,
                    "static_api_key": token,
                },
                "settings": {
                    "tenant_id": config["tenant_id"],
                    "account_tags": list(config.get("account_tags") or []),
                },
                "dataflows": {
                    "nebius-cloud-logs": {
                        "settings": {"enabled_services": config["streams"]}
                    },
                    "nebius-cloud-resources": {
                        "enabled": bool(config.get("resources_enabled"))
                    },
                    "nebius-cloud-service-quota-collection": {
                        "enabled": bool(config.get("service_quota_enabled"))
                    },
                },
            },
        }
    }
    write_json(files.payload, payload)
    return {
        "name": config["account_name"],
        "tenant_id": config["tenant_id"],
        "service_account_id": config["service_account_id"],
        "public_key_id": config["public_key_id"],
        "enabled_services": config["streams"],
        "account_tags": list(config.get("account_tags") or []),
        "resources_enabled": bool(config.get("resources_enabled")),
        "service_quota_enabled": bool(config.get("service_quota_enabled")),
    }


def _register_account(
    config: dict[str, Any],
    files: ProtectedFiles,
) -> dict[str, Any]:
    """POST once through Pup, then verify or reconcile a failed result."""
    try:
        _pup_api(
            "POST",
            ACCOUNTS_PATH,
            payload=files.payload.read_bytes(),
        )
    except SetupError as error:
        try:
            match = _find_verified_account(_list_accounts(), config)
        except SetupError as reconciliation_error:
            raise SetupError(
                "ambiguous_datadog_post",
                (
                    "Pup's POST result is ambiguous and account reconciliation "
                    "also failed. No second POST was attempted."
                ),
                details={
                    "post_error": error.as_dict(),
                    "reconciliation_error": reconciliation_error.as_dict(),
                },
            ) from error
        if match:
            return match
        raise SetupError(
            "datadog_create_failed",
            (
                "Pup could not create the Datadog Nebius account, and "
                "reconciliation found no exact account. No retry was attempted."
            ),
            details={"pup_error": error.as_dict()},
        ) from error
    match = _find_verified_account(_list_accounts(), config)
    if not match:
        raise SetupError(
            "datadog_account_not_visible",
            "Datadog accepted the account, but exact registration verification failed.",
        )
    return match
