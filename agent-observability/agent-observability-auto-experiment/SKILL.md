---
name: agent-observability-auto-experiment
description: >-
  Run an iterative code-improvement hill-climb against real Datadog LLM-Obs data, locally, with
  Claude Code as the agent. Establishes a baseline eval, makes one focused change, re-scores with
  the same harness, keeps the change only if it beats the best, and repeats. Use when the user
  says "run an auto experiment", "hill-climb this code", "iteratively improve X and measure the
  delta", "optimize this prompt/file against my traces", "auto-optimize against LLM-Obs", or wants
  the local equivalent of the auto_experiments worker. Works from an ml_app, a dataset_id, or a
  list of trace_ids.
---

# auto-experiment — local hill-climb improvement loop

This is the local, Claude-Code-driven version of the `auto_experiments` Temporal/Atlas worker
(`domains/ml_observability/apps/apis/auto_experiments/`). There, a remote Bits/Code-Gen agent runs
the loop; **here YOU (Claude Code) are the agent** and run it directly on the current git checkout.
No Temporal, no Code-Gen API — just git commits, a local eval harness, and Datadog LLM-Obs MCP
tools for the data.

**Read `references/rubrics.md` in full before iteration 1 and keep it in mind every iteration.**
It holds the non-negotiable rules (never invent a score; what to score; where the data lives; the
harness spec; the metric schema). This file is the control loop; that file is the law.

## Inputs (the experiment config)

Collect these from the user (ask only for what is missing — infer the rest). Repo = current
working directory.

| Field | Meaning | Default |
|---|---|---|
| `files_to_optimize` | file(s) whose code you change each iteration | **required** |
| `goal` | what "better" means; the judge rubric + optimization direction | **required** |
| `evaluators` | explicit evaluator/rubric text, if any | falls back to `goal` |
| `ml_app` | Datadog LLM-Obs app to pull traces from | required unless `dataset_id`/`trace_ids` given |
| data source | `dataset_id` \| `trace_ids` \| recent traces for `ml_app` | see priority below |
| `max_iterations` | how many changes to try (clamp **1–50**) | **2** |
| `base_branch` | branch the baseline is measured on | current branch / `main` |

Persist the config to `.auto_experiment/config.json` and update it as the run progresses (it is
the run's state + audit trail):

```json
{
  "repo_url": "...", "base_branch": "...", "files_to_optimize": [...],
  "goal": "...", "evaluators": "...", "ml_app": "...",
  "dataset_id": "...", "trace_ids": [...],
  "max_iterations": 2,
  "iteration_results": [],
  "final_result": {}
}
```

## Setup

1. Confirm a clean-ish working tree (stash or warn on unrelated changes). Note the starting SHA.
2. Create a scratch branch off `base_branch` for the experiment (e.g.
   `auto-experiment/<short-goal>`). All iteration commits land here; the user reviews/keeps the
   best commit at the end.
3. Write `.auto_experiment/config.json`. Add `.auto_experiment/` output files to nothing special
   — they are committed on purpose (they are the audit trail).

## Iteration 1 — baseline + first improvement

Mirrors `build_initial_prompt`. Four steps, in order.

### Step 1 — Load the evaluation data
Pick the data source in this priority order and materialize it to `.auto_experiment/data.jsonl`
(one scoreable datapoint per line: the input, plus expected/reference output if present):

1. **`dataset_id` present** → `get_llmobs_dataset_records` + `get_llmobs_full_dataset_records`.
2. **else non-empty `trace_ids`** → `get_llmobs_trace` (full tree), `get_llmobs_span_details`,
   `get_llmobs_span_content`.
3. **else** → fetch the last ~30 LLM traces for `ml_app` (search LLM-Obs spans), and record the
   trace IDs you used back into `config.json` `trace_ids` so later iterations reuse the SAME
   corpus.

Extract input/output per the **messages-source guidance** in `references/rubrics.md` (score the
`messages` field on the child LLM span, not the thin root `input.value`). Apply the
**data-selection guidance**: keep only traces with a scoreable target span; exclude infra/setup
spans from the set entirely.

### Step 2 — Build the harness and compute BEFORE (baseline)
Copy `references/eval_harness_template.py` to `.auto_experiment/eval_harness.py` and fill in
`generate_output` (run the REAL code under test from `files_to_optimize`) and `judge` (a REAL
LLM-as-judge, judge-selection order in the rubric). **No score literals anywhere.**

Run it against the **original, unmodified** code. `before_score` = the printed mean over scoreable
lines. It is a computed number, never a literal — obey the scoring policy.

Commit `eval_harness.py`, `data.jsonl`, `eval_results.jsonl`.

### Step 3 — Improve
Read `files_to_optimize`. Make **ONE focused change** toward `goal`. Commit it on the scratch
branch with a message explaining what changed and why.

### Step 4 — Compute AFTER (re-run the SAME harness)
Re-run `.auto_experiment/eval_harness.py` (same `evaluate_line`, same data) against the changed
code. `after_score` = the new printed mean. Re-write `eval_results.jsonl`. Write the metric object
(schema in the rubric) to `.auto_experiment/result.json` and commit it **in the same commit** as
the change. `delta = after_score - before_score`.

Decide `is_best` per the optimization direction in `goal`. If iteration 1 improved on the
baseline, it becomes the best (`best_sha` = this commit, `best_score` = after_score). Append the
row to `config.json` `iteration_results`.

## Iterations 2+ — hill climb

Mirrors `build_followup_prompt`. Baseline is already known — **do not recompute it**.

1. **Restore to the best-so-far**, so a discarded attempt cannot contaminate this one:
   - if a commit was kept → `git reset --hard <best_sha>` (stays on the scratch branch; the
     committed harness + data live in that commit, so they are preserved — do not recreate them).
   - if nothing has been kept yet → `git checkout <base_branch> -- <files_to_optimize>` (restore
     only the target files; the harness/data live only in the previous commit on this branch, so
     a hard reset to base would delete them).
2. `before_score` = the current best score (from `iteration_results`; iteration-1 baseline if
   nothing kept yet). Do NOT re-run the baseline.
3. Reuse the data from `data.jsonl` and the committed `eval_harness.py` — do not reload or rebuild.
4. Make **ONE new change, different from every previous attempt** (you can see prior attempts in
   `iteration_results`). Commit it.
5. Re-run the SAME harness → `after_score`. Re-write `eval_results.jsonl` + `result.json`, commit.
6. **Keep or discard**: if `is_best` (beats the best per the goal direction) → update
   `best_sha`/`best_score`, decision `kept`. Else `discarded`. Append the row.

## Stop conditions & guards

- Stop when `iteration == max_iterations`.
- **A change with no computable score is `no_change`, never a fabricated number** (harness won't
  run / no new commit / judge unreachable). Record the blocker in `reasoning`.
- Track consecutive `no_change` iterations; after **5 in a row**, stop early and report the best
  result so far with a stop reason (do not keep burning iterations).

## Final report

1. Ask yourself the run-level wrap-up and write `final_result` into `config.json`:
   `{ "baseline_score", "best_score", "best_iteration", "best_sha", "iterations_run",
   "stop_reason", "reasoning" }` (reasoning = what was tried across all iterations, what worked,
   what didn't, why the winner won).
2. Print a per-iteration table (iteration, delta, decision, sha) and name the best commit.
3. **If nothing beat the baseline**: report the baseline as the best result and leave the original
   code in place (`best_sha` empty). Do not fabricate an improvement.
4. Tell the user the scratch branch + best commit so they can open a PR from it if they want.

## Notes

- Every score is computed by running code. If you ever find yourself about to type a score
  number, stop — run the harness instead.
- Keep `.auto_experiment/` committed; it is the reproducible record of the run.
