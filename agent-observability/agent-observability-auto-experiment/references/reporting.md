# Reporting — the machine record, the human record, and the final report

This run has two reporting channels and they must always agree:

- **the LLM-Obs experiment** is the machine record — one event per iteration, queryable, tagged, and
  the reason the `$experiment-id` argument exists;
- **`.auto_experiment/narrative.md`** is the human record — a status table and a paragraph, rewritten
  wholesale every iteration, committed, readable in one screen by a resumed session or by the user.

Neither replaces the other, and a number that appears in one must appear in the other. The same
discipline the skill already applies between `reasoning` and its tags applies between these two.

---

## Report each iteration's score to LLM-Obs (every scored iteration)

Once you have a computed score for an iteration, submit **exactly one** eval-metric datapoint to
LLM-Obs with the `submit_llmobs_experiment_events` MCP tool. Do this once per iteration, right
after the score is computed and the iteration's commit / `result.json` is written — including
iteration 1 and the **iteration-0 baseline** (reported at the end of Step 2.4; there `score_value`
= the final `test` baseline and the decision tag is `decision:baseline`).

Immediately after this submission, **recompute `estimated_duration_time`** (the ETA in seconds to
the end of the whole run — `avg_iteration_elapsed × iterations_left`, → `0` after the last
iteration; see **Setup** step 5) and `update_llmobs_experiment` — one call, re-sending
`repo`/`branch`/`model` unchanged.

Call `submit_llmobs_experiment_events` — or, under `datadog_backend: pup`,
`pup llm-obs experiments events submit --metrics '[{…}]' <EXPERIMENT_ID>` with the same metric objects passed inline — with a single metric shaped exactly like this:

- `experiment_id`: `$experiment-id` (the validated skill argument, also persisted to `config.json`
  as `dd_auto_experiment_id`). Do not ask the user and do not invent one.
- `metrics`: an array containing exactly one object with these fields and no others:
  - `label`: always the literal string `auto_experiment_score`.
  - `metric_type`: `score`.
  - `score_value`: **this iteration's `test` score** — `result.json.test.after`, the number computed
    by the harness, never a literal or a rounded-for-display value. This changed meaning when the
    holdout started being scored every iteration: it used to be the val score. Runs from before and
    after that change are not comparable inside one experiment, so do not mix them.
    Still **exactly one event per iteration** — two events sharing an `iteration:<n>` tag would be
    collapsed by the dedup rule below, so the val score travels as a tag, not as a second metric.
  - `timestamp_ms`: the current wall-clock time as an epoch timestamp in **milliseconds**.
  - `tags`: start with `["iteration:<n>", "git.commit.sha:<sha>", "decision:<decision>"]` and
    **also add the decision-legibility tags below**. `<n>` is this iteration's number (`1` for the
    first improvement, `2` for the next, and so on), `<sha>` is the **full 40-character** Git commit
    SHA of the commit this iteration created for its change — the complete hash from
    `git rev-parse HEAD` after committing the iteration (e.g.
    `fd0fbab7c1232e125df7b22d9df856a2ef73ab65`), **never the abbreviated 7/8-char short hash** — and
    `<decision>` is this iteration's keep/discard decision recorded in `iteration_results` (`kept` or
    `discarded`; `baseline` for iteration 0; `no_change` for an iteration whose feasibility probe or
    harness produced no measured score — see **No-change iterations** below).
  - ⚠️ **Datadog NORMALIZES tag values — encode accordingly.** Tag values are lowercased and some
    characters are rewritten, so a tag is **not** a byte-faithful channel. Two rules follow, both
    learned from inspecting really-ingested events rather than from review:
    - **Never put a leading `+` in a tag value.** It is rewritten to `_`: a tag sent as
      `delta_vs_best:+0.0447` lands as `delta_vs_best:_0.0447`. The sign — the entire point of a
      delta — is destroyed. Worse, `-` *survives*, so negatives would land as `-0.1180` while
      positives land as `_0.1180`, an asymmetric encoding a consumer has to reverse-engineer.
    - **Never put case-sensitive text in a tag value.** `time_start:2026-07-22T14:31:07Z` lands as
      `...t14:31:07z`, which is no longer valid ISO-8601 and no longer byte-matches the
      `iteration_results` row.
    Keep the faithful values in `config.json`; put only normalization-safe forms in tags (unsigned
    decimals, integers, lowercase enums, epoch millis).
  - **Decision-legibility tags (required on every scored iteration).** `score_value` alone hides
    *how much to trust the move*: a `kept` best can be either a solid, significant gain or a
    within-noise wobble that was kept only because the point estimate rose — a raw number cannot
    show which. Surface the decision's basis **and its confidence** as structured, filterable tags
    so the "why" sits next to the score:
    - `basis:<significant|within_noise|overfit|unreplicated|regression|audit_failed|guardrail_regression|memorized|void_error_profile|promoted|regraded|baseline|no_change|stall_triage>` — the
      one-word basis (`significant` = kept, `significant:true` (cleared the t-test **and** `|Δ| ≥
      min_delta`); `within_noise` = **not significant** (`significant:false` — `|t| < 2` OR
      `|Δ| < min_delta`), read the score carefully — pair with the `decision` tag: `decision:kept` +
      `within_noise` is a **tentative best** (point estimate rose in the goal's direction but not
      significant), while `decision:discarded` + `within_noise` is a not-significant wobble that did
      **not** beat the best; `regression` = discarded, significantly worse (moved the wrong way);
      `audit_failed` = discarded, the mechanism audit failed (e.g. the denominator shrank) so the
      higher mean is an artifact — regardless of the point estimate; `promoted` = a `within_noise`
      best later confirmed `significant` at higher power. The holdout-era additions:
      `overfit` = val rose and test did not, so the change fitted the rows the improver read;
      `unreplicated` = test rose while val did not, which at these rep counts is usually noise and
      is not kept without a second run; `guardrail_regression` = the target improved but a `hold[]`
      metric regressed outside noise; `memorized` = the diff pasted datapoint content into an edited
      file and the hit was not explained; `void_error_profile` = the round's error profile diverged
      grossly from the baseline's, so it is not a comparable data point; `regraded` = a correction
      re-submitted after `evaluators` was fixed and every iteration re-scored in place;
      `stall_triage` = the categorization iteration, which changes no code).
    - `delta_vs_best:<X.XXXX>` (**absolute value, no sign character**) plus
      `delta_sign:<pos|neg|zero>` — the delta against the **previous best** (the number the decision
      uses), NOT vs baseline. The sign is a separate tag because a leading `+` does not survive tag
      normalization (see the warning above); splitting it keeps the magnitude filterable and the
      direction unambiguous in both directions. `delta_sign` is arithmetic (`after − best`), so on a
      minimize goal an improvement is `neg` — read improvement off `basis:`/`decision:`, not the sign.
    - `t_stat:<value>` (or `t_stat:null` when `se_diff == 0`) and `significant:<true|false>` — for a
      `within_noise` best, `significant:false` is what flags the kept score as low-confidence.
    - These four (`delta_vs_best`, `delta_sign`, `t_stat`, `significant`) describe a **comparison
      against the previous best**, so they apply only to an iteration that made one. **Iteration 0
      omits all four** (no previous best, no t-test) — see Step 2.4.
    - **Split tags (required on every scored iteration).** `split:test` names which split
      `score_value` came from, and `val_score:<X.XXXX>` / `test_score:<X.XXXX>` carry both numbers so
      a consumer can see the generalization gap without opening the repo. `overfit_gap:<X.XXXX>`
      (**absolute value, no sign character**) plus `overfit_gap_sign:<pos|neg|zero>` is
      `val_delta − test_delta`, the quantity the `overfit` basis is read off — split into magnitude
      and sign for the same tag-normalization reason as `delta_vs_best`. Also
      `split_mode:<val_test|all_rows>`, because in `all_rows` mode every number is in-sample and a
      consumer must not read it as held-out.
    - **Goal and guardrail tags.** `goal_target:<score|cost_usd|latency_s>` and
      `goal_direction:<higher|lower>`, plus per held metric `hold_<metric>_ratio:<X.XXXX>` and
      `hold_<metric>_status:<ok|within_noise|regressed>`. Without these a cost-targeted run and a
      quality-targeted run are indistinguishable in the experiment view even though their
      `score_value`s mean opposite things.
    - **Integrity tags.** `suspicious:<true|false>` (the sentence itself goes in `reasoning`, since
      tag values are lowercased and normalized) and `grader_version:<int>` (scores from different
      grader versions are not comparable, and the tag is what lets a consumer refuse to mix them).
    - `time_start_ms:<epoch_millis>` and `time_end_ms:<epoch_millis>` — this iteration's wall-clock
      start/end as **integer epoch milliseconds**, so the experiment view can show per-iteration
      duration. They must be the exact instants recorded as ISO-8601 in the `iteration_results` row
      (see **Per-iteration timing**), just expressed as millis; never fabricate or round to a
      different instant. Epoch millis rather than ISO because tag normalization lowercases the `T`
      and `Z` of an ISO string, leaving a value that neither parses as ISO-8601 nor byte-matches the
      row — integers pass through untouched.
  - **Distribution tags (required on every iteration that has a computed score).** `score_value` is
    a single mean — it hides whether the iteration scored uniformly well or split into perfect and
    zero datapoints, which is the difference between "broadly better" and "traded one bucket for
    another". Publish the row's `score_distribution` (see **Per-iteration score distribution**) as
    eight tags. Copy them from the `iteration_results` row — the same numbers, never re-derived by
    hand and never estimated:
    - **counts, as integers** — `dist_n:<int>`, `dist_zero:<int>`, `dist_perfect:<int>` (cases
      scored, cases scoring exactly 0.0, cases scoring exactly 1.0).
    - **nearest-rank five-number summary, 4 decimal places** — `dist_min:<X.XXXX>`,
      `dist_q1:<X.XXXX>`, `dist_median:<X.XXXX>`, `dist_q3:<X.XXXX>`, `dist_max:<X.XXXX>`.

    **Distribution basis.** With per-rep rows available, `dist_*` is computed from the **per-case
    means on the test split**, not from one pass's raw rows — a strictly better sample, and it
    removes the old caveat that the median would not line up with the reported score. Publish
    `dist_basis:case_means_test` alongside so the convention is self-describing; this is a
    **breaking change** to `dist_*` semantics, and a consumer comparing across it would be comparing
    different quantities.

    **The counts are not decoration — on a near-binary metric they are the only part that moves.**
    A real run had 26 of 34 cases at exactly 1.0, which pins `q1 = median = q3 = 1.0` and makes the
    quartiles look frozen across iterations, while `dist_zero` fell 10 → 5 and captured the actual
    improvement. Publishing quartiles alone would have reported a flat distribution for a run whose
    distribution changed substantially. The `dist_*` prefix keeps these distinct from `min_delta`, the
    keep/discard floor, which is unrelated to the score spread. The raw `values` array is **not**
    tagged (35+ tags per event); it stays in `config.json`. **Omit all eight on a `no_change`
    iteration** — it has no computed distribution (see **No-change iterations**).
    **These summarize the last run's per-datapoint spread, not the sample behind `score_value`**
    (which is the mean across `runs` — see **Per-iteration score distribution**), so
    `dist_median` will not generally equal `score_value` and a consumer must not read them as
    quartiles *of* the reported score. Say so in `reasoning` if the two look far apart.
  - `reasoning`: this iteration's `reasoning` string from `iteration_results`. **Lead with a
    one-line verdict** that states the decision and its basis in plain terms before the details,
    e.g. `"KEPT (tentative) — higher point estimate in the goal's direction (Δvs_best +0.016) but
    within noise (t=0.94, not significant); new best, but the gain may be noise — read the score
    carefully / confirm at higher power."` Then the usual detail (what was tried, which
    census bucket, mechanism-audit result). Use the same text recorded in `result.json`; do not
    fabricate. The lead line + the tags must agree.
  - Do **not** include `span_id`, `categorical_value`, or `boolean_value`.

Example arguments for iteration 5, whose harness scored `0.72` on test and `0.75` on val:

```json
{
  "experiment_id": "$experiment-id",
  "metrics": [
    {
      "label": "auto_experiment_score",
      "metric_type": "score",
      "score_value": 0.72,
      "reasoning": "KEPT — significant on test (Δvs_best +0.048, t=3.1, bootstrap CI [0.021, 0.074]). Rewrote the retrieval query builder to include entity synonyms (targeting the 'missed-retrieval' census bucket); val moved with test (gap +0.006), the mechanism audit passed on the paired set, and no held metric regressed.",
      "timestamp_ms": 1752430000000,
      "tags": ["iteration:5", "git.commit.sha:33ec6e0959bd46b0ea9c337cf6a28a763d3eeb0a", "decision:kept", "basis:significant", "split:test", "split_mode:val_test", "val_score:0.7500", "test_score:0.7200", "overfit_gap:0.0060", "overfit_gap_sign:pos", "delta_vs_best:0.0480", "delta_sign:pos", "t_stat:3.1", "significant:true", "goal_target:score", "goal_direction:higher", "hold_cost_usd_ratio:1.0400", "hold_cost_usd_status:ok", "suspicious:false", "grader_version:1", "time_start_ms:1753194667000", "time_end_ms:1753195132000", "dist_basis:case_means_test", "dist_n:34", "dist_zero:5", "dist_perfect:26", "dist_min:0.0000", "dist_q1:1.0000", "dist_median:1.0000", "dist_q3:1.0000", "dist_max:1.0000"]
    }
  ]
}
```

Rules:

- **One metric per iteration, plus corrections from two sanctioned classes.** Submit exactly one
  metric per iteration at the time it is scored, and never batch several iterations into one call.
  Two kinds of later event may supersede it:
  - a **promotion correction** (see final-report Higher-power confirmation): re-submitting that
    iteration with `decision:kept` + `basis:promoted` + `promoted:higher_power_confirmation` after a
    `within_noise` best is confirmed `significant` at higher power. It re-labels confidence; it is
    not a second measurement.
  - a **regrade correction**: after `evaluators` is fixed and every iteration is re-scored in place
    from stored outputs (rubrics.md **Stall taxonomy** → grader disagreement), re-submit each
    affected iteration with `basis:regraded`, the bumped `grader_version:<n>`, the new
    `score_value`, and a `reasoning` that says it supersedes the earlier event. This one **is** a
    new measurement — of the same outputs under a corrected grader.
- **Consumer dedup rule (state it, honor it).** Because the store is append-only, an iteration may
  have two events (an earlier `basis:within_noise` and a later promotion correction). Consumers of
  `auto_experiment_score` MUST dedupe **per `iteration:<n>` tag, keeping the event with the latest
  `timestamp_ms`** — that event carries the iteration's final decision. Equivalently: a
  `promoted:higher_power_confirmation` or `basis:regraded` event supersedes any earlier decision for
  the same `iteration:<n>`. Do not average or count both.
- The value you submit is the same computed `test.after` recorded in `result.json`; the two must
  always agree — **except a `no_change` iteration**, which has no computed score and instead
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
  measurement. **Send no `dist_*` tags** on a `no_change` event: no eval ran, so there is no
  distribution — carrying the previous best's spread forward would dress a non-measurement up as a
  measured one. Absent `dist_*` is the honest signal.
- `reasoning`: state plainly that no full eval ran, why (e.g. the probe result), and that the value
  is the carried-forward best — not a measured score.
- Send no `val_score`/`test_score`/`overfit_gap` either, for the same reason as `dist_*`: there is
  nothing measured to report on either split, and carrying the previous pair forward would dress a
  non-measurement up as a measurement.

So `no_change` is still submitted (one metric, as every iteration), but it is unambiguously a
non-measurement: carried-forward value + `decision:no_change`. Do **not** tag it `kept`/`discarded`
(those assert a real measurement) and do **not** overload the value to signal state.

---

## `narrative.md` — the human record

Rewrite `.auto_experiment/narrative.md` **wholesale at the end of every iteration** and commit it
with that iteration's commit. It is a snapshot of the state of play, not an append-only log.

Two parts, in this order.

**1. The status table.** Row 0 is the baseline; one row per iteration after that.

```
| iter | change (one line)        | test  | val   | Δtest  | basis        | $/case | p50 s | scored/excl/err | sha     |
|------|--------------------------|-------|-------|--------|--------------|--------|-------|-----------------|---------|
| 0    | baseline                 | 0.62  | 0.60  |   —    | baseline     | 0.026  | 19.8  | 34/2/0          | fd0fbab |
| 1    | when-to-retrieve rule    | 0.70  | 0.73  | +0.08  | significant  | 0.030  | 19.5  | 34/2/0          | 33ec6e0 |
| 2    | reworded the tool desc   | 0.62  | 0.71  | +0.00  | overfit      | 0.029  | 19.6  | 34/2/1          | a91c440 |
```

- **Flag only the deltas that clear noise.** Grey out or suppress the rest, so the user's eye lands
  on what actually moved rather than on four decimal places of wobble.
- **`test` and `val` side by side are the generalization-gap trajectory.** If they diverge round
  over round, say so in the paragraph — that is the loop telling you it is fitting the val rows.
- `$/case` comes from the rows' `model` × `usage` priced by the orchestrator, never from a
  maintained counter; cumulative spend is `sum(cost_usd)` over every iteration's rows **plus** the
  billed usage recorded on failed attempts in the error sidecars, because failed spend is still
  spend.
- In `split_mode: all_rows` there is one score column, not two, and the header says **in-sample**.

**2. One paragraph.** Which iteration is currently winning and *why*, in terms of the changes — not
a restatement of the table. "v4 has the best recall, but v3's tightening traded a little recall for
precision and nets the higher test score" is the register. Read every iteration's `reasoning` and
`result.json` before writing it, and overwrite the previous paragraph entirely.

---

## The final report

The headline is the **test delta, baseline → winner**, with its bootstrap CI. Everything else is
supporting detail.

- **If the test delta is within noise of zero — the CI straddles zero, or the paired test is not
  significant — say so plainly and recommend NOT merging.** Record
  `final_result.merge_recommendation` as one of `merge` / `do_not_merge_within_noise` /
  `do_not_merge_regression`. `best_sha` still points at the winner; the recommendation is a separate
  judgement about whether the winner is worth anything. An honest "this did not move the needle,
  here is what I would try with more budget" is more useful than a dressed-up marginal gain.
- **The pooled confirm run.** Before writing the headline, re-run baseline and winner on `test` at
  `max_runs` and **pool** with the runs already recorded, then recompute the bootstrap CI. This is
  the mitigation for `test` having been used to select the winner every iteration; report the
  confirm number as the headline and the per-iteration test scores as the selection path.
- **State the selection bias and the round count**, always: "`test` picked the winner across N
  iterations, so expect shrinkage relative to a never-selected holdout." When a three-way split was
  used, report the untouched split's delta instead and say that it is untouched.
- **Per-iteration table** — the `narrative.md` table, final state, with the noise-clearing deltas
  emphasised and the rest suppressed.
- **Tag each kept iteration `[REQUIRED]` or `[TUNE]`.** `[REQUIRED]` fixes something broken (a
  crash, an error, a contract violation); `[TUNE]` is a judgement call that improved the score and
  that the user could reasonably decline. The iteration is the right unit because each one is a
  single commit on the scratch branch, so the user can cherry-pick. Stored as
  `iteration_results[].change_class`.
- **Two or three before/after pairs** — the same datapoint id under the baseline and under the
  winner, drawn from the census bucket the winning iteration targeted. Both versions exist via
  `git show <sha>:.auto_experiment/eval_results.val.jsonl`. **Use val ids**: the discipline stays
  uniform, and there is no temptation to read holdout outputs while the loop could still continue.
  Offer test examples only once the run is declared over.
- **A failure taxonomy of the remaining zeros** — refusals, harness or serving errors, timeouts,
  versus genuine misses — from the error sidecars plus the `refusal` flag. A single failure rate
  hides all four, and they need completely different fixes.
- **What you would try next** — the concrete levers still on the table, including any census bucket
  whose lever sat **outside** `files_to_optimize`. That last one is often the most valuable line in
  the report.
- **The experiment link.** Point at the LLM-Obs experiment comparison for the per-iteration detail
  (`https://app.datadoghq.com/llm/experiment-comparison?baselineExperimentId=<id>&experimentIds=<...>`,
  the shape `agent-observability-experiment-analyzer` builds) rather than pasting the numbers twice.

**Every number behind a recommendation must be on the record before the recommendation is made.** If
a metric computed in a scratch script changes which variant you would pick, fold it in first — add
it to the harness so every row carries it, re-score the existing iterations in place so the
comparison is apples-to-apples, rebuild the table — *then* recommend. The user must be able to
verify the whole story from the repo alone, without this conversation.
