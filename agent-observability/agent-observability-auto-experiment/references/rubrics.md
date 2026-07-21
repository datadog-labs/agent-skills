# Auto-experiment rubrics (non-negotiable)

These rules are lifted from the production `auto_experiments` worker
(`domains/ml_observability/apps/apis/auto_experiments/service/prompts.py`). They are the
load-bearing parts of the loop — follow them verbatim. Paraphrasing here weakens the loop.

## Scoring policy (`_scoring_policy`)

⛔ **INVENTING SCORES IS FORBIDDEN.** Do NOT hard-code, estimate, guess, manually assign, or
carry over score numbers. Every score MUST be the return value of actually running code over the
data. Comments or arrays of "representative"/"fixed" scores are forbidden. If you cannot truly
compute a score, STOP and report the blocker — never substitute a made-up number.

A consequence for the loop: if a change is made but its score cannot be computed (harness won't
run, judge unreachable, etc.), that iteration is recorded as **no_change** with the blocker in
`reasoning` — it is never scored with a fabricated number.

## Noise & keep/discard policy (`_noise_policy`)

⚠️ **A single eval run is a NOISY ESTIMATE, not a measurement.** The code under test and any
LLM judge are stochastic, so the mean wiggles run-to-run. Treat every score as
`mean ± stdev` over **`AUTO_EXP_RUNS` (default 3) full re-runs of the eval on the same data**
(the harness does this and prints `stdev` + `run_means`). Consequences the loop MUST obey:

- **Keep only a delta that clears the noise floor.** An iteration is `is_best` / kept only if
  `after_mean` beats `best_mean` by more than the noise band —
  `|after_mean − best_mean| > max(pooled_stdev, min_delta)`, where `pooled_stdev` is the larger
  of the two runs' `stdev` and `min_delta` is a small floor from config (default `0.02` on a
  0–1 metric). A delta **within** the noise band is **not** an improvement: record it `discarded`
  (decision), not kept, no matter that the point estimate rose.
- **Compare on the same footing.** `best_mean`/`best_stdev` come from a real R-run harness run of
  the current best, not a stale single number carried forward. When in doubt, re-run best and
  candidate back-to-back so data/endpoint drift cancels. **`pooled_stdev` is recomputed from THESE
  two runs' `stdev` every iteration — never frozen at the baseline's.** A change that also reduces
  variance (e.g. a precision fix that collapses run-to-run wiggle) must be judged against the
  *current* noise, not the baseline's; freezing the baseline band silently penalizes it.
- **A within-noise "win" is the classic trap.** Point estimates of 15 vs 14 mean nothing when
  stdev is ~2. Do not keep it, do not report it as an improvement.
- **Fix underpowered runs by raising power, NOT by loosening the gate.** The gate compares a
  *difference of two means* against a single-run `stdev`; at small `runs` the standard error of that
  difference (`≈ stdev·√(2/runs)`) is large, so a genuine moderate gain can never clear the band no
  matter how real it is. The cure is more `runs`, not a lower threshold. **Never widen the band or
  drop `min_delta` to let a within-noise point-estimate through** — that reopens the false-keep trap.
- **Higher-power confirmation before final discard.** If the top within-band candidate has the
  run's **highest mean**, moves in the goal's direction, and is *close* to the band (roughly
  `|t| = |Δ| / (stdev·√(2/runs)) > 1`), it is a **promising-but-underpowered** result, not a
  confirmed null. Before discarding it for good, re-run **best and candidate back-to-back at the
  max `runs`** (the 10 clamp) and re-apply the gate on those higher-power means. Keep only if it now
  clears; otherwise discard with the higher-power numbers recorded. Do this for the single best
  candidate of the run, not every within-band wobble.

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

**Held-out split — hill-climb on `val`, prove on `test`.** After building the scoreable set, split
it **once, deterministically** (e.g. by a hash of the datapoint id, ~70% / 30%) into
`data.val.jsonl` and `data.test.jsonl`, committed alongside `data.jsonl`:

- **`val`** is the ONLY split the hill-climb reads. Every iteration's `before/after_score` and the
  keep/discard gate run on `val` (point the harness at it with `AUTO_EXP_DATA=.auto_experiment/data.val.jsonl`).
- **`test`** is untouched during the loop. Run it **once at the very end**, on the baseline commit
  and on the best commit, and report that baseline-vs-best `test` delta as the run's real result.
- **Why:** hill-climbing directly on the full set overfits the loop to that set's noise, so a
  within-noise "win" looks real. A change that only helps `val` but not held-out `test` is not a
  real improvement — the `test` delta is the honest headline. If `val` improved but `test` did
  not, say so plainly; do not report the `val` gain as the result.
- Keep the split small enough to run in the iteration budget but large enough that per-split stdev
  is meaningful; if the corpus is tiny, note the low power in `reasoning` rather than faking a split.

## Baseline failure census — localize the lever before you tweak (`_failure_census`)

Before iteration 1's first change, decompose **where the baseline actually loses**, so iterations
aim at a real failure mode instead of guessing. Blind prompt-tweaking is how a loop burns its
budget re-discovering that wording changes are noise.

- From the baseline `eval_results.jsonl`, bucket every **failing / low-scoring** datapoint by
  **root cause**, not by score. Use judge justifications + the trace to assign each a short cause
  tag. Generic buckets that fit most tasks: `wrong_retrieval` (needed input never fetched),
  `wrong_reasoning` (had the input, drew the wrong conclusion), `format/parse` (right answer, wrong
  shape), `refusal/empty`, `judge_disagreement` (output is fine, rubric is off), `data/label`
  (the reference is wrong). Adapt the tags to the task.
- Write the census to `.auto_experiment/census.json` (`{tag: count, examples: [ids]}`) and commit it.
  Surface the ranked buckets.
- **Every iteration must name the census bucket it targets** (in `result.json` `reasoning`) and be a
  change plausibly able to move THAT bucket. If the dominant bucket is not reachable by editing
  `files_to_optimize` (e.g. `data/label` errors, or a `wrong_retrieval` that needs a tool the code
  can't call), say so — that is a finding (the ceiling is not prompt/code-reachable), not a reason
  to keep tweaking the reachable-but-tiny buckets.

## Feasibility probe — prove reachability before you pay for a full eval (`_feasibility_probe`)

A full eval is the expensive step (R runs × every datapoint × real code + judge). Before spending
it on a hypothesis, run the **cheapest possible offline check that the lever CAN move the metric** —
an upper bound, not a measurement. Only run the full eval on hypotheses that pass.

- The probe answers "if this change worked perfectly, could it flip any currently-failing
  datapoint?" Examples: for a retrieval change, does the needed signal even exist in reach (a
  read-only API/tool call on the census's failing ids)? For a prompt change, on 2–3 failing
  examples does the edited prompt visibly change the output in the intended direction (a handful of
  direct model calls, not the full harness)?
- A probe that reaches **0** of the failing datapoints means the hypothesis is dead — record it
  `no_change` with the probe result in `reasoning` and move on **without** spending a full eval.
  (This is exactly how the production effort rejected semantic-search and dependency-graph levers in
  minutes instead of hours.)
- Keep probes read-only and offline where possible; never let a probe mutate `files_to_optimize`
  or the committed harness/data.

## Messages-source guidance — where the input/output lives (`_messages_source_guidance`)

**`messages` is the source of truth — the root span's `input.value` is usually a thin/truncated
summary and MUST NOT be scored when a `messages` field exists somewhere in the trace.**

- Call `get_llmobs_span_details` and read its `content_info` map for each span. It shows which
  fields exist and their size, e.g. `{"input": {"chars": 1520}, "messages": {"count": 12}}`.
  Find the span whose `messages` count is highest — that span holds the full conversation history
  (and often the system prompt).
- Fetch it with `get_llmobs_span_content(field="messages")` (use `path` like `$.messages` to
  extract). Do the same for the output side (`field="output"` / its messages).
- The full `messages` typically lives on a **child LLM span**, not the root span — drill into the
  trace tree (`get_llmobs_trace` / `expand_llmobs_spans`) and inspect child spans, do not stop at
  the root. Only fall back to `input.value` / `output.value` when NO span exposes `messages`.
- If a messages field is too large to process directly, summarize it first, then score on the
  summary.

## Metric selection — prefer deterministic ground truth over an LLM judge (`_metric_selection`)

The scorer is itself a noise source. **When a deterministic, ground-truth metric is available, use
it instead of an LLM judge** — it removes an entire layer of variance and can't be gamed:

- If datapoints carry a **reference/expected output** (dataset `expected_output`, gold label), score
  with an exact/programmatic check (exact match, F1, set overlap, a repo evaluator, `total_examples`
  from a pipeline, etc.) — deterministic, `stdev ≈ 0` across runs from the judge side.
- Use an **LLM-as-judge only when no ground truth exists** (open-ended quality). Then treat it as
  the noisiest component: bump `AUTO_EXP_RUNS` (≥5), pin the model + prompt, and expect a wider
  noise band.
- Either way the metric is **computed by running code** (scoring policy) — a deterministic checker
  and an LLM judge are both legitimate `judge()` implementations; prefer the deterministic one.
- State which metric kind you used in `reasoning`; a deterministic ground-truth metric is the
  strongest evidence, an LLM judge the weakest.

## Eval-harness spec (`_eval_harness_skill`)

Write a real, committed evaluation module `.auto_experiment/eval_harness.py` with:

- `generate_output(line)` — runs the **real code under test** to produce the output for ONE
  datapoint (import the real entrypoint; if the import bus-errors / fails, a copy of the needed
  function with ONLY the offending import stubbed; reconstruct from source as a last resort).
- `evaluate_line(line) -> {"output": ..., "score": <float 0-1>, "justification": ...}` — calls
  `generate_output`, then runs the **LLM-as-judge** on (input, generated output) using the
  evaluator from the config (`evaluators` field, else `goal`), and **returns the computed
  score**. There must be **NO score literals / hard-coded arrays** anywhere in this file.
  - **Judge model selection.** If the experiment config names a judge model, use it. **If no
    model is specified, default to the Claude model selected in the Claude Code session that
    invoked this skill** — i.e. the same model running this loop. Resolve that model id (the
    session/main-loop model) and call it through whichever Anthropic credential is available
    (`ANTHROPIC_API_KEY` / `CLAUDE_API_KEY`, or an internal Datadog/AI-gateway route). Only fall
    back to another provider (e.g. `OPENAI_API_KEY`) or an internal Datadog LLM Obs evaluator if
    the session model cannot be reached. Pin the resolved model id in `eval_harness.py` so the
    judge is identical across every iteration, and state in `reasoning` which model you used.
  - **Figure out how to make a real judge call yourself**: probe for an available LLM
    credential/endpoint and use whichever works. If, after genuinely trying, no judge can be
    reached, STOP and report the blocker — do NOT fabricate a score.
- a runner that applies `evaluate_line` to EVERY scoreable line of `data.jsonl` (per the
  exclusion rule above), writes each result to `.auto_experiment/eval_results.jsonl` (input
  snippet, output, score, justification), and prints the mean over scoreable lines.

The harness is built **once** in iteration 1 and **reused verbatim** in every later iteration —
only the code under test changes between iterations.

## Metric JSON schema — `.auto_experiment/result.json` (`_return_metric_block`)

After each scored iteration, write this exact object to `.auto_experiment/result.json` and commit
it in the same commit as the code change:

```json
{
  "before_score": <float 0-1 — best_mean going in>,
  "after_score": <float 0-1 — this iteration's mean over AUTO_EXP_RUNS runs>,
  "after_stdev": <float — across-run stdev the harness printed (the noise floor)>,
  "runs": <int — AUTO_EXP_RUNS used>,
  "delta": <after_score minus before_score>,
  "noise_band": <float — max(pooled_stdev, min_delta) used for the keep/discard gate>,
  "reasoning": "<REQUIRED — scoring method FIRST (how generate_output ran the code, how many scoreable lines evaluate_line ran over, runs), then what was tested/failed/succeeded, how many traces were excluded and why, and any caveat about reproducing production; 2-4 sentences; never empty>",
  "best_score": <best metric value across all iterations, considering the optimization direction>,
  "is_best": <REQUIRED — true ONLY if the delta clears the noise band (see Noise policy); false otherwise; never omit>
}
```

`is_best` drives keep/discard and must reflect the optimization direction in `goal` (higher is
better unless the goal says to minimize) **AND clear the noise band** (`|delta| > noise_band`) —
a within-noise point-estimate gain is `is_best: false`. `reasoning` is mandatory and never empty.

## Mechanism audit — confirm the change CAUSED the gain (`_mechanism_audit`)

A rising mean is necessary but not sufficient to keep a change. Before setting `is_best: true`,
confirm the improvement is **caused by the change**, not by an artifact:

- **Per-datapoint diff.** Diff the best vs candidate `eval_results.jsonl`: which datapoints flipped
  up, which down. The gain must come from datapoints the change plausibly touches (ideally in the
  census bucket it targeted). A mean that rose while the targeted datapoints did **not** flip is a
  red flag — the "gain" is probably noise or an unrelated wobble.
- **Denominator guard.** Confirm `scored`/`excluded` counts are the SAME across best and candidate.
  A higher mean from *fewer scored datapoints* (the change dropped hard cases out of the eval set)
  is an artifact, not an improvement — discard it. (A real production loop was fooled exactly this
  way: a "+0.1" that was only a shrinking denominator.)
- **Causality on regressions too.** If controls/negatives regressed, check whether the change even
  fired on them; a regression the change never touched is noise, one it caused is a real cost.
- Record the audit outcome (which datapoints moved and why it is/ isn't causal) in `reasoning`. If
  the audit fails, the iteration is `discarded` even though the point estimate rose.
