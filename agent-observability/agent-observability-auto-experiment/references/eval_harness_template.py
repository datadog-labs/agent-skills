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

Usage: `python .auto_experiment/eval_harness.py`  -> writes eval_results.jsonl, prints the mean.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

HERE = Path(__file__).parent
DATA = HERE / "data.jsonl"
RESULTS = HERE / "eval_results.jsonl"

# The optimization goal / evaluator text, copied from .auto_experiment/config.json.
# Used verbatim as the judge rubric so scoring is reproducible.
GOAL = os.environ.get("AUTO_EXP_GOAL", "<paste the experiment goal / evaluator rubric here>")


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
    """Real LLM-as-judge over (input, output). Returns (score in [0,1], justification).

    TODO: make a REAL judge call. Model selection (see rubrics.md):
      - If the config names a judge `model`, use it.
      - Else DEFAULT to the Claude model selected in the Claude Code session running this skill
        (the same model as the main loop), called via ANTHROPIC_API_KEY / CLAUDE_API_KEY or an
        internal Datadog/AI-gateway route.
      - Only fall back to another provider (OPENAI_API_KEY) or a Datadog LLM Obs evaluator if the
        session model cannot be reached.
    Pin the resolved model id so the judge is identical across every iteration.
    Score `output_text` against GOAL. If no judge can be reached after genuinely trying, raise —
    do NOT return a fabricated number.
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
        "input": (input_text or "")[:500],
        "output": output[:500],
        "score": float(score),
        "justification": justification,
    }


def main() -> None:
    scored: list[float] = []
    excluded = 0
    with RESULTS.open("w") as out:
        for raw in DATA.read_text().splitlines():
            raw = raw.strip()
            if not raw:
                continue
            line = json.loads(raw)
            result = evaluate_line(line)
            if result is None:
                excluded += 1
                continue
            out.write(json.dumps(result) + "\n")
            scored.append(result["score"])
    if not scored:
        raise SystemExit("no scoreable lines — cannot compute a mean (do NOT fabricate one)")
    mean = sum(scored) / len(scored)
    # This printed number is the before_score / after_score the loop reads. It is computed, never
    # a literal. `excluded` must be reported in the iteration's reasoning.
    print(json.dumps({"mean": mean, "scored": len(scored), "excluded": excluded}))


if __name__ == "__main__":
    main()
