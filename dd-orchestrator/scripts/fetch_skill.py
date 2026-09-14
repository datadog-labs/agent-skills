#!/usr/bin/env python3
"""Fetch a catalog skill from its public source so the orchestrator can INVOKE it even
when it is not installed in the session registry — invoke, not install.

Always fetches the NEWEST version: agent-skills is read at `main`, dd-source via the live
onboarding-API render. Nothing is pinned. The skill (SKILL.md + any references/ and
scripts/) is written into a run-scoped temp dir; the orchestrator then executes that
SKILL.md inline. See spec one-orchestrator-to-rule-them-all (fetch-and-invoke).

Fail closed: an unknown / non-public source, a missing node, or any non-200 response exits
non-zero — the orchestrator treats that as a hard failure of the node, never a silent skip.

Run:  python3 fetch_skill.py <skill_id> [--dest DIR] [--catalog PATH] [--plan]
      prints the local skill dir; --plan prints the resolved target without fetching.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import urllib.request
from urllib.parse import urlsplit

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL = os.path.dirname(HERE)
CATALOG = os.path.join(SKILL, "catalog.json")

RAW_HOST = "raw.githubusercontent.com"
ALLOWED_GH_REPO = "datadog-labs/agent-skills"     # the only public GitHub source
API_HOST = "api.datadoghq.com"                     # dd-source onboarding render
BRANCH = "main"                                     # always newest; never pinned


def _get(url, binary=False):
    req = urllib.request.Request(url, headers={"User-Agent": "dd-orchestrator-fetch"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        if resp.status != 200:
            raise RuntimeError(f"HTTP {resp.status} for {url}")
        data = resp.read()
    return data if binary else data.decode("utf-8")


def resolve_target(node):
    """Pure (no network): the newest-version fetch target for a catalog node.

    Returns ("github", "datadog-labs/agent-skills", "<skill_dir>")  — fetch dir at main
         or ("render", "<onboarding-api render url>")               — single md, newest
    Raises for any non-public / unknown source (fail closed).
    """
    src = node.get("source") or {}
    url = src.get("url", "")
    host = urlsplit(url).netloc
    if src.get("repo") == "agent-skills" and host == "github.com":
        parts = urlsplit(url).path.strip("/").split("/")     # org/repo/blob/<ref>/<path...>
        if len(parts) < 5 or parts[2] not in ("blob", "tree"):
            raise RuntimeError(f"malformed agent-skills url: {url!r}")
        org_repo = f"{parts[0]}/{parts[1]}"
        if org_repo != ALLOWED_GH_REPO:
            raise RuntimeError(f"refusing non-allowlisted repo: {org_repo}")
        rel = "/".join(parts[4:])
        skill_dir = os.path.dirname(rel) if src.get("path_type", "file") == "file" else rel
        return ("github", org_repo, skill_dir)
    if host == API_HOST and "/onboarding/skills/" in url:
        return ("render", url)
    raise RuntimeError(f"unsupported/non-public source: {url!r}")


def _fetch_github_dir(org_repo, dir_path, dest):
    """Recursively fetch a skill directory from agent-skills @ main (contents API + raw)."""
    api = f"https://api.github.com/repos/{org_repo}/contents/{dir_path}?ref={BRANCH}"
    for entry in json.loads(_get(api)):
        if entry["type"] == "dir":
            _fetch_github_dir(org_repo, f"{dir_path}/{entry['name']}",
                              os.path.join(dest, entry["name"]))
        elif entry["type"] == "file":
            raw = f"https://{RAW_HOST}/{org_repo}/{BRANCH}/{dir_path}/{entry['name']}"
            os.makedirs(dest, exist_ok=True)
            with open(os.path.join(dest, entry["name"]), "wb") as f:
                f.write(_get(raw, binary=True))


def _node(catalog_path, skill_id):
    with open(catalog_path) as f:
        for n in json.load(f)["nodes"]:
            if n["id"] == skill_id:
                return n
    raise RuntimeError(f"skill id {skill_id!r} not in catalog {catalog_path}")


def fetch(skill_id, dest=None, catalog_path=CATALOG):
    node = _node(catalog_path, skill_id)
    kind, *rest = resolve_target(node)
    # Run-scoped destination: a fresh dir per fetch so a partial/older fetch can never leave
    # stale files (e.g. an upstream-deleted script or a leftover SKILL.md) to be executed, and
    # concurrent fetches cannot interleave. Caller reads the returned path, so a unique dir is fine.
    dest = dest or tempfile.mkdtemp(prefix=f"dd-orch-{skill_id}-")
    os.makedirs(dest, exist_ok=True)
    if kind == "github":
        org_repo, skill_dir = rest
        _fetch_github_dir(org_repo, skill_dir, dest)
    else:  # render
        (url,) = rest
        with open(os.path.join(dest, "SKILL.md"), "w") as f:
            f.write(_get(url))
    if not os.path.exists(os.path.join(dest, "SKILL.md")):
        raise RuntimeError(f"fetched {skill_id} but no SKILL.md landed in {dest}")
    return dest


def main(argv=None):
    ap = argparse.ArgumentParser(description="fetch a catalog skill from its public source (newest)")
    ap.add_argument("skill_id")
    ap.add_argument("--dest")
    ap.add_argument("--catalog", default=CATALOG)
    ap.add_argument("--plan", action="store_true", help="print the resolved target; do not fetch")
    args = ap.parse_args(argv)
    try:
        if args.plan:
            print(resolve_target(_node(args.catalog, args.skill_id)))
        else:
            print(fetch(args.skill_id, args.dest, args.catalog))
    except Exception as exc:  # fail closed with a clear reason + non-zero exit
        print(f"fetch_skill: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
