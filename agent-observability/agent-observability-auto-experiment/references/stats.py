"""Copy this to `.auto_experiment/stats.py` in iteration 1, beside the harness.

The confidence numbers the loop reports, in one place so every iteration computes them the same way.

Two instruments, two scopes — `references/rubrics.md` "Noise & keep/discard policy" is emphatic that
they answer different questions and that neither may be quoted as evidence for the other's:

  * `t_two_sample` over RUN MEANS answers "is the mean different across reps?". Its n is the run
    count (3–5), so it is weak by construction.
  * `paired_bootstrap_delta` over PER-CASE MEANS answers "would this hold on another draw of
    cases?" — which is the question a holdout exists to answer, so it is the primary evidence for
    the test headline. Its n is the case count.

Neither is a keep gate. The keep is decided by the matrix in the rubric (test point estimate in the
goal's direction, val did not run away from test, mechanism audit passed, no guardrail regression,
no unexplained memorization hit); these functions only LABEL what survives it.

The paired bootstrap and the seed are lifted from the sibling skill
`agent-observability-build-eval-from-annotations/references/scoring.py`, deliberately by copy rather
than by import: that module's `metric` argument comes from a closed classification list
(`balanced_accuracy`, `macro_f1`, `cohens_kappa`, …) and expects `(truth, prediction)` label pairs,
while this skill's rows are `{id, score: float}`. Sharing the SEED keeps the two skills' intervals
reproducible side by side; sharing the module would couple them to each other's metric vocabulary.
`mcnemar` and per-class recall from that file do not apply here — there are no classes.

Usage from the loop, with the harness's `per_case_means` from each side:

    best = json.loads(best_stdout)["per_case_means"]
    cand = json.loads(cand_stdout)["per_case_means"]
    paired_bootstrap_delta(best, cand)   # -> {"delta", "ci_95", "p_two_sided", "n_paired"}
"""

from __future__ import annotations

import math
import random

# Same constant as the sibling skill's scorer, so two runs (and two skills) agree to the digit.
BOOTSTRAP_SEED = 20260917
BOOTSTRAP_RESAMPLES = 2000


def paired_bootstrap_delta(
    best: dict,
    candidate: dict,
    resamples: int = BOOTSTRAP_RESAMPLES,
) -> "dict | None":
    """Is the candidate better, on the cases BOTH variants scored? -> {delta, ci_95, p, n_paired}.

    `best` and `candidate` are `{case_id: mean_score_across_that_case's_usable_reps}` — the harness's
    `per_case_means`. Cases are resampled **as units**, so the two variants are always compared on
    the same cases: that pairing is what removes per-case difficulty, which is normally the largest
    variance term and the reason a t-test over three run means cannot resolve a real effect.

    Only the intersection is used. The two variants' key sets can differ when a case errored on
    every rep of one of them — which is exactly why the mechanism audit compares the paired set and
    the full set, and fails the iteration when they disagree by more than `min_delta`.

    Returns None when fewer than 2 cases are shared; a CI over one case is not a CI.
    """
    ids = sorted(set(best) & set(candidate))
    if len(ids) < 2:
        return None
    pairs = [(best[i], candidate[i]) for i in ids]
    observed = _mean([c for _, c in pairs]) - _mean([b for b, _ in pairs])

    rng = random.Random(BOOTSTRAP_SEED)
    n = len(pairs)
    deltas = []
    for _ in range(resamples):
        idx = [rng.randrange(n) for _ in range(n)]
        b = _mean([pairs[i][0] for i in idx])
        c = _mean([pairs[i][1] for i in idx])
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
        "n_paired": n,
        "scope": "paired over cases — would this hold on another draw of datapoints?",
    }


def t_two_sample(
    after_mean: float,
    after_stdev: float,
    best_mean: float,
    best_stdev: float,
    runs: int,
) -> "dict | None":
    """The run-means t-test that labels confidence. Returns None when SE_diff is 0.

    `SE_diff = √(after_stdev²/runs + best_stdev²/runs)` — the standard error of a DIFFERENCE of
    means, not a single run's stdev. Raw stdev is a property of the metric and does not shrink as
    runs are added, which is why the loop never gates on a raw-stdev band: such a gate could never
    be cleared by power and would discard real effects forever.

    None means the metric is deterministic on both sides (the common case for the ground-truth
    checkers this skill prefers). The t is then undefined, not infinite: the caller labels by
    `|delta| >= min_delta` instead. Guard the division at the call site.
    """
    se_diff = math.sqrt((after_stdev ** 2) / runs + (best_stdev ** 2) / runs)
    if se_diff == 0:
        return None
    delta = after_mean - best_mean
    return {
        "delta": round(delta, 4),
        "se_diff": round(se_diff, 6),
        "t_stat": round(abs(delta) / se_diff, 4),
        "scope": "two-sample over run means — is the mean different across reps?",
    }


def wilson(successes: int, total: int, z: float = 1.96) -> "tuple[float, float] | None":
    """Wilson interval on a pass rate. ONLY valid when every per-case score is exactly 0 or 1.

    Report it beside the bootstrap CI on a binary metric, where it is the tighter and better-behaved
    interval at small n. On a continuous score it is meaningless — the caller must check first,
    which is what `is_binary` is for.
    """
    if total <= 0:
        return None
    p = successes / total
    denom = 1 + z ** 2 / total
    centre = (p + z ** 2 / (2 * total)) / denom
    half = z * math.sqrt(p * (1 - p) / total + z ** 2 / (4 * total ** 2)) / denom
    return (round(max(0.0, centre - half), 4), round(min(1.0, centre + half), 4))


def is_binary(per_case_means: dict) -> bool:
    """True when every case scored exactly 0 or 1 — the precondition for `wilson`."""
    return all(v in (0, 1, 0.0, 1.0) for v in per_case_means.values())


def noise_floor(per_case_means: dict, resamples: int = BOOTSTRAP_RESAMPLES) -> "float | None":
    """Half-width of the paired-difference 95% CI against an identical variant.

    This is the number the climbability gate (Step 2.6) puts next to headroom and
    `min_shippable_delta`: the smallest difference this eval could distinguish from zero at the
    current case count and rep count. Estimated by bootstrapping the split's own per-case means
    against themselves with the pairing broken — i.e. how much the mean alone moves under
    resampling — which is the right order of magnitude without spending a second eval.
    """
    values = list(per_case_means.values())
    if len(values) < 2:
        return None
    rng = random.Random(BOOTSTRAP_SEED)
    n = len(values)
    means = []
    for _ in range(resamples):
        means.append(_mean([values[rng.randrange(n)] for _ in range(n)]))
    means.sort()
    lo = means[int(0.025 * resamples)]
    hi = means[min(resamples - 1, int(0.975 * resamples))]
    return round((hi - lo) / 2, 4)


def _mean(values: list) -> float:
    return sum(values) / len(values)
