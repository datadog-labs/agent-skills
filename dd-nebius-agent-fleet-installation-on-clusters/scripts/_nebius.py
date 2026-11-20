"""Nebius CLI reads for the agent fleet installation flow."""

from __future__ import annotations

import json
import shutil
import subprocess
from typing import Any

from _common import SetupError, sanitize_text

NEBIUS_INSTALL_DOC = "https://docs.nebius.com/cli/install"
RESOURCE_ID_PREFIX = "nebius-cloud:mk8s"


def _run(command: list[str], *, timeout: int = 120) -> subprocess.CompletedProcess[str]:
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
            "A Nebius CLI command failed; no automatic retry was attempted.",
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


def _check_cli() -> dict[str, str]:
    """The Nebius CLI must be installed with an authenticated profile."""
    if shutil.which("nebius") is None:
        raise SetupError(
            "nebius_cli_missing",
            "Install the Nebius CLI before installing the Agent fleet.",
            recoverable=False,
            details={
                "install_command": (
                    "curl -sSL https://artifacts.nebius.cloud/cli/install.sh | bash"
                ),
                "after_install": (
                    "Restart the terminal or run `exec -l $SHELL`, then verify "
                    "with `nebius version`."
                ),
                "docs": NEBIUS_INSTALL_DOC,
                "authenticate_command": "nebius profile create",
            },
        )
    identity = _profile_identity()
    return identity


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
            (
                "The active Nebius profile lacks tenant-id or project parent-id. "
                "Run `nebius profile create` and choose the tenant and project."
            ),
            recoverable=False,
            details={
                "profile": identity["profile"] or "unknown",
                "invalid_fields": invalid,
            },
        )
    return identity


def _first_string(value: Any, paths: tuple[tuple[str, ...], ...]) -> str | None:
    if not isinstance(value, dict):
        return None
    for path in paths:
        item: Any = value
        for key in path:
            if not isinstance(item, dict):
                item = None
                break
            item = item.get(key)
        if isinstance(item, str) and item:
            return item
    return None


def _cluster_items(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    if not isinstance(value, dict):
        return []
    for key in ("clusters", "items", "resources"):
        items = value.get(key)
        if isinstance(items, list):
            return [item for item in items if isinstance(item, dict)]
    return []


def _resource_id(
    region: str | None, tenant_id: str | None, cluster_id: str
) -> str | None:
    """MCI's id for a cluster, which rule queries match as `@_key`.

    `nebius-cloud:mk8s:<region>:<tenant>:cluster/<id>`, where the region is
    the project's (its spec region, else its name) as MCI derives it. None
    when the project's region is unknown.
    """
    if not (region and tenant_id and cluster_id):
        return None
    return f"{RESOURCE_ID_PREFIX}:{region}:{tenant_id}:cluster/{cluster_id}"


def _normalize_cluster(
    item: dict[str, Any],
    project: dict[str, str] | None = None,
    tenant_id: str | None = None,
) -> dict[str, Any] | None:
    cluster_id = _first_string(
        item, (("metadata", "id"), ("id",), ("name", "id"))
    )
    if not cluster_id:
        return None
    labels: dict[str, str] = {}
    for path in (("metadata", "labels"), ("spec", "labels"), ("labels",)):
        node: Any = item
        for key in path:
            node = node.get(key) if isinstance(node, dict) else None
        if isinstance(node, dict):
            labels.update(
                {str(k): str(v) for k, v in node.items() if isinstance(v, str)}
            )
    return {
        "id": cluster_id,
        "name": _first_string(item, (("metadata", "name"), ("name",))) or cluster_id,
        "region": _first_string(
            item, (("spec", "region"), ("region",), ("status", "region"))
        ) or (project or {}).get("region"),
        "status": _first_string(
            item,
            (("status", "status"), ("status", "state"), ("spec", "status"), ("status",)),
        ),
        "labels": labels,
        "project_id": project["id"] if project else None,
        "project_name": project["name"] if project else None,
        "resource_id": _resource_id(
            (project or {}).get("region"), tenant_id, cluster_id
        ),
    }


def _project_region(item: dict[str, Any]) -> str | None:
    """A project's region as MCI derives it: its spec region, else its name."""
    return _first_string(item, (("spec", "region"), ("metadata", "name")))


def _list_projects(binary: str, tenant_id: str) -> list[dict[str, str]]:
    """List the tenant's projects: the IAM containers that own mk8s clusters."""
    last_error: SetupError | None = None
    for base in (
        [binary, "iam", "v2", "project", "list"],
        [binary, "iam", "project", "list"],
    ):
        try:
            value = _run_json(
                [*base, "--parent-id", tenant_id, "--all", "--format", "json"]
            )
        except SetupError as error:
            last_error = error
            continue
        if isinstance(value, dict) and "items" in value:
            projects = []
            for item in value.get("items", []):
                if not isinstance(item, dict):
                    continue
                pid = _first_string(item, (("metadata", "id"), ("id",)))
                if not pid:
                    continue
                projects.append(
                    {
                        "id": pid,
                        "name": _first_string(
                            item, (("metadata", "name"), ("name",))
                        )
                        or pid,
                        "region": _project_region(item),
                    }
                )
            return projects
        if isinstance(value, list):
            return [
                {"id": item_id, "name": item_id, "region": _project_region(item)}
                for item_id, item in (
                    (_first_string(item, (("metadata", "id"), ("id",))), item)
                    for item in value
                    if isinstance(item, dict)
                )
                if item_id
            ]
    if last_error is not None:
        raise last_error
    return []


def _recognized(value: Any) -> bool:
    """`{}` is the documented empty listing; a dict with a known key carries items."""
    if not isinstance(value, dict):
        return isinstance(value, list)
    return value == {} or any(
        key in value for key in ("items", "clusters", "resources")
    )


def _int_or_none(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None


def _list_node_groups(binary: str, cluster_id: str) -> list[dict[str, Any]]:
    """Node groups for one cluster; an empty or failed listing is not fatal.

    The status block carries the node-group's real state: `state`
    (RUNNING, PROVISIONING, DEGRADED, ...), `node_count`,
    `ready_node_count`, and `target_node_count`. `spec.fixed_node_count`
    is only the *desired* count — a group stuck provisioning reports a
    fixed count but has no ready nodes, which is exactly the case the
    discovery must not mistake for a healthy cluster.
    """
    try:
        value = _run_json(
            [
                binary,
                "mk8s",
                "node-group",
                "list",
                "--parent-id",
                cluster_id,
                "--all",
                "--format",
                "json",
            ]
        )
    except SetupError:
        return []
    if not _recognized(value):
        return []
    result = []
    for item in _cluster_items(value):
        group_id = _first_string(item, (("metadata", "id"), ("id",)))
        if not group_id:
            continue
        result.append(
            {
                "id": group_id,
                "name": _first_string(item, (("metadata", "name"), ("name",)))
                or group_id,
                "fixed_node_count": _first_string(
                    item, (("spec", "fixed_node_count"), ("spec", "node_count"))
                ),
                "state": _first_string(item, (("status", "state"),)),
                "node_count": _first_string(item, (("status", "node_count"),)),
                "ready_node_count": _first_string(
                    item, (("status", "ready_node_count"),)
                ),
                "target_node_count": _first_string(
                    item, (("status", "target_node_count"),)
                ),
            }
        )
    return result


def _group_ready_nodes(group: dict[str, Any]) -> int | None:
    """Nodes that actually exist, preferring ready over desired counts."""
    for field in ("ready_node_count", "node_count", "fixed_node_count"):
        value = _int_or_none(group.get(field))
        if value is not None:
            return value
    return None


def _group_not_ready(group: dict[str, Any]) -> str | None:
    """A human-readable reason the group's nodes are not usable, if any."""
    state = group.get("state")
    if state and state != "RUNNING":
        return f"state {state}"
    ready = _int_or_none(group.get("ready_node_count"))
    target = _int_or_none(group.get("target_node_count"))
    if ready is not None and target is not None and ready < target:
        return f"{ready} of {target} nodes ready"
    node_count = _int_or_none(group.get("node_count"))
    if ready is not None and node_count is not None and ready < node_count:
        return f"{ready} of {node_count} nodes ready"
    return None


def _attach_node_groups(
    binary: str, cluster: dict[str, Any], notes: list[dict[str, str]]
) -> None:
    """A cluster without usable nodes can never finish installing; say so."""
    groups = _list_node_groups(binary, cluster["id"])
    cluster["node_groups"] = groups
    estimated = 0
    not_ready: list[str] = []
    for group in groups:
        ready = _group_ready_nodes(group)
        if ready is not None:
            estimated += ready
        reason = _group_not_ready(group)
        if reason:
            not_ready.append(f"{group['name']} ({reason})")
    cluster["estimated_nodes"] = estimated
    if not_ready:
        notes.append(
            {
                "code": "cluster_nodes_not_ready",
                "message": (
                    f"{cluster['name']} has node groups whose nodes are not "
                    "healthy yet: "
                    + "; ".join(not_ready)
                    + ". The spec's node count is a target, not a promise: "
                    "Datadog can deploy the Operator, but the install stays "
                    "pending and telemetry will not flow until the nodes are "
                    "ready."
                ),
            }
        )
    if not groups or estimated == 0:
        notes.append(
            {
                "code": "cluster_without_nodes",
                "message": (
                    f"{cluster['name']} has no usable nodes (no node groups, "
                    "none ready, or all with a fixed count of zero). Datadog "
                    "can deploy the Operator, but the install stays pending "
                    "until nodes exist and telemetry will not flow."
                ),
            }
        )


def _list_clusters(
    identity: dict[str, str],
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """List mk8s clusters across the tenant's projects.

    MCI reconciles every cluster in the tenant, and `mk8s cluster list`
    only accepts project parents, so the script lists the tenant's
    projects and each project's clusters. When project enumeration
    fails it falls back to the profile's own project, and the discovery
    output says so.
    """
    binary = "nebius"
    notes: list[dict[str, str]] = []
    try:
        projects = _list_projects(binary, identity["tenant_id"])
    except SetupError as error:
        projects = [{"id": identity["project_id"], "name": identity["project_id"]}]
        notes.append(
            {
                "code": "project_enumeration_failed",
                "message": (
                    "The tenant's projects could not be listed, so only the "
                    "profile's own project was scanned. "
                )
                + (error.as_dict().get("message") or ""),
            }
        )
    if not projects:
        projects = [{"id": identity["project_id"], "name": identity["project_id"]}]
        notes.append(
            {
                "code": "no_projects_visible",
                "message": (
                    "No projects were visible in the tenant, so only the "
                    "profile's own project was scanned."
                ),
            }
        )
    clusters: list[dict[str, Any]] = []
    seen: set[str] = set()
    failures: list[dict[str, Any]] = []
    for project in projects:
        try:
            value = _run_json(
                [
                    binary,
                    "mk8s",
                    "cluster",
                    "list",
                    "--parent-id",
                    project["id"],
                    "--all",
                    "--format",
                    "json",
                ]
            )
        except SetupError as error:
            failures.append(
                {"project_id": project["id"], "error": error.as_dict()}
            )
            continue
        if not _recognized(value):
            failures.append(
                {
                    "project_id": project["id"],
                    "unrecognized_response": True,
                }
            )
            continue
        for item in _cluster_items(value):
            normalized = _normalize_cluster(item, project, identity["tenant_id"])
            if normalized and normalized["id"] not in seen:
                seen.add(normalized["id"])
                _attach_node_groups(binary, normalized, notes)
                clusters.append(normalized)
    if failures and len(failures) == len(projects):
        raise SetupError(
            "nebius_cluster_list_failed",
            (
                "The Nebius CLI's cluster listing failed for every project; "
                "no automatic retry was attempted."
            ),
            details={"failures": failures},
        )
    if failures:
        notes.append(
            {
                "code": "some_projects_failed",
                "message": (
                    f"{len(failures)} of {len(projects)} projects could not "
                    "be scanned; their clusters may be missing from the "
                    "list."
                ),
            }
        )
    return clusters, notes
