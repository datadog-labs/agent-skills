"""Skeleton for `.auto_experiment/eval_harness.py`.

Copy this into `.auto_experiment/eval_harness.py` in iteration 1, then fill in the two TODOs:
`generate_output` (run the REAL code under test) and `judge` (a REAL LLM-as-judge call).

Hard rules (see references/rubrics.md):
  * NO score literals / hard-coded score arrays anywhere in this file. Every score is returned
    by `judge()` running over real data.
  * Score only scoreable target lines; EXCLUDE non-target/infra lines from the mean entirely
    (do not score them 0). The runner below skips a line when `generate_output` returns None.
  * The harness is written ONCE and reused verbatim across iterations — only the code under
    test (imported by `generate_output`) changes between iterations.

Usage: `python .auto_experiment/eval_harness.py`  -> writes eval_results.jsonl, prints
  {"mean", "stdev", "runs", "scored", "excluded", "run_means"}.

Noise: `generate_output` (and an LLM judge) are stochastic, so a single run's mean is a
noisy estimate. The runner re-runs the WHOLE eval `AUTO_EXP_RUNS` times (default 3) and
reports the mean-of-runs plus the across-run stdev. The loop feeds that stdev into the
standard error of the difference of means, `SE_diff = √(sd_cand²/n + sd_best²/n)`, and keeps a
change only if it is significant by a two-sample t-test (`|Δ|/SE_diff ≥ 2`, or `|Δ| ≥ min_delta`
when SE_diff is 0) — NOT if it clears a raw-stdev band (raw stdev doesn't shrink with runs). Only
the mean/stdev are computed here; the gate itself lives in the loop. See references/rubrics.md "Noise &
keep/discard policy". Point at a specific data split with `AUTO_EXP_DATA` (default data.jsonl).
"""

from __future__ import annotations

import json
import os
import statistics
from pathlib import Path

HERE = Path(__file__).parent
DATA = Path(os.environ.get("AUTO_EXP_DATA") or (HERE / "data.jsonl"))
RESULTS = HERE / "eval_results.jsonl"

# How many times to re-run the full eval to estimate the noise floor. >=3 so the loop
# can tell a real move from run-to-run wiggle. Same value across every iteration.
RUNS = max(1, int(os.environ.get("AUTO_EXP_RUNS", "3")))

# The EVALUATOR text (config `evaluators` field), copied from .auto_experiment/config.json and used
# verbatim as the judge rubric so scoring is reproducible. This is the `evaluators` field, NOT
# `goal` — `goal` is the optimization target; the judge must score against `evaluators`. Never
# score against `goal`.
EVALUATORS = os.environ.get("AUTO_EXP_EVALUATORS", "<paste the config `evaluators` rubric here>")


def generate_output(line: dict) -> "str | None":
    """Run the REAL code under test on ONE datapoint and return its output.

    TODO: import the real entrypoint from the target file(s) and call it with the datapoint's
    input. If the import fails (e.g. ddtrace.llmobs bus-errors in some sandboxes), copy the
    needed function into this file with ONLY the offending import stubbed; reconstruct from
    source as a last resort.

    Return None to EXCLUDE this line from the eval set (non-target / infra line, or no scoreable
    target span). Excluded lines are out of both numerator and denominator — never scored 0.
    """
    raise NotImplementedError("wire generate_output to the real code under test")


def judge(input_text: str, output_text: str) -> "tuple[float, str]":
    """Score (input, output). Returns (score in [0,1], justification).

    PREFER A DETERMINISTIC GROUND-TRUTH CHECK (see rubrics.md "Metric selection"): if the datapoint
    carries a reference/expected output or a programmatic checker exists (exact match, F1, set
    overlap, a repo evaluator, a pipeline count), implement `judge` as that deterministic comparison
    — it removes the judge's variance entirely. Fall back to an LLM-as-judge ONLY for open-ended
    quality with no ground truth (then bump AUTO_EXP_RUNS >= 5; the judge is the noisiest component).

    TODO (LLM-judge fallback only): make a REAL judge call. Model selection (see rubrics.md):
      - If the config names a judge `model`, use it.
      - Else DEFAULT to the Claude model selected in the Claude Code session running this skill
        (the same model as the main loop), called via ANTHROPIC_API_KEY / CLAUDE_API_KEY or an
        internal Datadog/AI-gateway route.
      - Only fall back to another provider (OPENAI_API_KEY) or a Datadog LLM Obs evaluator if the
        session model cannot be reached.
    Pin the resolved model id so the judge is identical across every iteration.
    Score `output_text` against EVALUATORS (the config `evaluators` rubric, never `goal`). If no
    judge can be reached after genuinely trying, raise — do NOT return a fabricated number.
    """
    raise NotImplementedError("wire judge to a real LLM-as-judge call; never fabricate a score")


def evaluate_line(line: dict) -> "dict | None":
    """Score ONE datapoint. None => excluded from the eval set (not scored 0)."""
    output = generate_output(line)
    if output is None:
        return None  # non-target / non-scoreable line — excluded from the mean
    input_text = line.get("input") if isinstance(line.get("input"), str) else json.dumps(line.get("input"))
    score, justification = judge(input_text, output)
    return {
        # Stable eval-set id FIRST — required so eval_results.jsonl can be diffed and cited by id
        # in the census / result reasoning / mechanism audit / LLM-Obs reasoning (see rubrics.md
        # "Refer to datapoints by their eval-set id everywhere"). If the dataset has no id field,
        # assign one deterministically when building data.jsonl and it flows through here.
        "id": line.get("id"),
        "input": (input_text or "")[:500],
        "output": output[:500],
        "score": float(score),
        "justification": justification,
    }


def _one_pass(lines: list) -> "tuple[list[dict], int]":
    """Score every scoreable line ONCE. Returns (results, excluded_count)."""
    results: list[dict] = []
    excluded = 0
    for line in lines:
        result = evaluate_line(line)
        if result is None:
            excluded += 1
            continue
        results.append(result)
    return results, excluded


def main() -> None:
    lines = [json.loads(r) for r in DATA.read_text().splitlines() if r.strip()]
    run_means: list[float] = []
    last_results: list[dict] = []
    excluded = 0
    # Re-run the whole eval RUNS times; each pass re-invokes the (stochastic) code under
    # test + judge, so the spread across passes is the run-to-run noise floor.
    for _ in range(RUNS):
        results, excluded = _one_pass(lines)
        if not results:
            raise SystemExit("no scoreable lines — cannot compute a mean (do NOT fabricate one)")
        run_means.append(sum(r["score"] for r in results) / len(results))
        last_results = results
    with RESULTS.open("w") as out:  # keep the last pass's per-line detail for audit
        for r in last_results:
            out.write(json.dumps(r) + "\n")
    mean = statistics.mean(run_means)
    stdev = statistics.pstdev(run_means) if len(run_means) > 1 else 0.0
    # `mean` is the before_score/after_score the loop reads; `stdev` feeds SE_diff for the
    # two-sample t-test keep/discard gate (raw stdev is NOT itself the threshold). Both computed,
    # never literals. `excluded` must be reported in the iteration's reasoning.
    print(json.dumps({
        "mean": mean, "stdev": stdev, "runs": RUNS,
        "scored": len(last_results), "excluded": excluded, "run_means": run_means,
    }))


if __name__ == "__main__":
    main()
