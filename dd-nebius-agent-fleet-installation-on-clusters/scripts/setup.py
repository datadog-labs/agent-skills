#!/usr/bin/env python3
"""Datadog agent fleet installation on Nebius Managed Kubernetes clusters.

Discovers the Nebius tenancy already integrated with Datadog and its mk8s
clusters, then creates the managed install rule. Datadog's server-side
Operator rollout installs the Agent fleet from there; this script does not
configure or verify anything after the rule exists. Container monitoring
and APM are on by default.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from _common import SetupError  # noqa: E402
from _datadog import (  # noqa: E402
    PROVIDER,
    RESOURCE_TYPE,
    _check_env_keys,
    _check_required_access,
    _check_rule_coverage,
    _cluster_name_from_resource_id,
    _create_rule,
    _delete_rule,
    _find_rule,
    _list_accounts,
    _list_api_keys,
    _list_rules,
    _rule_resources,
    _update_rule,
    _validate_pup,
)
from _nebius import _check_cli, _list_clusters  # noqa: E402
from _rule_query import RuleQuery, compose_rule_query, parse_rule_query  # noqa: E402

# `nebius-cloud:mk8s:<region>:<tenant>:cluster/<id>`; the id goes into the
# rule's query verbatim, so anything else is refused rather than quoted.
RESOURCE_ID_PATTERN = re.compile(r'nebius-cloud:mk8s:[^\s":()\\]+:[^\s":()\\]+:cluster/[^\s":()\\]+')


def emit(value: dict[str, Any]) -> None:
    print(json.dumps(value, indent=2, sort_keys=True, default=str))


def _resolve_account(
    accounts: list[dict[str, Any]],
    identity: dict[str, str],
    *,
    account_id: str | None,
    pup: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not accounts:
        # Scoped to the session's own org and site, never to Datadog as a
        # whole: the same Nebius tenant can be integrated under a
        # different org or site, and reading this error as "no
        # integration exists anywhere" is how duplicate integrations get
        # registered.
        site = (pup or {}).get("site") or "datadoghq.com"
        org = (pup or {}).get("org")
        where = f"{org} on {site}" if org else f"the session's org on {site}"
        raise SetupError(
            "integration_not_configured",
            (
                "No Datadog Nebius integration account exists in this "
                f"organization ({where}) — the error is scoped to that "
                "org, not to Datadog as a whole. Re-check that the session "
                "is signed into the intended Datadog org and site (pup "
                "auth status, DD_SITE) before setting up an integration "
                "(the dd-nebius-integration skill)."
            ),
            recoverable=False,
            details={"site": site, "org": org},
        )
    if account_id:
        for account in accounts:
            if account.get("id") != account_id:
                continue
            # Clusters are discovered through the CLI profile's tenant, so a
            # rule for another tenant's account would be built from the
            # wrong clusters.
            if account.get("tenant_id") != identity["tenant_id"]:
                raise SetupError(
                    "account_tenant_mismatch",
                    (
                        "The selected account belongs to a different Nebius "
                        "tenant. Switch the Nebius CLI profile to that tenant "
                        "and rerun."
                    ),
                    recoverable=False,
                    details={
                        "account_id": account_id,
                        "account_tenant_id": account.get("tenant_id"),
                        "cli_tenant_id": identity["tenant_id"],
                    },
                )
            return account
        raise SetupError(
            "account_not_found",
            f"No Datadog Nebius account has the id {account_id}.",
            recoverable=False,
            details={"available_accounts": accounts},
        )
    matches = [
        account
        for account in accounts
        if account.get("tenant_id") == identity["tenant_id"]
    ]
    if len(matches) > 1:
        raise SetupError(
            "multiple_accounts_for_tenant",
            (
                "Multiple Datadog Nebius accounts use this tenant. Pass "
                "--account-id to select one, or re-check the Nebius CLI "
                "profile's tenant (`nebius profile list`)."
            ),
            recoverable=False,
            details={"matching_accounts": matches},
        )
    if not matches:
        raise SetupError(
            "no_account_for_tenant",
            (
                "No Datadog Nebius account uses this tenant. Complete the "
                "Nebius integration for this tenant first (the "
                "dd-nebius-integration skill), or switch the Nebius CLI "
                "profile to the integrated tenant."
            ),
            recoverable=False,
            details={
                "cli_tenant_id": identity["tenant_id"],
                "available_accounts": accounts,
            },
        )
    return matches[0]


def _label_tags(cluster: dict[str, Any]) -> dict[str, str]:
    tags = dict(cluster.get("labels") or {})
    if cluster.get("region"):
        tags.setdefault("region", cluster["region"])
    return tags


def _isolating_query(
    clusters: list[dict[str, Any]], selected: list[dict[str, Any]]
) -> str | None:
    """One tag that every selected cluster has and no other cluster has."""
    if not selected:
        return None
    others = [cluster for cluster in clusters if cluster not in selected]
    shared: dict[str, str] | None = None
    for cluster in selected:
        tags = _label_tags(cluster)
        if shared is None:
            shared = tags
        else:
            shared = {
                key: value
                for key, value in tags.items()
                if shared.get(key) == value
            }
    if not shared or others is None:
        return None
    for key in sorted(shared):
        value = shared[key]
        if any(_label_tags(cluster).get(key) == value for cluster in others):
            continue
        return f"{key}:{value}"
    return None


def _resolve_query(
    clusters: list[dict[str, Any]],
    *,
    query: str | None,
    cluster_selection: str | None,
) -> tuple[str | None, list[dict[str, Any]], list[dict[str, str]]]:
    """Returns (query or None for all clusters, selected clusters, warnings)."""
    warnings: list[dict[str, str]] = []
    if query:
        return query, clusters, warnings
    if not cluster_selection:
        warnings.append(
            {
                "code": "all_clusters_covered",
                "message": (
                    "No query was given, so the rule covers every cluster in "
                    "the tenancy, including clusters created later."
                ),
            }
        )
        return None, clusters, warnings
    requested = [
        item.strip() for item in cluster_selection.split(",") if item.strip()
    ]
    selected = [
        cluster
        for cluster in clusters
        if cluster["id"] in requested or cluster["name"] in requested
    ]
    missing = [
        item
        for item in requested
        if item not in {cluster["id"] for cluster in selected}
        and item not in {cluster["name"] for cluster in selected}
    ]
    if missing:
        raise SetupError(
            "clusters_not_found",
            "Some requested clusters were not found in the tenancy.",
            recoverable=False,
            details={
                "missing": missing,
                "available": [
                    {"id": cluster["id"], "name": cluster["name"]}
                    for cluster in clusters
                ],
            },
        )
    if not selected:
        raise SetupError(
            "clusters_not_selected",
            "No clusters were selected.",
            recoverable=False,
        )
    isolating = _isolating_query(clusters, selected)
    if not isolating:
        if len(selected) == len(clusters):
            # Selecting every cluster is the all-clusters rule.
            warnings.append(
                {
                    "code": "all_clusters_covered",
                    "message": (
                        "Every cluster in the tenancy was selected, so the "
                        "rule covers all of them, including clusters created "
                        "later."
                    ),
                }
            )
            return None, selected, warnings
        raise SetupError(
            "no_isolating_query",
            (
                "The selected clusters share no label (or region) that the "
                "other clusters in the tenancy lack, so no rule query can "
                "cover exactly the selection. Add a unique label to the "
                "selected clusters, or pass --query yourself."
            ),
            details={
                "selected": [
                    {"id": cluster["id"], "labels": cluster.get("labels")}
                    for cluster in selected
                ],
                "all_clusters": [
                    {"id": cluster["id"], "labels": cluster.get("labels")}
                    for cluster in clusters
                ],
            },
        )
    return isolating, selected, warnings


def _resolve_api_key_uuid(
    *, api_key_name: str | None, api_key_uuid: str | None
) -> tuple[str, dict[str, Any]]:
    # An explicit UUID is the recovery path when the key list is denied, so
    # it must not depend on that list.
    if api_key_uuid:
        return api_key_uuid, {"id": api_key_uuid, "name": None}
    keys = _list_api_keys()
    if not keys:
        raise SetupError(
            "api_keys_unavailable",
            (
                "No API keys are visible to this session. Pass "
                "--api-key-uuid with the UUID of an API key from "
                "Organization Settings > API Keys."
            ),
        )
    if not api_key_name:
        raise SetupError(
            "api_key_required",
            "Select the API key the Agents should use.",
            details={
                "available_api_keys": keys,
                "remediation": "Pass --api-key-name or --api-key-uuid.",
            },
        )
    matches = [key for key in keys if key.get("name") == api_key_name]
    if len(matches) != 1:
        raise SetupError(
            "api_key_not_found",
            f"No single API key is named {api_key_name}.",
            details={"available_api_keys": keys},
        )
    return matches[0]["id"], matches[0]


def _preflight(account_id: str | None) -> dict[str, Any]:
    """Read-only checks shared by discovery and install.

    The ordering is enforced here, not left to the caller: the tenancy is
    identified and paired with its Datadog account before any other
    provider read runs. The Nebius CLI profile's tenant is read first and
    every integration account is enumerated and matched against it in
    `_resolve_account`, so an existing matching account is found — and a
    diverging one refused — before required access, rules, or clusters are
    read. A wrong org or tenant therefore stops the run before anything
    else can report a confidently wrong answer: the install makes Datadog
    act through the integration account while discovery previews clusters
    through the local Nebius CLI session, so a divergence would install a
    different tenancy than the one shown.
    """
    pup = _validate_pup()
    env_keys = _check_env_keys(pup)
    identity = _check_cli()
    accounts = _list_accounts()
    account = _resolve_account(
        accounts, identity, account_id=account_id, pup=pup
    )
    required_access = _check_required_access()
    rules = _list_rules()
    existing_rule = _find_rule(rules, account["id"])
    clusters, cluster_notes = _list_clusters(identity)
    return {
        "pup": pup,
        "env_keys": env_keys,
        "identity": identity,
        "required_access": required_access,
        "account": account,
        "existing_rule": existing_rule,
        "clusters": clusters,
        "cluster_notes": cluster_notes,
    }


def _coverage_row(
    cluster_id: str,
    name: str | None,
    resource_id: str | None,
    resource: dict[str, Any] | None,
    excluded: dict[str, str],
    included: dict[str, str],
    *,
    discovered: bool,
) -> dict[str, Any]:
    state = (resource or {}).get("state")
    if cluster_id in excluded:
        coverage = "excluded"
    elif (resource and state != "deleting") or cluster_id in included:
        # A cluster the query names explicitly is included even before the
        # rule lists it.
        coverage = "included"
    else:
        coverage = "not_covered"
    return {
        "id": cluster_id,
        "name": name or cluster_id,
        "coverage": coverage,
        "state": state,
        "reason": (resource or {}).get("reason"),
        # MCI's own id, when it has one for the cluster, beats the derived one.
        "resource_id": (resource or {}).get("resource_id")
        or excluded.get(cluster_id)
        or included.get(cluster_id)
        or resource_id,
        "discovered": discovered,
    }


def _rule_coverage(
    rule: dict[str, Any],
    clusters: list[dict[str, Any]],
    resources: list[dict[str, Any]],
) -> dict[str, Any]:
    """Sort the tenancy's clusters by what the existing rule does with them.

    Included: the rule matches the cluster now (one of its resources, unless
    it is being removed) or its query names it in a top-level `@_key`
    include. Excluded: the query names it in a top-level `-@_key` clause.
    Not covered: every other cluster. Beyond those exact names, the rule's
    resources, not its label query, say what it matches.
    """
    parsed = parse_rule_query(rule.get("query"))
    excluded = {
        _cluster_name_from_resource_id(value) or value: value
        for value in parsed.excludes
    }
    included = {
        _cluster_name_from_resource_id(value) or value: value
        for value in parsed.includes
    }
    by_cluster: dict[str, dict[str, Any]] = {}
    for resource in resources:
        cluster_id = _cluster_name_from_resource_id(resource.get("resource_id") or "")
        if cluster_id:
            by_cluster[cluster_id] = resource
    rows = [
        _coverage_row(
            cluster["id"],
            cluster.get("name"),
            cluster.get("resource_id"),
            by_cluster.get(cluster["id"]),
            excluded,
            included,
            discovered=True,
        )
        for cluster in clusters
    ]
    # Clusters the rule knows about that discovery did not list (another
    # project, or deleted since), so they can still be removed or re-added.
    listed = {cluster["id"] for cluster in clusters}
    for cluster_id in sorted((set(by_cluster) | set(excluded) | set(included)) - listed):
        rows.append(
            _coverage_row(
                cluster_id,
                None,
                None,
                by_cluster.get(cluster_id),
                excluded,
                included,
                discovered=False,
            )
        )
    counts = {"included": 0, "excluded": 0, "not_covered": 0}
    for row in rows:
        counts[row["coverage"]] += 1
    return {
        "rule_id": rule.get("id"),
        "query": rule.get("query") or "",
        "clusters": rows,
        "counts": counts,
    }


def _discover(account_id: str | None) -> dict[str, Any]:
    state = _preflight(account_id)
    warnings: list[dict[str, str]] = list(state.get("cluster_notes") or []) + list(
        (state.get("env_keys") or {}).get("warnings") or []
    )
    rule_coverage = None
    if state["existing_rule"] and state["existing_rule"].get("id"):
        resources: list[dict[str, Any]] = []
        try:
            resources = _rule_resources(state["existing_rule"]["id"])
        except SetupError as error:
            warnings.append(
                {
                    "code": "rule_resources_not_listed",
                    "message": (
                        "The existing rule's clusters could not be read, so "
                        "the coverage table shows only what its query "
                        "excludes. "
                    )
                    + (error.as_dict().get("message") or ""),
                }
            )
        rule_coverage = _rule_coverage(
            state["existing_rule"], state["clusters"], resources
        )
    api_keys = []
    try:
        api_keys = _list_api_keys()
    except SetupError as error:
        warnings.append(
            {
                "code": "api_keys_not_listed",
                "message": (
                    "API keys could not be listed; the install step will need "
                    "--api-key-uuid. "
                )
                + (error.as_dict().get("message") or ""),
            }
        )
    return {
        "status": "ready",
        "provider": PROVIDER,
        "resource_type": RESOURCE_TYPE,
        "datadog_session": {
            "site": (state["pup"] or {}).get("site"),
            "org": (state["pup"] or {}).get("org"),
            "env_keys": {
                "present": bool(state.get("env_keys")),
                "org": (state.get("env_keys") or {}).get("org"),
            },
        },
        "tenancy": {
            "tenant_id": state["identity"]["tenant_id"],
            "account_id": state["account"]["id"],
            "account_name": state["account"]["name"],
        },
        "required_role": state["required_access"],
        "clusters": state["clusters"],
        "existing_rule": state["existing_rule"],
        "rule_coverage": rule_coverage,
        "api_keys": api_keys,
        "warnings": warnings,
    }


def _install(
    *,
    account_id: str | None,
    query: str | None,
    clusters: str | None,
    api_key_name: str | None,
    api_key_uuid: str | None,
) -> dict[str, Any]:
    """Create (or reuse) the account's install rule and stop there.

    Datadog's server-side Operator rollout takes over once the rule exists;
    this script does not wait for it, push anything, or verify telemetry.
    """
    state = _preflight(account_id)
    account = state["account"]
    rule_query, selected, warnings = _resolve_query(
        state["clusters"], query=query, cluster_selection=clusters
    )
    warnings = (
        list(state.get("cluster_notes") or [])
        + list((state.get("env_keys") or {}).get("warnings") or [])
        + warnings
    )
    if state["existing_rule"]:
        # Reusing a rule with other coverage would cover clusters outside
        # the selection, or miss selected clusters.
        _check_rule_coverage(state["existing_rule"], rule_query)
    key_uuid, key_info = _resolve_api_key_uuid(
        api_key_name=api_key_name, api_key_uuid=api_key_uuid
    )
    if state["existing_rule"]:
        rule = state["existing_rule"]
        warnings.append(
            {
                "code": "rule_already_exists",
                "message": (
                    "An install rule already exists for this account, so no "
                    "new rule was created; its coverage was reused."
                ),
            }
        )
    else:
        # Rule writes ride the session's own token when it carries the
        # agent-install scope; otherwise they fall back to a full API
        # user's keys (exported DD_API_KEY/DD_APP_KEY when present, else a
        # one-shot pair minted through Pup and deleted right after). The
        # secret values never leave this process.
        rule, cleanup_warnings = _create_rule(
            account["id"], key_uuid, rule_query, state["pup"]
        )
        warnings.extend(cleanup_warnings)
    rule_id = rule.get("id")
    if not rule_id:
        raise SetupError(
            "rule_id_missing",
            "The install rule has no id.",
            details={"rule": rule},
        )
    return {
        "status": "rule_created",
        "provider": PROVIDER,
        "resource_type": RESOURCE_TYPE,
        "datadog_session": {
            "site": (state["pup"] or {}).get("site"),
            "org": (state["pup"] or {}).get("org"),
            "env_keys": {
                "present": bool(state.get("env_keys")),
                "org": (state.get("env_keys") or {}).get("org"),
            },
        },
        "rule": rule,
        "rule_id": rule_id,
        "rule_query": rule_query,
        "selected_clusters": [
            {"id": cluster["id"], "name": cluster["name"]} for cluster in selected
        ],
        "api_key": key_info,
        "note": (
            "Datadog rolls the Operator out to the covered clusters itself. "
            "Follow progress with --status <RULE_ID> or in Fleet Automation."
        ),
        "warnings": warnings,
    }


def _split_selection(selection: str | None) -> list[str]:
    return [item.strip() for item in (selection or "").split(",") if item.strip()]


def _select_rows(
    rows: list[dict[str, Any]], requested: list[str]
) -> list[dict[str, Any]]:
    """The coverage rows that the requested cluster ids or names select."""
    selected = [row for row in rows if row["id"] in requested or row["name"] in requested]
    known = {row["id"] for row in selected} | {row["name"] for row in selected}
    missing = [item for item in requested if item not in known]
    if missing:
        raise SetupError(
            "clusters_not_found",
            "Some requested clusters were not found in the tenancy or the rule.",
            recoverable=False,
            details={
                "missing": missing,
                "available": [{"id": row["id"], "name": row["name"]} for row in rows],
            },
        )
    return selected


def _row_key(row: dict[str, Any]) -> str:
    if not RESOURCE_ID_PATTERN.fullmatch(row.get("resource_id") or ""):
        raise SetupError(
            "cluster_resource_id_unknown",
            (
                f"The MCI resource id of {row['id']} is unknown (its project's "
                "region could not be read) or malformed, so the rule's query "
                "cannot name it."
            ),
            recoverable=False,
            details={"cluster": row["id"], "resource_id": row.get("resource_id")},
        )
    return row["resource_id"]


def _plan_update(
    current: RuleQuery,
    add_rows: list[dict[str, Any]],
    remove_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    """Exact includes and excludes after adding and removing clusters.

    An added cluster leaves the exclusions and, unless the label query
    already covers every cluster, joins the includes. A removed cluster
    leaves the includes and joins the exclusions, even when the rule does
    not list it yet, so a later label match cannot bring it back.
    """
    includes = set(current.includes)
    excludes = set(current.excludes)

    def without(values: set[str], cluster_id: str) -> set[str]:
        return {
            value
            for value in values
            if (_cluster_name_from_resource_id(value) or value) != cluster_id
        }

    added, removed, unchanged = [], [], []
    for row in add_rows:
        if row["coverage"] == "included":
            unchanged.append({"id": row["id"], "reason": "already_included"})
            continue
        if current.base == "" and row["coverage"] != "excluded":
            # The query already matches every cluster; the rule lists it once
            # Datadog reconciles.
            unchanged.append({"id": row["id"], "reason": "already_matched_by_query"})
            continue
        key = _row_key(row)
        excludes = without(excludes, row["id"])
        if current.base != "":
            includes.add(key)
        added.append(row)
    for row in remove_rows:
        if row["coverage"] == "excluded":
            unchanged.append({"id": row["id"], "reason": "already_excluded"})
            continue
        key = _row_key(row)
        includes = without(includes, row["id"])
        excludes.add(key)
        removed.append(row)
    return {
        "query": RuleQuery(current.base, tuple(sorted(includes)), tuple(sorted(excludes))),
        "added": added,
        "removed": removed,
        "unchanged": unchanged,
    }


def _update(
    *,
    account_id: str | None,
    rule_id: str,
    add_clusters: str | None,
    remove_clusters: str | None,
    dry_run: bool,
) -> dict[str, Any]:
    """Add clusters to or remove them from the account's existing rule.

    The rule's label query stays; only its exact exceptions change:
    `(<label query> OR @_key:<added>) AND -@_key:<removed>`. Datadog
    uninstalls the Agent from removed clusters and installs it on added
    ones itself; nothing is pushed or verified here.
    """
    requested_add = _split_selection(add_clusters)
    requested_remove = _split_selection(remove_clusters)
    if not requested_add and not requested_remove:
        raise SetupError(
            "update_selection_required",
            "Pass --add-clusters, --remove-clusters, or both.",
            recoverable=False,
        )
    state = _preflight(account_id)
    rule = state["existing_rule"]
    if not rule or rule.get("id") != rule_id:
        raise SetupError(
            "rule_not_found_for_account",
            f"Rule {rule_id} is not this account's install rule.",
            recoverable=False,
            details={"account_rule_id": (rule or {}).get("id")},
        )
    if rule.get("is_frozen"):
        raise SetupError(
            "rule_frozen",
            (
                "The rule is frozen to a captured set of clusters, so its "
                "query no longer decides coverage. Change it in Fleet "
                "Automation instead."
            ),
            recoverable=False,
            details={"rule_id": rule_id},
        )
    before = _rule_coverage(rule, state["clusters"], _rule_resources(rule_id))
    add_rows = _select_rows(before["clusters"], requested_add)
    remove_rows = _select_rows(before["clusters"], requested_remove)
    both = {row["id"] for row in add_rows} & {row["id"] for row in remove_rows}
    if both:
        raise SetupError(
            "clusters_added_and_removed",
            "The same clusters were passed to both --add-clusters and --remove-clusters.",
            recoverable=False,
            details={"clusters": sorted(both)},
        )
    plan = _plan_update(parse_rule_query(rule.get("query")), add_rows, remove_rows)
    added_ids = {row["id"] for row in plan["added"]}
    removed_ids = {row["id"] for row in plan["removed"]}
    result: dict[str, Any] = {
        "rule_id": rule_id,
        "query_before": rule.get("query") or "",
        "added": [{"id": row["id"], "name": row["name"]} for row in plan["added"]],
        "removed": [{"id": row["id"], "name": row["name"]} for row in plan["removed"]],
        "unchanged": plan["unchanged"],
        "coverage_before": before,
        "coverage_after": [
            dict(
                row,
                coverage="included"
                if row["id"] in added_ids
                else "excluded"
                if row["id"] in removed_ids
                else row["coverage"],
            )
            for row in before["clusters"]
        ],
        "warnings": list(state.get("cluster_notes") or []),
    }
    if not added_ids and not removed_ids:
        return {"status": "no_change", **result}
    try:
        new_query = compose_rule_query(plan["query"])
    except ValueError as error:
        raise SetupError(
            "rule_would_cover_nothing",
            (
                "Removing these clusters would leave the rule covering no "
                "cluster at all. Delete the rule with --remove-rule instead."
            ),
            recoverable=False,
            details={"rule_id": rule_id},
        ) from error
    result["query_after"] = new_query
    if dry_run:
        return {"status": "update_preview", **result}
    if not rule.get("api_key_uuid"):
        raise SetupError(
            "rule_api_key_unknown",
            (
                "The rule listing did not include the rule's API key, which "
                "an update must send back unchanged."
            ),
            details={"rule_id": rule_id},
        )
    updated, cleanup = _update_rule(rule, new_query, state["pup"])
    result["warnings"].extend(cleanup)
    result["rule"] = updated if updated.get("id") else {**rule, "query": new_query}
    # Datadog stores the normalized query; confirm it kept every exception.
    stored = parse_rule_query(result["rule"].get("query"))
    result["query_after"] = result["rule"].get("query")
    if not (
        set(plan["query"].includes) <= set(stored.includes)
        and set(plan["query"].excludes) <= set(stored.excludes)
    ):
        result["warnings"].append(
            {
                "code": "rule_query_not_as_sent",
                "message": (
                    "Datadog stored a query that does not name every added or "
                    "removed cluster; check the rule in Fleet Automation."
                ),
            }
        )
    return {"status": "rule_updated", **result}


def _status(rule_id: str) -> dict[str, Any]:
    resources = _rule_resources(rule_id)
    for resource in resources:
        resource["cluster_name"] = _cluster_name_from_resource_id(
            resource.get("resource_id") or ""
        )
    return {
        "status": "ok",
        "rule_id": rule_id,
        "resources": resources,
    }


def _remove_rule(rule_id: str) -> dict[str, Any]:
    # Rule deletion needs the same permission as creation, so it takes the
    # same route: the session's own token when it can write rules,
    # otherwise a one-shot key pair minted and deleted around the call.
    pup = _validate_pup()
    warnings = _delete_rule(rule_id, pup)
    return {
        "status": "rule_removed",
        "rule_id": rule_id,
        "warnings": warnings,
        "note": (
            "Datadog is removing the Operator, Agent, Cluster Agent, CRDs "
            "and webhooks from the covered clusters. Only a small "
            "datadog-operator-lock Lease remains in datadog-managed. This "
            "takes a few minutes."
        ),
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Install the Datadog Agent fleet on the Nebius Managed "
            "Kubernetes clusters of a tenancy integrated with Datadog."
        )
    )
    parser.add_argument(
        "--account-id",
        help="Datadog Nebius account UUID. Defaults to the account for the CLI profile's tenant.",
    )
    parser.add_argument(
        "--install",
        action="store_true",
        help=(
            "Create the managed install rule. Datadog's own Operator rollout "
            "takes over from there. Without this flag the script only "
            "discovers."
        ),
    )
    parser.add_argument(
        "--query",
        help=(
            "Rule query on cluster labels or region (for example "
            "'team:ml'). Omit with no --clusters to cover every cluster."
        ),
    )
    parser.add_argument(
        "--clusters",
        help=(
            "Comma-separated cluster ids or names to cover. The script "
            "derives an isolating query, or fails if no label isolates them."
        ),
    )
    parser.add_argument(
        "--api-key-name",
        help="Name of the Datadog API key the Agents should use.",
    )
    parser.add_argument(
        "--api-key-uuid",
        help="UUID of the Datadog API key the Agents should use.",
    )
    parser.add_argument(
        "--status",
        metavar="RULE_ID",
        help="Print the install status of one rule's clusters.",
    )
    parser.add_argument(
        "--remove-rule",
        metavar="RULE_ID",
        help="Delete the rule, uninstalling the Agent fleet from its clusters.",
    )
    parser.add_argument(
        "--update-rule",
        metavar="RULE_ID",
        help=(
            "Add clusters to or remove them from the account's existing rule "
            "(with --add-clusters and --remove-clusters), keeping its label "
            "query."
        ),
    )
    parser.add_argument(
        "--add-clusters",
        help="With --update-rule: comma-separated cluster ids or names to add.",
    )
    parser.add_argument(
        "--remove-clusters",
        help=(
            "With --update-rule: comma-separated cluster ids or names to "
            "remove. Datadog uninstalls the Agent from them."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="With --update-rule: show the new query and coverage without changing the rule.",
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
    # Each mode dispatches alone, so a flag meant for another mode must not
    # be silently dropped while this one writes (`--remove-rule --dry-run`
    # would otherwise delete the rule).
    modes = [
        flag
        for flag, value in (
            ("--install", args.install),
            ("--status", args.status),
            ("--remove-rule", args.remove_rule),
            ("--update-rule", args.update_rule),
        )
        if value
    ]
    if len(modes) > 1:
        raise SetupError(
            "conflicting_modes",
            f"Use only one of {', '.join(modes)} per run.",
            recoverable=False,
        )
    update_flags = [
        flag
        for flag, value in (
            ("--add-clusters", args.add_clusters),
            ("--remove-clusters", args.remove_clusters),
            ("--dry-run", args.dry_run),
        )
        if value
    ]
    if update_flags and not args.update_rule:
        raise SetupError(
            "update_flags_require_update_rule",
            f"{', '.join(update_flags)} only apply with --update-rule.",
            recoverable=False,
        )
    return args


def main(argv: list[str] | None = None) -> int:
    try:
        args = parse_args(argv)
        if args.status:
            emit(_status(args.status))
        elif args.remove_rule:
            emit(_remove_rule(args.remove_rule))
        elif args.update_rule:
            emit(
                _update(
                    account_id=args.account_id,
                    rule_id=args.update_rule,
                    add_clusters=args.add_clusters,
                    remove_clusters=args.remove_clusters,
                    dry_run=args.dry_run,
                )
            )
        elif args.install:
            emit(
                _install(
                    account_id=args.account_id,
                    query=args.query,
                    clusters=args.clusters,
                    api_key_name=args.api_key_name,
                    api_key_uuid=args.api_key_uuid,
                )
            )
        else:
            emit(_discover(args.account_id))
        return 0
    except SetupError as error:
        emit({"status": "error", "error": error.as_dict()})
        return 1


if __name__ == "__main__":
    sys.exit(main())
