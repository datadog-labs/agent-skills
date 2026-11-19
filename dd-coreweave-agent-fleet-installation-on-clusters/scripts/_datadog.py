"""Datadog requests through Pup: tenancy, rules, and API keys."""

from __future__ import annotations

import datetime
import json
import os
import re
import shutil
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from _common import SetupError, sanitize_text

PROVIDER = "coreweave-cloud"
RESOURCE_TYPE = "coreweave-cloud:cks:cluster"
ACCOUNTS_PATH = f"/api/v2/web-integrations/{PROVIDER}/accounts"
REQUIRED_ACCESS_PATH = f"/api/unstable/instrumenter/required_access/{PROVIDER}"
RULES_PATH = "/api/unstable/instrumenter/rules"
API_KEYS_PATH = "/api/v2/api_keys"
# Agent fleet installation on CoreWeave is a preview: the rules routes are
# middleware-gated by `agent-install-aws` (a bare 404 when off) and the
# cloud's resource types by `mci-coreweave` (a 400 naming the unsupported
# resource type when off). Both mean the same thing to the user.
PREVIEW_FEATURE_MESSAGE = (
    "This is a preview feature. Please reach out to Datadog support for "
    "early access."
)
# Account-level metrics the CoreWeave integration submits; their series
# carry `cluster_org` and `account_display_name` tags.
INTEGRATION_ORG_METRICS = (
    "coreweave.billing.instance_total",
    "coreweave.alerting.alerts",
)

# Scope names mirror Datadog permission names. Discovery reads the
# integration and its accounts; rule writes need the agent-install scope.
# `--extra-scopes` ADDS to Pup's normal set (`--scopes` replaces it — never
# use that here).
REQUIRED_SCOPES = (
    "integrations_read",
    "manage_integrations",
)
# Lists the API keys the Agents can use. Requested at every login but never
# *required*: some OAuth servers reject it, and `--api-key-uuid` selects
# the key without a listing.
API_KEYS_READ_SCOPE = "api_keys_read"
# The OAuth form of the Agent Install permission: a token that carries it
# writes install rules itself, so every login this flow suggests asks for
# it. Sessions that cannot carry it fall back to the one-shot key pair
# below, which is why it is requested but never *required*.
RULE_WRITE_SCOPE = "agent_install_write"
LOGIN_SCOPES = (
    "integrations_read",
    "manage_integrations",
    API_KEYS_READ_SCOPE,
    RULE_WRITE_SCOPE,
)
EXTRA_SCOPES_COMMAND = (
    "pup auth login --extra-scopes " + ",".join(LOGIN_SCOPES)
)

# When the session token cannot write rules, the rule POST and DELETE are
# sent as a full API user over HTTPS with an API + application key pair.
# The pair is resolved from DD_API_KEY / DD_APP_KEY when both are exported;
# otherwise it is minted one-shot through Pup (which can create keys where
# a raw OAuth token cannot) and deleted as soon as the rule operation is
# done. These are the scopes the minting login needs on top of
# LOGIN_SCOPES; `app_keys_write` and `app_keys_read` are rejected as
# invalid_scope by the OAuth server.
MINT_SCOPES = (
    "api_keys_write",
    "org_app_keys_read",
    "org_app_keys_write",
    "user_app_keys",
)
MINT_LOGIN_COMMAND = (
    "pup auth login --extra-scopes " + ",".join(LOGIN_SCOPES + MINT_SCOPES)
)
MINTED_KEY_PREFIX = "dd-coreweave-agent-fleet-rule-write-"
DEFAULT_SITE = "datadoghq.com"

PERMISSION_STATUSES = {401, 403}


def _is_preview_gate(error: SetupError) -> bool:
    """Whether a failed rules call is the preview feature being switched off.

    A 404 means the rules routes' `agent-install-aws` middleware gate is
    off. A 400 whose text names an unsupported resource type means the
    `mci-coreweave` rollout flag does not serve CoreWeave's resource types
    yet. Both are the same preview state, never a user or auth error.
    """
    details = error.details or {}
    status = details.get("http_status")
    if status == 404:
        return True
    if status == 400:
        stderr = str(details.get("stderr") or "").lower()
        return "unsupported resource_type" in stderr
    return False


def _preview_feature_error(error: SetupError, action: str) -> SetupError:
    return SetupError(
        "preview_feature",
        PREVIEW_FEATURE_MESSAGE,
        recoverable=False,
        details={
            "http_status": (error.details or {}).get("http_status"),
            "action": action,
            "remediation": PREVIEW_FEATURE_MESSAGE,
            "datadog_error": error.as_dict(),
        },
    )


def _pup_base(command: str) -> list[str]:
    return ["pup", command, "--no-envelope", "--output", "json"]


def _http_status(stderr: str | None) -> int | None:
    if not stderr:
        return None
    match = re.search(r"\bHTTP (\d{3})\b", stderr)
    return int(match.group(1)) if match else None


def _run_pup(
    command: list[str],
    *,
    payload: bytes | None = None,
    timeout: float | None = None,
) -> Any:
    # No client-side timeout for writes: abandoning a POST locally would only
    # make its outcome ambiguous. Reads may pass a bounded timeout.
    try:
        result = subprocess.run(
            command,
            input=payload,
            capture_output=True,
            check=False,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as error:
        raise SetupError(
            "pup_request_timeout",
            "Pup did not answer before the deadline.",
            details={"command": command[:2]},
        ) from error
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
            "datadog_request_failed",
            "Pup rejected or could not complete the Datadog request.",
            details={
                "command": command[:2],
                "http_status": _http_status(stderr),
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
    payload: dict[str, Any] | None = None,
    timeout: float | None = None,
) -> Any:
    command = _pup_base("api")
    command.extend(["--method", method])
    data = None
    if payload is not None:
        command.extend(["--input", "-"])
        data = json.dumps(payload).encode()
    command.append(path)
    return _run_pup(command, payload=data, timeout=timeout)


def _run_pup_quiet(
    command: list[str], *, timeout: float | None = None
) -> Any:
    """Run a Pup command whose stdout may contain fresh secrets.

    Identical failure semantics to `_run_pup`, but stdout is never copied
    into error details: a `pup api-keys create` that succeeds at the server
    but fails to parse locally would otherwise carry the new key value into
    the emitted JSON.
    """
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            check=False,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as error:
        raise SetupError(
            "pup_request_timeout",
            "Pup did not answer before the deadline.",
            details={"command": command[:2]},
        ) from error
    except OSError as error:
        raise SetupError(
            "pup_transport_error",
            "Pup could not complete the authenticated Datadog request.",
            details={
                "command": command[:2],
                "error_type": type(error).__name__,
            },
        ) from error
    stderr = result.stderr.decode(errors="replace")
    if result.returncode != 0:
        raise SetupError(
            "datadog_request_failed",
            "Pup rejected or could not complete the Datadog request.",
            details={
                "command": command[:2],
                "http_status": _http_status(stderr),
                "stderr": sanitize_text(stderr),
            },
        )
    stdout = result.stdout.decode(errors="replace")
    try:
        return json.loads(stdout) if stdout.strip() else {}
    except json.JSONDecodeError as error:
        raise SetupError(
            "pup_response_invalid",
            "Pup returned a non-JSON response.",
            details={"command": command[:2]},
        ) from error


def _keyed_api(
    method: str,
    path: str,
    *,
    credentials: dict[str, Any],
    payload: dict[str, Any] | None = None,
    timeout: float = 60.0,
) -> Any:
    """One HTTPS call to Datadog authenticated by an API + app key pair.

    Keyed rule writes are the fallback for sessions whose token cannot
    write rules itself (no `agent_install_write` scope): the POST and
    DELETE ride a full API user's keys rather than Pup's session token.
    Errors mirror `_pup_api`'s shape (an `http_status` detail) so the
    same permission mapping applies.
    """
    site = str(credentials.get("site") or DEFAULT_SITE)
    site = site.strip().lower().removeprefix("https://").rstrip("/")
    # Public sites serve the API at api.<site>, but internal dev sites
    # (e.g. ddintdev.datadoghq.com, selected through DD_SITE) serve it on
    # the site host itself: api.<site> there resolves to an intake load
    # balancer whose certificate does not cover the name, so the request
    # fails before it is ever sent. A transport failure means nothing was
    # written, so falling back to the site host keeps the write one-shot;
    # an HTTP status (auth, permission, validation) reached Datadog and
    # is reported without failover.
    hosts = [f"api.{site or DEFAULT_SITE}"]
    if site:
        hosts.append(site)
    transport_error: Exception | None = None
    for host in hosts:
        url = f"https://{host}{path}"
        headers = {
            "DD-API-KEY": credentials["api_key"],
            "DD-APPLICATION-KEY": credentials["app_key"],
            "Content-Type": "application/json",
        }
        data = json.dumps(payload).encode() if payload is not None else None
        request = urllib.request.Request(
            url, data=data, headers=headers, method=method
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                body = response.read().decode(errors="replace")
            return json.loads(body) if body.strip() else {}
        except urllib.error.HTTPError as error:
            try:
                detail = error.read().decode(errors="replace")
            except Exception:  # noqa: BLE001 - best effort on an error path
                detail = ""
            raise SetupError(
                "datadog_request_failed",
                "Datadog rejected the key-authenticated request.",
                details={
                    "command": ["api", path],
                    "http_status": error.code,
                    "stderr": sanitize_text(detail),
                },
            ) from error
        except (urllib.error.URLError, OSError) as error:
            transport_error = error
            continue
    raise SetupError(
        "datadog_transport_error",
        "Datadog could not be reached for the key-authenticated request.",
        details={
            "command": ["api", path],
            "error_type": type(transport_error).__name__,
            "hosts": hosts,
        },
    ) from transport_error


def _minted_key_name() -> str:
    stamp = datetime.datetime.now(
        datetime.timezone.utc
    ).strftime("%Y%m%dT%H%M%SZ")
    return MINTED_KEY_PREFIX + stamp


def _created_secret(value: Any, *, kind: str) -> dict[str, str]:
    """Extract the id/key from a `pup <x>-keys create` response."""
    data = value.get("data") if isinstance(value, dict) else None
    attributes = data.get("attributes") if isinstance(data, dict) else None
    secret = attributes.get("key") if isinstance(attributes, dict) else None
    if not isinstance(data, dict) or not data.get("id") or not secret:
        raise SetupError(
            "key_minting_failed",
            f"Pup did not return the created {kind} as expected.",
            details={"command": [f"{kind}s", "create"]},
        )
    return {"id": str(data["id"]), "key": str(secret)}


def _rule_write_credentials(pup: dict[str, Any]) -> dict[str, Any]:
    """The API + app key pair rule writes are authenticated with.

    Exported DD_API_KEY and DD_APP_KEY win when both are present — they
    are the user's own keys and need no extra scopes. Otherwise a
    one-shot pair is minted through Pup, whose login scopes let it create
    keys where a raw OAuth token cannot. `pup` is the session
    `_validate_pup` already read, so its token type and scopes decide
    whether minting can work without a second status read. The secret
    values never leave this process: they are held in memory for the
    rule call and deleted right after, and they are never printed or
    emitted.
    """
    resolved_site = (
        (pup.get("site") or os.environ.get("DD_SITE") or DEFAULT_SITE)
        .strip()
        .lower()
    )
    api_key = os.environ.get("DD_API_KEY")
    app_key = os.environ.get("DD_APP_KEY")
    if api_key and app_key:
        return {
            "source": "environment",
            "api_key": api_key,
            "app_key": app_key,
            "site": resolved_site,
        }
    scopes = {
        item
        for item in (pup.get("scopes") or [])
        if isinstance(item, str)
    }
    missing = sorted(set(MINT_SCOPES) - scopes)
    if missing and pup.get("token_type") == "bearer":
        raise SetupError(
            "pup_scopes_missing_for_key_minting",
            (
                "No DD_API_KEY/DD_APP_KEY are exported, and Pup's token "
                "lacks the scopes needed to mint the one-shot keys that "
                "rule writes require."
            ),
            details={
                "missing_scopes": missing,
                "remediation": (
                    f"Run `{MINT_LOGIN_COMMAND}`, or export DD_API_KEY and "
                    "DD_APP_KEY of a user who holds the Agent Install "
                    "permission."
                ),
            },
        )
    name = _minted_key_name()
    created_ids: list[str] = []
    api_info: dict[str, str] | None = None
    try:
        api_info = _created_secret(
            _run_pup_quiet(
                [
                    "pup",
                    "api-keys",
                    "create",
                    "--name",
                    name,
                    "--no-envelope",
                    "--output",
                    "json",
                ]
            ),
            kind="api_key",
        )
        created_ids.append(api_info["id"])
        app_info = _created_secret(
            _run_pup_quiet(
                [
                    "pup",
                    "app-keys",
                    "create",
                    "--name",
                    name,
                    "--no-envelope",
                    "--output",
                    "json",
                ]
            ),
            kind="app_key",
        )
        created_ids.append(app_info["id"])
    except SetupError as error:
        # A half-minted pair cannot authenticate its own cleanup (the
        # Datadog API needs an API key for every call), so surface the
        # created key ids rather than orphaning them silently.
        details = dict(error.details or {})
        if created_ids:
            details["created_keys_not_deleted"] = created_ids
            details["remediation"] = " ".join(
                [
                    str(details.get("remediation") or ""),
                    (
                        f"Keys {', '.join(created_ids)} were created but could "
                        "not be completed or deleted automatically; remove "
                        "them in Organization Settings > API Keys / "
                        "Application Keys."
                    ),
                ]
            ).strip()
        if details.get("http_status") in PERMISSION_STATUSES:
            raise SetupError(
                "pup_scopes_missing_for_key_minting",
                (
                    "Pup's session is not permitted to create the one-shot "
                    "keys that rule writes require."
                ),
                details=details,
            ) from error
        if error.reason == "key_minting_failed":
            raise
        raise SetupError(
            "key_minting_failed",
            "The one-shot keys for the rule write could not be minted.",
            details=details,
        ) from error
    return {
        "source": "minted",
        "api_key": api_info["key"],
        "api_key_id": api_info["id"],
        "app_key": app_info["key"],
        "app_key_id": app_info["id"],
        "name": name,
        "site": resolved_site,
    }


def _keyed_visible_accounts(
    credentials: dict[str, Any],
) -> list[str] | None:
    """Account ids the exported key pair can see, for the org check below.

    Returns None when the pair cannot read the accounts endpoint or the
    response does not parse as an account listing: indeterminate, not
    wrong — valid pairs may simply lack the read, and the write itself
    decides then. The org check below only fires on a listing that names
    accounts, none of them the selected one.
    """
    try:
        value = _keyed_api("GET", ACCOUNTS_PATH, credentials=credentials)
    except SetupError:
        return None
    visible = [
        account["id"]
        for account in _account_summary(value)
        if account.get("id")
    ]
    return visible or None


def _keyed_wrong_org_error(
    credentials: dict[str, Any], message: str
) -> SetupError:
    return SetupError(
        "rule_write_keys_wrong_org",
        message,
        details={
            "site": credentials.get("site"),
            "remediation": (
                "Export a key pair from the same org as the Pup session, "
                f"or run `{EXTRA_SCOPES_COMMAND}` so the rule write rides "
                "the session token instead."
            ),
        },
    )


def _pup_delete_key(command: str, key_id: str) -> None:
    """Delete one minted key through the Pup session that created it."""
    _run_pup(
        [
            "pup",
            command,
            "delete",
            key_id,
            "--yes",
            "--no-envelope",
            "--output",
            "json",
        ]
    )


def _release_rule_credentials(
    credentials: dict[str, Any] | None,
) -> list[dict[str, str]]:
    """Delete minted one-shot keys; exported keys are the user's, not ours.

    Both deletions go through the Pup session that minted the pair, never
    through the pair itself: an API key cannot delete itself, and once it
    is revoked the pair can no longer authenticate the second deletion.
    `pup app-keys delete` also uses the current-user endpoint that
    `pup app-keys create` made the key on. Best effort: a cleanup failure
    becomes a warning naming the key's id so it can be removed by hand,
    never a failure of the install itself.
    """
    if not credentials or credentials.get("source") != "minted":
        return []
    warnings: list[dict[str, str]] = []
    try:
        _pup_delete_key("api-keys", credentials["api_key_id"])
    except SetupError as error:
        warnings.append(
            {
                "code": "minted_api_key_not_deleted",
                "message": (
                    "The one-shot API key "
                    f"{credentials.get('api_key_id')} could not be deleted "
                    f"({error.message}). Remove it in Organization Settings "
                    "> API Keys."
                ),
            }
        )
    try:
        _pup_delete_key("app-keys", credentials["app_key_id"])
    except SetupError as error:
        warnings.append(
            {
                "code": "minted_app_key_not_deleted",
                "message": (
                    "The one-shot application key "
                    f"{credentials.get('app_key_id')} could not be deleted "
                    f"({error.message}). Remove it in Organization Settings "
                    "> Application Keys."
                ),
            }
        )
    return warnings


def _request_error(error: SetupError, context: str) -> SetupError:
    """Re-wrap a transport failure with the Datadog-side classification."""
    details = dict(error.details or {})
    status = details.get("http_status")
    if status in PERMISSION_STATUSES:
        return SetupError(
            "datadog_not_permitted",
            f"Pup's Datadog session is not permitted to {context}.",
            details={
                "http_status": status,
                "remediation": (
                    "Bearer sessions authorize by token scopes, not user RBAC. "
                    f"Run `{EXTRA_SCOPES_COMMAND}` (or add the missing scope "
                    "named in the error) and rerun."
                ),
                "pup_error": error.as_dict(),
            },
        )
    return error


def _validate_pup() -> dict[str, Any]:
    if shutil.which("pup") is None:
        raise SetupError(
            "pup_cli_missing",
            "Install Pup and authenticate with `pup auth login`.",
            recoverable=False,
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
            item for item in status.get("scopes", []) if isinstance(item, str)
        }
        missing = sorted(set(REQUIRED_SCOPES) - scopes)
        if missing:
            raise SetupError(
                "pup_scopes_missing",
                "Pup's OAuth token lacks scopes this flow needs.",
                details={
                    "missing_scopes": missing,
                    "remediation": (
                        f"Run `{EXTRA_SCOPES_COMMAND}`. `--extra-scopes` adds "
                        "to the normal set; never use `--scopes`, which "
                        "replaces it."
                    ),
                },
            )
    try:
        current_user = _pup_api("GET", "/api/v2/current_user")
    except SetupError as error:
        raise SetupError(
            "pup_authentication_failed",
            (
                "Pup could not authenticate to Datadog. Run `pup auth refresh`; "
                "if that fails, run `pup auth login`."
            ),
            details={"pup_error": error.as_dict()},
        ) from error
    # The session's own org, from the same read that proved
    # authentication: `pup auth status` reports no org for bearer
    # tokens, and the org scopes every later step of this flow.
    org = status.get("org")
    for item in (current_user or {}).get("included", []) or []:
        if isinstance(item, dict) and item.get("type") == "orgs":
            org = (item.get("attributes") or {}).get("name") or org
            break
    return {
        "site": status.get("site"),
        "org": org,
        "token_type": status.get("token_type"),
        "scopes": [
            item
            for item in (status.get("scopes") or [])
            if isinstance(item, str)
        ],
    }


def _check_env_keys(pup: dict[str, Any]) -> dict[str, Any] | None:
    """Validate an exported key pair and resolve its org (preflight).

    The pair only matters when a keyed rule write would run — the
    session token cannot write rules — but a wrong-site or wrong-org
    pair is exactly the late, confusing failure this check exists to
    prevent, so it runs at preflight on every run. Its findings are
    warnings, never errors: a token that can write rules makes the pair
    irrelevant, and discovery reads through Pup either way.
    """
    api_key = os.environ.get("DD_API_KEY")
    app_key = os.environ.get("DD_APP_KEY")
    if not (api_key and app_key):
        return None
    site = str(pup.get("site") or DEFAULT_SITE).strip().lower()
    credentials = {
        "source": "environment",
        "api_key": api_key,
        "app_key": app_key,
        "site": site,
    }
    result: dict[str, Any] = {"present": True, "site": site, "org": None}
    warnings: list[dict[str, str]] = []
    try:
        _keyed_api("GET", "/api/v1/validate", credentials=credentials)
    except SetupError as error:
        if (error.details or {}).get("http_status") in PERMISSION_STATUSES:
            warnings.append(
                {
                    "code": "env_keys_wrong_site",
                    "message": (
                        "The exported DD_API_KEY is not valid on "
                        f"{site}, so a key-authenticated rule write would "
                        "fail. Export a pair for this site, or rely on the "
                        "session token (agent_install_write)."
                    ),
                }
            )
            result["warnings"] = warnings
            return result
        raise
    try:
        value = _keyed_api(
            "GET", "/api/v2/current_user", credentials=credentials
        )
    except SetupError as error:
        if (error.details or {}).get("http_status") in PERMISSION_STATUSES:
            warnings.append(
                {
                    "code": "env_keys_org_unresolved",
                    "message": (
                        "The exported DD_API_KEY/DD_APP_KEY could not read "
                        "the current user, so their org could not be "
                        "resolved; a keyed rule write may still work."
                    ),
                }
            )
            result["warnings"] = warnings
            return result
        raise
    org = None
    for item in (value or {}).get("included", []) or []:
        if isinstance(item, dict) and item.get("type") == "orgs":
            org = (item.get("attributes") or {}).get("name")
            break
    result["org"] = org
    if org and pup.get("org") and org != pup.get("org"):
        warnings.append(
            {
                "code": "env_keys_wrong_org",
                "message": (
                    "The exported DD_API_KEY/DD_APP_KEY belong to org "
                    f"{org}, not this session's org {pup.get('org')} "
                    f"({site}), so a key-authenticated rule write would "
                    "target the wrong org. Export a pair from this "
                    "session's org, or rely on the session token "
                    "(agent_install_write)."
                ),
            }
        )
    result["warnings"] = warnings
    return result


def _account_summary(value: Any) -> list[dict[str, Any]]:
    items = value.get("data", []) if isinstance(value, dict) else []
    result = []
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        attributes = item.get("attributes", {})
        settings = (
            attributes.get("settings", {}) if isinstance(attributes, dict) else {}
        )
        result.append(
            {
                "id": item.get("id"),
                "name": attributes.get("name") if isinstance(attributes, dict) else None,
                "tenant_id": settings.get("tenant_id") if isinstance(settings, dict) else None,
            }
        )
    return result


def _integration_orgs() -> list[dict[str, Any]]:
    """The (CoreWeave org, account display name) pairs reporting telemetry.

    CoreWeave accounts expose no cloud org id through the integration
    API, but the metrics the integration submits carry `cluster_org` and
    `account_display_name` tags — the only place the org binding is
    observable without the integration token. Series are grouped by both
    tags so several accounts stay attributed. When no candidate metric
    has recent data this returns an empty list: an unknown, never a
    failure.
    """
    to_epoch = int(time.time())
    from_epoch = to_epoch - 2 * 3600
    pairs: list[dict[str, Any]] = []
    for metric in INTEGRATION_ORG_METRICS:
        query = f"avg:{metric}{{*}} by {{cluster_org,account_display_name}}"
        path = (
            f"v1/query?from={from_epoch}&to={to_epoch}"
            f"&query={urllib.parse.quote(query)}"
        )
        try:
            payload = _pup_api("GET", path)
        except SetupError:
            continue
        if not isinstance(payload, dict):
            continue
        for item in payload.get("series") or []:
            if not isinstance(item, dict):
                continue
            tags: dict[str, str] = {}
            for part in str(item.get("scope") or "").split(","):
                key, sep, value = part.partition(":")
                if sep:
                    tags[key.strip()] = value.strip()
            org = tags.get("cluster_org")
            if not org:
                continue
            pair = {
                "cluster_org": org,
                "account_display_name": tags.get("account_display_name"),
            }
            if pair not in pairs:
                pairs.append(pair)
    return pairs


def _list_accounts() -> list[dict[str, Any]]:
    try:
        return _account_summary(_pup_api("GET", ACCOUNTS_PATH))
    except SetupError as error:
        details = error.details or {}
        stderr = str(details.get("stderr") or "").lower()
        if details.get("http_status") in PERMISSION_STATUSES and (
            "not allowed" in stderr or "disabled" in stderr
        ):
            raise SetupError(
                "integration_not_available",
                (
                    f"Datadog's {PROVIDER} integration account API is "
                    "disabled for this organization, so the tenancy "
                    "cannot be read."
                ),
                recoverable=False,
                details={
                    "accounts_path": ACCOUNTS_PATH,
                    "remediation": (
                        "Enable the CoreWeave integration for this "
                        "organization first (the dd-coreweave-integration "
                        "skill or the CoreWeave tile in Datadog), or ask "
                        "your Datadog representative."
                    ),
                    "pup_error": error.as_dict(),
                },
            ) from error
        raise _request_error(
            error, f"list the {PROVIDER} integration accounts"
        ) from error


def _check_required_access() -> dict[str, Any]:
    """200 means the provider's MCI flags target this org; 404 means off."""
    try:
        return _pup_api("GET", REQUIRED_ACCESS_PATH)
    except SetupError as error:
        status = (error.details or {}).get("http_status")
        if status == 404:
            raise SetupError(
                "mci_not_enabled",
                (
                    "Agent fleet installation is not enabled for this "
                    "organization and provider yet."
                ),
                recoverable=False,
                details={
                    "required_access_path": REQUIRED_ACCESS_PATH,
                    "remediation": PREVIEW_FEATURE_MESSAGE,
                },
            ) from error
        raise _request_error(error, "read the required-access document") from error


def _list_api_keys() -> list[dict[str, Any]]:
    try:
        value = _pup_api("GET", API_KEYS_PATH)
    except SetupError as error:
        raise _request_error(error, "list API keys") from error
    items = value.get("data", []) if isinstance(value, dict) else []
    pairs = {
        (item.get("id"), (item.get("attributes") or {}).get("name"))
        for item in items
        if isinstance(item, dict) and item.get("id")
    }
    return [
        {"id": key_id, "name": name}
        for key_id, name in sorted(
            pairs, key=lambda pair: (pair[1] or pair[0] or "")
        )
    ]


def _rule_items(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    if not isinstance(value, dict):
        return []
    for key in ("rules", "data", "items"):
        items = value.get(key)
        if isinstance(items, list):
            return [item for item in items if isinstance(item, dict)]
    return []


def _find_rule(rules: list[dict[str, Any]], account_id: str) -> dict[str, Any] | None:
    for rule in rules:
        account = rule.get("account_id")
        if (
            isinstance(account, dict)
            and account.get("provider") == PROVIDER
            and account.get("account_id") == account_id
            and rule.get("resource_type") == RESOURCE_TYPE
        ):
            return rule
    return None


def _other_account_rules(
    rules: list[dict[str, Any]], account_id: str
) -> list[dict[str, Any]]:
    """Install rules of this provider owned by other Datadog accounts.

    Datadog permits one rule per account and resource type, so a leftover
    rule owned by a different account of the same provider can silently
    cover the same clusters: MCI registers a tenancy's clusters under the
    rule it discovers for them, regardless of which account created it,
    leaving a rule created here without resources and its install result
    partial. Looking them up first surfaces the overlap before a second
    rule is created.
    """
    others: list[dict[str, Any]] = []
    for rule in rules:
        if not isinstance(rule, dict):
            continue
        account = rule.get("account_id")
        if (
            isinstance(account, dict)
            and account.get("provider") == PROVIDER
            and account.get("account_id") != account_id
            and rule.get("resource_type") == RESOURCE_TYPE
        ):
            others.append(rule)
    return others


def _list_rules() -> list[dict[str, Any]]:
    try:
        return _rule_items(_pup_api("GET", RULES_PATH))
    except SetupError as error:
        if _is_preview_gate(error):
            raise _preview_feature_error(error, "read install rules") from error
        raise _request_error(error, "read install rules") from error


def _oauth_rule_write_permitted(pup: dict[str, Any]) -> bool:
    """Whether this session's own token can write install rules.

    A bearer token needs the `agent_install_write` scope — the OAuth form
    of the Agent Install permission. A full user session has no scopes:
    its writes ride the user's own permissions, which may already include
    Agent Install, so it takes the direct route too.
    """
    if pup.get("token_type") == "user":
        return True
    return RULE_WRITE_SCOPE in set(pup.get("scopes") or [])


def _create_rule(
    account_id: str,
    api_key_uuid: str,
    query: str | None,
    pup: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, str]]]:
    """Create the install rule with the session's own token when it can.

    The token carries `agent_install_write` (or is a full user session),
    so the POST rides Pup's OAuth session and no keys are touched. Only
    when the token cannot write rules does the POST fall back to a full
    API user's keys — exported DD_API_KEY/DD_APP_KEY when both are
    present, otherwise a one-shot pair minted through Pup and deleted
    right after the write. Returns the rule and cleanup warnings; the
    secret values never leave this process.
    """
    payload: dict[str, Any] = {
        "account_id": {"provider": PROVIDER, "account_id": account_id},
        "resource_type": RESOURCE_TYPE,
        "api_key_uuid": api_key_uuid,
    }
    if query:
        payload["query"] = query
    warnings: list[dict[str, str]] = []
    if _oauth_rule_write_permitted(pup):
        try:
            created = _pup_api("POST", RULES_PATH, payload=payload)
        except SetupError as error:
            if _is_preview_gate(error):
                raise _preview_feature_error(
                    error, "create the install rule"
                ) from error
            details = error.details or {}
            if details.get("http_status") in PERMISSION_STATUSES:
                # The scope is supposed to grant this write; if the server
                # still refuses, the token is not what its scopes claim.
                raise SetupError(
                    "rule_write_not_permitted",
                    (
                        "The session token carries "
                        f"`{RULE_WRITE_SCOPE}` but Datadog refused the "
                        "install-rule write."
                    ),
                    details={
                        "http_status": details.get("http_status"),
                        "transport": "oauth",
                        "remediation": (
                            f"Run `{EXTRA_SCOPES_COMMAND}` and rerun; if it "
                            "persists, ask your Datadog representative."
                        ),
                        "datadog_error": error.as_dict(),
                    },
                ) from error
            match = _find_rule(_list_rules(), account_id)
            if match:
                return match, warnings
            raise SetupError(
                "rule_create_failed",
                "Datadog rejected the install rule; no retry was attempted.",
                details={"datadog_error": error.as_dict()},
            ) from error
        if isinstance(created, dict) and created.get("id"):
            return created, warnings
        match = _find_rule(_list_rules(), account_id)
        if match:
            return match, warnings
        raise SetupError(
            "rule_create_ambiguous",
            "Datadog's response did not identify the created rule.",
            details={"response": created},
        )
    credentials = _rule_write_credentials(pup)
    try:
        # Exported keys can belong to any org; minted ones come from this
        # session and cannot. A pair from another org authenticates fine
        # and only fails at the POST with a confusing 400 ("no such
        # account in this org"), so the check runs first when the pair
        # can read the accounts endpoint; a pair that cannot (401/403) is
        # indeterminate and the POST's own error is mapped below instead.
        if credentials.get("source") == "environment":
            visible = _keyed_visible_accounts(credentials)
            if visible is not None and account_id not in visible:
                raise _keyed_wrong_org_error(
                    credentials,
                    (
                        "The exported DD_API_KEY/DD_APP_KEY do not see the "
                        "selected CoreWeave account, so they belong to a "
                        "different Datadog org than this session."
                    ),
                )
        try:
            created = _keyed_api(
                "POST", RULES_PATH, credentials=credentials, payload=payload
            )
        except SetupError as error:
            if _is_preview_gate(error):
                raise _preview_feature_error(
                    error, "create the install rule"
                ) from error
            details = error.details or {}
            if details.get("http_status") in PERMISSION_STATUSES:
                # The Agent Install permission is absent from most
                # permission catalogs, so keys owned by a user without it
                # cannot take this route.
                raise SetupError(
                    "rule_write_not_permitted",
                    (
                        "The keys used for this run cannot create install "
                        "rules. Rule writes need the Agent Install "
                        "permission, which is missing from most permission "
                        "catalogs, so the API and application keys must "
                        "belong to a user who holds it."
                    ),
                    details={
                        "http_status": details.get("http_status"),
                        "key_source": credentials.get("source"),
                        "remediation": (
                            f"Run `{EXTRA_SCOPES_COMMAND}` so the token "
                            "itself carries the permission, or export "
                            "DD_API_KEY and DD_APP_KEY owned by a user who "
                            "holds Agent Install (Organization Settings > "
                            "API Keys). Exported keys are used "
                            "automatically; without them the script mints "
                            "one-shot keys from this session, which carry "
                            "this session's user and their permissions."
                        ),
                        "datadog_error": error.as_dict(),
                    },
                ) from error
            stderr = str(details.get("stderr") or "")
            if "in this org" in stderr and PROVIDER in stderr:
                # The pair authenticated but its org holds no such
                # account — the write never applied, and the keys are
                # simply from another org.
                raise _keyed_wrong_org_error(
                    credentials,
                    (
                        "The exported DD_API_KEY/DD_APP_KEY belong to a "
                        "different Datadog org than this session: Datadog "
                        "rejected the rule because that org holds no such "
                        f"{PROVIDER} account."
                    ),
                ) from error
            match = _find_rule(_list_rules(), account_id)
            if match:
                return match, warnings
            raise SetupError(
                "rule_create_failed",
                "Datadog rejected the install rule; no retry was attempted.",
                details={"datadog_error": error.as_dict()},
            ) from error
        if not isinstance(created, dict) or not created.get("id"):
            match = _find_rule(_list_rules(), account_id)
            if match:
                return match, warnings
            raise SetupError(
                "rule_create_ambiguous",
                "Datadog's response did not identify the created rule.",
                details={"response": created},
            )
        return created, warnings
    finally:
        warnings.extend(_release_rule_credentials(credentials))


def _rule_resources(rule_id: str) -> list[dict[str, Any]]:
    try:
        value = _pup_api("GET", f"{RULES_PATH}/{rule_id}/resources")
    except SetupError as error:
        raise _request_error(error, "read the rule's cluster status") from error
    resources = value.get("resources", []) if isinstance(value, dict) else []
    return [item for item in resources if isinstance(item, dict)]


def _delete_rule(rule_id: str, pup: dict[str, Any]) -> list[dict[str, str]]:
    """Delete the rule with the session's own token when it can.

    Same route as creation: the OAuth token when it can write rules,
    otherwise a one-shot key pair minted around the call. Returns cleanup
    warnings (empty unless keys were used).
    """
    if _oauth_rule_write_permitted(pup):
        try:
            _pup_api("DELETE", f"{RULES_PATH}/{rule_id}")
        except SetupError as error:
            if _is_preview_gate(error):
                raise _preview_feature_error(
                    error, "delete the install rule"
                ) from error
            details = error.details or {}
            if details.get("http_status") in PERMISSION_STATUSES:
                raise SetupError(
                    "rule_write_not_permitted",
                    (
                        "The session token carries "
                        f"`{RULE_WRITE_SCOPE}` but Datadog refused the "
                        "install-rule deletion."
                    ),
                    details={
                        "http_status": details.get("http_status"),
                        "transport": "oauth",
                        "remediation": (
                            f"Run `{EXTRA_SCOPES_COMMAND}` and rerun; if it "
                            "persists, ask your Datadog representative."
                        ),
                        "datadog_error": error.as_dict(),
                    },
                ) from error
            raise
        return []
    credentials = _rule_write_credentials(pup)
    warnings: list[dict[str, str]] = []
    try:
        # Same org check as creation, adapted to deletion: keys from
        # another org do not see this org's rules at all. A rule that is
        # genuinely gone would fail the DELETE anyway; this check makes
        # the wrong-org case say so instead of a bare refusal. Keys that
        # cannot list rules (401/403) are indeterminate and the DELETE
        # decides.
        if credentials.get("source") == "environment":
            try:
                keyed_rules = _rule_items(
                    _keyed_api("GET", RULES_PATH, credentials=credentials)
                )
            except SetupError as error:
                if (error.details or {}).get("http_status") not in (
                    PERMISSION_STATUSES
                ):
                    raise
                keyed_rules = None
            if keyed_rules is not None and not any(
                rule.get("id") == rule_id for rule in keyed_rules
            ):
                raise _keyed_wrong_org_error(
                    credentials,
                    (
                        "The exported DD_API_KEY/DD_APP_KEY do not see rule "
                        f"{rule_id}: either they belong to a different "
                        "Datadog org than this session, or the rule no "
                        "longer exists."
                    ),
                )
        try:
            _keyed_api(
                "DELETE", f"{RULES_PATH}/{rule_id}", credentials=credentials
            )
        except SetupError as error:
            if _is_preview_gate(error):
                raise _preview_feature_error(
                    error, "delete the install rule"
                ) from error
            details = error.details or {}
            if details.get("http_status") in PERMISSION_STATUSES:
                raise SetupError(
                    "rule_write_not_permitted",
                    (
                        "The keys used for this run cannot delete install "
                        "rules. Rule writes and deletes need the Agent "
                        "Install permission, so the API and application "
                        "keys must belong to a user who holds it."
                    ),
                    details={
                        "http_status": details.get("http_status"),
                        "key_source": credentials.get("source"),
                        "remediation": (
                            f"Run `{EXTRA_SCOPES_COMMAND}` so the token "
                            "itself carries the permission, or export "
                            "DD_API_KEY and DD_APP_KEY owned by a user who "
                            "holds Agent Install."
                        ),
                        "datadog_error": error.as_dict(),
                    },
                ) from error
            raise
    finally:
        warnings.extend(_release_rule_credentials(credentials))
    return warnings


def _cluster_name_from_resource_id(resource_id: str) -> str | None:
    """`coreweave-cloud:cks:<zone>::cluster/<id>` -> `<id>`."""
    if "cluster/" not in resource_id:
        return None
    return resource_id.rsplit("cluster/", 1)[-1] or None
