#!/usr/bin/env python3
"""Score one judge version against the human labels, and compare it to the previous best.

    python scoring.py \
        --corpus  .build_eval_from_annotations/corpus/rows.jsonl \
        --pred    .build_eval_from_annotations/predictions/v1.jsonl \
        --metric  balanced_accuracy \
        --runs 3 \
        --baseline-pred .build_eval_from_annotations/predictions/v0.jsonl   # optional: vs the best

Stdlib only, on purpose: this runs anywhere the corpus does, with no install step.

Every number the loop makes a decision on comes from here. Nothing in this file guesses a value:
a row with no usable prediction is EXCLUDED and counted (rubric §6), never scored as wrong.

Two headlines come out of this file and they are not the same number:

* ``headline`` — the metric over the **majority vote** of ``runs`` passes. This is the fitting
  signal the hill-climb optimises and compares across iterations.
* ``deployable`` — the mean of the metric computed over each pass **on its own**. A published
  Datadog evaluator makes ONE call per span; it does not vote. Wherever the report quotes a number
  the user will experience, it is this one (rubric §4).
"""

from __future__ import annotations

import argparse
import json
import math
import random
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path

BOOTSTRAP_SEED = 20260917  # fixed, so two runs of the scorer on one prediction file agree
BOOTSTRAP_RESAMPLES = 2000


# --------------------------------------------------------------------------- helpers


def wilson(successes: int, total: int, z: float = 1.96):
    """Wilson 95% interval — the honest one at n=13, unlike the normal approximation.

    Only valid for a PROPORTION (a count of successes out of a count of trials). Raw accuracy is
    one; balanced accuracy, macro-F1, kappa, mean credit and MAE are not, and quoting a Wilson
    interval beside them is a wrong uncertainty number. Those get ``bootstrap_ci``.
    """
    if total == 0:
        return (0.0, 0.0)
    p = successes / total
    denom = 1 + z * z / total
    centre = (p + z * z / (2 * total)) / denom
    half = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / denom
    return (round(max(0.0, centre - half), 4), round(min(1.0, centre + half), 4))


def mcnemar_exact(b: int, c: int) -> float:
    """Two-sided exact binomial p on the discordant pairs. b/c = rows only one version got right.

    This tests EXACT-MATCH CORRECTNESS, per row, and nothing else. It is not a test of the
    configured headline when that headline is balanced accuracy, macro-F1, kappa, mean credit or
    MAE — a change can move any of those while b == c. For the headline, use
    ``paired_bootstrap_delta``; both are reported, labelled, and never conflated.
    """
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
# EVERY comparison in this file goes through `key()` for that reason — including the flip rate,
# which otherwise reports a stable judge as unstable purely because it reordered a list.


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
    """Majority vote over the usable passes. A tie returns None and is reported as a tie.

    Grouped by canonical key, so two passes that answered the same multi-select in a different
    order count as agreeing rather than as a 1-1 tie. An odd ``--runs`` does NOT guarantee an odd
    number of *usable* passes — unparseable passes are dropped first — so ties are reachable at
    every setting and the caller must handle them (rubric §6).
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
    """One label out of a joint verdict. `field=None` means the verdict IS the label.

    `field` is the label's ``label_schema_id`` wherever the queue has one: names drift when a
    schema is edited and two labels can share one (SKILL.md Phase 1). A name is accepted so a
    hand-written corpus still scores, but the run should key joint verdicts by id.
    """
    if field is None:
        return value
    if not isinstance(value, dict):
        raise SystemExit(
            f"--label-field {field!r} needs a verdict whose label is an object of "
            f"label-id -> value; got {type(value).__name__}."
        )
    return value.get(field, _MISSING)


# --------------------------------------------------------------------------- metrics

# Direction matters in two places that silently produce a wrong number otherwise: the constant-class
# baseline (which must pick the laziest judge's BEST score, i.e. min for a loss) and every
# "did it improve?" comparison in the loop.
HIGHER_IS_BETTER = {
    "accuracy": True,
    "mean_credit": True,
    "balanced_accuracy": True,
    "f1": True,
    "f1_minority": True,
    "fbeta_minority": True,
    "macro_f1": True,
    "weighted_f1": True,
    "cohens_kappa": True,
    "spearman": True,
    "mae": False,
}
METRICS = sorted(HIGHER_IS_BETTER)


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


def per_class_recall_ci(pairs):
    """Recall with its Wilson interval, per class. Recall IS a proportion, so Wilson applies.

    A bare recall of 1.0 on 6 rows has a Wilson lower bound near 0.61 — "measurable" is generous
    and the interval is what says so. The report quotes both (SKILL.md Phase 7).
    """
    hit, total = defaultdict(int), defaultdict(int)
    for truth, pred in pairs:
        cls = key(truth)
        total[cls] += 1
        hit[cls] += int(key(truth) == key(pred))
    return {c: {"recall": round(hit[c] / total[c], 4), "rows": total[c],
                "wilson_95": wilson(hit[c], total[c])} for c in total}


def class_support(pairs):
    """Rows per truth class. A recall computed on 1 row is a coin toss with a decimal point."""
    return dict(Counter(key(t) for t, _ in pairs))


def _fbeta_for(pairs, positive: str, beta: float) -> float:
    tp = sum(key(t) == positive and key(p) == positive for t, p in pairs)
    fp = sum(key(t) != positive and key(p) == positive for t, p in pairs)
    fn = sum(key(t) == positive and key(p) != positive for t, p in pairs)
    if tp == 0:
        return 0.0
    precision, recall = tp / (tp + fp), tp / (tp + fn)
    b2 = beta * beta
    return (1 + b2) * precision * recall / (b2 * precision + recall)


def minority_class(pairs) -> str:
    classes = Counter(key(t) for t, _ in pairs)
    return min(classes, key=classes.get)


def precision_for(pairs, positive: str):
    """-> (precision, predicted_positive_count). None when the judge never predicted the class."""
    tp = sum(key(t) == positive and key(p) == positive for t, p in pairs)
    fp = sum(key(t) != positive and key(p) == positive for t, p in pairs)
    return (None if tp + fp == 0 else round(tp / (tp + fp), 4)), tp + fp


def _ranks(values):
    """Average ranks, ties shared — what Spearman needs."""
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        shared = (i + j) / 2 + 1
        for k in range(i, j + 1):
            ranks[order[k]] = shared
        i = j + 1
    return ranks


def spearman(pairs) -> float:
    """Rank correlation between the human's numbers and the judge's. Stdlib Pearson on ranks."""
    truths = [float(t) for t, _ in pairs]
    preds = [float(p) for _, p in pairs]
    if len(set(truths)) < 2 or len(set(preds)) < 2:
        return 0.0  # no variance on one side: the coefficient is undefined, not 1.0
    rt, rp = _ranks(truths), _ranks(preds)
    mt, mp = statistics.fmean(rt), statistics.fmean(rp)
    num = sum((a - mt) * (b - mp) for a, b in zip(rt, rp))
    den = math.sqrt(sum((a - mt) ** 2 for a in rt) * sum((b - mp) ** 2 for b in rp))
    return 0.0 if den == 0 else num / den


def score(pairs, metric: str, credit=None, beta: float = 1.0) -> float:
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
    if metric in ("f1", "f1_minority", "fbeta_minority"):
        classes = Counter(key(t) for t, _ in pairs)
        if len(classes) > 2:
            raise SystemExit(
                f"{metric!r} needs a two-class corpus; this one has {len(classes)} classes, so the "
                "'minority class' is whichever class happens to be rarest and the score says "
                "nothing about the rest. Use macro_f1, weighted_f1, cohens_kappa or mean_credit."
            )
        # beta > 1 weights recall (missing a bad row costs more), beta < 1 weights precision
        # (a false alarm costs more). beta == 1 is F1 (rubric §4: asymmetric costs are the user's).
        return round(_fbeta_for(pairs, minority_class(pairs),
                                beta if metric == "fbeta_minority" else 1.0), 4)
    if metric in ("macro_f1", "weighted_f1"):
        support = Counter(key(t) for t, _ in pairs)
        scores, weights = [], []
        for cls in support:
            scores.append(_fbeta_for(pairs, cls, 1.0))
            weights.append(support[cls] if metric == "weighted_f1" else 1)
        return round(sum(s * w for s, w in zip(scores, weights)) / sum(weights), 4)
    if metric == "cohens_kappa":
        observed = sum(key(t) == key(p) for t, p in pairs) / len(pairs)
        truths = Counter(key(t) for t, _ in pairs)
        preds = Counter(key(p) for _, p in pairs)
        expected = sum(truths[c] * preds.get(c, 0) for c in truths) / (len(pairs) ** 2)
        return round(0.0 if expected == 1 else (observed - expected) / (1 - expected), 4)
    if metric == "spearman":
        return round(spearman(pairs), 4)
    if metric == "mae":
        return round(sum(abs(float(t) - float(p)) for t, p in pairs) / len(pairs), 4)
    raise SystemExit(f"unknown metric {metric!r}. Implemented: {', '.join(METRICS)}.")


def constant_baseline(pairs, metric: str, credit=None, beta: float = 1.0) -> float:
    """What the laziest possible judge scores. The headline must beat this to mean anything.

    "Best" depends on the metric's direction: for MAE the laziest judge's best constant is the one
    with the LOWEST error. Taking a max there would report the worst possible constant as the bar
    to clear, and every judge would look like it had learned something.
    """
    seen, candidates = set(), []
    for truth, _ in pairs:  # one candidate per distinct class, keeping the original value shape
        if key(truth) not in seen:
            seen.add(key(truth))
            candidates.append(truth)
    scores = [score([(t, cand) for t, _ in pairs], metric, credit, beta) for cand in candidates]
    return max(scores) if HIGHER_IS_BETTER.get(metric, True) else min(scores)


def bootstrap_ci(pairs, metric: str, credit=None, beta: float = 1.0,
                 resamples: int = BOOTSTRAP_RESAMPLES):
    """Percentile bootstrap 95% CI on ANY headline, including the ones Wilson cannot touch.

    Row-level resampling with replacement. At n=13 this interval is embarrassingly wide — that is
    the point: it is the honest width, and the report shows it rather than a Wilson interval
    computed on a different quantity.
    """
    if len(pairs) < 2:
        return None
    rng = random.Random(BOOTSTRAP_SEED)
    n = len(pairs)
    values = []
    for _ in range(resamples):
        sample = [pairs[rng.randrange(n)] for _ in range(n)]
        values.append(score(sample, metric, credit, beta))
    values.sort()
    lo = values[int(0.025 * resamples)]
    hi = values[min(resamples - 1, int(0.975 * resamples))]
    return (round(lo, 4), round(hi, 4))


def paired_bootstrap_delta(triples, metric: str, credit=None, beta: float = 1.0,
                           resamples: int = BOOTSTRAP_RESAMPLES):
    """Is the HEADLINE better, on the rows both versions scored? -> {delta, ci, p}.

    triples = [(truth, candidate_pred, baseline_pred)]. Rows are resampled as units, so the two
    versions are always compared on the same rows — the paired part. This is the test that matches
    the configured metric; McNemar beside it tests exact-match correctness only.
    """
    if len(triples) < 2:
        return None
    cand = [(t, c) for t, c, _ in triples]
    base = [(t, b) for t, _, b in triples]
    observed = score(cand, metric, credit, beta) - score(base, metric, credit, beta)
    rng = random.Random(BOOTSTRAP_SEED)
    n = len(triples)
    deltas = []
    for _ in range(resamples):
        idx = [rng.randrange(n) for _ in range(n)]
        c = score([(triples[i][0], triples[i][1]) for i in idx], metric, credit, beta)
        b = score([(triples[i][0], triples[i][2]) for i in idx], metric, credit, beta)
        deltas.append(c - b)
    deltas.sort()
    lo = deltas[int(0.025 * resamples)]
    hi = deltas[min(resamples - 1, int(0.975 * resamples))]
    share_le0 = sum(d <= 0 for d in deltas) / resamples
    share_ge0 = sum(d >= 0 for d in deltas) / resamples
    return {
        "delta": round(observed, 4),
        "ci_95": (round(lo, 4), round(hi, 4)),
        "p_two_sided": round(min(1.0, 2 * min(share_le0, share_ge0)), 4),
        "tests": f"the configured headline ({metric}), paired on the rows both versions scored",
    }


# --------------------------------------------------------------------------- load


def load_passes(path: str, label_field=None):
    """-> ({row_id: {run_index: label}}, {row_id: unparseable_count}, {row_id: [confidences]}, ...)

    The raw passes, un-aggregated. Majority voting reads this; so does the single-pass deployable
    score, which needs each pass on its own precisely because the shipped evaluator does not vote.
    """
    by_run = defaultdict(dict)
    confidences = defaultdict(list)
    unparseable = defaultdict(int)
    no_confidence = defaultdict(int)
    for line in Path(path).read_text().splitlines():
        if not line.strip():
            continue
        rec = json.loads(line)
        row_id = rec["id"]
        if rec.get("unparseable") or "label" not in rec:
            unparseable[row_id] += 1
            continue
        label = pick(rec["label"], label_field)
        if label is _MISSING:  # the pass answered, but not for this label of a joint verdict
            unparseable[row_id] += 1
            continue
        by_run[row_id][rec.get("run", len(by_run[row_id]))] = label
        if isinstance(rec.get("confidence"), (int, float)) and not isinstance(rec.get("confidence"), bool):
            confidences[row_id].append(rec["confidence"])
        else:
            no_confidence[row_id] += 1
    return by_run, unparseable, confidences, no_confidence


def load_predictions(path: str, label_field=None, min_usable_passes: int = 1):
    """-> {row_id: {"vote", "flipped", "usable", "tie", "insufficient_usable_passes", ...}}

    ``min_usable_passes`` is how many passes must have parsed before a vote is allowed to stand.
    An odd ``--runs`` does not make the usable count odd, so a row can arrive here with one usable
    pass out of three and a "unanimous" vote — that is one opinion wearing a majority's clothes.
    Below the floor the row is excluded and counted under its own reason, not lumped in with the
    unparseables and not scored as wrong (rubric §6).

    Confidence is carried through and reported, never used to weigh the vote: the prediction is the
    majority label and nothing else. A row's confidence is the mean of the passes that reported a
    usable one; ``None`` when no pass did.
    """
    by_run, unparseable, confidences, no_confidence = load_passes(path, label_field)
    out = {}
    for row_id in set(by_run) | set(unparseable):
        labels = list(by_run.get(row_id, {}).values())
        vote = majority(labels)
        tie = vote is None and bool(labels)
        short = len(labels) < min_usable_passes
        seen = confidences.get(row_id, [])
        out[row_id] = {
            "vote": vote,
            # set equality, exactly as the vote uses: ["a","b"] and ["b","a"] are one answer, and
            # counting them as a flip reports a stable judge as unstable (rubric §6).
            "flipped": len({key(v) for v in labels}) > 1,
            "usable": vote is not None and not short,
            "usable_passes": len(labels),
            "tie": tie,
            "insufficient_usable_passes": short,
            "unparseable_passes": unparseable.get(row_id, 0),
            "confidence": round(sum(seen) / len(seen), 1) if seen else None,
            "passes_without_confidence": no_confidence.get(row_id, 0),
        }
    return out


def single_pass_scores(by_run, truth, metric: str, credit=None, beta: float = 1.0):
    """The deployable number: each pass scored ALONE, because the evaluator makes one call.

    Returns the per-pass scores plus their mean and spread. The mean is what the report quotes as
    the score the user will actually experience; the spread is the run-to-run noise that
    ``min_delta`` is derived from.
    """
    runs = sorted({r for row in by_run.values() for r in row})
    per_run = []
    for run_index in runs:
        pairs = [(truth[rid], by_run[rid][run_index]) for rid in truth
                 if rid in by_run and run_index in by_run[rid]]
        if pairs:
            per_run.append({"run": run_index, "rows_scored": len(pairs),
                            "score": score(pairs, metric, credit, beta)})
    if not per_run:
        return None
    values = [r["score"] for r in per_run]
    return {
        "per_run": per_run,
        "mean": round(statistics.fmean(values), 4),
        "stdev": round(statistics.stdev(values), 4) if len(values) > 1 else 0.0,
        "min": round(min(values), 4),
        "max": round(max(values), 4),
        "note": "one judge call per row, no vote — this is what a published evaluator does",
    }


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
    ap.add_argument("--metric", default="balanced_accuracy", choices=METRICS)
    ap.add_argument("--beta", type=float, default=1.0,
                    help="for fbeta_minority: >1 weights recall, <1 weights precision. The user's "
                         "answer to 'do false positives and false negatives cost the same?'")
    ap.add_argument("--precision-floor", type=float,
                    help="report whether precision on the expensive class clears this floor; "
                         "a keep that breaches it is a discard (rubric §4)")
    ap.add_argument("--precision-floor-class",
                    help="which class the floor applies to (default: the minority class)")
    ap.add_argument("--split", default="train", choices=["train", "holdout", "all"])
    ap.add_argument("--runs", type=int,
                    help="passes per row this prediction file was produced with. Sets the "
                         "usable-pass floor so a 1-of-3 'unanimous' vote is excluded, not counted.")
    ap.add_argument("--baseline-pred", help="predictions of the current best, for the paired tests")
    ap.add_argument("--label-field",
                    help="for a joint judge: which label of the verdict to score, by "
                         "label_schema_id (names drift — SKILL.md Phase 1)")
    ap.add_argument("--match", default="exact", choices=["exact", "jaccard", "similarity_group"],
                    help="how much credit a partly-right answer gets (default: none)")
    ap.add_argument("--groups", help="JSON file: {\"groups\": [[classA, classB], ...], "
                                     "\"partial_credit\": 0.5} for --match similarity_group")
    ap.add_argument("--small-class-floor", type=int, default=6,
                    help="truth classes with fewer rows than this are reported as too uncertain "
                         "to read as a measurement")
    ap.add_argument("--bootstrap", type=int, default=BOOTSTRAP_RESAMPLES,
                    help="resamples for the headline CI and the paired test (0 disables)")
    args = ap.parse_args()

    groups, partial = None, 0.5
    if args.groups:
        spec = json.loads(Path(args.groups).read_text())
        groups, partial = spec.get("groups", []), spec.get("partial_credit", 0.5)
    if args.match == "similarity_group" and not groups:
        sys.exit("--match similarity_group needs --groups: the taxonomy is the user's, not the judge's.")
    credit = make_credit(args.match, groups, partial)
    min_usable = (args.runs // 2 + 1) if args.runs else 1

    rows = [json.loads(l) for l in Path(args.corpus).read_text().splitlines() if l.strip()]
    if args.split != "all":
        rows = [r for r in rows if r.get("split", "train") == args.split]
    truth = {}
    for r in rows:
        value = pick(r["label"], args.label_field)
        if value is _MISSING:
            sys.exit(f"corpus row {r['id']} has no label {args.label_field!r}.")
        truth[r["id"]] = value

    preds = load_predictions(args.pred, args.label_field, min_usable)
    by_run, _, _, _ = load_passes(args.pred, args.label_field)
    scored = [(rid, truth[rid], preds[rid]["vote"]) for rid in truth if preds.get(rid, {}).get("usable")]
    excluded = [rid for rid in truth if not preds.get(rid, {}).get("usable")]
    pairs = [(t, p) for _, t, p in scored]

    correct = {rid: (key(t) == key(p)) for rid, t, p in scored}
    headline = score(pairs, args.metric, credit, args.beta)
    hits = sum(correct.values())
    support = class_support(pairs)
    small = {c: n for c, n in support.items() if n < args.small_class_floor}

    result = {
        "metric": args.metric,
        "metric_direction": "higher_is_better" if HIGHER_IS_BETTER[args.metric] else "lower_is_better",
        "label_field": args.label_field,
        "match_mode": args.match,
        "split": args.split,
        "headline": headline,
        "headline_basis": f"majority vote of {args.runs or 'all'} passes — the FITTING signal",
        "headline_ci_95": bootstrap_ci(pairs, args.metric, credit, args.beta, args.bootstrap)
                          if args.bootstrap else None,
        "headline_ci_method": "percentile bootstrap over rows" if args.bootstrap else "disabled",
        "deployable": single_pass_scores(by_run, truth, args.metric, credit, args.beta),
        "rows_scored": len(pairs),
        "rows_excluded_unusable": len(excluded),
        "excluded_ids": excluded,
        "excluded_reasons": {
            "all_passes_unparseable": sorted(rid for rid in excluded
                                             if preds.get(rid, {}).get("usable_passes", 0) == 0),
            "tie": sorted(rid for rid in excluded if preds.get(rid, {}).get("tie")),
            "insufficient_usable_passes": sorted(
                rid for rid in excluded if preds.get(rid, {}).get("insufficient_usable_passes")),
            "no_prediction_for_row": sorted(rid for rid in excluded if rid not in preds),
        },
        "raw_accuracy": round(hits / len(pairs), 4) if pairs else 0.0,
        # Wilson applies to a proportion. It describes raw accuracy and NOTHING else here — the
        # headline's own interval is headline_ci_95 above.
        "wilson_95_on_raw_accuracy": wilson(hits, len(pairs)),
        "constant_class_baseline": constant_baseline(pairs, args.metric, credit, args.beta) if pairs else 0.0,
        "mean_credit": score(pairs, "mean_credit", credit),
        "per_class_recall": per_class_recall(pairs),
        "per_class_recall_ci": per_class_recall_ci(pairs),
        "class_support": support,
        # recall on 3 rows is not a measurement; the report must say so rather than quoting it
        "classes_below_floor": small,
        "confusion": {f"truth={t}|pred={p}": n for (t, p), n in confusion(pairs).items()},
        "flip_rate": round(sum(preds[rid]["flipped"] for rid, _, _ in scored) / len(scored), 4) if scored else 0.0,
        "correct_by_row": correct,
        "confidence": calibration(scored, preds),
    }

    if args.precision_floor is not None and pairs:
        cls = args.precision_floor_class or minority_class(pairs)
        observed, predicted = precision_for(pairs, cls)
        result["precision_floor"] = {
            "class": cls,
            "floor": args.precision_floor,
            "observed": observed,
            "predicted_positive": predicted,
            "met": observed is not None and observed >= args.precision_floor,
            "note": "None means the judge never predicted this class — an unmet floor, not a pass",
        }

    if args.baseline_pred:
        base = load_predictions(args.baseline_pred, args.label_field, min_usable)
        paired = [(rid, t, p, base[rid]["vote"]) for rid, t, p in scored
                  if base.get(rid, {}).get("usable")]
        b = sum(1 for _, t, p, bp in paired if key(p) == key(t) and key(bp) != key(t))
        c = sum(1 for _, t, p, bp in paired if key(p) != key(t) and key(bp) == key(t))
        result["vs_baseline"] = {
            "rows_paired": len(paired),
            "gained": b,
            "lost": c,
            "mcnemar_p": mcnemar_exact(b, c),
            "mcnemar_tests": "exact-match correctness per row — NOT the configured headline",
            "headline_delta": paired_bootstrap_delta(
                [(t, p, bp) for _, t, p, bp in paired], args.metric, credit, args.beta,
                args.bootstrap) if args.bootstrap else None,
            "gained_ids": [rid for rid, t, p, bp in paired if key(p) == key(t) and key(bp) != key(t)],
            "lost_ids": [rid for rid, t, p, bp in paired if key(p) != key(t) and key(bp) == key(t)],
        }

    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
