# Reporting — the machine record, the human record, and the final report

This run has two reporting channels and they must always agree:

- **the LLM-Obs experiment** is the machine record — one iteration-level event
  (`auto_experiment_score`) plus per-case events (`auto_experiment_case_score`) per scored
  iteration, queryable and tagged;
- **`.auto_experiment/narrative.md`** is the human record — a status table and a paragraph, rewritten
  wholesale every iteration, committed, readable in one screen by a resumed session or by the user.

Neither replaces the other, and a number that appears in one must appear in the other. The same
discipline the skill already applies between `reasoning` and its tags applies between these two.

---

## Report each iteration's score to LLM-Obs (every scored iteration)

Once you have a computed score for an iteration, submit **exactly one** iteration-level eval-metric
datapoint to LLM-Obs with `submit_llmobs_experiment_events` (or `pup llm-obs experiments events
submit --metrics '[{…}]' <EXPERIMENT_ID>` under `datadog_backend: pup`). Do this once per
iteration, right after the score is computed and the iteration's commit / `result.json` is written —
including iteration 1 and the **iteration-0 baseline** (reported at the end of Step 2.4; there
`score_value` = the final `test` baseline, or `all` baseline in `split_mode: all_rows`, and the
decision tag is `decision:baseline`).

Immediately after this submission, **recompute `estimated_duration_time`** (the ETA in seconds to
the end of the whole run — `avg_iteration_elapsed × iterations_left`, → `0` after the last
iteration; see **Setup** step 5) and `update_llmobs_experiment` — one call, re-sending
`repo`/`branch`/`model` unchanged.

Shape of the single iteration-level metric:

- `experiment_id`: `$experiment-id` (the validated skill argument or auto-created id, persisted to
  `config.json` as `dd_auto_experiment_id`). Do not invent one.
- `metrics`: an array containing exactly one object with these fields and no others:
  - `label`: always the literal string `auto_experiment_score`.
  - `metric_type`: `score`.
  - `score_value`: **this iteration's `test` score** (`result.json.test.after`, or `all.after` in
    `split_mode: all_rows`) — the number computed by the harness, never a literal or a
    rounded-for-display value. Because the holdout is scored every iteration, `score_value` is the
    held-out `test` score, **not** `val`. Do not mix pre-holdout (`val`-only) and holdout-era runs
    in one experiment. Still **exactly one `auto_experiment_score` event per iteration** — two
    events sharing an `iteration:<n>` tag would be collapsed by the dedup rule below, so the `val`
    score travels as a tag (`val_score:<X.XXXX>`), not as a second metric.
  - `timestamp_ms`: the current wall-clock time as an epoch timestamp in **milliseconds**.
  - `tags`: start with `["iteration:<n>", "git.commit.sha:<sha>", "decision:<decision>"]` and
    **also add the decision-legibility, split, goal/guardrail, distribution, usage, and integrity
    tags below**. `<n>` is this iteration's number (`0` for baseline, `1` for the first improvement,
    `2` for the next, …), `<sha>` is the **full 40-character** Git commit SHA from
    `git rev-parse HEAD` (**never** an abbreviated 7/8-char hash), and `<decision>` is `kept`,
    `discarded`, `baseline`, or `no_change`.
  - ⚠️ **Datadog NORMALIZES tag values — encode accordingly.** Tag values are lowercased and some
    characters are rewritten, so a tag is **not** a byte-faithful channel. Two rules follow, both
    learned from inspecting really-ingested events rather than from review:
    - **Never put a leading `+` in a tag value.** It is rewritten to `_`: a tag sent as
      `delta_vs_best:+0.0447` lands as `delta_vs_best:_0.0447`. Worse, `-` *survives*, so negatives
      would land as `-0.1180` while positives land as `_0.1180`. Split magnitude and sign into
      separate tags instead.
    - **Never put case-sensitive text in a tag value.** `time_start:2026-07-22T14:31:07Z` lands as
      `...t14:31:07z`, which is no longer valid ISO-8601 and no longer byte-matches the
      `iteration_results` row.
    Keep the faithful values in `config.json`; put only normalization-safe forms in tags (unsigned
    decimals, integers, lowercase enums, epoch millis).
  - **Decision-legibility tags (required on every scored iteration):**
    - `basis:<significant|within_noise|overfit|unreplicated|regression|audit_failed|guardrail_regression|memorized|void_error_profile|promoted|regraded|baseline|no_change|stall_triage>` —
      the one-word basis:
      - `significant` = kept, `significant:true` (cleared the t-test **and** `|Δ| ≥ min_delta`);
      - `within_noise` = **not significant** (`significant:false` — `|t| < 2` OR `|Δ| < min_delta`):
        pair with `decision` (`decision:kept` + `within_noise` = tentative best;
        `decision:discarded` + `within_noise` = not-significant wobble that did not beat best);
      - `overfit` = discarded and reverted: `val` rose and `test` did not, so the change fitted the
        rows the improver read;
      - `unreplicated` = discarded: `test` rose while `val` stayed flat/fell (usually noise; kept
        only if it repeats on a second run);
      - `regression` = discarded, significantly worse in the wrong direction;
      - `audit_failed` = discarded, mechanism audit failed (e.g. `excluded` changed, paired vs full
        disagreed by `> min_delta`, or suspicion check proved judge gaming);
      - `guardrail_regression` = discarded, target improved but a `goal.hold[]` metric regressed
        outside noise;
      - `memorized` = discarded, unexplained 5-gram memorization hit from `val` rows in the diff;
      - `void_error_profile` = `no_change`, error profile diverged grossly from baseline even on
        re-run;
      - `promoted` = a `within_noise` best later confirmed `significant` at higher power;
      - `regraded` = a correction re-submitted after `evaluators` was fixed and every iteration
        re-scored in place via `AUTO_EXP_REGRADE=1`;
      - `stall_triage` = the categorization iteration (`decision:no_change`), which changes no code.
    - `delta_vs_best:<X.XXXX>` (**absolute value, no sign character**) plus
      `delta_sign:<pos|neg|zero>` — the **test** delta against the **previous best** (not baseline).
      `delta_sign` is arithmetic (`after − best`), so on a minimize goal an improvement is `neg` —
      read improvement off `basis:`/`decision:`, not the sign.
    - `t_stat:<value>` (or `t_stat:null` when `se_diff == 0`) and `significant:<true|false>` — the
      run-level t-test confidence label on `test`.
    - `n_moved:<int>` — how many paired `test` cases actually changed (`d_i ≠ 0`) vs previous best.
    - **Iteration 0 omits `delta_vs_best`, `delta_sign`, `t_stat`, `significant`, `n_moved`, and
      `overfit_gap`** — there is no previous best to compare against. Absent is honest.
    - **Split tags (required on every scored iteration):** `split:<test|all>` names which split
      `score_value` came from; `split_mode:<val_test|all_rows>` (so `all_rows` in-sample scores are
      never read as held-out); in `val_test` mode also `val_score:<X.XXXX>`, `test_score:<X.XXXX>`,
      and (on iterations ≥ 1) `overfit_gap:<X.XXXX>` (**absolute value, no sign character**) plus
      `overfit_gap_sign:<pos|neg|zero>` for `val_delta − test_delta`.
    - **Goal and guardrail tags:** `goal_target:<score|cost_usd|latency_s>` and
      `goal_direction:<higher|lower>`, plus per held metric `hold_<metric>_ratio:<X.XXXX>` and
      `hold_<metric>_status:<ok|within_noise|regressed>`.
    - **Integrity tags:** `suspicious:<true|false>` (the concern sentence goes in `reasoning`) and
      `grader_version:<int>` (scores from different grader versions are not comparable).
    - **Timing tags:** `time_start_ms:<epoch_millis>` and `time_end_ms:<epoch_millis>` — exact
      integer epoch milliseconds matching the ISO-8601 timestamps in `iteration_results`.
  - **Distribution tags (required on every scored iteration):**
    - `dist_basis:case_means_test` (or `dist_basis:case_means_all` in `split_mode: all_rows`) —
      computed from the **per-case means across usable reps** (`per_case_means`) on the headline
      split, so the distribution describes the exact per-case sample behind the headline rather than
      only a single pass.
    - **counts, as integers:** `dist_n:<int>`, `dist_zero:<int>`, `dist_perfect:<int>` (cases
      scored, cases with `per_case_mean == 0.0`, cases with `per_case_mean == 1.0`). Required — on a
      near-binary metric, quartiles sit flat at `1.0` while `dist_zero` carries the movement.
    - **nearest-rank five-number summary, 4 decimal places:** `dist_min:<X.XXXX>`,
      `dist_q1:<X.XXXX>`, `dist_median:<X.XXXX>`, `dist_q3:<X.XXXX>`, `dist_max:<X.XXXX>`. Never
      interpolate quartiles. Omit all `dist_*` tags on `no_change` iterations.
  - **Resource-usage tags (every scored iteration, from the headline split's `usage` / latency
    summary):**
    - `latency_ms_mean:<int>` and `latency_ms_p95:<int>` (always measured), plus
      `tokens_per_case:<int>` (`input_tokens_mean + output_tokens_mean`, rounded) and
      `cost_usd_per_case:<X.XXXXXX>` **only when actually measured/priced**. **Omit an unmeasured
      tag rather than sending `0`** — absent reads as unknown; `0` falsely claims free. Omit all
      four on `no_change` iterations.
  - `reasoning`: this iteration's `reasoning` string from `iteration_results`. **Lead with a
    one-line verdict** stating decision, basis, `test` delta + paired bootstrap CI + `n_moved`, and
    `val` alignment, e.g. `"KEPT — significant on test (Δvs_best +0.048, bootstrap CI [0.021, 0.074],
    n_moved=5/34, t=3.1); val moved with test (gap +0.006)."`. Must agree with `result.json` and the
    tags.
  - Do **not** include `span_id`, `categorical_value`, or `boolean_value`.

Example arguments for iteration 5, whose harness scored `0.72` on `test` and `0.75` on `val`:

```json
{
  "experiment_id": "$experiment-id",
  "metrics": [
    {
      "label": "auto_experiment_score",
      "metric_type": "score",
      "score_value": 0.72,
      "reasoning": "KEPT — significant on test (Δvs_best +0.048, bootstrap CI [0.021, 0.074], n_moved=5/34, t=3.1). Rewrote the retrieval query builder to include entity synonyms (targeting the 'missed-retrieval' census bucket); val moved with test (gap +0.006), the mechanism audit passed on the paired set, and no held metric regressed.",
      "timestamp_ms": 1752430000000,
      "tags": ["iteration:5", "git.commit.sha:33ec6e0959bd46b0ea9c337cf6a28a763d3eeb0a", "decision:kept", "basis:significant", "split:test", "split_mode:val_test", "val_score:0.7500", "test_score:0.7200", "overfit_gap:0.0060", "overfit_gap_sign:pos", "delta_vs_best:0.0480", "delta_sign:pos", "t_stat:3.1", "significant:true", "n_moved:5", "goal_target:score", "goal_direction:higher", "hold_cost_usd_ratio:1.0400", "hold_cost_usd_status:ok", "suspicious:false", "grader_version:1", "time_start_ms:1753194667000", "time_end_ms:1753195132000", "dist_basis:case_means_test", "dist_n:34", "dist_zero:5", "dist_perfect:26", "dist_min:0.0000", "dist_q1:1.0000", "dist_median:1.0000", "dist_q3:1.0000", "dist_max:1.0000", "latency_ms_mean:19500", "latency_ms_p95:28400", "tokens_per_case:2140", "cost_usd_per_case:0.030000"]
    }
  ]
}
```

Rules:

- **One `auto_experiment_score` metric per iteration, plus corrections from two sanctioned
  classes.** Submit exactly one *iteration-level* metric per iteration at the time it is scored, and
  never batch several iterations into one call. (This rule governs `auto_experiment_score` only —
  per-case `auto_experiment_case_score` events below are a different label.) Two later events may
  supersede an iteration's score:
  - a **promotion correction** (Final report Higher-power confirmation): re-submitting that
    iteration with `decision:kept` + `basis:promoted` + `promoted:higher_power_confirmation` after a
    `within_noise` best is confirmed `significant` at higher power.
  - a **regrade correction**: after `evaluators` is fixed and every iteration is re-scored in place
    from stored outputs (`AUTO_EXP_REGRADE=1`, rubrics.md **Stall taxonomy** → grader disagreement),
    re-submit each affected iteration with `basis:regraded`, the bumped `grader_version:<n>`, the
    new `score_value`, and a `reasoning` stating it supersedes the earlier event.
- **Consumer dedup rule (state it, honor it).** Because the store is append-only, an iteration may
  have two events. Consumers of `auto_experiment_score` MUST dedupe **per `iteration:<n>` tag,
  keeping the event with the latest `timestamp_ms`** — equivalently, a
  `promoted:higher_power_confirmation` or `basis:regraded` event supersedes any earlier decision for
  the same `iteration:<n>`. Do not average or count both.
- `score_value` must always match `result.json.test.after` (or `all.after` in `all_rows` mode),
  except a `no_change` iteration, which carries forward `best_score` as a `decision:no_change`
  marker.

---

## Report per-case scores to LLM-Obs (every scored iteration)

An iteration mean says a change helped; it cannot say **which cases moved and why**. Right after the
iteration-level metric, submit **one event per scored datapoint** so every case in every iteration
is inspectable in the experiment.

The source is **that iteration's `eval_results.<split>.jsonl`** (aggregated to one row per `id`
using `per_case_means` across usable reps, with the latest usable rep's `justification`, `trace_id`,
and `span_id`) — copy the numbers, never re-derive or round them, and never submit a case the
harness did not score.

- `label`: the literal string `auto_experiment_case_score` (deliberately distinct from
  `auto_experiment_score` — **never mix the two labels in one aggregate**).
- `metric_type`: `score`; `score_value`: that case's `per_case_means[id]` from the harness.
- `span_id`: the case's experiment span id **when one exists** (discover ids with
  `list_llmobs_experiment_events`). With no experiment span, **omit `span_id`** — never invent one.
- `tags`: `["iteration:<n>", "split:<val|test|all>", "case_id:<eval-set id>",
  "git.commit.sha:<sha>", "grader_version:<int>"]`, plus `trace_id:<id>` / `case_span_id:<id>` when
  the harness captured the code-under-test's trace linkage (present only when instrumented).
  Tag-normalization rules apply unchanged.
- `reasoning`: the case's `justification` from `eval_results.<split>.jsonl`, truncated to a sane
  length — the judge's or checker's own words, never a summary you wrote.
- `timestamp_ms`: the submission time (the same instant for the whole batch is fine).

Mechanics and guards:

- **Submit `test` cases (the headline split) — and `val` cases too when within volume cap.** Always
  tag `split:test` or `split:val` so a consumer can filter cleanly.
- **Batch, but stay within one iteration and one split.** Send cases in batches (~50 per call).
  Never mix two iterations in one call, and never re-submit a case already submitted for that
  `(iteration, split, grader_version)`.
- **Cap the volume.** `cases × iterations` grows fast. If `(val_case_count + test_case_count) ×
  (max_iterations + 1)` exceeds **2000** events, submit per-case events for the **baseline, every
  kept iteration, and the final pooled confirm run** only (prioritizing `test` first), and say so in
  the final report.
- **Verify the first batch landed**, the same way the iteration metric is verified: the tool's
  accepted-count response must match the number of metrics sent. If per-case submission fails,
  **do not fail the run** — record `per_case_events: "failed: <reason>"` in `config.json` and carry
  on; the iteration-level metric is the load-bearing one.
- Record `per_case_events` in `config.json` as `"submitted"`, `"partial: <scope>"`, `"failed:
  <reason>"`, or `"skipped: <reason>"` for every run.
- A `no_change` iteration has no scored cases, so it submits **no** per-case events.

Pass the iteration number into the harness as `AUTO_EXP_ITERATION=<n>` on every eval invocation, so
every row carries the iteration it belongs to.

---

### No-change iterations — emit a carried-forward marker, not a measurement

A `no_change` iteration (feasibility probe reached 0 or ceiling below `min_delta`, harness wouldn't
run, judge unreachable, `stall_triage`, `void_error_profile`) has **no computed score**. The event
schema still requires a numeric `score_value` and a `reasoning`, so emit a labeled carry-forward:

- `score_value`: the **current `best_score`** (on `test`) carried forward (the iteration-0 baseline
  if nothing has been kept yet). **Never send `0`** — `0` plots as a cliff; carried-forward best
  plots as a flat line, which is the truth.
- `tags`: `decision:no_change` + `basis:<no_change|stall_triage|void_error_profile>` — **this tag,
  not the value, is the discriminator.** Consumers MUST exclude `decision:no_change` from any score
  aggregate. **Omit all `dist_*`, usage, `val_score`, `test_score`, and `overfit_gap` tags** — no
  eval ran, so carrying the previous iteration's numbers forward would dress a non-measurement up as
  a measurement.
- `reasoning`: state plainly that no full eval ran, why (e.g. probe result, stall triage), and that
  the value is the carried-forward best — not a measured score.

---

## `narrative.md` — the human record

Rewrite `.auto_experiment/narrative.md` **wholesale at the end of every iteration** and commit it
with that iteration's commit. It is a snapshot of the state of play, not an append-only log.

Two parts, in this order:

**1. The status table.** Row 0 is the baseline; one row per iteration after that.

```
| iter | change (one line)        | test  | val   | Δtest  | n_moved | basis        | $/case | p50 s | scored/excl/err | sha     |
|------|--------------------------|-------|-------|--------|---------|--------------|--------|-------|-----------------|---------|
| 0    | baseline                 | 0.62  | 0.60  |   —    |    —    | baseline     | 0.026  | 19.8  | 34/2/0          | fd0fbab |
| 1    | when-to-retrieve rule    | 0.70  | 0.73  | +0.08  |   5/34  | significant  | 0.030  | 19.5  | 34/2/0          | 33ec6e0 |
| 2    | reworded the tool desc   | 0.62  | 0.71  | +0.00  |   0/34  | overfit      | 0.029  | 19.6  | 34/2/1          | a91c440 |
```

- **Flag only the deltas that clear noise.** De-emphasize the rest so the user's eye lands on what
  actually moved.
- **`test` and `val` side by side are the generalization-gap trajectory.** If they diverge round
  over round, say so in the paragraph — that is the loop telling you it is fitting the `val` rows.
- `$/case` comes from the rows' `model` × `usage` priced by the orchestrator (or `cost_usd` when
  measured directly), never from a maintained counter; cumulative spend is `sum(cost_usd)` over
  every iteration's rows **plus** any billed usage on failed attempts in the error sidecars.
- Call out any kept change that raises `$/case` or `p95` latency by **>20%** even when cost/latency
  is not in `goal.hold[]`.
- In `split_mode: all_rows` there is one score column, not two, and the header explicitly says
  **in-sample**.

**2. One paragraph.** Which iteration is currently winning and *why*, in terms of the changes — not
a restatement of the table. Read every iteration's `reasoning` and `result.json` before writing it,
and overwrite the previous paragraph entirely.

---

## The final report

The headline is the **confirmed `test` delta, baseline → winner**, with its **paired bootstrap CI**,
**paired case-level `t_case`**, and **`n_moved`** (how many cases actually changed). Everything else
is supporting detail.

- **Run the pooled confirm BEFORE writing `final_result`.** Re-run baseline and winner on `test` at
  `max_runs` and **pool** with the runs already recorded, then recompute the paired bootstrap CI,
  paired case-level `t_case`, `n_moved`, and run-level two-sample t-test. This mitigates `test`
  having been used to select the winner every iteration; report the confirm number as the headline
  and the per-iteration `test` scores as the selection path.
- **State `n_moved` in the same sentence as the headline.** "+0.048 on 60 held-out cases, bootstrap
  CI [0.016, 0.083], carried by 3 cases that flipped" is honest; "+0.048, |t| = 9.4" alone is not.
  If `t_case < 2` or `n_moved ≤ 2`, state plainly that the gain rests on a handful of cases and may
  not generalize.
- **If the confirmed `test` delta is within noise of zero — the bootstrap CI straddles zero, or the
  paired test is not significant — say so plainly and recommend NOT merging.** Record
  `final_result.merge_recommendation` as one of `merge` / `do_not_merge_within_noise` /
  `do_not_merge_regression`. `best_sha` still points at the winner (or `""` if nothing beat baseline
  on `test`); the recommendation is a separate judgement about whether the winner is worth shipping.
- **State the selection bias and the round count**, always: *"`test` picked the winner across N
  iterations, so expect shrinkage relative to a never-selected holdout."* When a three-way split
  (`val` / `select` / `test`) was used, evaluate and report the untouched `test` split's delta
  instead and state that it was opened once. When `split_mode: all_rows`, state plainly that every
  number is in-sample and optimistic.
- **Per-iteration table** — the `narrative.md` table, final state.
- **Tag each kept iteration `[REQUIRED]` or `[TUNE]`.** `[REQUIRED]` fixes something broken (a
  crash, an error, a contract violation); `[TUNE]` is a judgement call that improved the score and
  that the user could reasonably decline. Stored as `iteration_results[].change_class`.
- **Two or three before/after pairs** — the same datapoint id under baseline and winner, drawn from
  the census bucket the winning iteration targeted, via `git show <sha>:.auto_experiment/eval_results.val.jsonl`.
  **Use `val` ids** while the loop could still resume; offer `test` examples only once the run is
  declared over.
- **A failure taxonomy of the remaining zeros** — refusals, harness or serving errors, timeouts,
  versus genuine misses — from the error sidecars plus the `refusal` flag.
- **What you would try next** — concrete levers still on the table, including any census bucket
  whose lever sat **outside** `files_to_optimize`.
- **The experiment link.** Point at the LLM-Obs experiment comparison
  (`https://app.datadoghq.com/llm/experiment-comparison?baselineExperimentId=<id>&experimentIds=<...>`)
  rather than pasting the numbers twice.

**Every number behind a recommendation must be on the record before the recommendation is made.** If
a metric computed in a scratch script changes which variant you would pick, fold it in first — add
it to the harness so every row carries it, re-score the existing iterations in place so the
comparison is apples-to-apples, rebuild the table — *then* recommend. The user must be able to
verify the whole story from the repo alone, without this conversation.
