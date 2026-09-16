#!/usr/bin/env python3
"""Render the labelled interactions into corpus/rows.jsonl through the evidence map.

The span this picks per trace MUST be the one `evidence_map.json` selects — the root
`recommendation_cycle` workflow span — because that is what `{{span_input}}`/`{{span_output}}`
resolve to on the deployed span-scoped evaluator. Indexing the search result by `trace_id` alone
would let any span of the trace win a dict overwrite, and the corpus would silently stop matching
the evidence map (and the deployed evaluator) on any multi-span trace.
"""
import json, pathlib, sys
from collections import defaultdict

BASE = pathlib.Path(__file__).parent
SEARCH = sys.argv[1]

MATCH = {"kind": "workflow", "name": "recommendation_cycle"}  # evidence_map.json, both selectors


def is_selected(span: dict) -> bool:
    """The evidence map's `match`: root + kind + name. Anything else is not the graded span."""
    meta_span = (span.get("meta") or {}).get("span") or {}
    kind = meta_span.get("kind") or span.get("kind")
    name = span.get("name") or meta_span.get("name")
    root = span.get("is_root_span")
    if root is None:  # not all span payloads carry the flag; absence of a parent is the fallback
        root = not (span.get("parent_id") or meta_span.get("parent_id"))
    return bool(root) and kind == MATCH["kind"] and name == MATCH["name"]


by_trace = defaultdict(list)
for span in json.load(open(SEARCH))["spans"]:
    if is_selected(span):
        by_trace[span["trace_id"]].append(span)

# Ambiguity is a corpus-integrity problem, not something to resolve by picking one arbitrarily.
ambiguous = {t: len(v) for t, v in by_trace.items() if len(v) > 1}
if ambiguous:
    raise SystemExit(
        f"{len(ambiguous)} trace(s) have more than one root recommendation_cycle span: "
        f"{ambiguous}. The evidence map says `take: first` over a unique match; refusing to "
        f"guess which span the human graded."
    )
spans = {t: v[0] for t, v in by_trace.items()}
labels = json.load(open(BASE / "labels.json"))
inputs = json.load(open(BASE / "inputs.json"))


def render(span_input: str, span_output: str) -> str:
    return (
        "[recommendation_context]\n"
        f"{span_input}\n"
        "[/recommendation_context]\n\n"
        "[recommended_song]\n"
        f"{span_output}\n"
        "[/recommended_song]"
    )


rows, unrenderable = [], []
for trace_id, meta in labels.items():
    span = spans.get(trace_id)
    span_in = inputs.get(trace_id)
    span_out = (span or {}).get("output", {}).get("preview")
    if not span or not span_in or not span_out:
        unrenderable.append(trace_id)
        continue
    rows.append({
        "id": meta["interaction_id"],
        "content_id": trace_id,
        "span_id": span["span_id"],
        "payload": render(span_in, span_out),
        "label": meta["label"],
        "human_reasoning": None,
        # No reviewer id on the row. It is constant across this queue so it carries no per-row
        # signal, but the evidence map excludes reviewer identity outright and a corpus row is the
        # wrong place to park an internal user id.
    })

rows.sort(key=lambda r: r["id"])
out = BASE / "corpus" / "rows.jsonl"
out.write_text("".join(json.dumps(r) + "\n" for r in rows))
print(f"wrote {len(rows)} rows -> {out}")
print(f"unrenderable: {len(unrenderable)} {unrenderable}")
print("class balance:", {"true": sum(r['label'] for r in rows), "false": sum(not r['label'] for r in rows)})
