#!/usr/bin/env python3
"""Score one judge version against the human labels, and compare it to the previous best.

    python scoring.py \
        --corpus  .build_eval_from_annotations/corpus/rows.jsonl \
        --pred    .build_eval_from_annotations/predictions/v1.jsonl \
        --metric  balanced_accuracy \
        --baseline-pred .build_eval_from_annotations/predictions/v0.jsonl   # optional: McNemar vs the best

Stdlib only, on purpose: this runs anywhere the corpus does, with no install step.

Every number the loop makes a decision on comes from here. Nothing in this file guesses a value:
a row with no usable prediction is EXCLUDED and counted (rubric §6), never scored as wrong.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
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


# --------------------------------------------------------------------------- label identity

# A categorical label value arrives from the annotation API as a LIST, even when only one value was
# chosen (`["permanent"]`), and a multi-select row carries several. Truth and prediction therefore
# have to be compared as sets, not as JSON text: `["a","b"]` and `["b","a"]` are the same answer.


def _atom(value):
    """One class, hashable. Scalars stay themselves so the report reads in the user's own words."""
    return value if isinstance(value, (str, int, float, bool)) or value is None \
        else json.dumps(value, sort_keys=True)


def as_set(value) -> frozenset:
    """Any label value -> the set of classes it names. A scalar is a set of one."""
    if isinstance(value, (list, tuple, set, frozenset)):
        return frozenset(_atom(v) for v in value)
    return frozenset([_atom(value)])


def key(value) -> str:
    """Canonical, order-insensitive, READABLE identity of a label value.

    Readable matters: this string is what lands in the confusion matrix and the per-class recall of
    the final report, where a human has to recognise their own classes in it.
    """
    parts = sorted(str(a) for a in as_set(value))
    return "+".join(parts) if parts else "<empty>"


def make_credit(mode: str, groups=None, partial: float = 0.5):
    """-> credit(truth, pred) in [0,1]. How much of a right answer a prediction is.

    `exact` is all-or-nothing and is the only honest default. The graded modes exist because some
    label sets have near-misses that are genuinely worth more than a wrong answer — but the grading
    has to come from the user's own taxonomy, never from the judge's opinion of its own answer:

    * `jaccard` — overlap over union, for multi-select labels where getting 1 of 2 right is
      partial work done;
    * `similarity_group` — `partial` credit when truth and prediction fall in the same
      user-supplied group of classes, mirroring an experiment's own similarity metric.
    """
    lookup = {}
    for index, group in enumerate(groups or []):
        for member in group:
            lookup[_atom(member)] = index

    def credit(truth, pred) -> float:
        t, p = as_set(truth), as_set(pred)
        if t == p:
            return 1.0
        if mode == "exact":
            return 0.0
        if mode == "jaccard":
            return len(t & p) / len(t | p) if (t | p) else 0.0
        if mode == "similarity_group":
            groups_t = {lookup[m] for m in t if m in lookup}
            groups_p = {lookup[m] for m in p if m in lookup}
            return partial if groups_t and groups_t == groups_p else 0.0
        raise SystemExit(f"unknown match mode {mode!r}")

    return credit


def majority(values: list):
    """Majority vote over the runs. Ties (possible only with an even --runs) return None.

    Grouped by canonical key, so two passes that answered the same multi-select in a different
    order count as agreeing rather than as a 1-1 tie.
    """
    if not values:
        return None
    counts = Counter(key(v) for v in values).most_common()
    if len(counts) > 1 and counts[0][1] == counts[1][1]:
        return None
    winner = counts[0][0]
    return next(v for v in values if key(v) == winner)


_MISSING = object()


def pick(value, field):
    """One label out of a joint verdict. `field=None` means the verdict IS the label."""
    if field is None:
        return value
    if not isinstance(value, dict):
        raise SystemExit(
            f"--label-field {field!r} needs a verdict whose label is an object of "
            f"label-name -> value; got {type(value).__name__}."
        )
    return value.get(field, _MISSING)


# --------------------------------------------------------------------------- metrics


def confusion(pairs):
    """pairs = [(truth, pred)]. Returns {(truth, pred): count} over whatever classes exist."""
    matrix = defaultdict(int)
    for truth, pred in pairs:
        matrix[(key(truth), key(pred))] += 1
    return matrix


def per_class_recall(pairs):
    hit, total = defaultdict(int), defaultdict(int)
    for truth, pred in pairs:
        cls = key(truth)
        total[cls] += 1
        hit[cls] += int(key(truth) == key(pred))
    return {k: round(hit[k] / total[k], 4) for k in total}


def class_support(pairs):
    """Rows per truth class. A recall computed on 1 row is a coin toss with a decimal point."""
    return dict(Counter(key(t) for t, _ in pairs))


def score(pairs, metric: str, credit=None) -> float:
    if not pairs:
        return 0.0
    credit = credit or make_credit("exact")
    if metric == "accuracy":
        return round(sum(key(t) == key(p) for t, p in pairs) / len(pairs), 4)
    if metric == "mean_credit":
        # the graded headline: partial answers score partially. Only meaningful with a --match
        # mode other than exact, where it is identical to accuracy.
        return round(sum(credit(t, p) for t, p in pairs) / len(pairs), 4)
    if metric == "balanced_accuracy":
        recalls = per_class_recall(pairs)
        return round(sum(recalls.values()) / len(recalls), 4)
    if metric in ("f1", "f1_minority"):
        classes = Counter(key(t) for t, _ in pairs)
        if len(classes) > 2:
            raise SystemExit(
                f"{metric!r} needs a two-class corpus; this one has {len(classes)} classes, so the "
                "'minority class' is whichever class happens to be rarest and the score says "
                "nothing about the rest. Use macro_f1, cohens_kappa or mean_credit."
            )
        positive = min(classes, key=classes.get)  # minority class is the positive one
        tp = sum(key(t) == positive and key(p) == positive for t, p in pairs)
        fp = sum(key(t) != positive and key(p) == positive for t, p in pairs)
        fn = sum(key(t) == positive and key(p) != positive for t, p in pairs)
        if tp == 0:
            return 0.0
        precision, recall = tp / (tp + fp), tp / (tp + fn)
        return round(2 * precision * recall / (precision + recall), 4)
    if metric == "macro_f1":
        classes = {key(t) for t, _ in pairs}
        scores = []
        for cls in classes:
            tp = sum(key(t) == cls and key(p) == cls for t, p in pairs)
            fp = sum(key(t) != cls and key(p) == cls for t, p in pairs)
            fn = sum(key(t) == cls and key(p) != cls for t, p in pairs)
            scores.append(0.0 if tp == 0 else 2 * tp / (2 * tp + fp + fn))
        return round(sum(scores) / len(scores), 4)
    if metric == "cohens_kappa":
        observed = sum(key(t) == key(p) for t, p in pairs) / len(pairs)
        truths = Counter(key(t) for t, _ in pairs)
        preds = Counter(key(p) for _, p in pairs)
        expected = sum(truths[c] * preds.get(c, 0) for c in truths) / (len(pairs) ** 2)
        return round(0.0 if expected == 1 else (observed - expected) / (1 - expected), 4)
    if metric == "mae":
        return round(sum(abs(float(t) - float(p)) for t, p in pairs) / len(pairs), 4)
    raise SystemExit(f"unknown metric {metric!r}")


def constant_baseline(pairs, metric: str, credit=None) -> float:
    """What the laziest possible judge scores. The headline must beat this to mean anything."""
    seen, candidates = set(), []
    for truth, _ in pairs:  # one candidate per distinct class, keeping the original value shape
        if key(truth) not in seen:
            seen.add(key(truth))
            candidates.append(truth)
    return max(score([(t, cand) for t, _ in pairs], metric, credit) for cand in candidates)


# --------------------------------------------------------------------------- load


def load_predictions(path: str, label_field=None):
    """-> {row_id: {"vote": label|None, "flipped": bool, "usable": bool, "confidence": int|None}}

    Confidence is carried through and reported, never used to weigh the vote: the prediction is the
    majority label and nothing else (rubric §6). A row's confidence is the mean of the passes that
    reported a usable one; ``None`` when no pass did.
    """
    passes = defaultdict(list)
    confidences = defaultdict(list)
    unparseable = defaultdict(int)
    no_confidence = defaultdict(int)
    for line in Path(path).read_text().splitlines():
        if not line.strip():
            continue
        rec = json.loads(line)
        if rec.get("unparseable") or "label" not in rec:
            unparseable[rec["id"]] += 1
            continue
        label = pick(rec["label"], label_field)
        if label is _MISSING:  # the pass answered, but not for this label of a joint verdict
            unparseable[rec["id"]] += 1
            continue
        passes[rec["id"]].append(label)
        if isinstance(rec.get("confidence"), (int, float)) and not isinstance(rec.get("confidence"), bool):
            confidences[rec["id"]].append(rec["confidence"])
        else:
            no_confidence[rec["id"]] += 1
    out = {}
    for row_id in set(passes) | set(unparseable):
        labels = passes.get(row_id, [])
        vote = majority(labels)
        seen = confidences.get(row_id, [])
        out[row_id] = {
            "vote": vote,
            "flipped": len({json.dumps(v) for v in labels}) > 1,
            "usable": vote is not None,
            "unparseable_passes": unparseable.get(row_id, 0),
            "confidence": round(sum(seen) / len(seen), 1) if seen else None,
            "passes_without_confidence": no_confidence.get(row_id, 0),
        }
    return out


def calibration(scored, preds):
    """Is the judge's stated confidence worth anything? Compare it against being right.

    A judge equally confident when wrong as when right is telling the user nothing, and that is a
    reportable fact about the judge — not a reason to change the score.
    """
    with_conf = [(preds[rid]["confidence"], key(t) == key(p)) for rid, t, p in scored
                 if preds[rid]["confidence"] is not None]
    missing = sum(1 for rid, _, _ in scored if preds[rid]["confidence"] is None)
    if not with_conf:
        return {"rows_with_confidence": 0, "rows_without_confidence": missing}
    right = [c for c, ok in with_conf if ok]
    wrong = [c for c, ok in with_conf if not ok]
    bands = {}
    for lo, hi in ((0, 59), (60, 79), (80, 89), (90, 100)):
        band = [ok for c, ok in with_conf if lo <= c <= hi]
        if band:
            bands[f"{lo}-{hi}%"] = {"rows": len(band), "accuracy": round(sum(band) / len(band), 4)}
    return {
        "rows_with_confidence": len(with_conf),
        "rows_without_confidence": missing,
        "mean_confidence": round(sum(c for c, _ in with_conf) / len(with_conf), 1),
        "mean_confidence_when_right": round(sum(right) / len(right), 1) if right else None,
        "mean_confidence_when_wrong": round(sum(wrong) / len(wrong), 1) if wrong else None,
        "accuracy_by_confidence_band": bands,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus", required=True)
    ap.add_argument("--pred", required=True)
    ap.add_argument("--metric", default="balanced_accuracy")
    ap.add_argument("--split", default="train", choices=["train", "holdout", "all"])
    ap.add_argument("--baseline-pred", help="predictions of the current best, for McNemar")
    ap.add_argument("--label-field", help="for a joint judge: which label of the verdict to score")
    ap.add_argument("--match", default="exact", choices=["exact", "jaccard", "similarity_group"],
                    help="how much credit a partly-right answer gets (default: none)")
    ap.add_argument("--groups", help="JSON file: {\"groups\": [[classA, classB], ...], "
                                     "\"partial_credit\": 0.5} for --match similarity_group")
    ap.add_argument("--small-class-floor", type=int, default=6,
                    help="truth classes with fewer rows than this are reported as not measurable")
    args = ap.parse_args()

    groups, partial = None, 0.5
    if args.groups:
        spec = json.loads(Path(args.groups).read_text())
        groups, partial = spec.get("groups", []), spec.get("partial_credit", 0.5)
    if args.match == "similarity_group" and not groups:
        sys.exit("--match similarity_group needs --groups: the taxonomy is the user's, not the judge's.")
    credit = make_credit(args.match, groups, partial)

    rows = [json.loads(l) for l in Path(args.corpus).read_text().splitlines() if l.strip()]
    if args.split != "all":
        rows = [r for r in rows if r.get("split", "train") == args.split]
    truth = {}
    for r in rows:
        value = pick(r["label"], args.label_field)
        if value is _MISSING:
            sys.exit(f"corpus row {r['id']} has no label {args.label_field!r}.")
        truth[r["id"]] = value

    preds = load_predictions(args.pred, args.label_field)
    scored = [(rid, truth[rid], preds[rid]["vote"]) for rid in truth if preds.get(rid, {}).get("usable")]
    excluded = [rid for rid in truth if not preds.get(rid, {}).get("usable")]
    pairs = [(t, p) for _, t, p in scored]

    correct = {rid: (key(t) == key(p)) for rid, t, p in scored}
    headline = score(pairs, args.metric, credit)
    hits = sum(correct.values())
    support = class_support(pairs)
    small = {c: n for c, n in support.items() if n < args.small_class_floor}

    result = {
        "metric": args.metric,
        "label_field": args.label_field,
        "match_mode": args.match,
        "split": args.split,
        "headline": headline,
        "rows_scored": len(pairs),
        "rows_excluded_unusable": len(excluded),
        "excluded_ids": excluded,
        "raw_accuracy": round(hits / len(pairs), 4) if pairs else 0.0,
        "wilson_95_on_accuracy": wilson(hits, len(pairs)),
        "constant_class_baseline": constant_baseline(pairs, args.metric, credit) if pairs else 0.0,
        "mean_credit": score(pairs, "mean_credit", credit),
        "per_class_recall": per_class_recall(pairs),
        "class_support": support,
        # recall on 3 rows is not a measurement; the report must say so rather than quoting it
        "classes_below_floor": small,
        "confusion": {f"truth={t}|pred={p}": n for (t, p), n in confusion(pairs).items()},
        "flip_rate": round(sum(preds[rid]["flipped"] for rid, _, _ in scored) / len(scored), 4) if scored else 0.0,
        "correct_by_row": correct,
        "confidence": calibration(scored, preds),
    }

    if args.baseline_pred:
        base = load_predictions(args.baseline_pred)
        b = c = 0  # b: only candidate right, c: only baseline right
        for rid, t, p in scored:
            if not base.get(rid, {}).get("usable"):
                continue
            base_right, cand_right = key(base[rid]["vote"]) == key(t), key(p) == key(t)
            b += int(cand_right and not base_right)
            c += int(base_right and not cand_right)
        result["vs_baseline"] = {
            "gained": b,
            "lost": c,
            "mcnemar_p": mcnemar_exact(b, c),
            "gained_ids": [rid for rid, t, p in scored if base.get(rid, {}).get("usable")
                           and key(p) == key(t) and key(base[rid]["vote"]) != key(t)],
            "lost_ids": [rid for rid, t, p in scored if base.get(rid, {}).get("usable")
                         and key(p) != key(t) and key(base[rid]["vote"]) == key(t)],
        }

    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
