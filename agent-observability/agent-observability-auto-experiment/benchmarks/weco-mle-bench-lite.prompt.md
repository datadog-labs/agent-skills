# Benchmark prompt — auto-experiment skill vs Weco AIDE² on MLE-Bench Lite

> Paste this whole file into Claude Code (with the `agent-observability-auto-experiment` skill
> available and the Datadog LLM-Obs MCP connected). It runs the skill end-to-end on MLE-Bench
> Lite and prints a comparison table against the Weco AIDE² article. **Do not fill any score by
> hand** — every number in the final table comes from a real run of the harness or is quoted from
> the article. If any step cannot produce a real score, STOP and report the blocker.

---

## Objective

Reproduce, at small scale, the "recursive self-improvement" measurement from Weco's blog
*First evidence of recursive self-improvement* (AIDE²), using our local
`agent-observability-auto-experiment` skill as the improving agent. The article's headline is the
**improvement delta over baseline on the held-out MLE-Bench Lite benchmark**
(AIDE47 **+0.053**, p=0.0024; AIDE85 **+0.042**, p=0.0041 vs AIDE0). Run the auto-experiment
skill on the **same benchmark** — hill-climbing a baseline ML pipeline whose held-out medal-rate
is the metric — then emit a structural comparison table against the article. This benchmarks the
skill's self-improvement loop, not a Kaggle leaderboard entry.

---

## Prerequisites (fail loudly — never fabricate)

Check all of these before doing anything else. If any is missing, STOP and tell the user exactly
what to provide — do **not** proceed with a placeholder or an invented score.

1. **Kaggle API credentials** at `~/.kaggle/kaggle.json` (`chmod 600`). MLE-Bench downloads
   competition data through the Kaggle CLI; without creds `mlebench prepare` cannot fetch data.
2. **Datadog LLM-Obs MCP reachable** (the `submit_llmobs_experiment_events`,
   `add_llmobs_dataset_records`, `launch_llmobs_experiment`, `update_llmobs_experiment` tools). The
   skill reports one metric per iteration; the setup step creates the backing dataset + experiment.
3. **Python 3.10+** with `pip`, plus enough disk for a handful of small competitions (each
   selected competition is tens–hundreds of MB, not the full 158 GB Lite set).
4. **`DD_AUTO_EXPERIMENT_ID`** — will be created and exported in Setup step 4. The skill reads it
   from the environment; if it is unset when the skill runs, the skill skips LLM-Obs submission
   (acceptable, but prefer to set it so the run is observable).

---

## Setup (do this yourself, before invoking the skill)

### 1. Install MLE-Bench and pick a CPU-runnable subset of Lite

```bash
git clone https://github.com/openai/mle-bench.git ~/mle-bench
cd ~/mle-bench && pip install -e .
cat experiments/splits/low.txt        # the 22 MLE-Bench Lite competitions
```

Full Lite (22 comps / 158 GB / GPU / ~24 h each) is infeasible for a 50-iteration hill-climb.
**Select a lightweight subset** from `low.txt` by this rule: keep **tabular and text**
competitions that train on CPU in minutes; **exclude image and audio** (need a GPU / large
downloads). Candidate subset (verify each still appears in `low.txt` before using):

- `random-acts-of-pizza` (text/tabular, metric AUC)
- `spooky-author-identification` (text, metric multi-class logloss — lower is better)
- `detecting-insults-in-social-commentary` (text, metric AUC)
- `nomad2018-predict-transparent-conductors` (tabular regression, metric RMSLE — lower is better)
- one `tabular-playground-series-*` if present in `low.txt`

Aim for **4–6 competitions**. Prepare each (downloads + builds the local train/test split and the
grader):

```bash
for c in <chosen competitions>; do mlebench prepare -c "$c"; done
```

Record, per competition, its **metric** and **optimization direction** (higher/lower is better)
and the **prepared data dir** — you need the direction for the harness and the goal.

### 2. Create the working repo + baseline pipeline (this is `files_to_optimize`)

Make a scratch working dir — **this dir is the skill's current working directory / repo under
test** — and write a single deliberately-naive baseline pipeline. Keep it real, runnable, and
weak on purpose so there is genuine headroom to climb:

```
~/mle-bench-autoexp/           # cwd for the skill run; git init it
  solve.py                     # <- files_to_optimize
```

`solve.py` contract: given a prepared competition dir (train data + sample submission), it writes
a valid `submission.csv`. Baseline behavior = the trivial predictor per metric: **majority class
/ sample-submission constant** for classification, **train-mean** for regression. No feature
engineering, no real model. This is the analogue of AIDE0.

`git init` the dir and commit `solve.py` so the skill has a clean baseline SHA.

### 3. Upload the competitions as an LLM-Obs dataset (the skill's data source)

The skill needs a `dataset_id` **or** `ml_app`. Upload **one record per competition** so each
competition is one scoreable datapoint:

Use `add_llmobs_dataset_records` with a new dataset (e.g. name `weco-mle-bench-lite-benchmark`),
one record per selected competition:

```json
{
  "input_data":  {"competition_id": "<comp>", "data_dir": "<prepared dir>",
                  "metric": "<auc|logloss|rmsle|...>", "direction": "<max|min>"},
  "expected_output": {"medal": "bronze-threshold graded by mlebench grade-sample"}
}
```

Capture the returned **`dataset_id`**.

### 4. Create the LLM-Obs experiment and export its id

Create an experiment on that dataset with `launch_llmobs_experiment` (name e.g.
`auto-experiment-weco-mle-bench-lite`). Capture the experiment id and export it so the skill picks
it up:

```bash
export DD_AUTO_EXPERIMENT_ID="<experiment id>"
```

### 5. Sanity-check the deterministic grader on the baseline

Prove the evaluator works before spending any iteration budget:

```bash
for c in <chosen competitions>; do
  python solve.py --competition-dir "<prepared dir for $c>"      # writes submission.csv
  mlebench grade-sample "<submission.csv>" "$c"                  # prints medal? + score
done
```

Confirm you get a real per-competition medal 0/1 and thus a real baseline **medal-rate** (mean
over competitions). If the grader errors or returns nothing, STOP — the benchmark cannot proceed
without a real metric.

---

## Invoke the auto-experiment skill

From `~/mle-bench-autoexp/` (cwd = repo under test, with `DD_AUTO_EXPERIMENT_ID` exported), invoke
the **`agent-observability-auto-experiment`** skill. Satisfy its mandatory intake gate with this
fully-resolved config (present it back to the user for validation before starting, per the skill):

| Field | Value |
|---|---|
| `files_to_optimize` | `solve.py` (the whole pipeline is fair game: model, features, preprocessing, ensembling — anything in `solve.py`) |
| `goal` | **Maximize the held-out Any-Medal rate** across the selected MLE-Bench Lite competitions (bronze threshold, 0/1 per competition). Direction: higher is better. |
| `evaluators` | **Deterministic ground truth, no LLM judge.** For each competition datapoint: run `solve.py` → `submission.csv`, then `mlebench grade-sample <submission.csv> <competition>`; score = **1 if a bronze medal (or better) is earned, else 0**. The mean over competitions is the medal-rate. Respect each competition's metric direction (some metrics are lower-is-better) — the medal decision already encodes that; the harness must not re-invert it. |
| data source | `dataset_id` = the dataset from Setup step 3 (`weco-mle-bench-lite-benchmark`). |
| `max_iterations` | **50** |
| `model` | default (the Claude model of this session) |
| `base_branch` | the baseline branch/commit from Setup step 2 |

Notes to honor while the skill runs (they are in the skill/rubrics, restated for this benchmark):

- The harness's `judge()` is the **deterministic `mlebench grade-sample` call**, not an LLM judge
  — `stdev` from the judge side is ~0; run-to-run noise comes only from stochastic model training
  in `solve.py`. Let Step 2.4 derive `runs`/`min_delta` from that measured noise.
- The skill splits the competitions ~70/30 into `val` (hill-climb gate) and `test` (held-out).
  With only 4–6 competitions the `test` split is tiny — **state the reduced statistical power in
  `reasoning`** (rubric: note low power, never fake a split). This is expected for a scaled-down
  benchmark; the article ran on the full held-out set with 3 seeds.
- Every kept change must pass the **mechanism audit + denominator guard** — this is our analogue
  of the article's reward-hacking control (a higher mean from fewer graded competitions is an
  artifact, discard it).

Let the skill run all 50 iterations (or stop early on its plateau/`no_change` guards). It reports
one `auto_experiment_score` metric per scored iteration to `DD_AUTO_EXPERIMENT_ID` and records
everything in `.auto_experiment/config.json`.

---

## Compare against the article

After the run, read `.auto_experiment/config.json` — specifically `final_result`
(`baseline_score`, `best_score`, `best_iteration`, `iterations_run`, `stop_reason`) and
`noise_calibration` (`runs_final`, `baseline_stdev`, `min_delta`) — plus count `kept` vs
`discarded` rows in `iteration_results`. Then print this table, filling the **right column only
from the real run** (leave a cell blank and say "not reached" rather than inventing a number):

| Aspect | AIDE² (Weco article) | auto-experiment skill (this run) |
|---|---|---|
| Improve target | the agent's own scaffold (recursive) | `solve.py` ML pipeline under test |
| Outer-loop steps | 100 over 8 days | 50 iterations (`iterations_run` = _fill_) |
| Held-out benchmark | MLE-Bench Lite (22 comps, never optimized on) | MLE-Bench Lite CPU subset (_N_ comps), `test` split |
| Metric | Any-Medal % improvement vs AIDE0 | held-out `test` medal-rate delta vs baseline |
| Baseline score | AIDE0 (reference) | `final_result.baseline_score` = _fill_ |
| Best score | AIDE47 / AIDE85 | `final_result.best_score` = _fill_ (iter `best_iteration`) |
| **Improvement delta** | AIDE47 **+0.053** (p=0.0024); AIDE85 **+0.042** (p=0.0041) | `best_score − baseline_score` = _fill_ ± noise band `max(pooled_stdev, min_delta)` |
| Change-rejection rate | ~90% of changes rejected | `discarded / iterations_run` = _fill_ |
| Significance gate | p < 0.05, 3 seeds | `max(pooled_stdev, min_delta)` noise band (`runs_final`, `min_delta` = _fill_) |
| Reward-hacking control | KernelBench 63% → 34% (AIDE0→AIDE85) | mechanism audit + denominator guard (kept only if delta is causal) |
| Driving model | claude-opus-4.7 (outer) / gemini-3-flash (inner) | session Claude model (_fill from config metadata_) |

**Honesty note to append:** state the subset size and that the `test` split is small → wide noise
band / low statistical power, so a within-noise result is a **tentative** improvement, not a
confidently-demonstrated one. If the held-out `test` delta improves in the goal's direction but did
**not** clear significance, keep the best as best but **flag it tentative** and say plainly the gain
is within noise / did not clearly generalize (do not report the `val` gain as a confident headline).
Only if `test` shows no improvement in direction (flat or a regression) report the **baseline as
best**.
Point the user at the scratch branch + best commit and the LLM-Obs experiment
(`DD_AUTO_EXPERIMENT_ID`) so they can inspect the per-iteration trajectory.
