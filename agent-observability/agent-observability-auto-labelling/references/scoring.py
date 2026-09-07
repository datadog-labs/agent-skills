#!/usr/bin/env python3
"""Score one judge version against the human labels, and compare it to the previous best.

    python scoring.py \
        --corpus  .auto_labelling/corpus/rows.jsonl \
        --pred    .auto_labelling/predictions/v1.jsonl \
        --metric  balanced_accuracy \
        --baseline-pred .auto_labelling/predictions/v0.jsonl   # optional: McNemar vs the best

Stdlib only, on purpose: this runs anywhere the corpus does, with no install step.

Every number the loop makes a decision on comes from here. Nothing in this file guesses a value:
a row with no usable prediction is EXCLUDED and counted (rubric §6), never scored as wrong.
"""

from __future__ import annotations

import argparse
import json
import math
from collections import Counter, defaultdict
from pathlib import Path


# --------------------------------------------------------------------------- helpers


def wilson(successes: int, total: int, z: float = 1.96):
    """Wilson 95% interval — the honest one at n=13, unlike the normal approximation."""
    if total == 0:
        return (0.0, 0.0)
    p = successes / total
    denom = 1 + z * z / total
    centre = (p + z * z / (2 * total)) / denom
    half = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / denom
    return (round(max(0.0, centre - half), 4), round(min(1.0, centre + half), 4))


def mcnemar_exact(b: int, c: int) -> float:
    """Two-sided exact binomial p on the discordant pairs. b/c = rows only one version got right."""
    n = b + c
    if n == 0:
        return 1.0
    k = min(b, c)
    tail = sum(math.comb(n, i) for i in range(k + 1)) / (2 ** n)
    return round(min(1.0, 2 * tail), 4)


def majority(values: list):
    """Majority vote over the runs. Ties (possible only with an even --runs) return None."""
    if not values:
        return None
    counts = Counter(map(json.dumps, values)).most_common()
    if len(counts) > 1 and counts[0][1] == counts[1][1]:
        return None
    return json.loads(counts[0][0])


# --------------------------------------------------------------------------- metrics


def confusion(pairs):
    """pairs = [(truth, pred)]. Returns {(truth, pred): count} over whatever classes exist."""
    matrix = defaultdict(int)
    for truth, pred in pairs:
        matrix[(json.dumps(truth), json.dumps(pred))] += 1
    return matrix


def per_class_recall(pairs):
    hit, total = defaultdict(int), defaultdict(int)
    for truth, pred in pairs:
        key = json.dumps(truth)
        total[key] += 1
        hit[key] += int(truth == pred)
    return {k: round(hit[k] / total[k], 4) for k in total}


def score(pairs, metric: str) -> float:
    if not pairs:
        return 0.0
    if metric == "accuracy":
        return round(sum(t == p for t, p in pairs) / len(pairs), 4)
    if metric == "balanced_accuracy":
        recalls = per_class_recall(pairs)
        return round(sum(recalls.values()) / len(recalls), 4)
    if metric in ("f1", "f1_minority"):
        classes = Counter(json.dumps(t) for t, _ in pairs)
        positive = json.loads(min(classes, key=classes.get))  # minority class is the positive one
        tp = sum(t == positive and p == positive for t, p in pairs)
        fp = sum(t != positive and p == positive for t, p in pairs)
        fn = sum(t == positive and p != positive for t, p in pairs)
        if tp == 0:
            return 0.0
        precision, recall = tp / (tp + fp), tp / (tp + fn)
        return round(2 * precision * recall / (precision + recall), 4)
    if metric == "macro_f1":
        classes = {json.dumps(t) for t, _ in pairs}
        scores = []
        for cls in classes:
            tp = sum(json.dumps(t) == cls and json.dumps(p) == cls for t, p in pairs)
            fp = sum(json.dumps(t) != cls and json.dumps(p) == cls for t, p in pairs)
            fn = sum(json.dumps(t) == cls and json.dumps(p) != cls for t, p in pairs)
            scores.append(0.0 if tp == 0 else 2 * tp / (2 * tp + fp + fn))
        return round(sum(scores) / len(scores), 4)
    if metric == "cohens_kappa":
        observed = sum(t == p for t, p in pairs) / len(pairs)
        truths = Counter(json.dumps(t) for t, _ in pairs)
        preds = Counter(json.dumps(p) for _, p in pairs)
        expected = sum(truths[c] * preds.get(c, 0) for c in truths) / (len(pairs) ** 2)
        return round(0.0 if expected == 1 else (observed - expected) / (1 - expected), 4)
    if metric == "mae":
        return round(sum(abs(float(t) - float(p)) for t, p in pairs) / len(pairs), 4)
    raise SystemExit(f"unknown metric {metric!r}")


def constant_baseline(pairs, metric: str) -> float:
    """What the laziest possible judge scores. The headline must beat this to mean anything."""
    classes = {json.dumps(t) for t, _ in pairs}
    return max(score([(t, json.loads(cls)) for t, _ in pairs], metric) for cls in classes)


# --------------------------------------------------------------------------- load


def load_predictions(path: str):
    """-> {row_id: {"vote": label|None, "flipped": bool, "usable": bool}}"""
    passes = defaultdict(list)
    unparseable = defaultdict(int)
    for line in Path(path).read_text().splitlines():
        if not line.strip():
            continue
        rec = json.loads(line)
        if rec.get("unparseable") or "label" not in rec:
            unparseable[rec["id"]] += 1
        else:
            passes[rec["id"]].append(rec["label"])
    out = {}
    for row_id in set(passes) | set(unparseable):
        labels = passes.get(row_id, [])
        vote = majority(labels)
        out[row_id] = {
            "vote": vote,
            "flipped": len({json.dumps(v) for v in labels}) > 1,
            "usable": vote is not None,
            "unparseable_passes": unparseable.get(row_id, 0),
        }
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus", required=True)
    ap.add_argument("--pred", required=True)
    ap.add_argument("--metric", default="balanced_accuracy")
    ap.add_argument("--split", default="train", choices=["train", "holdout", "all"])
    ap.add_argument("--baseline-pred", help="predictions of the current best, for McNemar")
    args = ap.parse_args()

    rows = [json.loads(l) for l in Path(args.corpus).read_text().splitlines() if l.strip()]
    if args.split != "all":
        rows = [r for r in rows if r.get("split", "train") == args.split]
    truth = {r["id"]: r["label"] for r in rows}

    preds = load_predictions(args.pred)
    scored = [(rid, truth[rid], preds[rid]["vote"]) for rid in truth if preds.get(rid, {}).get("usable")]
    excluded = [rid for rid in truth if not preds.get(rid, {}).get("usable")]
    pairs = [(t, p) for _, t, p in scored]

    correct = {rid: (t == p) for rid, t, p in scored}
    headline = score(pairs, args.metric)
    hits = sum(correct.values())

    result = {
        "metric": args.metric,
        "split": args.split,
        "headline": headline,
        "rows_scored": len(pairs),
        "rows_excluded_unusable": len(excluded),
        "excluded_ids": excluded,
        "raw_accuracy": round(hits / len(pairs), 4) if pairs else 0.0,
        "wilson_95_on_accuracy": wilson(hits, len(pairs)),
        "constant_class_baseline": constant_baseline(pairs, args.metric) if pairs else 0.0,
        "per_class_recall": per_class_recall(pairs),
        "confusion": {f"truth={t}|pred={p}": n for (t, p), n in confusion(pairs).items()},
        "flip_rate": round(sum(preds[rid]["flipped"] for rid, _, _ in scored) / len(scored), 4) if scored else 0.0,
        "correct_by_row": correct,
    }

    if args.baseline_pred:
        base = load_predictions(args.baseline_pred)
        b = c = 0  # b: only candidate right, c: only baseline right
        for rid, t, p in scored:
            if not base.get(rid, {}).get("usable"):
                continue
            base_right, cand_right = base[rid]["vote"] == t, p == t
            b += int(cand_right and not base_right)
            c += int(base_right and not cand_right)
        result["vs_baseline"] = {
            "gained": b,
            "lost": c,
            "mcnemar_p": mcnemar_exact(b, c),
            "gained_ids": [rid for rid, t, p in scored
                           if base.get(rid, {}).get("usable") and p == t and base[rid]["vote"] != t],
            "lost_ids": [rid for rid, t, p in scored
                         if base.get(rid, {}).get("usable") and p != t and base[rid]["vote"] == t],
        }

    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
