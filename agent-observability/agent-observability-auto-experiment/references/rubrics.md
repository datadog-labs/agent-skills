# Auto-experiment rubrics (non-negotiable)

These rules govern **this skill's** loop, and they are load-bearing: paraphrasing them weakens it.

The production `auto_experiments` worker
(`domains/ml_observability/apps/apis/auto_experiments/service/prompts.py`) is their **ancestor**,
not a synchronization contract. The `(_anchor)` in each heading names the worker prompt fragment the
section grew from, kept so a reader can trace the lineage — but the worker defines only six of those
anchors, and this skill has diverged from several of them deliberately. Nothing here changes the
worker, and the worker is not the authority on anything below.

| section | status |
|---|---|
| `_scoring_policy` | **inherited** (the `no_change` carve-out is skill-only) |
| `_messages_source_guidance` | **inherited** |
| `_data_selection_guidance` | **diverged** — the holdout is scored every iteration; `split_mode`; stratification; corpus size gate |
| `_eval_harness_skill` | **diverged** — two parity templates, hydrated cache, required `AUTO_EXP_SPLIT`, per-rep rows, errors sidecar, redaction |
| `_return_metric_block` | **diverged** — split-qualified schema with paired case-level stats & guardrails |
| `_config_update_block` | **inherited** (absorbed into SKILL.md's `config.json` contract) |
| `_noise_policy` | **skill-only** — no counterpart in the worker |
| `_two_uncertainties` | **skill-only** |
| `_failure_census` | **skill-only** |
| `_feasibility_probe` | **skill-only** |
| `_metric_selection` | **skill-only** |
| `_mechanism_audit` | **skill-only** |
| `_data_isolation`, `_stall_taxonomy`, `_goal_and_guardrails` | **skill-only** |

**Vocabulary note.** The sibling skill `agent-observability-build-eval-from-annotations` (and
Anthropic's `eval-hillclimb.md`) says `train`/`holdout` or `train`/`test` where this skill says
`val`/`test`. The names here stay as they are — they are already in `config.json`, in the dataset
names, and in every sentence below — so read `val` as the search/analysis ("train") split and
`test` as the held-out-from-analysis selection split.

## Scoring policy (`_scoring_policy`)

⛔ **INVENTING SCORES IS FORBIDDEN.** Do NOT hard-code, estimate, guess, manually assign, or
carry over score numbers. Every score MUST be the return value of actually running code over the
data. Comments or arrays of "representative"/"fixed" scores are forbidden. If you cannot truly
compute a score, STOP and report the blocker — never substitute a made-up number.

A consequence for the loop: if a change is made but its score cannot be computed (harness won't
run, judge unreachable, etc.), that iteration is recorded as **no_change** with the blocker in
`reasoning` — it is never scored with a fabricated number.

**The one carve-out — the `no_change` LLM-Obs marker (not a fabricated score).** The experiment
event schema requires a numeric `score_value`, so a `no_change` iteration submits the current
`best_score` **carried forward**, tagged `decision:no_change`. This does not violate the rule
above: it is explicitly **not a measurement of the change** (the change was never scored), it is
labeled as such by the tag, and consumers MUST exclude `decision:no_change` from score aggregates.
Carrying forward the best is honest ("best unchanged"); inventing a number to *represent the
change's quality* is what stays forbidden. See `references/reporting.md` **No-change iterations**.

## Goal, target and guardrails (`_goal_and_guardrails`)

A hill-climb is not always "make the number go up". Cutting cost or latency while quality holds is
just as common, and the loop behaves differently for each — so the goal carries a **target**, a
**direction**, and the metrics it must **hold**.

`goal` accepts either the free-text string the skill has always taken (back-compatible: it becomes
`{text: <the string>, target: "score", direction: <inferred from the text>, hold: []}`) or the
object:

```json
"goal": {
  "text": "<the user's words, verbatim — still must-ask>",
  "target": "score" | "cost_usd" | "latency_s",
  "direction": "higher" | "lower",
  "hold": ["score"]
}
```

- **`text` stays must-ask and verbatim.** `target`/`direction`/`hold` come from their own
  `AskUserQuestion` with four options: raise `score`; cut `cost_usd` holding `score`; cut
  `latency_s` holding `score`; cut both holding `score`. Never infer them from `text`.
- When `target != "score"`, `hold` defaults to `["score"]` — a cost win that wrecks quality is not a
  win.
- When `target == "score"`, `hold` defaults to **`[]`**, and cost and latency are **reported every
  iteration as ratios against baseline** without gating (calling out any kept change that raises
  cost or p95 latency by >20%). This is deliberate: hard guardrails on every recorded metric
  strangle legitimate quality wins, and the user can opt in at intake.
- **A guardrail fires only on a regression outside noise**: the held metric moved the wrong way AND
  `|t| ≥ 2` AND `|Δ| ≥ min_delta_h`. Within-noise movement is reported, never discarded on.
- **Per-guardrail floors are relative**, derived in Step 2.4 alongside the target's:
  `min_delta_h = max(0.02 × baseline_h, stdev_h · √(8 / max_runs))`. Cost and latency are not on a
  0–1 scale, so the absolute `0.02` floor the score uses is meaningless for them.
- **The harness captures `usage` and `latency_s` unconditionally**, even for a pure score goal. A
  goal can change mid-run, and the report must always be able to show the quality-vs-cost trade-off
  in absolute numbers rather than implying it.
- When `files_to_optimize` includes model selection or API parameters, model choice and
  `effort`/`max_tokens` are usually the largest cost levers — say so once when the target is cost.

## Noise & keep/discard policy (`_noise_policy`)

⚠️ **A single eval run is a NOISY ESTIMATE, not a measurement.** The code under test and any LLM
judge are stochastic, so the mean wiggles run-to-run. Treat every score as `mean ± stdev` over
**`AUTO_EXP_RUNS` full re-runs of the eval on the same data** (the harness does this and prints
`stdev` + `run_means`), on **each** split.

### The keep decision is made on `test`, and `val` is what can veto it

Both splits are scored every iteration (`split_mode: val_test`). The decision reads them together:

| val | test | decision | `basis` |
|---|---|---|---|
| up in the goal's direction | up in the goal's direction | **kept**; confidence labelled from the test evidence | `significant` / `within_noise` |
| up | flat, or down within noise | **discarded, revert** — the change overfit to the cases the improver read | `overfit` |
| up | down outside noise | **discarded** | `regression` |
| flat or down | up | **not kept by default** — at these rep counts that pattern is usually noise; keep only if it repeats on a second run | `unreplicated` |
| both flat / down within noise | | **discarded** | `within_noise` |
| any | any, mechanism audit failed | **discarded** | `audit_failed` |
| any | any, a `hold[]` metric regressed outside noise | **discarded** | `guardrail_regression` |
| any | any, an unexplained memorization hit | **discarded** | `memorized` |

Precedence when several apply: **`audit_failed` > `guardrail_regression` > `memorized` >
`regression` > `overfit` > `unreplicated` > `significant` > `within_noise`.**

**What survives from the old single-split rule, and what does not.** "Keep the higher point estimate
as best; significance is a CONFIDENCE label, not a keep gate" is still true — but it now reads the
**test** point estimate. A test-up-within-noise change (with `val` also moving in direction) is
still kept and flagged tentative. What changed is that **a `val`-up / `test`-flat change is now
discarded immediately (`basis:overfit`)**, where it used to be kept until the final report. That is
the single biggest behavioural change in this loop, and it is the whole reason the holdout is
measured every iteration: a machine-driven loop has no other channel through which "this only helped
the rows the improver read" can become visible.

**Zero-variance case (`SE_diff == 0`).** A fully deterministic metric (both stdevs `0` — common for
the ground-truth checkers this rubric prefers) makes `t = Δ/SE_diff` undefined. Do not compute the
t-test then; the move is exact, so `|Δ| ≥ min_delta` in the goal's direction labels it `significant`
(else `within_noise`). Guard the division. The paired case-level tests still run and are still
informative: zero run-to-run variance does not mean zero variance across which cases you happened to
draw.

**Compare on the same footing.** `best_mean`/`best_stdev` come from a real R-run harness run of the
current best, not a stale single number carried forward. When in doubt, re-run best and candidate
back-to-back so data/endpoint drift cancels. **Recompute the spread from THESE two runs every
iteration — never freeze it at the baseline's.** A change that also reduces variance (a precision
fix that collapses run-to-run wiggle) must be judged against the *current* noise; freezing the
baseline band silently penalizes it. And **do NOT gate on a raw-stdev band** (`max(pooled_stdev,
min_delta)`): raw `stdev` is a property of the metric and does not shrink as you add runs, so such a
gate can never be cleared by power and would discard real effects forever. `SE_diff` does shrink
with runs — which is why Step 2.4 derives `runs` from `min_delta`, and why higher-power confirmation
can resolve a borderline candidate.

**A within-noise win is kept, but LABELED.** Point estimates of 0.15 vs 0.14 with stdev ~0.02 might
be noise — the loop keeps the higher-in-direction candidate as the new best anyway (provided `test`
moved with `val`), tagged `within_noise` / tentative, and its `reasoning` MUST say the gain could be
noise and the score should be read carefully. What is forbidden is **hiding** the uncertainty, not
keeping it.

**Significance labels a move; it never stops the run.** A within-noise keep is progress, so it
**resets** the plateau discard streak like any other keep. The loop triggers stall triage (and
eventually stops on a plateau) only when the last **3 scored** iterations were all **discarded**
(SKILL.md, *Stop conditions & guards*). Stopping on "no significant improvement in 3" would end
every run on a noisy metric at iteration 3, since there `min_delta` often exceeds any single
iteration's gain — and those are precisely the runs where a chain of kept tentative steps compounds
into a significant total, confirmed by the pooled confirm run at the end.

**Raise power to gain CONFIDENCE, not to unlock the keep.** Adding `runs` shrinks `SE_diff` so `|t|`
can cross 2 and upgrade a tentative best from `within_noise` to `significant`. Never present a
`within_noise` best as `significant` without the runs to back it.

**Higher-power confirmation to upgrade a tentative best (optional).** If the current best was kept
`within_noise`, you MAY re-run best and prior best back-to-back on `test` at the `max_runs` ceiling
and **pool** with the existing runs (`max_runs` caps each harness invocation's `runs`, not the
pooled total). This does not change *what* is best; it only re-labels the confidence. Label by the
same t-test and paired case tests, never by a raw-stdev band. Do this for the single best candidate
of the run, not every wobble.

### Suspicion — an implausible jump is a measurement bug until proven otherwise

A model-graded score that leaps further than the change could reasonably explain is far more often
the judge being gamed than the app getting better, and it looks exactly like a breakthrough until
someone checks. **Trigger** when the test delta exceeds `max(3 × min_delta, 0.15)` in one iteration,
**or** exceeds the census-implied reachable ceiling for the bucket it targeted (a fix can gain at
most what the cases showing that behavior currently lose:
`bucket_count / scored × (1 − mean_bucket_score)`). On trigger, before accepting:

1. hand-read 3–5 flipped datapoints' outputs and judge justifications — orchestrator, **`val` rows
   only**;
2. confirm the judge is not rewarding a surface pattern the change happened to introduce: length, a
   phrase, a format, a self-assessment the output now emits;
3. record `suspicious: "<one-line concern>"` (`null` when clean) and `suspicion_checked: true` on
   `result.json` and the `iteration_results` row, and publish `suspicious:<true|false>` as a tag —
   the sentence goes in `reasoning`, because tag values are lowercased and normalized;
4. if the judge was gamed, discard with `basis:audit_failed` and file the finding as a
   `grader_disagreement` entry for the stall taxonomy.

The rule applies to **deterministic** metrics too, with a different investigation: recompute the
headline from the raw rows and check `scored` / `excluded` / `errored`. There, a suspicious jump
means a denominator or data bug, not a gamed judge.

## Two kinds of uncertainty — run-to-run vs case-to-case (`_two_uncertainties`)

The two-sample t-test over run means answers exactly one question: *would this gain reproduce if I
reran the eval?* It does **not** answer the question a reader usually hears in the word
"significant": *would this gain hold on other cases?* Those are different uncertainties with
different denominators, and conflating them is the most common way an honest loop overclaims.

| question | varies | denominator | instrument (`scripts/stats.py`) |
|---|---|---|---|
| does the gain reproduce? | rerun noise (sampling temperature, judge wobble) | `runs` | `t_two_sample` (`\|t\| = \|Δ\| / SE_diff`) |
| does the gain generalize? | which cases are in the set | **number of cases** | `paired_bootstrap_delta` + `paired_case_test` + **`n_moved`** |

A run-level `|t| = 9.4` on a 60-case split can mean nothing more than "two cases flipped, and they
flip the same way every time". That is a real finding — it is just not evidence about the other
9,940 cases you did not score.

**So compute both, and report both** using `.auto_experiment/stats.py` (copied from
`scripts/stats.py`, seed `BOOTSTRAP_SEED = 20260917`, 2000 resamples) over `per_case_means`
paired **by eval-set `id`**:

- **`paired_bootstrap_delta(best, candidate)`** — primary evidence for the `test` headline:
  `{delta, ci_95, p_two_sided, n_paired, n_moved, scope}`. Resamples cases as units.
- **`paired_case_test(best, candidate)`** — parametric paired case-level t-test:
  `{mean_d, sd_d, se_case, t_case, n_cases, n_moved, scope}` (`t_case` is `null` when `sd_d == 0`).
- **`n_moved`** — how many paired cases have `d_i ≠ 0`. **State it in the same sentence as every
  headline**: "+0.048 on 60 held-out cases, bootstrap CI [0.016, 0.083], carried by 3 cases that
  flipped" is honest; "+0.048, |t| = 9.4" alone is not.
- **`wilson(successes, total)`** — report beside the bootstrap CI **only** when `is_binary` is true
  (every per-case score is in `{0, 1}`).
- **Practical-effect floor** `|Δ| ≥ min_delta` applies alongside both instruments.

**Neither test is a keep gate.** They are confidence labels. Any headline whose bootstrap CI
straddles zero, whose `t_case < 2`, or whose `n_moved ≤ 2` must say plainly that the gain rests on a
handful of cases / within noise and may not generalize.

## Data-selection guidance — what enters the eval set (`_data_selection_guidance`)

**Choosing what to score.** Identify the **target unit** from the experiment `goal`/`evaluators`
— the span/operation that produces the artifact being optimized (e.g. the recommendation /
answer / generation span). For each trace, locate the scoreable target span, then:

- **Score every trace that contains a scoreable target span.** Do NOT subsample, truncate, or
  drop scoreable datapoints for convenience.
- **EXCLUDE traces that have no scoreable target span** (setup/infra spans such as
  `mcp.initialize`, `session_summary`, health checks) from the eval set entirely — do NOT score
  them 0.0. A non-target trace scored 0.0 drags the mean down and hides real changes.
- The mean is computed over **scoreable datapoints only** — excluded traces are out of both the
  numerator AND the denominator. **Report how many traces you excluded and why** in `reasoning`;
  never exclude a scoreable datapoint to inflate the score.
- **For an `annotation_queue_id` source, three more exclusions apply before any of the above**, all
  for the same reason — the datapoint has no ground truth to score against: interactions still
  **pending** review, interactions whose mapped `expected_output` label was left **empty** (`""` for
  text, `[]` for categorical), and interactions whose reviewers **disagree** on that label. Count
  each group and report it; never resolve a disagreement yourself to keep the datapoint.

**Held-out split — hill-climb on `val`, decide on `test`.** After building the scoreable set and
passing the Corpus size gate (SKILL.md Step 1), split it **once, deterministically**, into **two
Datadog LLM-Obs Datasets** — a `val` dataset and a `test` dataset, both named with the run's single
UTC timestamp and their ids recorded in `config.json` (`val_dataset_id`, `test_dataset_id`,
`split_created_at`). The split is created **once per run, at the start, and never again**: not on a
later iteration, not after restoring `files_to_optimize`, not when a local cache is missing (re-hydrate from
the same ids). The single sanctioned exception is the one baseline population re-draw at Step 2.6.
In `dataset_mode: local_file`, the split writes gitignored `data.val.jsonl` / `data.test.jsonl`
instead — still running the Corpus size gate, stratification, `split` stamping, and count assertion.

- **Both splits are scored every iteration.** `val` is the split the census, the describers and the
  improvement sub-agent read. `test` is scored alongside it on every iteration but is **never read
  by any sub-agent**, and **its score is the headline and picks the winner**.
- **`val` up, `test` flat means the change overfit to the cases the improver read** — revert it
  immediately (`basis:overfit`). Scoring `test` only once at the end tells you about overfit only
  after the entire iteration budget is spent.
- **The wall is a gitignored directory, not a habit.** The entire `.auto_experiment/holdout/`
  directory (its cache `holdout/cache/<test_dataset_id>.jsonl`, raw rows
  `holdout/eval_results.test.jsonl`, and `holdout/errors.test.jsonl`) is **gitignored** so no
  sub-agent can inspect raw `test` inputs/outputs via `git log`, `git show`, or `rg`. Only
  aggregate `test` stats (`result.json`, `config.json`, `narrative.md`) are committed. The
  orchestrator reads `holdout/`; **no sub-agent ever opens `.auto_experiment/holdout/`**, and that
  sentence goes verbatim into every sub-agent briefing.
- **Why not keep it unscored until the end:** because the loop is decided by machine every round
  with no human reviewing rows, so the only channel through which a `val`-overfit becomes visible
  mid-run is a `test` measurement. (For large corpora `≥ 100` scoreable rows, offer a 3-way split
  `val` / `select` / `test` so `test` stays genuinely untouched until final confirmation.)

**Stratify the split — and never split by score.** Draw the split stratified on the most informative
categorical available, in this priority order:

1. an `annotation_queue_id` source with a categorical `expected_output` label → that label;
2. an explicit `stratify_by` field the user named at intake;
3. a trace-derived source → the target span's operation `name`;
4. `expected_output` present and categorical-like (≲10 distinct values across the corpus) → its
   value;
5. fallback → tercile of input character length. Weak, but cheap and better than unstratified.

Record the key and the per-stratum val/test counts in `config.json.split_strata`. **Splitting by
baseline score is structurally impossible here** — the split is minted in Step 1, before any score
exists — which prevents train-slice regression to the mean.

**Corpus size gate & `split_mode` — only split when the holdout can carry a measurement:**

| scoreable corpus | action | `split_mode` |
|---|---|---|
| **< 10** | **STOP.** Ask the user for a larger window, more traces, a dataset, or a queue. Never split or score. | — |
| **10–29** | **Warn explicitly and ask** (`AskUserQuestion`, offering "widen the source" first). If confirmed, do **not** fake a 3–8 case holdout: score the whole corpus every iteration (`AUTO_EXP_SPLIT=all`, `eval_results.all.jsonl`, no `holdout/`) and label **every** number **in-sample and optimistic** (overfit gate cannot fire). | `all_rows` |
| **30–49** | **Warn explicitly and ask** (`AskUserQuestion`: state val/test sizes and `1/test_count` per flipped case, offering "widen the source" first). If confirmed, split ~70/30. | `val_test` |
| **≥ 50** | Proceed with ~70/30 split. (At **≥ 100**, **offer** an optional 3-way `val` / `select` / `test` split; never pick it silently.) | `val_test` (or 3-way if chosen) |

**The baseline population check, and the one re-draw it may trigger.** At the Step 2.6 gate, the
`val` and `test` baseline means must agree within noise (`|val_mean − test_mean| ≤ 2 · SE_diff`).
If they do not, the two splits are not the same population:

- **exactly one re-draw is allowed**, and only there — before any change has been made, while
  `iteration_results` holds nothing but the iteration-0 row;
- bump `split_seed` 0 → 1, mint new timestamped dataset names, leave the old datasets in place
  recorded as `superseded_split_dataset_ids`, increment `split_redraws`, and re-run the baseline on
  both splits, discarding the old numbers;
- a second failure is **not** a second re-draw — report that the corpus is heterogeneous via
  `AskUserQuestion` (proceed with caveat / enlarge corpus / abort).

**The `test` split is a selection set, and the report must say so.** Once `test` picks the winner
every round, it is being selected on, and its delta overstates the true gain by an amount that grows
with the round count (winner's curse). Three rules, all required:

- the final report always states how many rounds the selection ran over (`selection_rounds`);
- the end-of-run **pooled confirm run** re-runs baseline and winner on `test` at `max_runs` and
  pools with the existing runs (SKILL.md **Final report**) — be explicit that pooling on the same
  `test` cases reduces **rerun variance**, **not** **case-selection bias**;
- when the scoreable corpus is ≥ 100, **offer** a three-way split at intake (`val` / `select` /
  `test`, where `test` is genuinely opened once) — the only design that removes case-selection bias.

**Stamp & assert.** Write `split` (`"val"` / `"test"` / `"all"`) onto every record when created, and
assert `val_case_count + test_case_count == corpus_count` immediately after creating the split.

## Anti-memorization & data isolation (`_data_isolation`)

A held-out split only measures generalization if the thing proposing changes cannot reach it, and a
change only generalizes if it encodes a *behavior* rather than the *content* of the rows it was
derived from. Four rules, all structural where they can be:

1. **Describe the behavior, not the content.** A change that pastes a noun, phrase, entity name,
   numeric constant, or fragment of an `expected_output` occurring in a `val` datapoint into an
   edited file is **memorization**, and the iteration is discarded regardless of what the score did.
   The improvement sub-agent's briefing says this verbatim, and its rationale must name the behavior
   and how many datapoints show it — not quote the vivid one.
2. **Mechanical memorization check**, run by the orchestrator after the sub-agent returns its diff
   and before the full eval: take the added lines from `git diff`; for each added line of ≥5 words,
   test whether any 5-gram of it appears verbatim in any `val` row's `input` or `expected_output`. A
   hit is a **stop-and-review**, not an automatic discard — legitimate collisions happen (a domain
   term, an API name, a schema field). Record `memorization_check: {checked, hits, verdict}` in
   `result.json`; an unexplained hit is `discarded`, `basis:memorized`.
3. **`expected_output` must be unreachable by the code under test.** The harness passes a
   **redacted copy** of the row to `generate` — `expected_output`, `reference`, `gold` and `label`
   stripped — and gives the reference only to `grade`. `generate` must not re-open the cache file to
   get around this.
4. **Census describers are `val`-only and reference-free.** They read `eval_results.val.jsonl`;
   `census.json` cites only `val` ids; `failing_total`/`described` are `val`-scoped. They are **not**
   shown `expected_output` either — a describer given the gold string describes the gap to the gold
   string (content, not behavior). When a deterministic ground-truth check makes a description
   meaningless without the reference, hand them a *derived* difference (what kind of thing differed)
   rather than the raw reference. The orchestrator also records `holdout_failing_total` — a bare
   count, no descriptions — so the report can say whether `test` failure volume tracks `val`'s.

## Baseline failure census — localize the lever before you tweak (`_failure_census`)

Before iteration 1's first change, decompose **where the baseline actually loses**, so iterations
aim at a real failure mode instead of guessing.

**The zeros are triaged before the census starts** — SKILL.md **Step 2.3**, which runs before Step
2.4's noise derivation because dropping harness-error rows changes the denominator and therefore the
`stdev` that `runs` and `min_delta` are derived from. By the time the describers run, the rows they
see are rows the model genuinely got wrong. The rest of the census runs in **two phases — describe,
then synthesize** — kept strictly separate.

### The triage that precedes it (SKILL.md Step 2.3)

Classify **every** datapoint scoring exactly 0 at baseline as either a **harness error** or a
**grader verdict**, using `errors.val.jsonl` plus a read of the output:

- **Harness-error rows leave the denominator** on both splits before anything else happens, and the
  baseline is re-scored after the drop.
- **Spot-check the grader verdicts.** Read the lowest-scoring handful (`val` only) — output,
  justification, reference. If more than roughly one in ten look like *grader* errors rather than
  model errors, fix the rubric or reference **with the user's explicit approval**, re-grade the
  baseline in place from stored outputs (`AUTO_EXP_REGRADE=1`, no model re-run), bump
  `grader_version`, and only then continue.
- If the problem is the **corpus** itself (wrong ground truth, no class balance, stale traffic),
  name the sibling skill (`agent-observability-build-eval-from-annotations` or
  `agent-observability-eval-bootstrap`) and stop.

Record the triage counts in `census.json` alongside the buckets.

### Phase A — describe, do NOT classify

**Fan out parallel describer sub-agents** over **every** failing / low-scoring `val` datapoint in
the baseline `eval_results.val.jsonl` (batch several per agent; hard cap at a **random** 200 if
there are more). Each describer gets:
- the datapoint's input, generated output, and judge justification, **AND**
- **all available execution signals** for that `val` case: the row's `signals` dict (tool calls,
  retrieved/filtered counts, branches taken, retries, parser fallbacks, step latencies), plus — when
  instrumented — the **LLM-Obs child spans** (`tool`, `retrieval`, `workflow`, `task` spans via
  `get_llmobs_trace`/`expand_llmobs_spans`/`get_llmobs_span_details`) and **APM trace spans**
  (`search_datadog_spans`/`get_datadog_trace` or `pup apm`) linked via `trace_id`/`span_id`.

Giving describers only final `(input, output)` text forces them to guess prompt tweaks; giving them
execution signals and trace spans exposes where the *code* actually broke (empty retrieval, wrong
tool arguments, truncated context, bad filtering/ranking, broken parser, slow/failing downstream
call). Each describer returns **one or two factual sentences about what it observes** across both
execution and output:

- **`val` only, and no `expected_output`.** Never `.auto_experiment/holdout/`. Where a deterministic
  check needs context, pass a derived difference rather than the raw gold string.
- **Hand the describers NO category vocabulary.** No bucket list, no candidate tags. Ask *what
  happened in the code and output*, never *which kind is this*.
- **Describe facts, not judgments.** "Retrieval returned 0 docs because `build_query` quoted the raw
  sentence; LLM answered from memory" is a fact. "Prompt was too vague" is an unsupported guess.
- **Pass `domain_notes` to every describer** (SKILL.md **Domain notes**).
- Give each describer whatever rendering makes the datapoint legible (for structured/numeric data,
  a rendered view plus raw values as a sidecar).

### Phase B — synthesize the descriptions into emergent archetypes (localized to code layers)

You (the orchestrator) read the descriptions **alongside the code map** you built across
`files_to_optimize` in Step 2, and **only then** name the buckets. Group descriptions that say the
same thing, name each group after what they actually say, write a one-line definition, localize each
bucket to its **`likely_layer`** (`retrieval` | `tool_io` | `preprocessing` | `control_flow` |
`postprocessing` | `model_params` | `prompt`) and **`code_locations`** (`file:symbol`), and compute
its **reachable ceiling**: `bucket_count / scored × (1 − mean_bucket_score)`.

If synthesis genuinely yields nothing coherent, these generic buckets are prior art as a last resort
at synthesis time only: `wrong_retrieval`, `wrong_reasoning`, `format/parse`, `refusal/empty`,
`judge_disagreement`, `data/label`.

### The census file — keep it auditable

Write `.auto_experiment/census.json` and commit it:

```json
{
  "failing_total": 47,
  "described": 47,
  "holdout_failing_total": 20,
  "triage": {"harness_errors_dropped": 1, "grader_errors_found": 0},
  "code_map": [
    {"stage": "retrieval", "location": "backend/search.py:build_query", "role": "builds index query from user input"},
    {"stage": "llm_call", "location": "backend/agent.py:run_turn", "role": "calls model with retrieved docs and tools"},
    {"stage": "postprocessing", "location": "backend/parser.py:extract_sql", "role": "extracts SQL from response"}
  ],
  "trace_signals_used": ["harness.signals", "llmobs_child_spans", "apm_spans"],
  "descriptions": [
    {"id": "BL11", "score": 0.0, "description": "build_query stripped WHERE clauses before retrieval; retrieved_count=0; output dropped status='open'"}
  ],
  "buckets": [
    {"tag": "predicate_dropped_in_query_builder", "count": 12, "reachable_ceiling": 0.255,
     "likely_layer": "retrieval", "code_locations": ["backend/search.py:build_query"],
     "trace_evidence": "11/12 cases had signals.retrieved_count == 0 on LLM-Obs retrieval span",
     "examples": ["BL11", "BL34"],
     "definition": "query builder strips filter predicates before retrieval, starving the model of matching rows"}
  ]
}
```

- **Describe EVERY failing `val` case, up to a hard cap of 200.** Above 200 failures, describe a
  **random** 200 (never the first 200) and record `"sampling": "random-200"`.
- **`failing_total` and `described` are both required, and every claim states its coverage.** A
  partial census below the cap requires a stated reason in the file ("it was quicker" is not one).
- Bucket `count` sums must not exceed `described`.

### Rules that hold across both phases

- **Refer to datapoints by their eval-set `id` everywhere** — census `examples`, `result.json`
  `reasoning`, mechanism-audit notes, and LLM-Obs `reasoning` all cite the concrete eval-set `id`,
  never a bare row index or dataset record UUID.
- **Every iteration must name the census bucket it targets** (using its emergent tag) and be a
  change plausibly able to move THAT bucket. If the dominant bucket is outside `files_to_optimize`,
  say so as a finding.
- **Code-before-prompt root-cause rule.** Never edit a prompt to compensate for a bug or missing
  signal in retrieval, tool schemas/implementations, preprocessing, routing/control flow, parsing,
  retries, or model parameters. Inspect the code path and execution traces/signals first, and fix
  the layer where the failure originates. Edit a prompt **only** when the baseline/iteration traces
  and `signals` confirm the code already delivered the right inputs, tools, and parameters, and the
  model still misbehaved. If two consecutive prompt-only iterations are discarded while non-prompt
  code lives in `files_to_optimize`, the next iteration MUST investigate a code/tool/retrieval/param
  lever instead of another rewording.

## Feasibility probe — prove reachability before you pay for a full eval (`_feasibility_probe`)

Before spending a full two-split eval (`R` runs × both splits × code + grader), run the **cheapest
possible offline check that the lever CAN move the metric** — an upper bound, not a measurement:

- A probe that reaches **0** failing datapoints is dead → record `no_change` with the probe result
  and skip the full eval.
- **The probe must also size the win, not just prove it is non-zero.** A fix can gain at most its
  bucket's reachable ceiling: `bucket_count / scored × (1 − mean_bucket_score)`. If that ceiling is
  below `min_delta`, do **not** inflate the change — record `no_change` and treat it as a stall
  (whose honest remedies are more runs or more cases). Record the ceiling next to the probe result;
  the suspicion rule later checks implausible jumps against it.
- Keep probes read-only and offline where possible; never mutate `files_to_optimize` or the harness.
- **Fix the behaviour at its root rather than rewording a line.** One focused hypothesis per
  iteration, kept or reverted whole (the `breadth_pass` in **Stall taxonomy** is the single
  documented exception).

## Messages-source guidance — where the input/output lives (`_messages_source_guidance`)

**`messages` is the source of truth — the root span's `input.value` is usually a thin/truncated
summary and MUST NOT be scored when a `messages` field exists somewhere in the trace.**

- Call `get_llmobs_span_details` and read `content_info` for each span. Find the span whose
  `messages` count is highest — typically a **child LLM span**, not the root span.
- Fetch it with `get_llmobs_span_content(field="messages")` (use `path` like `$.messages`). Do the
  same for the output side. Only fall back to `input.value` / `output.value` when NO span exposes
  `messages`.
- If a messages field is too large to process directly, summarize it first, then score on the
  summary.

## Metric selection — prefer deterministic ground truth over an LLM judge (`_metric_selection`)

The scorer is itself a noise source. **When a deterministic, ground-truth metric is available, use
it instead of an LLM judge**:

- If datapoints carry a **reference/expected output** (dataset `expected_output`, gold label), score
  with an exact/programmatic check (exact match, F1, set overlap, a repo evaluator, pipeline count).
- **An annotation queue's labels are ground truth.** When `annotation_label_map` names an
  `expected_output` label, score programmatically and do **not** add an LLM judge on top.
- Use an **LLM-as-judge only when no ground truth exists** (open-ended quality). Propose
  `max_runs ≥ 5` at intake, pin the model + prompt, and close four judge biases before the first
  paid pass:
  - **Verbosity:** explicitly instruct the judge not to reward length for its own sake.
  - **Self-preference:** when `files_to_optimize` includes model selection, do not judge with a
    model from the same family being swapped in or out.
  - **Label deference:** never tell the judge which output is the reference, baseline, or human one.
  - **Known negatives:** empty string, `"I don't know"`, and an answer to a different question must
    all fail (checked by the Step 2.2 null-baseline probe).
- **One metric, with the rest as guardrails.** Express the primary property in `evaluators` and put
  secondary axes (cost, latency, or quality when optimizing cost/latency) in `goal.hold[]`.
- State which metric kind you used in `reasoning`.

## Eval-harness spec (`_eval_harness_skill`)

**Companions.** `scripts/stats.py` and `scripts/loop_helper.py` are copied to
`.auto_experiment/` alongside the harness (always Python, orchestrator-side — the harness matches
the code under test's runtime, the orchestrator does not). Use `loop_helper.py` (`split`,
`calibrate`, `check-memorization`, `decide`, `build-tags`, `render-narrative`, `verify-final`) so
deterministic math, audit rules, and tag normalization are executed by tested code instead of by
hand.

**Language.** Python (`.auto_experiment/eval_harness.py`, from `references/eval_harness_template.py`)
or Node/ESM (`.auto_experiment/eval_harness.mjs`, from `references/eval_harness_template.mjs`).

**The two templates are ONE contract.** They emit identical stdout keys, result-row keys, error-row
keys, env vars, and failure classes. A change to either is a change to both.

**Env vars:**
- `AUTO_EXP_SPLIT` (**required**: `val` | `select` | `test` | `all` — no silent default, so a caller
  can never accidentally score `val` when intending `select` or `test`; both `select` and `test`
  write under gitignored `holdout/`)
- `AUTO_EXP_DATASET_ID` or `AUTO_EXP_DATA` (exactly ONE; setting both is a **hard error** so a
  stale `AUTO_EXP_DATA` can never silently shadow `AUTO_EXP_DATASET_ID`)
- `AUTO_EXP_RESULTS` (optional explicit output file path, e.g. for confirm runs)
- `AUTO_EXP_WORKERS` (optional per-pass concurrency, default `1`; set `4`–`8` for I/O-bound LLM calls without rewriting the harness; preserves deterministic row order)
- `AUTO_EXP_RUNS`, `AUTO_EXP_EVALUATORS`, `AUTO_EXP_DOMAIN_NOTES`, `AUTO_EXP_ITERATION`,
  `AUTO_EXP_REGRADE`

**Structural split verification:** Before scoring, the harness cross-checks `config.json` (if
present) and hard-errors if: (1) `AUTO_EXP_DATASET_ID` equals the *other* split's dataset id or
disagrees with `<split>_dataset_id`; (2) any row carries a `split` field that disagrees with
`AUTO_EXP_SPLIT`; or (3) `len(lines)` disagrees with `<split>_case_count` (unless regrading).

**Stdout contract** — one JSON object per invocation, one invocation per split, two per iteration:

```
{split, dataset_id, data_path, mean, stdev, runs, scored, excluded, errored, errored_cases,
 truncated, refusals, run_means, per_case_means, metric_stats, latency_p50, latency_p95, usage,
 models_seen, grader_version}
```

Write `.auto_experiment/eval_harness.py` (or `.mjs`) with:

- `generate(line)` — runs the **real code under test** on a **REDACTED row** (`expected_output` /
  `reference` / `gold` / `label` stripped). Must not re-open the cache. Returns `None` to exclude,
  a bare string, or `{output, model, usage, latency_s, stop_reason, refusal, signals}`. `model` is
  read from the response, never from config. Capture rich intermediate execution `signals` (tools
  called, retrieval counts/scores, branches, retries, parser fallbacks, step latencies, APM trace
  id) so the census inspects how the code actually ran, not only final output text.
- `grade(line, generation) -> (score, justification)` — receives the **full** row and scores against
  `evaluators` verbatim (never `goal`). No score literals anywhere. Use `build_judge_prompt(...)`
  for LLM-judge calls (prompt-injection delimiters + separate trusted `domain_notes` block). Raise
  `HarnessError("judge_error", ...)` if unreachable.
- **Every rep's rows are kept** in `eval_results.<split>.jsonl` (under `holdout/` for `test`), row
  shape: `{id, split, rep, iteration, input, output, score, justification, trace_id, span_id, model,
  usage, latency_s, stop_reason, status, refusal, signals}`. `output` is stored in full (never
  truncated) so `AUTO_EXP_REGRADE=1` faithfully re-grades stored responses. `status` is `truncated`
  when `stop_reason` is `max_tokens`/`length`; `refusal` is a graded outcome, not an error.
- **Errors sidecar** `errors.<split>.jsonl`, one row per failed attempt:
  `{id, split, rep, iteration, failure_class, message, latency_s, timestamp}` with `failure_class`
  in `harness_error | serving_error | rate_limited | timeout | judge_error | parse_error`. A clean
  run deletes any stale sidecar.
- **Three outcomes, three meanings:**

  | outcome | meaning | denominator | counter |
  |---|---|---|---|
  | **excluded** | `generate` returned `None` — no scoreable target. *By design.* | out of both | `excluded` (per pass) |
  | **errored** | attempt raised, timed out, or judge unreachable. *By accident.* | out of both, **never scored 0** | `errored` (attempts) / `errored_cases` |
  | **scored 0** | model produced output and grader judged it wrong | **in the mean** | — |

- **Regrade mode** (`AUTO_EXP_REGRADE=1`): re-run `grade` over stored outputs in place without
  calling `generate`.

The harness is built **once** in iteration 1 and **reused verbatim** in every later iteration.
Before every run, confirm it has not drifted: `git diff --quiet -- <harness_path>`.

## Metric JSON schema — `.auto_experiment/result.json` (`_return_metric_block`)

After each scored iteration, write this split-qualified object to `.auto_experiment/result.json` and
commit it in the same commit as the code change. There is no bare `before_score`/`after_score` —
every score is explicitly scoped to its split:

```json
{
  "headline_split": "test",
  "split_mode": "val_test",
  "grader_version": <int — bump when `evaluators` changes; scores across versions are NOT comparable>,
  "runs": <int — AUTO_EXP_RUNS used, same for both splits>,
  "min_delta": <float — practical-effect floor from Step 2.4>,

  "val":  {"dataset_id": "<id|null>", "before": <float>, "after": <float>, "stdev": <float>,
           "delta": <after − before>, "se_diff": <float>, "t_stat": <float|null>,
           "scored": <int>, "excluded": <int>, "errored": <int>, "truncated": <int>},

  "test": {"dataset_id": "<id|null>", "before": <float>, "after": <float>, "stdev": <float>,
           "delta": <after − before>, "se_diff": <float>, "t_stat": <float|null>,
           "bootstrap": {"delta": <float>, "ci_95": [<lo>, <hi>], "p_two_sided": <float>,
                         "n_paired": <int>, "n_moved": <int>},
           "paired_case": {"mean_d": <float>, "sd_d": <float>, "se_case": <float>,
                           "t_case": <float|null>, "n_cases": <int>, "n_moved": <int>},
           "scored": <int>, "excluded": <int>, "errored": <int>, "truncated": <int>},

  "overfit_gap": <val delta minus test delta — the quantity `overfit` is read off>,
  "guardrails": [{"metric": "cost_usd", "before": <float>, "after": <float>, "ratio": <float>,
                  "min_delta": <float>, "status": "ok|within_noise|regressed"}],
  "usage": {"latency_ms_mean": <float|null>, "latency_ms_p95": <float|null>,
            "input_tokens_mean": <float|null>, "output_tokens_mean": <float|null>,
            "cost_usd_mean": <float|null>, "cases_reporting_tokens": <int>},

  "significant": <REQUIRED bool — TEST confidence label: |t_stat| ≥ 2 AND |delta| ≥ min_delta
                  (or se_diff == 0 with |delta| ≥ min_delta). NOT the keep decision.>,
  "is_best": <REQUIRED bool — decided by the Noise & keep/discard matrix above>,
  "basis": "<one of significant|within_noise|overfit|unreplicated|regression|audit_failed|
             guardrail_regression|memorized|void_error_profile|stall_triage|no_change>",

  "suspicious": <string|null — one-line concern when the jump triggered the suspicion rule>,
  "suspicion_checked": <bool>,
  "memorization_check": {"checked": <bool>, "hits": [<added line>, ...], "verdict": "clean|explained|memorized"},
  "lever_layer": "retrieval|tool_io|preprocessing|control_flow|postprocessing|model_params|prompt",
  "change_class": "REQUIRED|TUNE",

  "reasoning": "<REQUIRED — lead with decision + basis + test delta + bootstrap CI + n_moved; then
                 scoring method, case counts on each split, what was tested, excluded/errored
                 counts, mechanism-audit outcome, and caveats; 2-4 sentences; never empty.>"
}
```

## Mechanism audit — confirm the change CAUSED the gain (`_mechanism_audit`)

Before setting `is_best: true`, confirm the improvement is **caused by the change**, not an
artifact:

- **Per-datapoint diff.** Diff best vs candidate `eval_results.val.jsonl` (aggregated to
  `per_case_means`): the gain must come from datapoints the change plausibly touches (ideally in the
  targeted census bucket).
- **Denominator guard, three-valued:**
  - **`excluded` must be identical** across best and candidate on both splits. A change in
    `excluded` means the code dropped hard cases out of the eval set → discard (`basis:audit_failed`).
  - **`errored` may differ**, but if `errored_candidate > errored_best + max(1, 0.05 × case_count)`,
    the error profile diverged grossly → void and re-run; if the re-run also diverges, record
    `no_change` with `basis:void_error_profile`.
  - **Diff on the paired set.** Compute the delta over `ids_scored_best ∩ ids_scored_candidate`
    **and** over the full set on both splits. If they disagree by more than `min_delta`, the audit
    **fails** (`basis:audit_failed`).
- **Truncation guard.** Flag any increase in `truncated` count even when the mean rose.
- **Memorization check.** Run the 5-gram check on the diff and record `memorization_check`. An
  unexplained hit is `discarded`, `basis:memorized`.
- **Causality on regressions too.** Check whether the change fired on regressed controls/negatives.
- Record the audit outcome in `reasoning`. A failed audit discards the iteration regardless of point
  estimate.

## Stall taxonomy — categorize before grinding (`_stall_taxonomy`)

The analyze → change → score loop assumes every remaining failure is caused by the code you are
tuning. Once easy gaps are filled, what remains increasingly comes from the grader, harness, scope
structure, or plain variance. The trigger is **3 consecutive discarded scored iterations**
(`plateau_discard_streak == 3` — resets on any `kept` iteration, including a `within_noise` keep;
`no_change` neither increments nor resets).

On the first 3-discard streak (at most once per run, and only if ≥2 iterations remain), spend **one**
iteration categorizing instead of changing (`decision:no_change`, `basis:stall_triage`). Reuse the
census machinery — fresh describer sub-agents over the remaining `val` failures, bucketed by root
cause rather than behaviour — and write the counts to `census.json.stall_buckets`:

| bucket | tell | dispatch |
|---|---|---|
| `artifact_gap` | reachable from `files_to_optimize`; code lacks the fact or capability | reset streak and keep iterating — the streak was bad levers, not a ceiling |
| `grader_disagreement` | spot-check says output is right and grader marked it wrong; or `evaluators` and `goal` conflict | the grader-disagreement path below |
| `harness_infra` | rows in `errors.<split>.jsonl` or zero-scored rows with infra markers | fix the harness, exclude errored cases, void-and-re-run affected rounds |
| `structural` | content exists in scope but model never reaches it; same bucket recurs ≥3×; or lever sits **outside** `files_to_optimize` | reorganize rather than add, or run one `breadth_pass`. If the lever is **outside** `files_to_optimize`, stop and report the scope finding |
| `variance` | per-case flips between identical-code runs are as large as the round-over-round delta (`stdev ≈ |Δ|`) | stop with `stop_reason: "noise floor reached (stall: variance)"` and offer higher `max_runs` or a larger corpus |

**The long tail (`breadth_pass`).** If categorization finds **≥6 buckets each costing ≤2 cases**
with no coverage, permit **one** explicitly labelled `change_kind: "breadth_pass"` iteration that
drafts minimal coverage for all of them at once. State this explicitly in `reasoning`.

**The grader-disagreement path:**

1. **Propose the grader fix to the user and get an explicit yes** (`evaluators` is user-approved
   verbatim).
2. Snapshot `eval_results.val.jsonl` into `.auto_experiment/pre_regrade/` (committed) and
   `holdout/eval_results.test.jsonl` into `.auto_experiment/holdout/pre_regrade/` (kept gitignored
   inside `holdout/`), commit, and bump `config.json.grader_version`.
3. Re-score **from stored outputs only** via `AUTO_EXP_REGRADE=1` on both splits (no model re-run).
4. Re-score **every** iteration with stored rows, including baseline.
5. Compare old vs new: how many cases moved, and **did the ranking flip?**
   - previous best still best and test lead held → continue and note the re-grade in the final report;
   - ranking flipped or lead collapsed into noise → show the before/after table and ask
     (`AskUserQuestion`): restart from baseline / accept new ranking noting old selection / stop.
6. Re-submit every affected LLM-Obs event with `basis:regraded`, bumped `grader_version:<n>`, new
   `score_value`, and superseding `reasoning` (and re-submit affected per-case events).
7. Every `result.json` and `iteration_results` row carries `grader_version`.

**Mid-run grader drift.** When one bucket resists three iterations of changes that look right,
re-read its rubric before writing a fourth.
