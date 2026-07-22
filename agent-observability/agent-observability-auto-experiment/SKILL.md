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

Repo = current working directory. **Fields marked _must ask_ are mandatory — never proceed with a
silent default; collect them from the user.** Fields marked _default_ may be filled without asking,
but **every field (must-ask and default alike) must be shown to the user and validated before the
run starts** (see the Mandatory intake gate below).

| Field | Meaning | Source |
|---|---|---|
| `files_to_optimize` | the **edit scope**: one or more files, a **folder**, or globs. **Any code inside the scope is fair game to modify** — tool/retrieval code, the pipeline, config, data-shaping, or prompts — not just prompt wording. Everything outside the scope is off-limits. | **must ask** |
| `goal` | what "better" means; the judge rubric + optimization direction | **must ask** |
| `evaluators` | explicit evaluator/rubric text — how each datapoint is scored (ground-truth check vs LLM-judge, pass criteria, direction). | **must ask** (do NOT silently fall back to `goal`) |
| data source | where the eval data comes from — **either** a `dataset_id` **or** an `ml_app` to pull traces from (optionally narrowed by explicit `trace_ids`). | **must ask** — mandatory; the run cannot start without one of `dataset_id` / `ml_app` (priority below) |
| `max_iterations` | how many changes to try (clamp **1–50**) | _default_ **2** |
| `model` | judge model id | _default_: the Claude model selected in this session (see rubric) |
| `base_branch` | branch the baseline is measured on | _default_: current branch / `main` |

`runs` and `min_delta` are **not inputs** — they are **derived** from the measured baseline noise in
Step 2.4, not chosen by anyone. Do **not** ask for them and do **not** show them in the all-params
validation. They are computed during the run and displayed once, at the end, with their reasoning.

### Mandatory intake gate — do this FIRST, before Setup

Before writing any config or touching git:

1. Collect every **must-ask** field from an explicit user answer. If any is missing, ask for it — do
   **not** default, infer, or guess:
   - **`files_to_optimize`** — the user names the concrete file(s)/folder/globs. Never assume the
     scope from context. Resolve a folder/glob to the concrete editable file list.
   - **`goal`** — the optimization target + direction.
   - **`evaluators`** — how a datapoint is scored (pass/fail, metric, direction). Do not reuse
     `goal` as the evaluator. **Use the user's evaluator text verbatim. NEVER invent, extend,
     narrow, or change the metric or direction of an evaluator** — do not turn "recall" into "F1",
     do not add a precision term the user didn't ask for, do not flip the direction. If `goal` and
     the user's `evaluators` appear to disagree (e.g. `goal` says "balanced precision and recall"
     but the stated evaluator is recall-only), **STOP and ask the user which one governs** — do
     **not** silently reconcile them by rewriting the rubric. The metric the harness optimizes must
     be the one the user approved, or every keep/discard decision optimizes the wrong objective.
   - **data source** — **mandatory**: the user must provide **either** a `dataset_id` **or** an
     `ml_app` to find traces from (optionally narrowed by explicit `trace_ids`). Do not auto-pick,
     do not guess an `ml_app`, and do not start the run with neither — if both are missing, ask.

   **A detailed, specific goal is NOT permission to infer any must-ask field.** A rich goal is the
   single most common cause of wrongly auto-filling `files_to_optimize`, `evaluators`, and the data
   source — the more the goal spells out (a filename, a metric, a dataset), the *harder* you must
   resist reading those as answers. A goal that mentions `v12.md` is not the user choosing
   `files_to_optimize`; a goal that says "balanced precision and recall" is not the user handing you
   an evaluator; a goal that names a dataset is not the user selecting the data source. **Ask
   anyway, for every must-ask field, every time — even when you are confident you could guess it.**
   This gate is a hard STOP: if any must-ask field lacks an explicit user answer, do not write
   `config.json`, do not create the scratch branch, do not run the harness — ask (use
   `AskUserQuestion`) and wait.
2. Fill the **default** fields (`max_iterations`, `model`, `base_branch`) with their defaults above.
   Do **not** touch `runs`/`min_delta` here — they are derived in Step 2.4, not intake params.
3. **Show ALL parameters back to the user — must-ask and defaulted alike — and get explicit
   validation before starting the run.** Present the full resolved config (including the concrete
   expanded `files_to_optimize` list and each default value) and let the user confirm or override
   any field. Do **not** show `runs`/`min_delta` here (they aren't chosen yet). Show the
   `evaluators` text **exactly as the user gave it**; if you believe it needs any change, present
   the change as an explicit *proposal* ("you said recall-only; your goal mentions precision too —
   score recall-only, or switch to F1?") and record only what the user picks. Never persist an
   evaluator the user did not approve verbatim. Only after the user validates do you write
   `config.json` and proceed to Setup.

Persist the config to `.auto_experiment/config.json` and update it as the run progresses (it is
the run's state + audit trail):

```json
{
  "repo_url": "...", "base_branch": "...", "files_to_optimize": [...],
  "goal": "...", "evaluators": "...", "ml_app": "...",
  "dataset_id": "...", "trace_ids": [...],
  "max_iterations": 2,
  "runs": null,
  "min_delta": null,
  "iteration_results": [],
  "final_result": {}
}
```

`runs` and `min_delta` start `null` — they are **computed and written in Step 2.4** from the
measured baseline noise, never chosen at intake.

## Scope — optimize the whole selected surface, not just the prompt

`files_to_optimize` is a **scope**, not a prompt pointer. It may be a set of files, a directory, or
globs — expand a directory to its editable files (e.g. every `*.py` under it) and treat **all of
them as the code under test**. Within that scope you may change **anything that moves the metric**:
retrieval/tool code, request logic, filtering, output shape, ranking, config, or prompts. Let the
**failure census** decide *which* file the lever lives in — do **not** default to rewording a
prompt. In practice the biggest wins are often in tool/retrieval code (what the model can fetch),
not prompt phrasing; a prompt-only search finds nothing when the headroom is in the tools.

**Hard scope guard:** never edit a file outside `files_to_optimize`. If the census's dominant lever
is out of scope, say so (that's a finding) — do not silently tweak in-scope-but-irrelevant files.

## Setup

1. Confirm a clean-ish working tree (stash or warn on unrelated changes). Note the starting SHA.
   If `files_to_optimize` names a folder/globs, resolve it to the concrete editable file list and
   record that list in `config.json` (it is the scope for every iteration + the restore boundary).
2. Create a scratch branch off `base_branch` for the experiment (e.g.
   `auto-experiment/<short-goal>`). All iteration commits land here; the user reviews/keeps the
   best commit at the end.
3. Write `.auto_experiment/config.json`. Add `.auto_experiment/` output files to nothing special
   — they are committed on purpose (they are the audit trail).
4. This run reports one score per iteration to the LLM-Obs experiment identified by the
   `DD_AUTO_EXPERIMENT_ID` environment variable (set in the environment before this skill is
   invoked — read it, don't ask the user). See **Report each iteration's score to LLM-Obs**.
5. **Record the run context on the experiment before iterations start.** Call
   `update_llmobs_experiment` once with `experiment_id` = `$DD_AUTO_EXPERIMENT_ID` (skip if unset)
   and `metadata` set to a JSON struct containing the repo name, the scratch branch name, and the
   model running this skill, e.g.
   `{"repo": "<repo>", "branch": "<scratch-branch>", "model": "<model>"}`. Derive `repo` from the
   git remote (`basename -s .git $(git remote get-url origin)`, or `owner/repo`), `branch` from the
   branch created in step 2, and `model` = the `provider/model-id` of the model/agent driving this
   session (e.g. `openai/gpt-4-turbo`, `anthropic/claude-opus-4-8`). `metadata` **replaces**
   existing metadata, so include all three keys in the one call. Do this in Setup, before Step 1.
   **Verify it landed** (see gate below) — this is the step most often silently skipped, because it
   is an MCP side-effect with no local artifact, unlike the file/branch writes above.

### Setup verification gate — do this BEFORE Step 1

Setup steps 2 and 5 have **external** effects (a git branch; an MCP write to the experiment) that
leave no obvious local trace, so a loop racing to iteration 1 can skip them and nothing downstream
notices. Before starting Step 1, **explicitly verify every setup step against a concrete artifact**
and do not proceed until all pass. Re-run the missing step if any check fails; never assume a step
ran because you intended it to.

| # | step | verification (must actually run the check, not recall it) |
|---|---|---|
| 1 | clean tree + start SHA | `git rev-parse HEAD` recorded in `config.json` `start_sha`; tree clean or unrelated changes stashed |
| 2 | scratch branch | `git branch --show-current` equals the scratch branch off `base_branch` |
| 3 | `config.json` written | file exists with every required field populated (incl. the resolved `files_to_optimize` list, `evaluators` verbatim, data source) |
| 4 | `DD_AUTO_EXPERIMENT_ID` | env var read; if unset, that is recorded and per-iteration reporting is knowingly skipped |
| 5 | run context on experiment | confirm the `update_llmobs_experiment` call **actually returned a success response in hand** (not merely that you intended to call it). For the us5 MCP that response is `updated_fields` containing `"metadata"` — accept that, or any non-error response acknowledging the metadata write if the tool's shape differs. The check is "the call was made and acknowledged", so do not hard-block on one exact field name; if the tool errored or was never called, re-run it. Skip only if `DD_AUTO_EXPERIMENT_ID` is unset. |

State the gate result briefly (each step ✓ with its evidence) before Step 1. This same
"external-effect step → verify against an artifact" discipline is why per-iteration score
submissions are also confirmed by the tool's `metrics_ingested` response, not assumed.

## Execution model — orchestrator + fresh per-iteration sub-agents

Split the two roles so context stays clean and iterations don't anchor on each other:

- **You are the orchestrator.** You own the durable state (`config.json`, `census.json`, `best_sha`,
  the branch), the harness, and every keep/discard decision. You do NOT accumulate the raw work of
  each attempt in your own context.
- **Each improvement iteration runs in a FRESH sub-agent** (spawn via the Agent tool). Hand it a
  compact briefing — not your whole transcript: the `goal`/`evaluators`, the full editable **scope**
  (`files_to_optimize` expanded — it may change ANY file in scope, not just a prompt), the
  ranked `census.json` buckets (+ the bucket to target this iteration), the current `best_sha`, and
  **one-line summaries of prior attempts** (what was tried → kept/discarded, from `iteration_results`)
  so it won't repeat them. Its job: make ONE change + return a short summary (what it changed, which
  bucket, feasibility-probe result). You (orchestrator) run the harness, apply the noise gate +
  mechanism audit, commit/keep/discard, and update state.
- **Why:** a fresh bounded context per iteration avoids anchoring on dead ideas and stops the
  orchestrator's context from bloating over a long run — the same reason the production loop spawns a
  new `claude --print` per iteration instead of one long-lived agent. If sub-agents are unavailable,
  emulate it: before each iteration, re-read only the briefing above and deliberately ignore the
  narrative of previous attempts beyond their one-line outcomes.

## Deterministic helper — `scripts/auto_experiment.py` (do NOT re-derive the math by hand)

The loop's **MACHINE work** — the keep/discard t-test, the mechanism/denominator guard, the exact
LLM-Obs metric payload, the config state machine, and the stop conditions — lives in
`scripts/auto_experiment.py` (tested in `scripts/test_auto_experiment.py`). It is the single source
of truth for those mechanics: **call it, do not restate its formulas or hand-build its payloads.**
Re-deriving the SE_diff / `|t|≥2` / `min_delta` / zero-variance gate or the tag list by hand is
exactly how the drift this skill kept hitting gets in. `references/rubrics.md` still holds the *why*
(the law); this script holds the *how* (the arithmetic). Nothing in it calls an LLM, git, or MCP —
those stay with you.

| subcommand | does | replaces (don't hand-do) |
|---|---|---|
| `decide --before --after --before-stdev --after-stdev --runs [--min-delta] [--direction min]` | two-sample t-test verdict → `{delta, se_diff, t_stat, significant, within_noise, is_best_numeric, …}` | the keep/discard formula |
| `audit --best <eval_results.jsonl> --candidate <…> [--best-excluded --candidate-excluded]` | per-datapoint flips + `denominator_ok` | the mechanism/denominator diff |
| `submit-payload --iteration --sha --score --now-ms $(date +%s%3N) [--decision --basis --delta-vs-best --t-stat --significant --reasoning]` | the EXACT `submit_llmobs_experiment_events` args | hand-building tags/timestamp/shape |
| `record --config <config.json> --row '<json>'` | append the iteration row + advance `best_sha`/`best_score` | editing the config best-pointer by hand |
| `stop-check --config <config.json>` | `{stop, stop_reason}` (max / plateau / no_change streak) | restating the stop thresholds |

The script is part of **this skill**, not the repo under test — invoke it by its skill path. The
sections below say **which** subcommand to run and how to read its output; they no longer repeat the
arithmetic it owns.

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
`generate_output` (run the REAL code under test from `files_to_optimize`) and `judge`. **Prefer a
deterministic ground-truth metric** (reference output / programmatic checker / pipeline count) and
use an LLM-as-judge only when no ground truth exists — see the rubric's **Metric selection**. **No
score literals anywhere.**

Run it against the **original, unmodified** code with a **fixed pilot** `AUTO_EXP_RUNS` (**3** — an
internal bootstrap value, not a user param): the harness re-runs the whole eval R times and prints
`{mean, stdev, run_means, ...}`. `before_score` = the printed `mean`; also record `stdev` (the
noise floor). Both computed numbers, never literals — obey the scoring policy and the **Noise &
keep/discard policy** in the rubric. This pilot noise is what Step 2.4 turns into the real `runs`
and `min_delta`.

Commit `eval_harness.py`, `data.jsonl`, `data.val.jsonl`, `data.test.jsonl`, `eval_results.jsonl`.

**Do NOT report the baseline to LLM-Obs yet.** Step 2.4 may raise `runs` and re-run the baseline,
which **replaces** this pilot `mean`/`stdev`. Reporting the pilot now would publish an
`iteration:0` score that disagrees with the baseline the keep/discard gate actually uses. The
iteration-0 report is deferred to the end of Step 2.4, once the final derived-runs baseline exists.

### Step 2.4 — Derive `runs` and `min_delta` from the measured baseline noise
The pilot baseline (3 runs) gives a **real** noise floor (`stdev`, `run_means`). `runs` and
`min_delta` are **computed from it**, not chosen — derive both here, silently (no user prompt; they
are surfaced only in the final report, with reasoning):

- **`min_delta`** (compute first — `runs` depends on it) — set it **relative to measured noise**:
  `min_delta = max(0.02, k · baseline_stdev)` (e.g. `k ≈ 0.5`), so the floor tracks how noisy this
  metric actually is — a noisy metric gets a higher bar, a rock-steady one keeps the small floor.
- **`runs`** — the gate compares a *difference of two means*, so the noise that matters is the
  standard error of that difference: `SE_diff ≈ stdev · sqrt(2 / runs)`. For a real gain of size
  `min_delta` to be *resolvable* (clear the band at ~2·SE), you need `SE_diff ≲ min_delta / 2`,
  i.e. **`runs ≥ 8 · (baseline_stdev / min_delta)²`**. Compute that; if it exceeds the current
  `runs`, **you MUST raise `runs` to it** (clamp **2–10**) and **re-run the baseline** at the new
  `runs` (the re-run's `mean`/`stdev` replace the pilot's). This is not advisory — an underpowered
  run makes every moderate gain permanently uncallable (the classic failure: a true +0.05 that can
  never clear a 0.055 band at `runs=3`). Only if the pilot is already tight enough that the formula
  yields `≤ 3` does `runs` stay `3`. If the formula wants more than the clamp of 10, set `runs = 10`
  and **record in `config.json` that the metric is too noisy to fully resolve `min_delta` at 10
  runs** (so downstream discards of near-band candidates are known-underpowered, not confirmed
  nulls — see the **Higher-power confirmation** rule in the rubric).

Write the derived `runs` and `min_delta` into `config.json` (they started `null`) alongside the raw
baseline `stdev` + `run_means` you derived them from (audit trail). Every downstream iteration uses
these values. Do this once, here — do not recompute the gate mid-run.

**First commit the final baseline state, THEN report it to LLM-Obs as iteration 0** (deferred from
Step 2 so it reflects the final derived-runs baseline, not the pilot). If Step 2.4 raised `runs` and
re-ran the baseline, the working tree's `eval_results.jsonl` + `config.json` now hold the re-run
numbers but the commit from Step 2 still holds the pilot — **commit the updated baseline artifacts
now** (amend the Step 2 commit or add a new one) so a single commit contains the final
`eval_results.jsonl`, derived `runs`/`min_delta`, and `run_means`. Only then submit exactly one
eval-metric datapoint with `score_value` = the **final** `before_score` (the re-run mean if `runs`
was raised, else the pilot mean) and tags `["iteration:0",
"git.commit.sha:<baseline_commit_sha>", "decision:baseline"]` — the sha is the **full 40-character**
hash of that just-committed final-baseline commit (`git rev-parse HEAD`), and the score must match
the `before_score` every downstream iteration gates against. Same call shape and rules as **Report
each iteration's score to LLM-Obs**; this is the only submission with `iteration:0` and
`decision:baseline`.

### Step 2.5 — Census the baseline failures
Before changing anything, decompose **where the baseline loses** per the rubric's **Baseline
failure census**: bucket every failing datapoint by root cause, write `.auto_experiment/census.json`,
commit it, and surface the ranked buckets. This tells you which lever is worth pulling — and whether
the dominant failure mode is even reachable by editing `files_to_optimize`.

### Step 3 — Improve
Read the whole scope (`files_to_optimize`, expanded). Make **ONE focused change** toward `goal`,
aimed at the **largest census bucket you can plausibly move** (name that bucket in the iteration's
`reasoning`), **in whichever in-scope file holds the lever** — edit the tool/retrieval code if the
census says the misses are retrieval, the output/format code if they're formatting, and so on. Do
**not** default to rewording a prompt when the lever is elsewhere. Commit it on the scratch branch
with a message explaining what changed and why.

Before the (expensive) full eval, run a **feasibility probe** per the rubric's **Feasibility probe**:
the cheapest offline check that this change *could* move a failing census bucket. If the probe
reaches 0 failing datapoints, record the iteration `no_change` with the probe result and skip to the
next hypothesis — do **not** spend a full eval on a dead lever.

### Step 4 — Compute AFTER (re-run the SAME harness)
Re-run `.auto_experiment/eval_harness.py` (same `evaluate_line`, same data) against the changed
code. `after_score` = the new printed mean. Re-write `eval_results.jsonl`. Write the metric object
(schema in the rubric) to `.auto_experiment/result.json` and commit it **in the same commit** as
the change. `delta = after_score - before_score`.

Decide `is_best` with the **two-sample t-test gate** — run
`auto_experiment.py decide --before <before_score> --after <after_score> --before-stdev <best_stdev>
--after-stdev <after_stdev> --runs <runs> [--min-delta <min_delta>] [--direction min]`. Keep only if
its `is_best_numeric` is true (the script encodes: moves in the goal's direction AND `|t| ≥ 2` AND
`|delta| ≥ min_delta`, with the zero-variance fallback when `se_diff == 0`) — do NOT re-derive that
formula or gate on a raw-stdev band; see the rubric's Noise policy for *why*. If `is_best_numeric`
is true, run the **Mechanism audit**: `auto_experiment.py audit --best <baseline eval_results.jsonl>
--candidate <this eval_results.jsonl>` and require `denominator_ok` plus a gain that traces to the
datapoints the change touched (its census bucket) — a mean that rose while the targeted datapoints
didn't flip is a red flag. If iteration 1 passes both, it becomes the best; record it with
`auto_experiment.py record --config .auto_experiment/config.json --row '<iteration row json>'`
(this appends the row and advances `best_sha`/`best_score` — don't hand-edit the pointer).

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
   `iteration_results`), aimed at a named `census.json` bucket, **in whichever in-scope file holds
   the lever** (tool/retrieval/pipeline/config/prompt — not prompt-only). Commit it.
5. **Feasibility probe first** (rubric): cheap offline check the change can move its target bucket;
   if it reaches 0 failing datapoints, record `no_change` and skip the full eval. Otherwise re-run
   the SAME harness on `val` → `after_score`. Re-write `eval_results.jsonl` + `result.json`, commit.
6. **Keep or discard**: run `auto_experiment.py decide …` (same call as Step 4, with
   `--before <best_score>`) — keep only if `is_best_numeric` is true **and** the **Mechanism audit**
   passes: `auto_experiment.py audit --best <(git show <best_sha>:.auto_experiment/eval_results.jsonl)`
   `--candidate .auto_experiment/eval_results.jsonl` → require `denominator_ok` and a gain from the
   datapoints the change touched. Then record with `auto_experiment.py record --config … --row …`
   (decision `kept`, advances best). A t-test-insignificant gain, a `denominator_ok:false` artifact,
   or a regression → `discarded` (record the row, best unchanged). A borderline `discarded` here
   (`within_noise` true because `|t|` is just under 2) is the candidate the final **Higher-power
   confirmation** re-tests at more runs.
7. Report this iteration's score to LLM-Obs (tag `iteration:<n>`) — see **Report each iteration's
   score to LLM-Obs**.

## Report each iteration's score to LLM-Obs (every scored iteration)

Once you have a computed score for an iteration, submit **exactly one** eval-metric datapoint to
LLM-Obs with the `submit_llmobs_experiment_events` MCP tool. Do this once per iteration, right
after the score is computed and the iteration's commit / `result.json` is written — including
iteration 1 and the **iteration-0 baseline** (reported at the end of Step 2.4; there `score_value`
= `before_score` and the decision tag is `decision:baseline`).

**Do NOT hand-build the payload** — shape it with the helper, then make the MCP call yourself:

```
python3 <skill>/scripts/auto_experiment.py submit-payload \
  --iteration <n> --sha "$(git rev-parse HEAD)" --score <after_score> \
  --now-ms "$(date +%s%3N)" --experiment-id "$DD_AUTO_EXPERIMENT_ID" \
  --decision <kept|discarded|baseline|no_change> \
  --basis <significant|within_noise|regression|promoted|baseline|no_change> \
  --delta-vs-best <±delta-vs-best> --t-stat <t or omit for zero-variance> \
  --significant <true|false> --reasoning "<lead verdict + detail>"
```

It returns the exact `submit_llmobs_experiment_events` args — **exactly one** `score` metric
labelled `auto_experiment_score`, `score_value` = the harness-computed `after_score` (never a
literal), the epoch-ms timestamp, the **full 40-char** sha, and the tags — which you pass straight to
the MCP tool. If it returns `{"skip": …}` (`DD_AUTO_EXPERIMENT_ID` unset), make no call and note the
skip in `reasoning`. Pull `--delta-vs-best`, `--t-stat`, `--significant` **directly from the
`decide` output** so the reported tags can't drift from the gate that made the decision.

**Why the decision tags exist (you supply their values; the script places them):** `score_value`
alone confuses the dashboard — an iteration can post a **higher** score than the kept best and still
be `discarded`, because the gate compares vs the *best* and requires significance, which a raw
number can't show. The tags put the "why" next to the score:
- `basis:` — `significant` (kept) | `within_noise` | `regression` | `promoted` | `baseline` | `no_change`
- `delta_vs_best:` — delta vs the **current best** (NOT vs baseline); this is what makes "higher
  score, still discarded" legible
- `t_stat:` — the two-sample t (`t_stat:null` when `se_diff == 0`); `significant:` — `true|false`

`--reasoning` must **lead with a one-line verdict** (decision + basis) before the detail, e.g.
`"DISCARDED — within noise of best (Δvs_best +0.016, t=0.94); higher point estimate, not
statistically above best, not a regression."` — the lead line and the tags must agree. Report once
per iteration, including iteration 1 and the **iteration-0 baseline** (Step 2.4;
`--decision baseline --basis baseline`).

Rules:

- **One metric per iteration, plus at most one correction.** Submit exactly one metric per
  iteration at the time it is scored, and never batch several iterations into one call. The **only**
  second event allowed for the same iteration is a **promotion correction** (see final-report
  Higher-power confirmation): re-submitting that iteration with `decision:kept` +
  `promoted:higher_power_confirmation` after it is promoted. That correction is not a second
  measurement.
- **Consumer dedup rule (state it, honor it).** Because the store is append-only, an iteration may
  have two events (an earlier `decision:discarded` and a later promotion correction). Consumers of
  `auto_experiment_score` MUST dedupe **per `iteration:<n>` tag, keeping the event with the latest
  `timestamp_ms`** — that event carries the iteration's final decision. Equivalently: a
  `promoted:higher_power_confirmation` event supersedes any earlier decision for the same
  `iteration:<n>`. Do not average or count both.
- The value you submit is the same computed `after_score` recorded in `result.json`; the two must
  always agree — **except a `no_change` iteration**, which has no computed `after_score` and instead
  carries forward `best_score` as a `decision:no_change` marker (see **No-change iterations**).

### No-change iterations — emit a carried-forward marker, not a measurement

A `no_change` iteration (feasibility probe inconclusive, harness wouldn't run, judge unreachable, no
new commit) has **no computed score**. The event schema still requires a numeric `score_value` and a
`reasoning`, so you cannot omit them — but you must **not** invent a measurement. Emit a labeled
carry-forward instead:

- `score_value`: the **current `best_score`** carried forward (the iteration-1 baseline if nothing
  has been kept yet). This is `no_change`'s only honest value: the best is *unchanged*, so the score
  is *unchanged*. **Never send `0`** — `0` reads as a catastrophic regression a naive chart plots as
  a cliff. Carried-forward best plots as a flat line, which is the truth.
- `tags`: `decision:no_change` — **this tag, not the value, is the discriminator.** A `score_value`
  alone can never distinguish a no-eval carry-forward from a genuinely-measured `0`; only the
  `decision` tag can. Consumers of `auto_experiment_score` **must** branch on `decision` — exclude
  `decision:no_change` from any score aggregate (mean/best-pick), since its value is a marker, not a
  measurement.
- `reasoning`: state plainly that no full eval ran, why (e.g. the probe result), and that the value
  is the carried-forward best — not a measured score.

So `no_change` is still submitted (one metric, as every iteration), but it is unambiguously a
non-measurement: carried-forward value + `decision:no_change`. Do **not** tag it `kept`/`discarded`
(those assert a real measurement) and do **not** overload the value to signal state.

## Stop conditions & guards

After each iteration's row is recorded, run `auto_experiment.py stop-check --config
.auto_experiment/config.json`; if it returns `stop: true`, stop and report the current best with its
`stop_reason`. The script owns the thresholds — do not restate them — but the conditions it encodes,
and *why*:

- **`reached max_iterations`** — the hard budget.
- **`plateau (deltas within noise)`** — the last **3** rows all `discarded` AND `within_noise`
  (`|t| < 2`, not significant either way). Continuing past a noise plateau just generates
  within-noise wiggle; escalate instead (a new census bucket, a different dimension, or accept the
  ceiling). A real *regression* streak (`within_noise:false`) is NOT a plateau — the script
  distinguishes them, keep going.
- **`no_change streak (nothing computable)`** — **5** consecutive `no_change` rows; give up rather
  than burn iterations.

Guard: **a change with no computable score is `no_change`, never a fabricated number** (harness won't
run / no new commit / judge unreachable / feasibility probe reached 0). Record the blocker in
`reasoning`; its LLM-Obs submission is the carried-forward marker (`decision:no_change`,
`score_value` = current best), not a measured score — see **No-change iterations**.

## Final report

1. Ask yourself the run-level wrap-up and write `final_result` into `config.json`:
   `{ "baseline_score", "best_score", "best_iteration", "best_sha", "iterations_run",
   "stop_reason", "reasoning", "noise_calibration" }` (reasoning = what was tried across all
   iterations, what worked, what didn't, why the winner won). `noise_calibration` records the
   Step 2.4 derivation — **this is where `runs`/`min_delta` are first shown to the user**, since
   they were never intake params:
   `{ "runs_pilot", "runs_final", "baseline_stdev", "run_means", "min_delta" }`. State in the
   summary that `runs`/`min_delta` were **computed from the measured baseline noise** (not chosen),
   with the reasoning, so the user sees the gate that actually governed every keep/discard decision.
2. **Higher-power confirmation of the top within-noise candidate** (do this BEFORE the held-out
   test, and before naming the best). Scan the `discarded` rows for the one with the run's **best
   `val` mean in the goal's direction** that was discarded only because it was borderline
   (`within_noise` true with `|t|` just under 2, per its recorded `decide` output) — a
   **promising-but-underpowered** result, not a confirmed null. Re-run the current best and that
   candidate back-to-back at the max `runs` on `val` and **pool with their existing runs** (e.g.
   10 + 10 → 20 per side), then run `auto_experiment.py decide …` on the pooled means/stdevs with
   `--runs <pooled>`. **The same gate decides** — keep iff `is_best_numeric` (more runs shrink
   `SE_diff`, which is the whole point; `min_delta` floor still applies, so a rerun that shrinks
   `|delta|` below the floor does not promote). If it now passes, it becomes the best; else leave it
   discarded with the higher-power numbers recorded. Do this for the **single** best candidate only.
   - **Propagate a promotion to LLM-Obs.** The iteration's metric was already submitted with its
     iteration-level `decision:discarded`. If confirmation **promotes** it, that tag is now wrong —
     the dashboard would show the run's best as `discarded`. Re-run `submit-payload` for that
     iteration (same `--iteration`, `--sha`, `--score`) with `--decision kept --basis promoted` and
     a `--reasoning` that supersedes the earlier discarded decision (cite the t-test), and send it.
     This is the one sanctioned exception to "exactly one metric per iteration" — a correction, not
     a second measurement. Leave a genuinely-discarded candidate's metric as-is.
   - **Propagate a promotion to LLM-Obs.** The iteration's metric was already submitted with its
     iteration-level `decision:discarded`. If confirmation **promotes** it, that tag is now wrong —
     the dashboard would show the run's best as `discarded`. Re-submit that iteration's metric
     (same `iteration:<n>`, same sha, same `score_value`) with `decision:kept` plus a
     `promoted:higher_power_confirmation` tag and a `reasoning` stating it supersedes the earlier
     discarded decision (cite the t-test). This is the one sanctioned exception to "exactly one
     metric per iteration" — the later event is a correction, not a second measurement. Leave a
     genuinely-discarded candidate's metric as-is.
3. **Held-out `test` comparison (the real headline).** Run the harness once on the **baseline**
   commit and once on the **best** commit against `.auto_experiment/data.test.jsonl`
   (`AUTO_EXP_DATA=.auto_experiment/data.test.jsonl`), both at the derived `runs` count, then judge
   the baseline-vs-best `test` delta with the **same gate**: `auto_experiment.py decide
   --before <baseline_test> --after <best_test> --before-stdev <…> --after-stdev <…> --runs <runs>`.
   Report that as the run's result — the `val` hill-climb gain is not the headline. If `test` is not
   significant (`is_best_numeric:false`) even though `val` was, say so explicitly (the win did not
   generalize) and treat baseline as best.
4. Print a per-iteration table (iteration, val delta, decision, sha) and name the best commit.
5. **If nothing beat the baseline on `test`**: report the baseline as the best result and leave the
   original code in place (`best_sha` empty). Do not fabricate an improvement.
6. Tell the user the scratch branch + best commit so they can open a PR from it if they want.
7. **Mark the experiment finished in LLM-Obs.** Call `update_llmobs_experiment` with
   `experiment_id` = `$DD_AUTO_EXPERIMENT_ID` (skip if unset) exactly once at the very end — after
   the last iteration, or immediately whenever you give up early. Set `status: "completed"` for any
   run that reached the final report (including one where baseline stayed best — a run that
   finished cleanly is completed, not failed). Set `status: "failed"` with a short `error` when the
   run could not finish — the harness never ran, setup was blocked, or you abandoned before any
   scored iteration. This status update is separate from the per-iteration metric submissions; make
   it once, last.

## Notes

- Every score is computed by running code. If you ever find yourself about to type a score
  number, stop — run the harness instead.
- Keep `.auto_experiment/` committed; it is the reproducible record of the run.
