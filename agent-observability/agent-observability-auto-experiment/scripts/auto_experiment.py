"""Deterministic orchestration for the auto-experiment hill-climb loop.

The SKILL.md loop has two kinds of work:
  * MODEL work — reasoning that must be done by Claude Code (build the failure census, decide
    the ONE change to make, implement `generate_output`/`judge`, judge causality in the audit).
  * MACHINE work — fixed control flow that is the SAME every run: the noise-band keep/discard
    math, the state machine in config.json, the stop conditions, the exact LLM-Obs metric
    payload. This module owns the MACHINE work so Claude never re-derives it (and can't get the
    ms-timestamp / sha / "exactly one metric" payload wrong by hand).

Every subcommand reads/writes JSON so it composes with the loop and is unit-testable
(see test_auto_experiment.py). The pure functions below hold the logic; the CLI is a thin shell.

Subcommands:
  decide          keep/discard math for one iteration (two-sample t-test, is_best)
  audit           per-datapoint diff + denominator guard between best and candidate results
  submit-payload  emit the exact `submit_llmobs_experiment_events` metrics payload for Claude to send
  record          append an iteration row to config.json and advance the running best pointer
  stop-check      report whether a stop condition (max / plateau / no_change streak) is hit

Nothing here calls an LLM, git, or an MCP tool — those stay with Claude. `submit-payload` only
SHAPES the payload; Claude makes the actual MCP call (a python process has no MCP access).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

# --- keep/discard math (Noise & keep/discard policy in references/rubrics.md) ----------------
#
# The gate is a TWO-SAMPLE t-test on a difference of means, NOT a raw-stdev band. The band a
# single run's stdev defines does not shrink as you add runs, so it can never be cleared by power
# and would discard real effects forever. The standard error of the *difference* does shrink with
# runs, which is why Step 2.4 derives `runs` from `min_delta`. Keep iff the move is in the goal's
# direction AND |t| = |delta| / SE_diff >= 2 AND |delta| >= min_delta (a practical-effect floor).

MIN_DELTA_DEFAULT = 0.02  # small floor on a 0-1 metric so a tiny-SE run still needs a real move
T_THRESHOLD = 2.0  # |t| >= 2 ~ 95% significance (inclusive: the runs derivation lands at t=2)


def se_diff(before_stdev: float, after_stdev: float, runs: int) -> float:
    """Standard error of the difference of two means, each estimated over `runs` reps.

    SE_diff = sqrt(before_stdev**2 / runs + after_stdev**2 / runs). This is the quantity that
    shrinks as `runs` grows (unlike a raw stdev), so more runs can resolve a real difference.
    """
    r = int(runs)
    if r <= 0:
        raise ValueError(f"runs must be >= 1, got {runs!r}")
    return (float(before_stdev) ** 2 / r + float(after_stdev) ** 2 / r) ** 0.5


def decide(
    before: float,
    after: float,
    before_stdev: float,
    after_stdev: float,
    runs: int,
    min_delta: float = MIN_DELTA_DEFAULT,
    direction: str = "max",
) -> dict:
    """Compute the numeric keep/discard verdict for one iteration via a two-sample t-test.

    `is_best_numeric` is TRUE only when the move is in the goal's direction AND it is statistically
    significant (|t| >= 2, or a deterministic move >= min_delta when SE_diff == 0) AND its
    magnitude reaches the `min_delta` practical floor. It is the NUMERIC verdict only — the loop
    still requires the mechanism audit (see `audit`) to pass before actually keeping. `within_noise`
    (not a significant move in EITHER direction) feeds the plateau stop and distinguishes a
    within-noise non-move from a real, significant regression.

    Zero-variance case: if SE_diff == 0 (a fully deterministic metric, both stdevs 0), the t-test
    is undefined (division by zero); `t_stat` is None and significance is decided by the floor
    alone (|delta| >= min_delta) — a deterministic move of at least min_delta is real.
    """
    if direction not in ("max", "min"):
        raise ValueError(f"direction must be 'max' or 'min', got {direction!r}")
    delta = float(after) - float(before)
    md = float(min_delta)
    sed = se_diff(before_stdev, after_stdev, runs)
    improved = delta > 0 if direction == "max" else delta < 0
    meets_floor = abs(delta) >= md
    if sed == 0.0:
        t_stat = None
        magnitude_significant = meets_floor  # deterministic move >= floor is real
    else:
        t_stat = abs(delta) / sed
        magnitude_significant = (t_stat >= T_THRESHOLD) and meets_floor
    is_best_numeric = improved and magnitude_significant
    return {
        "delta": delta,
        "se_diff": sed,
        "t_stat": t_stat,  # None when se_diff == 0 (zero-variance rule applies)
        "runs": int(runs),
        "min_delta": md,
        "direction": direction,
        "improved": improved,
        "meets_floor": meets_floor,
        "significant": magnitude_significant,
        # not a significant move in EITHER direction => within noise (plateau candidate),
        # as opposed to a significant regression (significant but not improved)
        "within_noise": not magnitude_significant,
        "is_best_numeric": is_best_numeric,
    }


# --- mechanism audit (Mechanism audit in references/rubrics.md) ------------------------------


def _read_scores(path: str) -> list:
    """Read the per-line scores from an eval_results.jsonl (same order = same datapoint)."""
    lines = [l for l in Path(path).read_text().splitlines() if l.strip()]
    return [float(json.loads(l)["score"]) for l in lines]


def audit(
    best_scores: list,
    candidate_scores: list,
    best_excluded: "int | None" = None,
    candidate_excluded: "int | None" = None,
) -> dict:
    """Per-datapoint diff + denominator guard between the best and candidate runs.

    Results are aligned by index (same data.jsonl, same order). Reports which datapoints flipped
    up/down so Claude can judge whether the gain is CAUSED by the change (in its targeted census
    bucket) rather than an unrelated wobble. `denominator_ok` is the hard guard: a higher mean
    from FEWER scored datapoints (the change dropped hard cases out of the eval set) is an
    artifact, not an improvement.
    """
    scored_match = len(best_scores) == len(candidate_scores)
    n = min(len(best_scores), len(candidate_scores))
    up, down, same = [], [], 0
    for i in range(n):
        d = candidate_scores[i] - best_scores[i]
        if d > 0:
            up.append({"index": i, "before": best_scores[i], "after": candidate_scores[i]})
        elif d < 0:
            down.append({"index": i, "before": best_scores[i], "after": candidate_scores[i]})
        else:
            same += 1
    excluded_match = None
    if best_excluded is not None and candidate_excluded is not None:
        excluded_match = int(best_excluded) == int(candidate_excluded)
    # denominator guard: same number scored AND (if given) same number excluded
    denominator_ok = scored_match and (excluded_match is not False)
    return {
        "best_scored": len(best_scores),
        "candidate_scored": len(candidate_scores),
        "scored_match": scored_match,
        "excluded_match": excluded_match,
        "denominator_ok": denominator_ok,
        "flipped_up": up,
        "flipped_down": down,
        "unchanged": same,
    }


# --- LLM-Obs metric payload (Report each iteration's score in SKILL.md) ----------------------

SCORE_LABEL = "auto_experiment_score"


def submit_payload(
    iteration: int,
    sha: str,
    score: float,
    experiment_id: str,
    now_ms: int,
    decision: str = "",
    reasoning: str = "",
    basis: str = "",
    delta_vs_best: "float | None" = None,
    t_stat: "float | None" = None,
    significant: "bool | None" = None,
) -> dict:
    """Shape the EXACT `submit_llmobs_experiment_events` payload for one scored iteration.

    Returns `{"skip": reason}` when there is no experiment id to report to (Claude records the
    skip in `reasoning` and makes no MCP call). Otherwise returns the args dict for the MCP tool:
    exactly one `score` metric, tags carrying the iteration number, the FULL 40-char commit sha,
    the keep/discard `decision` (`baseline` for iteration 0), and the **decision-legibility tags**
    (`basis`, `delta_vs_best`, `t_stat`, `significant`) so a higher score that is still discarded
    reads legibly on the dashboard — plus the iteration's `reasoning`. Claude passes this straight
    to the tool — it never hand-builds the ms timestamp / tags / sha.
    """
    if not experiment_id:
        return {"skip": "DD_AUTO_EXPERIMENT_ID unset/empty — no experiment to report to"}
    if score is None:
        # a no_change iteration has no computed score; emitting a score metric would fabricate one
        raise ValueError("no score to submit (no_change iteration must not report a score)")
    tags = [f"iteration:{int(iteration)}", f"git.commit.sha:{sha}"]
    if decision:
        tags.append(f"decision:{decision}")
    # decision-legibility tags: the "why" next to the score (see Report each iteration's score).
    # Each is added only when provided, so bare calls stay back-compatible.
    if basis:
        tags.append(f"basis:{basis}")
    if delta_vs_best is not None:
        tags.append(f"delta_vs_best:{delta_vs_best:+.4f}")
    if t_stat is not None:
        tags.append(f"t_stat:{t_stat:.2f}")
    elif significant is not None:
        # significance was computed but t is undefined (se_diff == 0, zero-variance rule)
        tags.append("t_stat:null")
    if significant is not None:
        tags.append(f"significant:{str(bool(significant)).lower()}")
    metric = {
        "label": SCORE_LABEL,
        "metric_type": "score",
        "score_value": float(score),
        "timestamp_ms": int(now_ms),
        "tags": tags,
    }
    if reasoning:
        metric["reasoning"] = reasoning
    return {"experiment_id": experiment_id, "metrics": [metric]}


# --- config.json state machine (Inputs / iteration_results in SKILL.md) ----------------------


def _load_config(path: str) -> dict:
    return json.loads(Path(path).read_text())


def _save_config(path: str, config: dict) -> None:
    Path(path).write_text(json.dumps(config, indent=2) + "\n")


def record_row(config: dict, row: dict) -> dict:
    """Append an iteration row and advance the running best pointer if the row was kept.

    A row is `kept` only when the loop already applied BOTH the numeric gate and the mechanism
    audit — this function trusts `row["decision"]` and just maintains state; it does not re-judge.
    """
    config.setdefault("iteration_results", []).append(row)
    if row.get("decision") == "kept":
        config["best_sha"] = row.get("sha")
        config["best_score"] = row.get("after_score")
        config["best_iteration"] = row.get("iteration")
    return config


# --- stop conditions (Stop conditions & guards in SKILL.md) ----------------------------------

PLATEAU_WINDOW = 3  # consecutive within-noise discards => plateau
NO_CHANGE_STREAK = 5  # consecutive no_change iterations => give up


def stop_check(config: dict) -> dict:
    """Report whether a stop condition is hit, given the iteration_results so far.

    Order matters: max_iterations first (hard budget), then the plateau (last 3 discards all
    WITHIN the noise band — a real regression streak does NOT count), then the no_change streak.
    """
    results = config.get("iteration_results", [])
    max_iterations = int(config.get("max_iterations", 2))
    n = len(results)
    if n >= max_iterations:
        return {"stop": True, "stop_reason": "reached max_iterations"}
    # plateau: last PLATEAU_WINDOW rows all discarded AND within the noise band
    if n >= PLATEAU_WINDOW:
        window = results[-PLATEAU_WINDOW:]
        if all(r.get("decision") == "discarded" and r.get("within_noise") for r in window):
            return {"stop": True, "stop_reason": "plateau (deltas within noise)"}
    # no_change streak: last NO_CHANGE_STREAK rows all no_change
    if n >= NO_CHANGE_STREAK:
        window = results[-NO_CHANGE_STREAK:]
        if all(r.get("decision") == "no_change" for r in window):
            return {"stop": True, "stop_reason": "no_change streak (nothing computable)"}
    return {"stop": False, "stop_reason": None}


# --- CLI -------------------------------------------------------------------------------------


def _emit(obj: dict) -> None:
    print(json.dumps(obj))


def main(argv: "list[str] | None" = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    d = sub.add_parser("decide", help="keep/discard math for one iteration")
    d.add_argument("--before", type=float, required=True)
    d.add_argument("--after", type=float, required=True)
    d.add_argument("--before-stdev", type=float, required=True)
    d.add_argument("--after-stdev", type=float, required=True)
    d.add_argument("--runs", type=int, required=True, help="reps per side (for SE_diff); same for best+candidate")
    d.add_argument("--min-delta", type=float, default=MIN_DELTA_DEFAULT)
    d.add_argument("--direction", choices=["max", "min"], default="max")

    a = sub.add_parser("audit", help="per-datapoint diff + denominator guard")
    a.add_argument("--best", required=True, help="best commit's eval_results.jsonl")
    a.add_argument("--candidate", required=True, help="this iteration's eval_results.jsonl")
    a.add_argument("--best-excluded", type=int, default=None)
    a.add_argument("--candidate-excluded", type=int, default=None)

    s = sub.add_parser("submit-payload", help="shape the LLM-Obs metric payload for Claude to send")
    s.add_argument("--iteration", type=int, required=True)
    s.add_argument("--sha", required=True)
    s.add_argument("--score", type=float, required=True)
    s.add_argument("--now-ms", type=int, required=True, help="epoch ms; pass `date +%s%3N`")
    s.add_argument("--experiment-id", default=os.environ.get("DD_AUTO_EXPERIMENT_ID", ""))
    s.add_argument("--decision", default="", help="kept|discarded; baseline for iteration 0")
    s.add_argument("--reasoning", default="", help="this iteration's reasoning string")
    s.add_argument("--basis", default="", help="significant|within_noise|regression|promoted|baseline|no_change")
    s.add_argument("--delta-vs-best", type=float, default=None, help="delta against the current best")
    s.add_argument("--t-stat", type=float, default=None, help="the two-sample t; omit for zero-variance")
    s.add_argument("--significant", choices=["true", "false"], default=None)

    r = sub.add_parser("record", help="append an iteration row to config.json + advance best")
    r.add_argument("--config", required=True)
    r.add_argument("--row", required=True, help="JSON iteration row")

    sc = sub.add_parser("stop-check", help="is a stop condition hit?")
    sc.add_argument("--config", required=True)

    args = p.parse_args(argv)

    if args.cmd == "decide":
        _emit(decide(args.before, args.after, args.before_stdev, args.after_stdev,
                     args.runs, args.min_delta, args.direction))
    elif args.cmd == "audit":
        _emit(audit(_read_scores(args.best), _read_scores(args.candidate),
                    args.best_excluded, args.candidate_excluded))
    elif args.cmd == "submit-payload":
        sig = None if args.significant is None else (args.significant == "true")
        _emit(submit_payload(args.iteration, args.sha, args.score, args.experiment_id,
                             args.now_ms, args.decision, args.reasoning,
                             args.basis, args.delta_vs_best, args.t_stat, sig))
    elif args.cmd == "record":
        config = _load_config(args.config)
        config = record_row(config, json.loads(args.row))
        _save_config(args.config, config)
        _emit({"best_sha": config.get("best_sha"), "best_score": config.get("best_score"),
               "best_iteration": config.get("best_iteration"),
               "iterations_recorded": len(config.get("iteration_results", []))})
    elif args.cmd == "stop-check":
        _emit(stop_check(_load_config(args.config)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
