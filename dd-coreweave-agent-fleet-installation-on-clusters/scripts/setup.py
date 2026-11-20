#!/usr/bin/env python3
"""Datadog agent fleet installation on CoreWeave CKS clusters.

Discovers the CoreWeave tenancy already integrated with Datadog and its
CKS clusters, then creates the managed install rule. Datadog's own
server-side Operator rollout takes over from there: the script stops at
rule creation and does not wait for or verify the rollout.
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
from _coreweave import _check_cli, _list_clusters_with_health  # noqa: E402
from _datadog import (  # noqa: E402
    PROVIDER,
    RESOURCE_TYPE,
    _check_env_keys,
    _check_required_access,
    _cluster_name_from_resource_id,
    _create_rule,
    _delete_rule,
    _find_rule,
    _integration_orgs,
    _list_accounts,
    _list_api_keys,
    _list_rules,
    _other_account_rules,
    _rule_resources,
    _validate_pup,
)


def emit(value: dict[str, Any]) -> None:
    print(json.dumps(value, indent=2, sort_keys=True, default=str))


def _resolve_account(
    accounts: list[dict[str, Any]],
    *,
    identity: dict[str, Any],
    integration_orgs: list[dict[str, Any]],
    account_id: str | None,
    pup: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """CoreWeave has no cloud-side org ID: the Datadog account names the tenancy.

    When several accounts exist, the integration's telemetry pairs
    accounts with CoreWeave orgs (`account_display_name` +
    `cluster_org`), so the account whose org matches the local cwic
    session is selected automatically instead of demanding --account-id.
    """
    if not accounts:
        # Scoped to the session's own org and site, never to Datadog as a
        # whole: the same organization can hold the integration under a
        # different org or site, and reading this error as "no
        # integration exists anywhere" is how duplicate integrations get
        # registered.
        site = (pup or {}).get("site") or "datadoghq.com"
        org = (pup or {}).get("org")
        where = f"{org} on {site}" if org else f"the session's org on {site}"
        raise SetupError(
            "integration_not_configured",
            (
                "No Datadog CoreWeave integration account exists in this "
                f"organization ({where}) — the error is scoped to that "
                "org, not to Datadog as a whole. Re-check that the session "
                "is signed into the intended Datadog org and site (pup "
                "auth status, DD_SITE) before setting up an integration "
                "(the dd-coreweave-integration skill)."
            ),
            recoverable=False,
            details={"site": site, "org": org},
        )
    if account_id:
        for account in accounts:
            if account.get("id") == account_id:
                return account
        raise SetupError(
            "account_not_found",
            f"No Datadog CoreWeave account has the id {account_id}.",
            recoverable=False,
            details={"available_accounts": accounts},
        )
    if len(accounts) == 1:
        return accounts[0]
    local_org = (identity or {}).get("org_id")
    if local_org:
        for account in accounts:
            slug = _account_name_slug(account.get("name"))
            for pair in integration_orgs:
                if (
                    pair.get("cluster_org") == local_org
                    and pair.get("account_display_name") == slug
                ):
                    return account
    raise SetupError(
        "multiple_accounts",
        (
            "Multiple Datadog CoreWeave accounts exist and none could be "
            "matched to the cwic session's CoreWeave org through the "
            "integration's telemetry. Pass --account-id to select the one "
            "whose CoreWeave organization holds the clusters, or re-check "
            "the cwic session's org (cwic auth whoami)."
        ),
        recoverable=False,
        details={"available_accounts": accounts},
    )


def _label_tags(cluster: dict[str, Any]) -> dict[str, str]:
    """CKS clusters have no labels; the rule can match on name and zone."""
    tags = {"name": cluster["id"]}
    if cluster.get("region"):
        tags["zone"] = cluster["region"]
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
    if not shared:
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
                    "No query was given, so the rule covers every cluster "
                    "in the tenancy, including clusters created later."
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
    # A single selection is isolated by its own name tag; several need a
    # shared zone that no other cluster uses.
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
                "The selected clusters share no tag (name, zone) that the "
                "other clusters in the tenancy lack, so no rule query can "
                "cover exactly the selection. Pass --query yourself."
            ),
            details={
                "selected": [
                    {"id": cluster["id"], "region": cluster.get("region")}
                    for cluster in selected
                ],
                "all_clusters": [
                    {"id": cluster["id"], "region": cluster.get("region")}
                    for cluster in clusters
                ],
            },
        )
    return isolating, selected, warnings


def _resolve_api_key_uuid(
    *, api_key_name: str | None, api_key_uuid: str | None
) -> tuple[str, dict[str, Any]]:
    # An explicit UUID needs no listing, so it works for sessions whose
    # token cannot read API keys (no `api_keys_read`).
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
    identified and org-paired before any other provider read runs. The
    integration's accounts are enumerated first and each is paired with
    the cwic session's org through the integration's own telemetry, so an
    existing matching account is found — and a diverging one refused —
    before required access, rules, or clusters are read. A wrong org or
    tenancy therefore stops the run before anything else can report a
    confidently wrong answer. The org pairing matters because the
    install makes Datadog act through the integration account's token
    while discovery previews clusters through the local cwic session: a
    divergence would install a different tenancy than the one shown.
    """
    pup = _validate_pup()
    env_keys = _check_env_keys(pup)
    identity = _check_cli()
    accounts = _list_accounts()
    integration_orgs = _integration_orgs()
    account = _resolve_account(
        accounts,
        identity=identity,
        integration_orgs=integration_orgs,
        account_id=account_id,
        pup=pup,
    )
    org_pairing = _verify_org_pairing(identity, account, integration_orgs)
    required_access = _check_required_access()
    rules = _list_rules()
    existing_rule = _find_rule(rules, account["id"])
    other_rules = _other_account_rules(rules, account["id"])
    clusters, cluster_notes = _list_clusters_with_health()
    return {
        "pup": pup,
        "env_keys": env_keys,
        "identity": identity,
        "required_access": required_access,
        "account": account,
        "org_pairing": org_pairing,
        "existing_rule": existing_rule,
        "other_rules": other_rules,
        "clusters": clusters,
        "cluster_notes": cluster_notes,
        "integration_orgs": integration_orgs,
    }


def _account_name_slug(name: str | None) -> str:
    """The tag value the integration reports for an account display name.

    `Datadog POC` is reported as `account_display_name:datadog_poc`.
    """
    return re.sub(r"[^a-z0-9]+", "_", (name or "").lower()).strip("_")


def _verify_org_pairing(
    identity: dict[str, Any],
    account: dict[str, Any],
    integration_orgs: list[dict[str, Any]],
) -> dict[str, Any]:
    """Gate: the cwic session and the integration must name the same org.

    CoreWeave accounts expose no cloud org id, but the integration's
    metrics carry `cluster_org` and `account_display_name` tags, so the
    pairing is verified from the telemetry attributed to the selected
    account. With no such telemetry — none at all, only other accounts',
    or an unreadable session org — the pairing is reported unknown
    (`verified: None`): never a guess, never a failure. Another account's
    telemetry establishes neither a match nor a mismatch. A confirmed
    divergence raises `cluster_org_mismatch`, because every later step
    would preview one tenancy and install another.
    """
    local_org = (identity or {}).get("org_id")
    if not local_org:
        return {"verified": None, "reason": "cwic session org unreadable"}
    if not integration_orgs:
        return {"verified": None, "reason": "no recent integration telemetry"}
    attributed = [
        pair
        for pair in integration_orgs
        if pair.get("account_display_name")
        == _account_name_slug(account.get("name"))
    ]
    if not attributed:
        return {
            "verified": None,
            "reason": "no recent integration telemetry attributed to this account",
        }
    org = attributed[0].get("cluster_org")
    if org == local_org:
        return {
            "verified": True,
            "local_org": local_org,
            "integration_org": org,
            "account_display_name": attributed[0].get("account_display_name"),
        }
    raise SetupError(
        "cluster_org_mismatch",
        (
            f"The Datadog account {account.get('name')!r} is connected "
            f"to CoreWeave org {org}, but the cwic session is logged "
            f"into org {local_org}. Log in to the CoreWeave "
            "organization the Datadog account holds (cwic auth login) "
            "or select the matching Datadog account with --account-id, "
            "then rerun."
        ),
        recoverable=False,
        details={"local_org": local_org, "integration_orgs": integration_orgs},
    )


def _discover(account_id: str | None) -> dict[str, Any]:
    state = _preflight(account_id)
    warnings: list[dict[str, str]] = list(state.get("cluster_notes") or []) + list(
        (state.get("env_keys") or {}).get("warnings") or []
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
    if state["other_rules"]:
        warnings.append(
            {
                "code": "rule_exists_other_account",
                "message": (
                    "Install rules already exist under other CoreWeave "
                    "accounts in this organization (see other_account_rules). "
                    "Datadog may register this tenancy's clusters — and "
                    "report their install state — under those rules instead "
                    "of one created for this account. Ask your Datadog "
                    "representative before creating another rule."
                ),
            }
        )
    org_pairing = state.get("org_pairing") or {}
    if org_pairing.get("verified") is None:
        warnings.append(
            {
                "code": "cluster_org_unknown",
                "message": (
                    "The cwic session's CoreWeave org could not be paired "
                    f"with the integration's ({org_pairing.get('reason')}), "
                    "so the tenancy match is unverified. The install can "
                    "proceed, but confirm the session is logged into the "
                    "organization the Datadog account holds."
                ),
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
            "account_id": state["account"]["id"],
            "account_name": state["account"]["name"],
        },
        "coreweave_session": {
            "org_id": (state["identity"] or {}).get("org_id"),
            "cks_admin": (state["identity"] or {}).get("cks_admin"),
        },
        "integration_orgs": state["integration_orgs"],
        "org_pairing": org_pairing,
        "required_role": state["required_access"],
        "clusters": state["clusters"],
        "gpu_clusters": [
            cluster["name"]
            for cluster in state["clusters"]
            if cluster.get("gpu") is True
        ],
        "existing_rule": state["existing_rule"],
        "other_account_rules": state["other_rules"],
        "api_keys": api_keys,
        "warnings": warnings,
    }


def _ensure_rule(
    state: dict[str, Any],
    *,
    rule_query: str | None,
    key_uuid: str,
    warnings: list[dict[str, str]],
) -> dict[str, Any]:
    """The account's install rule: reused when it exists, created otherwise.

    Datadog permits one rule per account and resource type, so an existing
    rule is reused rather than duplicated — but only when its query is the
    coverage just approved. A rule that covers other clusters would cover
    clusters the user never selected, so a mismatch stops before any write.
    """
    rule = state["existing_rule"]
    if rule:
        existing_query = (rule.get("query") or "").strip() or None
        requested_query = (rule_query or "").strip() or None
        if existing_query != requested_query:
            raise SetupError(
                "existing_rule_coverage_mismatch",
                (
                    "An install rule already exists for this account and "
                    "covers different clusters than the approved selection, "
                    "so nothing was changed."
                ),
                recoverable=False,
                details={
                    "rule_id": rule.get("id"),
                    "existing_query": existing_query,
                    "requested_query": requested_query,
                    "remediation": (
                        "Rerun with the existing rule's coverage (its query "
                        "as --query, or no --query/--clusters when it has "
                        "none), or remove it with --remove-rule "
                        f"{rule.get('id')} and rerun to create a rule for "
                        "the approved clusters."
                    ),
                },
            )
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
        if state["other_rules"]:
            warnings.append(
                {
                    "code": "rule_exists_other_account",
                    "message": (
                        "Install rules already exist under other CoreWeave "
                        "accounts in this organization (see "
                        "other_account_rules). Datadog may register this "
                        "tenancy's clusters — and report their install "
                        "state — under those rules instead of the one "
                        "created here, leaving this rule without resources "
                        "and its install result partial. Ask your Datadog "
                        "representative before creating another rule."
                    ),
                }
            )
        # Rule writes ride the session's own token when it carries the
        # agent-install scope; otherwise they fall back to a full API
        # user's keys (exported DD_API_KEY/DD_APP_KEY when present, else a
        # one-shot pair minted through Pup and deleted right after). The
        # secret values never leave this process.
        rule, cleanup_warnings = _create_rule(
            state["account"]["id"], key_uuid, rule_query, state["pup"]
        )
        warnings.extend(cleanup_warnings)
    if not rule.get("id"):
        raise SetupError(
            "rule_id_missing",
            "The install rule has no id.",
            details={"rule": rule},
        )
    return rule


def _install(
    *,
    account_id: str | None,
    query: str | None,
    clusters: str | None,
    api_key_name: str | None,
    api_key_uuid: str | None,
) -> dict[str, Any]:
    """Creates (or reuses) the install rule, then stops.

    Datadog's server-side Operator rollout takes over once the rule
    exists. The script does not wait for it, configure anything further,
    or verify telemetry; `--status` reports progress read-only.
    """
    state = _preflight(account_id)
    rule_query, selected, warnings = _resolve_query(
        state["clusters"], query=query, cluster_selection=clusters
    )
    warnings = (
        list(state.get("cluster_notes") or [])
        + list((state.get("env_keys") or {}).get("warnings") or [])
        + warnings
    )
    org_pairing = state.get("org_pairing") or {}
    if org_pairing.get("verified") is None:
        warnings.append(
            {
                "code": "cluster_org_unknown",
                "message": (
                    "The cwic session's CoreWeave org could not be paired "
                    f"with the integration's ({org_pairing.get('reason')}), "
                    "so the tenancy match is unverified."
                ),
            }
        )
    key_uuid, key_info = _resolve_api_key_uuid(
        api_key_name=api_key_name, api_key_uuid=api_key_uuid
    )
    reused = state["existing_rule"] is not None
    rule = _ensure_rule(
        state, rule_query=rule_query, key_uuid=key_uuid, warnings=warnings
    )
    return {
        "status": "rule_exists" if reused else "rule_created",
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
        "rule_query": rule_query,
        "other_account_rules": state["other_rules"],
        "selected_clusters": [
            {"id": cluster["id"], "name": cluster["name"]} for cluster in selected
        ],
        "api_key": key_info,
        "coreweave_session": {
            "org_id": (state["identity"] or {}).get("org_id"),
            "cks_admin": (state["identity"] or {}).get("cks_admin"),
        },
        "integration_orgs": state["integration_orgs"],
        "org_pairing": state["org_pairing"],
        "warnings": warnings,
        "note": (
            "Datadog rolls out the Operator from here. This skill stops at "
            "rule creation and does not configure or verify anything "
            f"further; use `--status {rule['id']}` for a read-only progress "
            "read."
        ),
    }


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
            "Install the Datadog Agent fleet on the CoreWeave CKS clusters "
            "of a tenancy integrated with Datadog."
        )
    )
    parser.add_argument(
        "--account-id",
        help="Datadog CoreWeave account UUID. Defaults to the only account, and is required when several exist.",
    )
    parser.add_argument(
        "--install",
        action="store_true",
        help=(
            "Create the managed install rule. Without this flag the script "
            "only discovers."
        ),
    )
    parser.add_argument(
        "--query",
        help=(
            "Rule query on cluster name or zone (for example "
            "'zone:us-east-1'). Omit with no --clusters to cover every "
            "cluster."
        ),
    )
    parser.add_argument(
        "--clusters",
        help=(
            "Comma-separated cluster ids or names to cover. The script "
            "derives an isolating query, or fails if no tag isolates them."
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
        if args.status:
            emit(_status(args.status))
        elif args.remove_rule:
            emit(_remove_rule(args.remove_rule))
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
