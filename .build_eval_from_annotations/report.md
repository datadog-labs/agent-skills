# follows_feedback — a judge fitted to 13 human labels

Queue: **Music Recommender - Follows Feedback** (`ac4252d8-825e-410a-a3f6-5200d97c2118`), staging,
project `457c54a8-f2d9-4f5e-84d3-451af1ff8cde`.

## Headline — the deployable score

**F1 on the false class = 0.9333** over the **7 rows the published evaluator will actually see**
(`filter has_feedback:true`), across three full re-runs: `[1.0, 0.8, 1.0]`, stdev 0.094.
Constant-class floor on that scope: **0.6**. Accuracy on the measured pass-set 7/7, Wilson 95% CI
on accuracy `[0.646, 1.0]`.

The number the hill-climb optimised is different and larger in scope: **0.9333 over all 13 labelled
rows** (floor 0.375). They coincide by arithmetic, not by construction — the filter was chosen
after fitting, so the deployed scope was re-measured rather than assumed.

**Every score here is in-sample.** 13 usable rows is below the 40-row threshold at which this skill
splits a holdout, and stratifying a 3-row class would put the only examples of a class on one side
and score the judge on the other. There is no held-out number and the report does not pretend to
one. Read all of these as optimistic.

## The corpus

| | |
|---|---|
| total interactions in queue | 19 |
| annotated (the corpus) | 13 |
| pending, excluded | 6 — no label, no ground truth; they are the evaluator's target, not its training set |
| contested, dropped | 0 (single reviewer throughout) |
| empty labels, dropped | 0 |
| unrenderable | 0 — all 13 `content_id`s resolved |
| class balance | 10 true / 3 false |
| reviewer reasoning texts | **0 of 13** |

The label schema has `has_reasoning: true` and not one reviewer rationale was written. The first
judge draft therefore had no human explanation to learn from and was written from the label
definition alone. That caps how good a first draft can be; it is not the loop's fault.

**The minority class has 3 rows against a floor of 8.** The Wilson interval is wide enough to
swallow most improvements the loop can produce, and one row is worth 0.2 of F1. This was put to the
user before the run started and they chose to proceed. Labelling ~5 more `false` rows would buy
more than any further prompt iteration.

## Where the signal lives (evidence map)

`follows_feedback` asks whether the next recommendation respects what the user just said, and both
halves of that question sit on the **root `recommendation_cycle` span**: `input.value` carries the
previous song, the known/liked reaction, the verbatim feedback quote when there is one, and the
session-insight banner; `output.value` carries the song chosen.

Deliberately excluded, each for a reason:

- **strategist span** (the app's stated plan) — the human graded the song, not the plan. On row
  `10459dc6` the strategy announced a pivot to classic rock and the row was still failed.
- **`spotify_search` tool output** — track id, artist, title, embed url. No genre, era or style
  field exists anywhere in the trace, so the genre judgement is necessarily the judge's own music
  knowledge.
- **existing span evaluations** (`goal-completeness`, `prompt-rule-violation`, `discovery`,
  `tool-retry-loop`) — prior verdicts on the same property are leakage.
- **assessment / reviewer id / annotation timestamp** — exist only because a human already graded.

**Corpus-renderer defect — this run's `[recommended_song]` provenance is not verifiable.**
`build_corpus.py` indexed the span search by `trace_id` alone, so for a multi-span trace whichever
span came last in the search result won the dict overwrite and supplied `output.preview`. These
traces each carry a strategist span, a `spotify_search` tool span and three OpenAI llm spans, so the
rendered song may not have come from the root `recommendation_cycle` span the evidence map selects.
`corpus/rows.jsonl` and the raw search result are both gitignored, so which span actually won cannot
be recovered from this artifact. The script now matches root + `kind: workflow` +
`name: recommendation_cycle` and refuses to guess when a trace has more than one match — but that
fix does not retroactively validate the scores above. Treat the fidelity claim below as asserted,
not verified, and re-render before relying on it.

**Fidelity gap: none at the evidence level.** `{{span_input}}` and `{{span_output}}` at
`eval_scope: span` with `root_spans_only` resolve to exactly the two fields the local renderer
used. That syntax was verified empirically against `feedback_actionability`, a live evaluator on
the same ml_app — not assumed.

## The fit

| iteration | F1(false), 13 rows | Δ vs best | decision | basis | sha |
|---|---|---|---|---|---|
| 0 baseline | 0.8000 | — | baseline | — | `f21ed69` |
| 1 | 0.9333 | +0.1333 | kept | loop heuristic (t=2.45; **McNemar p=1.0**) | `a84e1bb` |

Stopped after one iteration at the **ceiling**: the judge then agreed with every labelled row
(10 TN / 3 TP, zero errors), so the failure census was empty and there was no bucket left to
target. Inventing an iteration against a 3-row minority class would be tuning to one row.

**Baseline.** The first draft already scored 0.8 against a 0.375 floor, perfectly stable — three
full re-runs all 0.8, no row flipped. Its single error: the user wrote *"I said I don't want rap!
why do you continue giving me rap?!"*, the app recommended `Slank - Woke Up, Pt. 2 (Make Good)`, and
all three passes identified Slank as an Indonesian rock band, concluded the pick left the vetoed
genre, and passed the row. The human failed it. The judge had converted *"I don't recognise this as
rap"* into *"this is not rap"*. Its confidences on that row (70/72/72) were the three lowest in the
corpus, so its own uncertainty already marked it.

**Iteration 1.** Inverted the burden of proof under a veto: a pass must be positively established
from real familiarity with the artist's music, and a name, nationality, era or broad scene label is
explicitly declared insufficient. The no-veto fallback was preserved verbatim, so rows where the
user stated no constraint are untouched. Mechanism audit passed — same 13-row denominator, **1 row
gained, 0 lost**, and the gained row is exactly the one the change targeted.

**This keep was not statistically significant — read the basis carefully.** The `significant: true`
and `basis: significant` recorded in `result.json` are the *loop heuristic's* verdict, computed by
a t-test over three re-run means. That t-test measures re-run noise on a fixed in-sample corpus,
not a label-level effect. The row-paired test — the right instrument here — is in the same
artifact and says nothing happened: `iter1_diagnostics.vs_baseline.mcnemar_p = **1.0**`. Read the
keep as "the loop's rule fired", not "a statistical bar was cleared". t=2.45 clears that rule
arithmetically but rests on a single row of a 3-row class. And `min_delta` = 0.02 was derived from a baseline stdev of **0.0** — a fluke of a
perfectly stable baseline — while iteration 1's own stdev was 0.094, five times that floor. The bar
every keep was judged against is more permissive than the metric's real noise at this size.

**Stability.** The `flip_rate: 0.0` in `baseline_diagnostics` and `iter1_diagnostics` is computed
over a *single* saved diagnostic pass-set, so it cannot express cross-run instability and should
not be read as a three-run stability metric. The row the fix won is in fact not settled: it answered `false` in 2 of 3 independent full re-runs, at confidence 55. It
sits on the judge's decision boundary.

**Confidence calibration** (baseline, where there was an error to calibrate against): on the saved
baseline pass, mean **84.3** over the 12 rows it got right (`eval_results.baseline.jsonl`) against
**70** on the one it got wrong; across all three passes that wrong row averaged **71.3**
(`census.json`: 70/72/72). The two sides come from different pass-counts — the per-run right-row
confidences were not retained — so treat this as directional, not a measured calibration curve.
The wrong row fell in the 60-79% band while every row at 80%+ was correct. The field carries real signal rather than being decorative. It is reported, never used to
decide.

`runs` = 3 and `min_delta` = 0.02 were **computed** from the measured baseline noise, not chosen:
`min_delta = max(0.02, 0.5 × 0.0)`, and `runs ≥ 8 × (0.0 / 0.02)² = 0` fell below the floor of 3.

## What was NOT measured — read this before enabling

The iteration-1 rule can only ever push a verdict toward `false`, and **this corpus contains no true
row where the user vetoed a genre**. Every row with a veto in it is a false row. So the rule's
false-positive risk — a legitimate pivot to an artist the judge can only characterise at arm's
length, failed anyway — is *structurally invisible* in this measurement. That is the first thing to
check against new labels.

## Findings worth your attention

1. **One human label is worth a second look** — `10459dc6`. Slank is an Indonesian rock band, and
   the judge's independent reading of the row (three times, then a fourth after the fix, reluctantly)
   was that the pick left the rejected genre. The human's label is the ground truth here and was
   never edited, but if that label is wrong then iteration 1 taught the judge to reproduce a
   labelling error and the true baseline was already at the ceiling.
2. **A broken OpenAI integration is live on this app.** `feedback_actionability` (enabled, 100%
   sampling) carries `Incorrect API key provided … HTTP 401` in its error field on these traces.
   It has been scoring nothing.
3. **The corpus dataset could not be created.** `create_llmobs_dataset` returned a 500, then
   returned a success body for id `223ebfa5-2477-4f7c-ac3d-52f114d0aae2` which is not readable
   (404) and not writable ("not found in project") minutes later, and never appeared in the
   project listing. The run's corpus therefore has no dataset-id provenance link; its source of
   truth is the annotation queue.
4. **`pup` could not be used.** It is installed (1.8.0) but authenticated only against prod
   (`datadoghq.com`); the queue is on staging. The backend was switched to `mcp` on the user's
   instruction rather than silently falling back.
5. **Skill bug — now fixed.** `judge_harness_template.py` wrote only `eval_results.jsonl`, an
   audit file with one record per row keyed `output`, while `scoring.py --pred` wants one record
   per *pass* keyed `label`. Feeding the harness's own output to the scorer silently reported
   every row unusable and a headline of 0.0, so the diagnostics in this report came from running
   `judge_runner.py` separately. Both the template and this run's copy of the harness now also
   write `predictions.jsonl` in the scorer's contract — every pass of every run, the whole verdict
   under `label`, unparseable passes recorded rather than dropped. That is what makes the
   cross-run flip rate measurable at all; the `flip_rate: 0.0` above predates it.

## Published

| | |
|---|---|
| evaluator | `follows_feedback` |
| ml_app | `music-recommender` |
| scope | `span`, `root_spans_only: true`, `filter: has_feedback:true` |
| sampling | 100% |
| judge | `anthropic` / `claude-opus-5` — the same model the prompt was fitted on |
| enabled | **false** |

Two things the shipped evaluator does differently from the judge that was measured:

- **`confidence` is not a typed field.** The write was refused with
  `llm_judge_config.output_schema: invalid BYOP output schema` when `confidence` was an integer
  property. It was retried without it: the shipped `output_schema` emits `boolean_eval` and
  `reasoning`, and the prompt requires the reasoning to end with `Confidence: NN%`. So confidence
  survives as text, not as a queryable number.
- **The Anthropic integration is unverified.** No other evaluator in this org runs on an
  `anthropic` provider, so there is no evidence the integration is configured. The write was
  accepted, but a write only records configuration — if the integration is missing, this will error
  on its first real span. Check the evaluator's error field after enabling.

## Recommended next steps

1. Enable it on a small sample and read the first verdicts by hand before trusting any of them.
2. Re-run the fidelity check the skill recommends: once it has scored some of the **already
   labelled** rows, compare its verdicts against the human labels again. The 0.9333 was measured
   with a local renderer; the deployed number is the one that matters.
3. Label the 6 pending interactions — especially any where the user vetoes a genre and the app
   *does* pivot correctly. That is the exact blind spot named above.
4. The 6 pending interactions were **not** annotated by this run. Predicting a label is not the same
   as a human agreeing with it, and writing predictions into the queue would destroy the ground
   truth any future run depends on.
