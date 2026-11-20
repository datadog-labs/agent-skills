"""CoreWeave CLI reads for the agent fleet installation flow."""

from __future__ import annotations

import json
import shutil
import subprocess
from typing import Any

from _common import SetupError, sanitize_text

CWIC_REPO = "https://github.com/coreweave/cwic"
CWIC_TOKEN_DOC = "https://docs.coreweave.com/products/cks/reference/cks-api"
AUTH_TIMEOUT = 60
COMMAND_TIMEOUT = 120


def _run(
    command: list[str], *, timeout: int = COMMAND_TIMEOUT
) -> subprocess.CompletedProcess[str]:
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
            "A CoreWeave CLI command timed out; no automatic retry was attempted.",
            command=command,
            stderr=sanitize_text(
                exc.stderr.decode() if isinstance(exc.stderr, bytes) else exc.stderr
            ),
            details={"timeout_seconds": timeout},
        ) from exc
    if result.returncode:
        raise SetupError(
            "coreweave_command_failed",
            "A CoreWeave CLI command failed; no automatic retry was attempted.",
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
            "invalid_coreweave_json",
            "A CoreWeave CLI command returned invalid JSON.",
            command=command,
            details={"stdout_bytes": len(result.stdout.encode())},
        ) from exc


def _check_cli() -> dict[str, Any]:
    """The CoreWeave CLI (cwic) must be installed and logged in."""
    if shutil.which("cwic") is None:
        raise SetupError(
            "cwic_cli_missing",
            "Install the CoreWeave CLI (cwic) before installing the Agent fleet.",
            recoverable=False,
            details={
                "docs": CWIC_REPO,
                "install_hint": (
                    "See the cwic README for the install steps for your "
                    "platform, then restart the terminal or run "
                    "`exec -l $SHELL`."
                ),
                "authenticate_command": "cwic auth login",
            },
        )
    # `-o json`: the default table output is not machine-readable, and the
    # org id and roles of the session are needed to pair it with the org
    # the Datadog integration holds.
    whoami = _run(
        ["cwic", "auth", "whoami", "-o", "json"], timeout=AUTH_TIMEOUT
    )
    principal = None
    try:
        principal = json.loads(whoami.stdout) if whoami.stdout.strip() else None
    except json.JSONDecodeError:
        principal = None
    if not isinstance(principal, dict):
        principal = {"raw": sanitize_text(whoami.stdout)}
    # `cwic auth whoami` exits 0 even when logged out ("Not logged in"),
    # so an unparseable or uid-less principal is a missing session — fail
    # here rather than continuing into reads that fail late and
    # misleadingly.
    if not principal.get("uid"):
        raise SetupError(
            "cwic_not_logged_in",
            (
                "The CoreWeave CLI has no logged-in session. Run "
                "`cwic auth login` (it opens a browser; the token page is "
                "https://console.coreweave.com/tokens) and rerun."
            ),
            recoverable=False,
            details={
                "authenticate_command": "cwic auth login",
                "whoami_output": sanitize_text(whoami.stdout),
            },
        )
    roles = principal.get("roles") if isinstance(principal.get("roles"), list) else []
    org_id = principal.get("org_id") if isinstance(principal.get("org_id"), str) else None
    return {
        "principal": principal,
        # Non-secret identity used to pair the session with the org the
        # Datadog integration holds (see _integration_orgs in _datadog.py):
        # the org this cwic login belongs to, and whether it can install
        # (CKS Admin is the role MCI needs).
        "org_id": org_id,
        "cks_admin": any("CKS Admin" in str(role) for role in roles),
    }


def _first_string(value: Any, keys: tuple[str, ...]) -> str | None:
    if not isinstance(value, dict):
        return None
    for key in keys:
        item = value.get(key)
        if isinstance(item, str) and item:
            return item
    return None


_CLUSTER_LIST_KEYS = ("clusters", "items", "data", "resources")


def _cluster_items(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    if not isinstance(value, dict):
        return []
    for key in _CLUSTER_LIST_KEYS:
        items = value.get(key)
        if isinstance(items, list):
            return [item for item in items if isinstance(item, dict)]
    return []


def _empty_inventory(value: Any) -> bool:
    """`[]` or `{"clusters": []}` (any known key): a tenancy with no clusters."""
    if value == []:
        return True
    return isinstance(value, dict) and any(
        value.get(key) == [] for key in _CLUSTER_LIST_KEYS
    )


def _normalize_cluster(item: dict[str, Any]) -> dict[str, Any] | None:
    # The rule's `name` tag is the cluster ID (UUID), not the human name.
    cluster_id = _first_string(
        item, ("id", "cluster_id", "clusterId", "name", "clusterName", "cluster_name")
    )
    if not cluster_id:
        return None
    name = (
        _first_string(item, ("name", "clusterName", "cluster_name"))
        or cluster_id
    )
    return {
        "id": cluster_id,
        "name": name,
        "region": _first_string(item, ("zone", "availabilityZone", "region")),
        "status": _first_string(item, ("status", "state", "phase")),
        # Private endpoints cannot be installed on; surface the flag.
        "public": item.get("public"),
        # CKS clusters carry no labels; the rule can only match on the
        # cluster's own tags (name, zone).
        "labels": {},
    }


def _parse_table_clusters(stdout: str) -> list[dict[str, Any]]:
    """Fallback: parse `cwic cluster get` table output, first column = name."""
    clusters = []
    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        lowered = line.lower()
        if lowered.startswith(("name", "cluster", "id")) and "\t" not in line:
            # Header row.
            continue
        name = line.split()[0]
        if name and name not in {cluster["id"] for cluster in clusters}:
            clusters.append(
                {
                    "id": name,
                    "name": name,
                    "region": None,
                    "status": None,
                    "labels": {},
                }
            )
    return clusters


def _list_clusters() -> list[dict[str, Any]]:
    """List CKS clusters through cwic, tolerating its output variations."""
    attempts: list[dict[str, Any]] = []
    for extra in (["-o", "json"], ["--output", "json"], []):
        try:
            result = _run(["cwic", "cluster", "get", *extra])
        except SetupError as error:
            attempts.append(error.as_dict())
            continue
        stdout = result.stdout
        try:
            value = json.loads(stdout) if stdout.strip() else None
        except json.JSONDecodeError:
            value = None
        if isinstance(value, (list, dict)):
            raw_items = _cluster_items(value)
            clusters = [
                normalized
                for item in raw_items
                if (normalized := _normalize_cluster(item)) is not None
            ]
            # Recognized cluster objects (or a recognizable empty list of
            # them) settle it; a JSON shape with none of the known keys
            # falls through to the next attempt. JSON is never parsed as a
            # table: its first column would turn `[]` or other JSON
            # punctuation into fictitious, selectable clusters.
            if clusters or raw_items or _empty_inventory(value):
                return clusters
            attempts.append({"stdout": sanitize_text(stdout)})
            continue
        table = _parse_table_clusters(stdout)
        if table:
            return table
        attempts.append({"stdout": sanitize_text(stdout)})
    raise SetupError(
        "coreweave_cluster_list_unparsed",
        (
            "The CoreWeave CLI listed clusters in a shape this script "
            "could not parse. Run `cwic cluster get` and report the "
            "output shape, or pass `--query` yourself."
        ),
        recoverable=False,
        details={"attempts": attempts},
    )


# ---------------------------------------------------------------------------
# Node pools: health, readiness, and GPU detection per cluster.
#
# `cwic` has no node-pool listing that works without a kubeconfig, but
# `cwic cks cluster get <cluster> -o json` returns the cluster resource
# with its node pools, and `cwic cluster describe <cluster>` prints them
# for humans. Both shapes vary by cwic version, so every read here is
# tolerant: a cluster whose pools cannot be read is reported with a
# `node_pools_unread` note (health unknown) rather than failing the
# discovery.


_POOL_LIST_KEYS = (
    "nodePools",
    "nodepools",
    "node_pools",
    "pools",
    "nodePoolList",
)


def _pool_items(value: Any) -> list[dict[str, Any]] | None:
    """Node-pool dicts from a cluster resource, or None when absent."""
    candidates: list[Any] = [value]
    if isinstance(value, dict):
        for key in ("spec", "status", "cluster", "data", "attributes"):
            nested = value.get(key)
            if isinstance(nested, dict):
                candidates.append(nested)
        candidates.extend(
            value.get(key) for key in _POOL_LIST_KEYS if key in value
        )
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        for key in _POOL_LIST_KEYS:
            items = candidate.get(key)
            if isinstance(items, list):
                pools = [item for item in items if isinstance(item, dict)]
                if pools:
                    return pools
    return None


def _pool_field(
    pool: dict[str, Any], paths: tuple[tuple[str, ...], ...]
) -> Any:
    for path in paths:
        node: Any = pool
        for key in path:
            node = node.get(key) if isinstance(node, dict) else None
        if node is not None and node != "":
            return node
    return None


def _int_or_none(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None


def _normalize_pool(pool: dict[str, Any]) -> dict[str, Any] | None:
    name = _pool_field(
        pool, (("name",), ("metadata", "name"), ("nodePoolName",), ("id",))
    )
    if not isinstance(name, str) or not name:
        return None
    return {
        "name": name,
        "instance_type": _pool_field(
            pool,
            (
                ("spec", "instanceType"),
                ("spec", "instance_type"),
                ("spec", "machineType"),
                ("spec", "machine_type"),
                ("spec", "nodeType"),
                ("spec", "node_type"),
                ("spec", "flavor"),
                ("instanceType",),
                ("instance_type",),
                ("machineType",),
                ("machine_type",),
                ("nodeType",),
                ("node_type",),
                ("flavor",),
                ("type",),
            ),
        ),
        "state": _pool_field(
            pool,
            (
                ("status", "state"),
                ("status", "phase"),
                ("status", "status"),
                ("state",),
                ("phase",),
            ),
        ),
        "replicas": _pool_field(
            pool,
            (
                ("spec", "replicas"),
                ("spec", "count"),
                ("spec", "minReplicas"),
                ("replicas",),
                ("count",),
                ("desiredReplicas",),
                ("status", "replicas"),
            ),
        ),
        "ready_replicas": _pool_field(
            pool,
            (
                ("status", "readyReplicas"),
                ("status", "ready_replicas"),
                ("status", "ready"),
                ("status", "availableReplicas"),
                ("status", "available_replicas"),
                ("readyReplicas",),
                ("ready",),
            ),
        ),
    }


_GPU_MARKERS = (
    "gpu",
    "a100",
    "a10g",
    "a16",
    "h100",
    "h200",
    "b200",
    "gb200",
    "gb300",
    "l40",
    "rtx",
    "hgx",
    "mi300",
    "mi250",
    "v100",
    "p100",
    "a6000",
)


def _pool_is_gpu(pool: dict[str, Any]) -> bool:
    """A node pool counts as GPU when its type or name names a GPU flavor.

    CoreWeave GPU flavors put the accelerator in the instance type (for
    example gpu-h100-hgx-80gb); CPU flavors (for example cpu-slurm) do
    not match any marker.
    """
    haystack = " ".join(
        str(pool.get(field) or "")
        for field in ("name", "instance_type")
    ).lower()
    return any(marker in haystack for marker in _GPU_MARKERS)


def _pool_ready_nodes(pool: dict[str, Any]) -> int | None:
    """Nodes that actually exist, preferring ready over desired counts."""
    for field in ("ready_replicas", "replicas"):
        value = _int_or_none(pool.get(field))
        if value is not None:
            return value
    return None


_RUNNING_STATES = {
    "running",
    "ready",
    "active",
    "healthy",
    "available",
    "provisioned",
}


def _pool_not_ready(pool: dict[str, Any]) -> str | None:
    """A human-readable reason the pool's nodes are not usable, if any."""
    state = pool.get("state")
    if isinstance(state, str) and state.strip():
        if state.strip().lower() not in _RUNNING_STATES:
            return f"state {state}"
    ready = _int_or_none(pool.get("ready_replicas"))
    target = _int_or_none(pool.get("replicas"))
    if ready is not None and target is not None and ready < target:
        return f"{ready} of {target} nodes ready"
    return None


def _describe_cluster(cluster: dict[str, Any]) -> Any:
    """The cluster resource with its node pools, or None when unreadable.

    Read-only: `cwic cks cluster get` fetches the resource through the
    CKS API with the cwic token, so no kubeconfig is generated and no
    cluster-local credentials are touched. Every shape variation is
    tolerated; a failure here costs the health check, not the discovery.
    """
    targets = [cluster["id"]]
    if cluster.get("name") and cluster["name"] != cluster["id"]:
        targets.append(cluster["name"])
    for target in targets:
        for extra in (["-o", "json"], ["--output", "json"]):
            try:
                result = _run(["cwic", "cks", "cluster", "get", target, *extra])
            except SetupError:
                continue
            try:
                return json.loads(result.stdout) if result.stdout.strip() else None
            except json.JSONDecodeError:
                continue
    return None


def _attach_node_pools(
    cluster: dict[str, Any],
    notes: list[dict[str, str]],
) -> None:
    """Attach node-pool health, node estimates, and GPU detection."""
    described = _describe_cluster(cluster)
    pools_raw = _pool_items(described)
    if pools_raw is None:
        cluster["node_pools"] = None
        cluster["gpu"] = None
        notes.append(
            {
                "code": "node_pools_unread",
                "message": (
                    f"{cluster['name']}'s node pools could not be read, so "
                    "their health and whether the cluster has GPU nodes is "
                    "unknown. The install can proceed; check the node pools "
                    "in the CoreWeave console if telemetry does not arrive."
                ),
            }
        )
        return
    pools = [
        normalized
        for pool in pools_raw
        if (normalized := _normalize_pool(pool)) is not None
    ]
    cluster["node_pools"] = pools
    cluster["gpu"] = any(_pool_is_gpu(pool) for pool in pools)
    estimated = 0
    not_ready: list[str] = []
    for pool in pools:
        ready = _pool_ready_nodes(pool)
        if ready is not None:
            estimated += ready
        reason = _pool_not_ready(pool)
        if reason:
            not_ready.append(f"{pool['name']} ({reason})")
    cluster["estimated_nodes"] = estimated
    if not_ready:
        notes.append(
            {
                "code": "cluster_nodes_not_ready",
                "message": (
                    f"{cluster['name']} has node pools whose nodes are not "
                    "healthy yet: "
                    + "; ".join(not_ready)
                    + ". The pool's replica count is a target, not a "
                    "promise: Datadog can deploy the Operator, but the "
                    "install stays pending and telemetry will not flow "
                    "until the nodes are ready."
                ),
            }
        )
    if not pools or estimated == 0:
        notes.append(
            {
                "code": "cluster_without_nodes",
                "message": (
                    f"{cluster['name']} has no usable nodes (no node pools, "
                    "none ready, or all with a replica count of zero). "
                    "Datadog can deploy the Operator, but the install stays "
                    "pending until nodes exist and telemetry will not flow."
                ),
            }
        )


def _list_clusters_with_health() -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """List CKS clusters with node-pool health and GPU detection attached."""
    clusters = _list_clusters()
    notes: list[dict[str, str]] = []
    for cluster in clusters:
        _attach_node_pools(cluster, notes)
    return clusters, notes
