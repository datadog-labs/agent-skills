"""Nebius CLI reads and single-attempt provisioning helpers."""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any, NoReturn

import _discovery as discovery
from _common import ProtectedFiles, SetupError, sanitize_text, write_json


def _run(command: list[str], *, timeout: int = 60) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise SetupError(
            "command_timed_out",
            "A Nebius CLI command timed out; no automatic retry was attempted.",
            command=command,
            stderr=sanitize_text(
                exc.stderr.decode() if isinstance(exc.stderr, bytes) else exc.stderr
            ),
            details={"timeout_seconds": timeout},
        ) from exc
    if result.returncode:
        raise SetupError(
            "nebius_command_failed",
            "A Nebius CLI command failed; no automatic write retry was attempted.",
            command=command,
            exit_code=result.returncode,
            stderr=sanitize_text(result.stderr),
        )
    return result


def _run_json(command: list[str]) -> Any:
    result = _run(command)
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise SetupError(
            "invalid_nebius_json",
            "A Nebius CLI command returned invalid JSON.",
            command=command,
            details={"stdout_bytes": len(result.stdout.encode())},
        ) from exc


def _ids(value: Any) -> set[str]:
    result = set()
    for item in discovery._items(value):
        resource_id, _ = discovery._identity(item)
        if resource_id:
            result.add(resource_id)
    return result


def _profile_identity(binary: str = "nebius") -> dict[str, str]:
    """Read only non-secret IDs selected by the Nebius CLI."""
    identity = {
        "profile": _run([binary, "profile", "current"]).stdout.strip(),
        "tenant_id": _run([binary, "config", "get", "tenant-id"]).stdout.strip(),
        "project_id": _run([binary, "config", "get", "parent-id"]).stdout.strip(),
    }
    invalid = [
        key
        for key, prefix in {
            "tenant_id": "tenant-",
            "project_id": "project-",
        }.items()
        if not identity[key].startswith(prefix)
    ]
    if not identity["profile"] or invalid:
        raise SetupError(
            "nebius_profile_identity_incomplete",
            "The active Nebius profile lacks tenant-id or project parent-id.",
            recoverable=False,
            details={
                "profile": identity["profile"] or "unknown",
                "invalid_fields": invalid,
            },
        )
    return identity


def _list_authorized_keys(binary: str, service_account_id: str) -> Any:
    return {
        "items": discovery._paginated_items(
            _run_json,
            [
                binary,
                "iam",
                "auth-public-key",
                "list-by-account",
                "--account-service-account-id",
                service_account_id,
            ],
        )
    }


def _list_static_keys(binary: str, project_id: str) -> Any:
    return _run_json(
        [
            binary,
            "iam",
            "static-key",
            "list",
            "--parent-id",
            project_id,
            "--all",
            "--format",
            "json",
        ]
    )


def _list_service_accounts(
    binary: str,
    project_id: str,
) -> list[dict[str, Any]]:
    value = _run_json(
        [
            binary,
            "iam",
            "service-account",
            "list",
            "--parent-id",
            project_id,
            "--all",
            "--format",
            "json",
        ]
    )
    result = []
    for item in discovery._items(value):
        account_id, name = discovery._identity(item)
        if account_id:
            project_id = discovery._first_string(
                item,
                (
                    ("metadata", "parent_id"),
                    ("metadata", "parentId"),
                    ("parent_id",),
                    ("parentId",),
                ),
            )
            result.append(
                {"id": account_id, "name": name, "project_id": project_id}
            )
    return result


def _list_memberships(
    binary: str, service_account_id: str
) -> list[dict[str, Any]]:
    items = discovery._paginated_items(
        _run_json,
        [
            binary,
            "iam",
            "group-membership",
            "list-member-of",
            "--subject-id",
            service_account_id,
        ],
    )
    memberships = [
        normalized
        for item in items
        if (normalized := discovery._membership(item)) is not None
    ]
    return memberships


def _has_membership(
    memberships: list[dict[str, Any]], group_id: str
) -> bool:
    return any(item.get("group_id") == group_id for item in memberships)


def _ensure_admin_membership(config: dict[str, Any], binary: str) -> None:
    groups = _run_json(
        [
            binary,
            "iam",
            "group",
            "list",
            "--parent-id",
            config["tenant_id"],
            "--all",
            "--format",
            "json",
        ]
    )
    admins = [
        group_id
        for value in discovery._items(groups)
        for group_id, name in [discovery._identity(value)]
        if group_id and name.lower() == "admins"
    ]
    if len(admins) != 1:
        raise SetupError(
            "admins_group_not_found",
            "Could not identify exactly one tenant admins group.",
        )
    admin_group_id = admins[0]
    memberships = _list_memberships(binary, config["service_account_id"])
    if _has_membership(memberships, admin_group_id):
        return
    try:
        _run(
            [
                binary,
                "iam",
                "group-membership",
                "create",
                "--parent-id",
                admin_group_id,
                "--member-id",
                config["service_account_id"],
                "--format",
                "json",
            ]
        )
    except SetupError as error:
        memberships = _list_memberships(binary, config["service_account_id"])
        if _has_membership(memberships, admin_group_id):
            return
        raise error
    memberships = _list_memberships(binary, config["service_account_id"])
    if not _has_membership(memberships, admin_group_id):
        raise SetupError(
            "admin_membership_not_verified",
            "The admins membership was not visible after the single create attempt.",
        )


def _reconcile_created_service_account(
    config: dict[str, Any],
    binary: str,
    before: set[str],
    cause: Exception | None = None,
) -> str:
    candidates = [
        item
        for item in _list_service_accounts(binary, config["project_id"])
        if item["id"] not in before
        and item["name"] == config["service_account_name"]
        and item["project_id"] == config["project_id"]
    ]
    if len(candidates) == 1:
        return candidates[0]["id"]
    if isinstance(cause, SetupError):
        raise cause
    error = SetupError(
        "service_account_creation_ambiguous",
        (
            "Service-account creation could not be safely reconciled; "
            "no retry was attempted."
        ),
    )
    if cause is not None:
        raise error from cause
    raise error


def _create_service_account(config: dict[str, Any], binary: str) -> None:
    accounts = _list_service_accounts(binary, config["project_id"])
    matches = [
        item
        for item in accounts
        if item["name"] == config["service_account_name"]
    ]
    if len(matches) > 1:
        raise SetupError(
            "service_account_creation_ambiguous",
            "Multiple service accounts have the requested name.",
            details={"service_account_ids": [item["id"] for item in matches]},
        )
    if matches:
        config["service_account_id"] = matches[0]["id"]
        config["project_id"] = matches[0]["project_id"]
        _ensure_admin_membership(config, binary)
        return
    before = {item["id"] for item in accounts}
    command = [
        binary,
        "iam",
        "service-account",
        "create",
        "--name",
        config["service_account_name"],
        "--parent-id",
        config["project_id"],
        "--format",
        "json",
    ]
    generated_id = ""
    try:
        result = _run(command)
        generated_id, _ = discovery._identity(json.loads(result.stdout))
    except (SetupError, json.JSONDecodeError) as error:
        generated_id = _reconcile_created_service_account(
            config,
            binary,
            before,
            cause=error,
        )
    if not generated_id:
        generated_id = _reconcile_created_service_account(
            config,
            binary,
            before,
        )
    config["service_account_id"] = generated_id
    _ensure_admin_membership(config, binary)


def _public_key_from_private(private_key: Path) -> str:
    try:
        result = subprocess.run(
            ["openssl", "pkey", "-in", str(private_key), "-pubout"],
            shell=False,
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise SetupError(
            "public_key_derivation_failed",
            "Could not derive public material from the generated private key.",
        ) from error
    if result.returncode:
        raise SetupError(
            "public_key_derivation_failed",
            "Could not derive public material from the generated private key.",
            exit_code=result.returncode,
            stderr=sanitize_text(result.stderr),
        )
    return "".join(result.stdout.split())


def _matching_new_authorized_ids(
    value: Any,
    *,
    before: set[str],
    private_key: Path,
) -> list[str]:
    expected_public_key = _public_key_from_private(private_key)
    matches = []
    for item in discovery._items(value):
        key_id, _ = discovery._identity(item)
        public_key = discovery._first_string(
            item,
            (
                ("spec", "data"),
                ("data",),
            ),
        )
        if (
            key_id
            and key_id not in before
            and "".join(public_key.split()) == expected_public_key
        ):
            matches.append(key_id)
    return sorted(matches)


def _reconcile_generated_authorized_key(
    config: dict[str, Any],
    binary: str,
    files: ProtectedFiles,
    before: set[str],
    cause: Exception | None = None,
) -> str:
    if not files.private_key.is_file():
        if isinstance(cause, SetupError):
            raise cause
        new_ids: list[str] = []
    else:
        new_ids = _matching_new_authorized_ids(
            _list_authorized_keys(binary, config["service_account_id"]),
            before=before,
            private_key=files.private_key,
        )
        if len(new_ids) == 1:
            return new_ids[0]
    error = SetupError(
        "authorized_key_creation_ambiguous",
        (
            "Authorized-key creation could not be safely reconciled. "
            "No retry was attempted."
        ),
        details={"new_key_ids": new_ids},
    )
    if cause is not None:
        raise error from cause
    raise error


def _generate_authorized_key(
    config: dict[str, Any], binary: str, files: ProtectedFiles
) -> None:
    before = _ids(_list_authorized_keys(binary, config["service_account_id"]))
    command = [
        binary,
        "iam",
        "auth-public-key",
        "generate",
        "--service-account-id",
        config["service_account_id"],
        "--output",
        str(files.private_key),
        "--output-format",
        "pem",
        "--expires-at",
        config["authorized_expires_at"],
        "--format",
        "json",
    ]
    generated_id = ""
    try:
        result = _run(command)
        generated_id, _ = discovery._identity(json.loads(result.stdout))
    except (SetupError, json.JSONDecodeError) as error:
        generated_id = _reconcile_generated_authorized_key(
            config,
            binary,
            files,
            before,
            cause=error,
        )
    if files.private_key.is_file():
        os.chmod(files.private_key, 0o600)
    if not generated_id:
        generated_id = _reconcile_generated_authorized_key(
            config,
            binary,
            files,
            before,
        )
    if (
        not generated_id
        or not files.private_key.is_file()
        or "PRIVATE KEY" not in files.private_key.read_text(encoding="utf-8")
    ):
        raise SetupError(
            "authorized_key_material_missing",
            "The authorized key or matching private material is incomplete.",
        )
    config["public_key_id"] = generated_id


def _static_key_name(config: dict[str, Any], existing: Any) -> str:
    account = re.sub(r"[^a-z0-9-]+", "-", config["account_name"].lower()).strip("-")
    base = f"datadog-{account[:40]}-{config['service_account_id'][-6:]}".replace(
        "--", "-"
    )
    names = {
        name
        for item in discovery._items(existing)
        for _, name in [discovery._identity(item)]
        if name
    }
    if base not in names:
        return base
    suffix = 2
    while f"{base}-{suffix}" in names:
        suffix += 1
    return f"{base}-{suffix}"


def _matching_new_static_key_ids(
    value: Any,
    *,
    before: set[str],
    key_name: str,
    service_account_id: str,
) -> list[str]:
    matches = []
    for item in discovery._items(value):
        key_id, name = discovery._identity(item)
        owner = discovery._first_string(
            item,
            (
                ("spec", "account", "service_account", "id"),
                ("spec", "account", "serviceAccount", "id"),
            ),
        )
        if (
            key_id
            and key_id not in before
            and name == key_name
            and owner == service_account_id
        ):
            matches.append(key_id)
    return sorted(matches)


def _raise_static_key_creation_ambiguous(
    config: dict[str, Any],
    binary: str,
    *,
    before: set[str],
    key_name: str,
    message: str,
    cause: Exception | None = None,
) -> NoReturn:
    new_ids = _matching_new_static_key_ids(
        _list_static_keys(binary, config["project_id"]),
        before=before,
        key_name=key_name,
        service_account_id=config["service_account_id"],
    )
    error = SetupError(
        "static_key_creation_ambiguous",
        message,
        details={"new_key_ids": new_ids},
    )
    if cause is not None:
        raise error from cause
    raise error


def _issue_static_key(
    config: dict[str, Any], binary: str, files: ProtectedFiles
) -> None:
    existing = _list_static_keys(binary, config["project_id"])
    before = _ids(existing)
    key_name = _static_key_name(config, existing)
    try:
        result = _run(
            [
                binary,
                "iam",
                "static-key",
                "issue",
                "--name",
                key_name,
                "--parent-id",
                config["project_id"],
                "--account-service-account-id",
                config["service_account_id"],
                "--service",
                "observability",
                "--expires-at",
                config["static_expires_at"],
                "--format",
                "json",
            ]
        )
        parsed = json.loads(result.stdout)
        token = parsed.get("token") if isinstance(parsed, dict) else None
    except (SetupError, json.JSONDecodeError) as error:
        _raise_static_key_creation_ambiguous(
            config,
            binary,
            before=before,
            key_name=key_name,
            message=(
                "Static-key creation did not return recoverable token material. "
                "No retry was attempted; revoke any listed new key before rerunning."
            ),
            cause=error,
        )
    if not isinstance(token, str) or not token:
        _raise_static_key_creation_ambiguous(
            config,
            binary,
            before=before,
            key_name=key_name,
            message=(
                "Nebius did not return recoverable static-key token material. "
                "No retry was attempted; revoke any listed new key before rerunning."
            ),
        )
    write_json(files.static_key, {"token": token})
