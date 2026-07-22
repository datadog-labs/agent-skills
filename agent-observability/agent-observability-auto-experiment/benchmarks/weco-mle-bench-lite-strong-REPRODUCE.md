# Reproduce: auto-experiment on a STRONG MLE-Bench-Lite baseline (fair-footing vs Weco AIDE²)

End-to-end steps to reproduce the **fair-footing** benchmark: run the auto-experiment hill-climb
on top of a *mature* model baseline (not a trivial stub) over 35 iterations, and compare the kept
improvement to Weco's AIDE² blog (MLE-Bench Lite: AIDE47 +0.053, AIDE85 +0.042).

**Headline result to expect:** baseline mean-percentile **0.760** → best **0.787** (**+0.027**,
1 iteration kept), then a plateau — **34/35 attempts rejected (97%)**. Deterministic grading, no
invented scores.

---

## 0. Prerequisites / environment

| Need | Why | Fix if missing |
|---|---|---|
| **Python ≥ 3.11** | mle-bench requires it | `pyenv install 3.12.9` |
| **Kaggle account + API token** | download competition data | see step 2 |
| **git, git-lfs** | mle-bench leaderboards are LFS pointers | step 3 |
| **~2 GB disk** | prepared data for 3 comps | — |
| Datadog LLM-Obs MCP (optional) | per-iteration score push | skipped here (`DD_AUTO_EXPERIMENT_ID` unset) |

```bash
# dedicated venv on 3.12 (3.10 fails mle-bench's >=3.11 pin)
~/.pyenv/versions/3.12.9/bin/python3 -m venv ~/mle-bench-venv
~/mle-bench-venv/bin/pip install --upgrade pip
```

---

## 1. Install mle-bench

```bash
git clone https://github.com/openai/mle-bench.git ~/mle-bench
cd ~/mle-bench && ~/mle-bench-venv/bin/pip install -e .
```

**Gotcha — kaggle version:** the new Kaggle `KGAT_` API tokens need **kaggle ≥ 1.7**, but
mle-bench pins `kaggle<1.7`. Upgrade anyway (auth wins; download API is compatible):

```bash
~/mle-bench-venv/bin/pip install --upgrade "kaggle>=1.7.4"   # installs 2.x
```

**Gotcha — `kaggle.rest` removed in kaggle 2.x** (mle-bench imports `from kaggle.rest import
ApiException`). Add a one-line shim:

```bash
echo 'ApiException = IOError' > ~/mle-bench-venv/lib/python3.12/site-packages/kaggle/rest.py
```

---

## 2. Kaggle auth + accept competition rules

```bash
export KAGGLE_API_TOKEN=KGAT_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx     # your token
~/mle-bench-venv/bin/kaggle competitions list | head             # smoke test
```

**Gotcha — 403 on download = rules not accepted.** Listing files works without acceptance;
downloading does NOT. Click **"Late Submission" / "I Understand and Accept"** on each competition
page (website only, one-time):

- https://www.kaggle.com/c/random-acts-of-pizza/rules
- https://www.kaggle.com/c/spooky-author-identification/rules
- https://www.kaggle.com/c/nomad2018-predict-transparent-conductors/rules

---

## 3. git-lfs + pull leaderboards

Leaderboards (needed for medal thresholds / percentile) ship as LFS pointers.

```bash
# user-local git-lfs (no sudo)
curl -sL https://github.com/git-lfs/git-lfs/releases/download/v3.5.1/git-lfs-linux-amd64-v3.5.1.tar.gz -o /tmp/gitlfs.tgz
tar -C /tmp -xzf /tmp/gitlfs.tgz && mkdir -p ~/bin && cp /tmp/git-lfs-3.5.1/git-lfs ~/bin/
export PATH="$HOME/bin:$PATH"
cd ~/mle-bench && git lfs install --local
git lfs pull -I "mlebench/competitions/random-acts-of-pizza/leaderboard.csv,mlebench/competitions/spooky-author-identification/leaderboard.csv,mlebench/competitions/nomad2018-predict-transparent-conductors/leaderboard.csv"
```

---

## 4. Prepare the 3 competitions

```bash
cd ~/mle-bench
for c in random-acts-of-pizza spooky-author-identification nomad2018-predict-transparent-conductors; do
  ~/mle-bench-venv/bin/mlebench prepare -c "$c"
done
# data lands in ~/.cache/mle-bench/data/<comp>/prepared/{public,private}
```

---

## 5. Working repo — STRONG baseline + harness

Create `~/mle-bench-autoexp-strong/` (git repo, = the skill's cwd) with two files.

### `solve.py` (the STRONG baseline = iteration 0)
Given `<competition_id> <out_csv>`, trains a mature model per comp and writes a submission:
- **pizza** (AUC↑): LogisticRegression on request-time numeric features **+ TF-IDF of request
  text+title** (`hstack`), C=2.
- **spooky** (logloss↓): word(1-2)+char_wb(3-5) TF-IDF → LogisticRegression C=4.
- **nomad** (RMSLE↓): `GradientBoostingRegressor` per target on the tabular features, clip ≥0.

(Full source is the committed `solve.py` in the run repo; it is the iter5 winner of the trivial
run promoted to baseline.)

### `.auto_experiment/eval_harness.py`
- `generate_output(comp, seed)` → subprocess `python solve.py comp out.csv`.
- `evaluate_comp` → `grade_csv(out, competition)` (mle-bench's own grader) for the raw metric,
  then **leaderboard percentile** = fraction of Kaggle teams beaten (direction-aware, from
  `leaderboard.csv`). Per-datapoint score = percentile; also records `any_medal`/`above_median`.
- runner: `AUTO_EXP_RUNS` full re-runs (different seeds) → prints/writes `{mean, stdev,
  run_means, per_comp, medal_rate}` to `.auto_experiment/last_run.json`.

```bash
cd ~/mle-bench-autoexp-strong && git init && git add -A && git commit -m "strong baseline + harness"
# baseline score (expect mean ~0.760, stdev ~0.0002, medal_rate 0.333)
AUTO_EXP_RUNS=5 ~/mle-bench-venv/bin/python .auto_experiment/eval_harness.py
```

**Noise gate (derived, not chosen):** baseline stdev ≈ 0.0002 ⇒ `runs=3`, `min_delta=0.02`
(the floor dominates). **Keep an iteration only if `|Δmean| > 0.02` AND the per-datapoint
mechanism audit passes** (only the edited competition moved; scored-count unchanged at 3).

---

## 6. Hill-climb — 35 iterations

Each iteration: make ONE focused change to `solve.py`, re-run the harness (`AUTO_EXP_RUNS=3`),
compare to the current best, **keep** (commit) if Δ>0.02 else **revert** (`git checkout -- solve.py`).

**Iters 1–5** run one-at-a-time (see `REPORT.md`). Only **iter5 is kept**:

> **iter5 (kept, +0.027):** pizza = ensemble LogisticRegression(text+numeric) with
> `HistGradientBoostingClassifier` on numeric features, probabilities averaged 0.5/0.5.
> Pizza AUC 0.657→0.687, mean 0.760→**0.787**.

**Iters 6–35** are batched candidate sweeps (independent-competition search, keep best-per-comp):

```bash
export KAGGLE_API_TOKEN=KGAT_...
cd ~/mle-bench-autoexp-strong
~/mle-bench-venv/bin/python .auto_experiment/sweep10.py    # iter6-15  -> sweep10.json
~/mle-bench-venv/bin/python .auto_experiment/sweep20.py    # iter16-35 -> sweep20.json
```

`sweep20.py` also tries a **nomad geometry lever** (features parsed from `geometry.xyz`: atom-species
counts, cell volume, density) — a real *negative* result (overfits ~2.4k rows, keep tabular baseline).

**Expected sweep outcome:** 0/30 clear the 0.02 gate. Best single candidate ≈ +0.007; combining the
best of 20 across comps ≈ **+0.013**, still under the floor → discarded. Best stays **0.787**.

---

## 7. Expected final tally

| | value |
|---|---|
| Baseline mean percentile | **0.760** |
| Best | **0.787** (iter5) |
| Kept / total | **1 / 35** |
| Rejection rate | **97%** |
| Cumulative Δ | **+0.027** |
| Medal-rate | 0.333 (nomad bronze), unchanged |

**Verdict vs AIDE²:** +0.027 is the same order as AIDE²'s +0.042–0.053, at a matching rejection
rate (~90–97%). On par per unit of search headroom, marginally below in raw total — the small
3-comp / sklearn search space saturates after one real step, so extra iterations don't move it.

---

## Notes / gotchas recap
- Run everything with `~/mle-bench-venv/bin/python` (has pandas + sklearn + mlebench); the base
  3.10 env does not.
- `KAGGLE_API_TOKEN` must be exported for any command that touches the grader/data path.
- Only **kept** iterations are committed to `solve.py`; discarded attempts are reverted — their code
  lives in `sweep10.py` / `sweep20.py` (iter6–35) and the run `REPORT.md`.
- Scoring policy: never hand-write a score. Every number is `grade_csv` over real submissions.
- Grading is deterministic; run-to-run wiggle (~0.0002) comes only from stochastic model training.
