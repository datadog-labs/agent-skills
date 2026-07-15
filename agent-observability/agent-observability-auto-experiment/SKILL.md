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
| `model` | judge model id | **the Claude model selected in this session** (see rubric) |
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
  "reps": 3,
  "min_delta": 0.02,
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
4. This run reports one score per iteration to the LLM-Obs experiment identified by the
   `DD_AUTO_EXPERIMENT_ID` environment variable (set in the environment before this skill is
   invoked — read it, don't ask the user). See **Report each iteration's score to LLM-Obs**.

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

Then **split once, deterministically** (hash of datapoint id, ~70/30) into
`.auto_experiment/data.val.jsonl` (the hill-climb gate) and `.auto_experiment/data.test.jsonl`
(held out) — see the rubric's **Held-out split**. Every iteration scores on `val`
(`AUTO_EXP_DATA=.auto_experiment/data.val.jsonl`); `test` is run only in the final report.

### Step 2 — Build the harness and compute BEFORE (baseline)
Copy `references/eval_harness_template.py` to `.auto_experiment/eval_harness.py` and fill in
`generate_output` (run the REAL code under test from `files_to_optimize`) and `judge` (a REAL
LLM-as-judge, judge-selection order in the rubric). **No score literals anywhere.**

Run it against the **original, unmodified** code with `AUTO_EXP_REPS` (= config `reps`, default 3):
the harness re-runs the whole eval R times and prints `{mean, stdev, rep_means, ...}`.
`before_score` = the printed `mean`; also record `stdev` (the noise floor). Both computed numbers,
never literals — obey the scoring policy and the **Noise & keep/discard policy** in the rubric.

Commit `eval_harness.py`, `data.jsonl`, `data.val.jsonl`, `data.test.jsonl`, `eval_results.jsonl`.

### Step 2.5 — Census the baseline failures
Before changing anything, decompose **where the baseline loses** per the rubric's **Baseline
failure census**: bucket every failing datapoint by root cause, write `.auto_experiment/census.json`,
commit it, and surface the ranked buckets. This tells you which lever is worth pulling — and whether
the dominant failure mode is even reachable by editing `files_to_optimize`.

### Step 3 — Improve
Read `files_to_optimize`. Make **ONE focused change** toward `goal`, aimed at the **largest census
bucket you can plausibly move** (name that bucket in the iteration's `reasoning`). Commit it on the
scratch branch with a message explaining what changed and why.

Before the (expensive) full eval, run a **feasibility probe** per the rubric's **Feasibility probe**:
the cheapest offline check that this change *could* move a failing census bucket. If the probe
reaches 0 failing datapoints, record the iteration `no_change` with the probe result and skip to the
next hypothesis — do **not** spend a full eval on a dead lever.

### Step 4 — Compute AFTER (re-run the SAME harness)
Re-run `.auto_experiment/eval_harness.py` (same `evaluate_line`, same data) against the changed
code. `after_score` = the new printed mean. Re-write `eval_results.jsonl`. Write the metric object
(schema in the rubric) to `.auto_experiment/result.json` and commit it **in the same commit** as
the change. `delta = after_score - before_score`.

Decide `is_best` per the optimization direction in `goal` **and the Noise & keep/discard policy**:
keep only if `|after_score − before_score| > max(pooled_stdev, min_delta)`. A within-noise gain is
`is_best: false` (discarded), not kept. If iteration 1 clears the band, it becomes the best
(`best_sha` = this commit, `best_score` = after_score). Append the row to `config.json`
`iteration_results`.

Then report this iteration's score to LLM-Obs (tag `iteration:1`) — see **Report each iteration's
score to LLM-Obs**.

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
   `iteration_results`), aimed at a named `census.json` bucket. Commit it.
5. **Feasibility probe first** (rubric): cheap offline check the change can move its target bucket;
   if it reaches 0 failing datapoints, record `no_change` and skip the full eval. Otherwise re-run
   the SAME harness on `val` → `after_score`. Re-write `eval_results.jsonl` + `result.json`, commit.
6. **Keep or discard**: keep only if the delta clears the noise band (`|after_score − before_score|
   > max(pooled_stdev, min_delta)`, per the Noise policy) in the goal's direction → update
   `best_sha`/`best_score`, decision `kept`. A within-noise gain or a regression → `discarded`.
   Append the row.
7. Report this iteration's score to LLM-Obs (tag `iteration:<n>`) — see **Report each iteration's
   score to LLM-Obs**.

## Report each iteration's score to LLM-Obs (every scored iteration)

Once you have a computed score for an iteration, submit **exactly one** eval-metric datapoint to
LLM-Obs with the `submit_llmobs_experiment_events` MCP tool. Do this once per iteration, right
after the score is computed and the iteration's commit / `result.json` is written — including
iteration 1.

Call `submit_llmobs_experiment_events` with a single metric shaped exactly like this:

- `experiment_id`: the value of the `DD_AUTO_EXPERIMENT_ID` environment variable (read it from the
  environment; do not ask the user and do not invent one). If it is unset or empty, skip the
  submission and note that in the iteration `reasoning`.
- `metrics`: an array containing exactly one object with these fields and no others:
  - `label`: always the literal string `auto_experiment_score`.
  - `metric_type`: `score`.
  - `score_value`: the score this iteration produced (`after_score`) — the number computed by the
    harness, never a literal or a rounded-for-display value.
  - `timestamp_ms`: the current wall-clock time as an epoch timestamp in **milliseconds**.
  - `tags`: `["iteration:<n>", "git.commit.sha:<sha>"]`, where `<n>` is this iteration's number
    (`1` for the first improvement, `2` for the next, and so on) and `<sha>` is the full Git
    commit SHA of the commit this iteration created for its change (i.e. `git rev-parse HEAD`
    after committing the iteration).
  - Do **not** include `span_id`, `categorical_value`, or `boolean_value`.

Example arguments for iteration 5 whose harness computed a score of `0.72`:

```json
{
  "experiment_id": "<value of $DD_AUTO_EXPERIMENT_ID>",
  "metrics": [
    {
      "label": "auto_experiment_score",
      "metric_type": "score",
      "score_value": 0.72,
      "timestamp_ms": 1752430000000,
      "tags": ["iteration:5", "git.commit.sha:33ec6e0959bd46b0ea9c337cf6a28a763d3eeb0a"]
    }
  ]
}
```

Rules:

- **Exactly one metric per iteration.** Never submit more than one metric for the same iteration
  and never batch several iterations into one call.
- **Only for scored iterations.** A `no_change` iteration has no computed score, so there is no
  `score_value` to send — skip the submission for it (emitting a `score` metric without a real
  score would violate the scoring policy). Record the skip in `reasoning`.
- The value you submit is the same computed `after_score` recorded in `result.json`; the two must
  always agree.

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
2. **Held-out `test` comparison (the real headline).** Run the harness once on the **baseline**
   commit and once on the **best** commit against `.auto_experiment/data.test.jsonl`
   (`AUTO_EXP_DATA=.auto_experiment/data.test.jsonl`), both at `reps` reps. Report the
   baseline-vs-best `test` delta with its noise band as the run's result — the `val` hill-climb
   gain is not the headline. If `test` did not clear the noise band even though `val` did, say so
   explicitly (the win did not generalize) and treat baseline as best.
3. Print a per-iteration table (iteration, val delta, decision, sha) and name the best commit.
4. **If nothing beat the baseline on `test`**: report the baseline as the best result and leave the
   original code in place (`best_sha` empty). Do not fabricate an improvement.
5. Tell the user the scratch branch + best commit so they can open a PR from it if they want.

## Notes

- Every score is computed by running code. If you ever find yourself about to type a score
  number, stop — run the harness instead.
- Keep `.auto_experiment/` committed; it is the reproducible record of the run.
