#!/usr/bin/env python3
"""Build a DatasetRecordRaw[] JSON from a cached trace-export state file.

Called by the eval-pipeline skill in Phase 4 when `--trace-export` (or an
auto-detected trace list) is in effect — the skill has already validated
each trace_id against Datadog in the Precheck and cached the kept rows to
`<output-dir>/state/00-trace-export.json`. This script turns that cache
into a dataset ready for `LLMObs.create_dataset(records=...)`, applying
the standard PII scrub and provenance tagging.

Usage:
    python build_dataset_from_export.py \\
        --state <path to state/00-trace-export.json> \\
        --output <path to dataset_<ml_app>_<YYYYMMDD>.json> \\
        --ml-app <ml_app>

Output on stdout:
    Wrote <N> records to <path>
    PII redactions: <K>
    Records with expected_output: <M>/<N>

Fails loudly if the state file is missing or malformed.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys


PII_PATTERNS = [
    (re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"), "<REDACTED:email>"),
    (re.compile(r"\b\d{3}[- ]?\d{2}[- ]?\d{4}\b"), "<REDACTED:ssn>"),
    (re.compile(r"\b(?:\+?\d{1,2}[- ]?)?\(?\d{3}\)?[- ]?\d{3}[- ]?\d{4}\b"), "<REDACTED:phone>"),
    (re.compile(r"\bsk-[A-Za-z0-9]{20,}\b"), "<REDACTED:api-key>"),
]


def scrub(value):
    if not isinstance(value, str):
        return value
    for pattern, replacement in PII_PATTERNS:
        value = pattern.sub(replacement, value)
    return value


def build_records(cached: dict, ml_app: str) -> tuple[list[dict], int]:
    records: list[dict] = []
    redactions = 0
    for row in cached["records"]:
        raw_input = row.get("input") or ""
        scrubbed_input = scrub(raw_input)
        raw_expected = row.get("expected_output") or row.get("output") or ""
        scrubbed_expected = scrub(raw_expected) if raw_expected else None

        if scrubbed_input != raw_input:
            redactions += 1
        if scrubbed_expected and scrubbed_expected != raw_expected:
            redactions += 1

        record = {
            "input_data": {"input": scrubbed_input},
            "metadata": {"trace_id": row["trace_id"], "ml_app": ml_app},
            "tags": [
                f"ml_app:{ml_app}",
                "source:annotation-queue",
                f"trace_id:{row['trace_id']}",
            ],
        }
        if scrubbed_expected:
            record["expected_output"] = scrubbed_expected
        records.append(record)

    return records, redactions


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", required=True, help="Path to state/00-trace-export.json")
    parser.add_argument("--output", required=True, help="Path to write the DatasetRecordRaw[] JSON")
    parser.add_argument("--ml-app", required=True, help="ml_app name for record tagging + metadata")
    args = parser.parse_args()

    state_path = pathlib.Path(args.state)
    if not state_path.exists():
        print(f"ERROR: state file not found: {state_path}", file=sys.stderr)
        return 2

    try:
        cached = json.loads(state_path.read_text())
    except json.JSONDecodeError as exc:
        print(f"ERROR: state file is not valid JSON: {exc}", file=sys.stderr)
        return 2

    if "records" not in cached or not isinstance(cached["records"], list):
        print(f"ERROR: state file missing 'records' list", file=sys.stderr)
        return 2

    records, redactions = build_records(cached, args.ml_app)

    output_path = pathlib.Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(records, indent=2))

    with_expected = sum(1 for record in records if "expected_output" in record)
    print(f"Wrote {len(records)} records to {output_path}")
    print(f"PII redactions: {redactions}")
    print(f"Records with expected_output: {with_expected}/{len(records)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
