"""Phase 5 dataset publisher for agent-observability-eval-pipeline.

CLI wrapper around `LLMObs.create_dataset(records=...)` that:
  - Auto-loads credentials via the shared load_env helper (script dir,
    cwd, parent dirs, ~/.datadog/credentials). Shell env wins over
    file values, so users can override by `export DD_API_KEY=...`.
  - Defensively normalizes per-record `tags` (wraps bare strings as
    `tag:<value>`, drops empties) so a malformed upstream dataset
    cannot trip the SDK's `validate_tags_list` ValueError.
  - Creates the Datadog project lazily on first `LLMObs.enable()` call.

Usage:
  python publish_dataset.py \\
    --records /abs/path/to/dataset.json \\
    --dataset-name my_seed_20260529 \\
    --project-name my-llm-app \\
    [--env-file /abs/path/to/extra.env]

Prints `OK dataset_name=... record_count=... url=...` on success.
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys

# Make sibling load_env.py importable regardless of how the script is invoked.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from load_env import load_env_files  # noqa: E402


def normalize_tags(raw):
    """Return (normalized_tags, fix_count).

    Wraps bare strings as `tag:<value>`, drops empty/None, preserves
    malformed leading/trailing colons by wrapping the original as
    `tag:<original>` so the SDK's `validate_tags_list` cannot reject
    the record. See agent-observability/agent-observability-eval-bootstrap/SKILL.md Phase 3D
    "Tag normalization" for the rationale.
    """
    fixed = []
    fix_count = 0
    for t in raw or []:
        if not isinstance(t, str):
            fix_count += 1
            continue
        t = t.strip()
        if not t:
            fix_count += 1
            continue
        if ":" in t:
            k, _, v = t.partition(":")
            if k and v:
                fixed.append(t)
                continue
            fixed.append(f"tag:{t}")
            fix_count += 1
            continue
        fixed.append(f"tag:{t}")
        fix_count += 1
    return fixed, fix_count


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--records", required=True, help="Absolute path to the DatasetRecordRaw[] JSON file.")
    parser.add_argument("--dataset-name", required=True, help="Name to publish the dataset under in Datadog.")
    parser.add_argument("--project-name", required=True, help="Datadog project to publish the dataset under. Created lazily.")
    parser.add_argument("--env-file", action="append", default=[], help="Extra .env path(s) to load FIRST (repeatable).")
    args = parser.parse_args()

    loaded = load_env_files(args.env_file)
    if loaded:
        print(f"Loaded credentials from: {', '.join(loaded)}")

    api_key = os.getenv("DD_API_KEY")
    app_key = os.getenv("DD_APPLICATION_KEY") or os.getenv("DD_APP_KEY")
    if not api_key:
        print("ERROR: DD_API_KEY is not set. Export it in your shell or add it to a discovered .env file.", file=sys.stderr)
        return 2
    if not app_key:
        print("ERROR: DD_APPLICATION_KEY (or DD_APP_KEY) is not set. Same fallback paths as above.", file=sys.stderr)
        return 2

    from ddtrace.llmobs import LLMObs  # imported AFTER env load so credentials are picked up

    LLMObs.enable(
        api_key=api_key,
        app_key=app_key,
        site=os.getenv("DD_SITE", "datadoghq.com"),
        project_name=args.project_name,  # project is created lazily here if it does not exist
        agentless_enabled=True,
    )

    with open(args.records) as f:
        records = json.load(f)

    total_fixes = 0
    for r in records:
        if "tags" in r:
            r["tags"], n = normalize_tags(r.get("tags"))
            total_fixes += n
    if total_fixes:
        print(
            f"WARNING: normalized {total_fixes} malformed tag(s) before publish "
            "(bare strings wrapped as 'tag:<value>'; empties dropped)."
        )

    dataset = LLMObs.create_dataset(
        dataset_name=args.dataset_name,
        description=f"Seed dataset for {args.dataset_name} (eval-pipeline flow, sampled from production traces).",
        records=records,
    )
    url = dataset.url if hasattr(dataset, "url") else "<inspect in UI>"
    print(f"OK dataset_name={args.dataset_name} record_count={len(records)} url={url}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
