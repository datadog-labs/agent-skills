#!/usr/bin/env python3
"""Render the labelled interactions into corpus/rows.jsonl through the evidence map."""
import json, pathlib, sys

BASE = pathlib.Path(__file__).parent
SEARCH = sys.argv[1]

spans = {s["trace_id"]: s for s in json.load(open(SEARCH))["spans"]}
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
        "labelled_by": "e11e1b0a-fcb0-11ef-87e8-3aa2651b05cb",
    })

rows.sort(key=lambda r: r["id"])
out = BASE / "corpus" / "rows.jsonl"
out.write_text("".join(json.dumps(r) + "\n" for r in rows))
print(f"wrote {len(rows)} rows -> {out}")
print(f"unrenderable: {len(unrenderable)} {unrenderable}")
print("class balance:", {"true": sum(r['label'] for r in rows), "false": sum(not r['label'] for r in rows)})
