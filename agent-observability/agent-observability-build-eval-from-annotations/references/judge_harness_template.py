#!/usr/bin/env python3
"""Eval harness for fitting a JUDGE, driven by agent-observability-auto-experiment.

Copy to `.auto_experiment/eval_harness.py` and set `harness_provided_by:
agent-observability-build-eval-from-annotations` in `.auto_experiment/config.json`. Step 2 of
auto-experiment then validates this file instead of generating its own.

What differs from auto-experiment's own template, and why:

* the **object under test is a prompt**, not application code. `files_to_optimize` points at the
  judge prompt; each pass reads it fresh from disk, so the iteration's edit is what gets measured.
* **`mean` is a corpus metric, not a row average.** F1 on a class, macro-F1 and Cohen's κ are
  computed over the whole run's confusion matrix. Reporting a row-averaged accuracy instead would
  hide the failure this whole skill exists to catch: on a skewed corpus a judge that answers with
  the majority class every time scores well and catches nothing. Per-row correctness is still
  written to `eval_results.jsonl` for the audit.
* **ground truth is the human label** carried on each corpus row, never a model's opinion.

Contract with the loop (unchanged): stdout is exactly
`{mean, stdev, runs, scored, excluded, run_means}`, and `stdev` is the spread of `run_means` across
full re-runs. No score literals anywhere in this file.

Env:
  AUTO_EXP_DATA        corpus jsonl: {id, payload, label, split?}          (required)
  AUTO_EXP_RUNS        full re-runs of the whole corpus (default 3)
  AUTO_EXP_PROMPT      path to the judge prompt under optimization         (required)
  AUTO_EXP_METRIC      metric name understood by scoring.py (default balanced_accuracy)
  AUTO_EXP_LABEL_FIELD one label out of a joint verdict, optional
  AUTO_EXP_MATCH       exact | jaccard | similarity_group  (default exact)
  AUTO_EXP_GROUPS      path to the similarity-groups json, when AUTO_EXP_MATCH needs it
  AUTO_EXP_SPLIT       train | holdout | all (default train)
  AUTO_EXP_MODEL       judge model id
  AUTO_EXP_CONCURRENCY parallel judge calls (default 8)
"""

from __future__ import annotations

import json
import os
import statistics
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
# judge_runner + scoring are this skill's own references; the harness reuses them rather than
# re-implementing the judge call or the metrics.
REFS = Path(os.environ.get("BUILD_EVAL_REFS") or HERE)
sys.path.insert(0, str(REFS))

import judge_runner  # noqa: E402
import scoring  # noqa: E402

DATA = Path(os.environ["AUTO_EXP_DATA"])
PROMPT = Path(os.environ["AUTO_EXP_PROMPT"])
RUNS = max(3, int(os.environ.get("AUTO_EXP_RUNS", "3")))
METRIC = os.environ.get("AUTO_EXP_METRIC", "balanced_accuracy")
LABEL_FIELD = os.environ.get("AUTO_EXP_LABEL_FIELD") or None
MATCH = os.environ.get("AUTO_EXP_MATCH", "exact")
SPLIT = os.environ.get("AUTO_EXP_SPLIT", "train")
MODEL = os.environ.get("AUTO_EXP_MODEL", "claude-opus-5")
CONCURRENCY = int(os.environ.get("AUTO_EXP_CONCURRENCY", "8"))
RESULTS = Path(".auto_experiment/eval_results.jsonl")


def _credit():
    groups, partial = None, 0.5
    spec_path = os.environ.get("AUTO_EXP_GROUPS")
    if spec_path:
        spec = json.loads(Path(spec_path).read_text())
        groups, partial = spec.get("groups", []), spec.get("partial_credit", 0.5)
    if MATCH == "similarity_group" and not groups:
        raise SystemExit("AUTO_EXP_MATCH=similarity_group needs AUTO_EXP_GROUPS: the taxonomy is "
                         "the user's, not the judge's.")
    return scoring.make_credit(MATCH, groups, partial)


def _one_pass(rows: list, system: str, call) -> "tuple[list[dict], int]":
    """One full sweep of the corpus. Returns (results, excluded).

    A row whose pass is unparseable is EXCLUDED and counted, never scored as wrong — punishing the
    judge for a parsing bug and handing it a free correct answer are both inventions.
    """
    def judge_one(row):
        out = judge_runner.judge_row(call, system, row, 0, MODEL)
        if out.get("unparseable") or "label" not in out:
            return None
        predicted = scoring.pick(out["label"], LABEL_FIELD)
        if predicted is scoring._MISSING:
            return None
        return {"id": row["id"], "output": predicted,
                "score": float(scoring.key(predicted) == scoring.key(
                    scoring.pick(row["label"], LABEL_FIELD))),
                "confidence": out.get("confidence"),
                "justification": (out.get("reasoning") or "")[:400]}

    with ThreadPoolExecutor(max_workers=CONCURRENCY) as pool:
        scored = list(pool.map(judge_one, rows))
    results = [r for r in scored if r is not None]
    return results, len(scored) - len(results)


def main() -> None:
    if not DATA.exists():
        raise SystemExit(f"corpus missing: {DATA}")
    if not PROMPT.exists():
        raise SystemExit(f"judge prompt missing: {PROMPT} — it is the object under optimization")

    rows = [json.loads(line) for line in DATA.read_text().splitlines() if line.strip()]
    if SPLIT != "all":
        rows = [r for r in rows if r.get("split", "train") == SPLIT]
    if not rows:
        raise SystemExit(f"no rows in split {SPLIT!r} — cannot compute a score (do NOT fabricate one)")

    credit = _credit()
    call = judge_runner.pick_backend()
    run_means: list[float] = []
    last_results: list[dict] = []
    excluded = 0

    for _ in range(RUNS):
        # Re-read the prompt every pass: the iteration's edit is the thing being measured.
        system = PROMPT.read_text()
        results, excluded = _one_pass(rows, system, call)
        if not results:
            raise SystemExit("no scoreable rows — cannot compute a mean (do NOT fabricate one)")
        truth = {r["id"]: scoring.pick(r["label"], LABEL_FIELD) for r in rows}
        pairs = [(truth[r["id"]], r["output"]) for r in results]
        # The corpus metric, NOT the mean of `score` — see this file's docstring.
        run_means.append(scoring.score(pairs, METRIC, credit))
        last_results = results

    RESULTS.parent.mkdir(parents=True, exist_ok=True)
    with open(RESULTS, "w") as fh:
        for r in last_results:
            fh.write(json.dumps(r) + "\n")

    print(json.dumps({
        "mean": statistics.mean(run_means),
        "stdev": statistics.pstdev(run_means) if len(run_means) > 1 else 0.0,
        "runs": RUNS,
        "scored": len(last_results),
        "excluded": excluded,
        "run_means": run_means,
        "metric": METRIC,          # extra keys are ignored by the loop; kept for the audit trail
        "match_mode": MATCH,
        "label_field": LABEL_FIELD,
    }))


if __name__ == "__main__":
    main()
