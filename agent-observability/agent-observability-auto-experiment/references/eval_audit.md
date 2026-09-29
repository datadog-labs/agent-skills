# Eval-trust gates — prove the measurement before you climb on it

A hill-climb over a broken eval is worse than no hill-climb: you "improve" an artifact, declare
victory, and ship nothing. In practice the most surprising eval results turn out to be bugs in the
eval rather than facts about the code, so the checks below pay for themselves the first time one
fires.

This file holds the procedures. SKILL.md holds the ordering and carries a short pointer at each
gate. **Each gate is a hard STOP**: a failing check is answered by fixing it or by asking the user,
never by proceeding with a caveat in the log.

There are three gates, placed where the data each one needs first exists:

| gate | where | what it needs | what it catches |
|---|---|---|---|
| **1 — harness trust** | SKILL.md Step 2.2, after the harness is written, **before** the pilot | 1–2 cases | wiring bugs, a broken or too-lenient grader, a non-deterministic judge, a substituted model, a scope that does not match the plan |
| **2 — zero triage** | SKILL.md Step 2.3, **before** the noise derivation and the census | the baseline rows + errors sidecar | infra failures scored as model failures; a census built on wrong grader verdicts |
| **3 — climbability** | SKILL.md Step 2.6, after Step 2.4 derives `runs`/`min_delta` | both baselines | an eval that cannot resolve the win being sought; a saturated metric; val and test that are not the same population |

Gate 1 costs a case or two against a pilot that costs `3 × corpus`. Gate 3 costs nothing but
arithmetic. Both are cheap relative to discovering the problem after five iterations.

**How to report what you find.** These checks are directives to you; the message to the human is
not. The person who built the eval almost always has context you lack — a constraint, a deadline, a
deliberate trade-off. So: frame findings as observations and suggestions ("something worth looking
at is…", "one thing that can cause trouble here is…") rather than verdicts; lead with the things
likely to make the numbers actively misleading and separate them from the things that only add
noise; cite the file, the case id, the line; **say when things are fine** — an audit that finds
nothing is a valid result, so do not manufacture concerns; and where a finding is a small change
you can just make, offer to make it rather than only flagging it.

---

## Gate 1 — harness trust (SKILL.md Step 2.2)

Run every check. Each lists its procedure, its pass criterion, and what to do when it fails.

### Oracle probe

**Procedure.** Take ~5 datapoints that carry a reference. Feed the reference itself through `grade`
as though it were the model's output. Where the corpus has no reference, hand-write a known-good
answer for 3 rows.

**Pass.** Near 1.0 on essentially all of them.

**On failure.** The grader or the harness is broken, not the code under test — a reference answer
that cannot pass the grader means nothing else measured today means anything. **STOP**, find the
bug, re-run.

### Null-baseline probe

**Procedure.** Feed the grader three known negatives: an empty string, `"I don't know"`, and a
confident answer to a *different* question.

**Pass.** All three fail.

**On failure.** The grader is too lenient — it is rewarding a shape rather than a substance, and
every score it produces is partly measuring that shape. **STOP** and tighten the rubric (with the
user's approval; `evaluators` is theirs verbatim).

### Judge determinism

**Procedure.** Run `grade` twice over the same stored outputs for ~5 datapoints. Count how many
verdicts changed. Record the share as `config.json.judge_flip_rate`.

**Pass.** Any value is acceptable — this check measures rather than gates. A deterministic
ground-truth metric gives `0.0` and the rest of this bullet is moot.

**What to do with it.** A non-zero flip rate is grader variance sitting on top of model variance,
and it is a **floor on `min_delta`**: a threshold finer than the grader's own reproducibility can
never fire honestly. Feed it into the Step 2.4 derivation, and let it carry the argument for
`max_runs ≥ 5` that `_metric_selection` already makes on principle.

### Ground-truth isolation

**Procedure.** Assert that `generate` received a row with no `expected_output` / `reference` /
`gold` / `label` key (the template redacts it; this confirms the wiring was not undone), and that
`generate` does not re-open the cache file or the dataset.

**Pass.** No reference field reachable from the code under test.

**On failure.** **STOP.** Code under optimization pressure finds `line["expected_output"]`
eventually, and a loop that finds it will "win" without improving anything.

### Mechanism wired

**Procedure.** Whatever the score is supposed to depend on — a tool, a retrieval step, a file the
code reads — disable it and re-run one case; enable it and confirm it engages in the output.

**Pass.** The score moves when the mechanism is removed.

**On failure.** The eval is not measuring the lever you plan to pull, and no amount of tuning will
show up. Say so before spending an iteration, and either fix the wiring or point the run at a
different lever.

### Served model

**Procedure.** Read `model` from the **response** on one smoke case, not from config, and compare it
to what was asked for. The harness surfaces this every run as `models_seen`.

**Pass.** It matches (allowing documented alias → snapshot resolution).

**On failure.** A provider fallback or capacity reroute means the score was served by a model nobody
chose, and the comparison measures nothing. **STOP.**

### Resolved-scope print

**Procedure.** Before the pilot, print what the run actually resolved: case count × reps × split ×
model × estimated cost. Compare against the intake recap.

**Pass.** It matches the approved plan.

**On failure.** A dry run that resolves a different case count than the plan is a **stop**, not a
warning — something upstream disagrees about what the corpus is.

### Headline recomputes from raw rows

**Procedure.** Recompute the mean from `eval_results.<split>.jsonl` yourself and compare it to the
`mean` the harness printed.

**Pass.** Equal to 1e-9.

**On failure.** A mean-vs-sum or per-rep-vs-per-case mixup produces phantom breakthroughs that look
exactly like a result until someone hand-checks. Run this check **at the gate and again every
iteration**; it costs nothing.

### Tier-1 corpus checks

**Procedure.** One short script over the hydrated cache reporting: exact- and near-duplicate rate,
stratum balance, input and reference length distributions, schema validity, missing fields,
malformed rows.

**Pass.** Nothing anomalous, or anomalies the user recognises and accepts.

**On failure.** Report per the posture above. A corpus problem is a **sibling skill's** job, not
this loop's — name it and stop rather than rebuilding the eval mid-climb.

*(Deliberately not run: a per-case LLM auditor over the whole corpus. It costs roughly N cheap-model
calls and largely duplicates the Step 2.5 census fan-out, which already reads every failing
datapoint. Offer it as an opt-in when the user suspects the cases themselves; never default to it.)*

### Conditional — build variance

**Only when** the change under test *builds* something the eval then scores (a memory store, a
retrieval index, a synthesized corpus) rather than being read directly. Then reps measure only the
noise of scoring a fixed build, and the build's own run-to-run variance — often the larger term — is
sampled once per variant and invisible to every gate.

**Procedure.** Rebuild the artifact 2–3 times with the code **unchanged** and score each build.

**Pass.** The spread across no-change rebuilds is smaller than a plausible one-edit effect.

**On failure.** Build K times per variant and compare build-pooled means, or move the lever closer
to the score. Adding reps over one build cannot see this.

---

## Gate 2 — zero triage (SKILL.md Step 2.3)

The procedure lives in `rubrics.md` **Baseline failure census → the triage that precedes it**,
because it is part of the census contract. It runs *before* Step 2.4 rather than inside the census,
because dropping harness-error rows changes the denominator and therefore the `stdev` the noise
derivation reads. In summary, and in this order:

1. Classify every exactly-zero baseline datapoint as **harness error** or **grader verdict**, using
   `errors.val.jsonl` plus a read of the output.
2. Drop the harness-error rows from the denominator on **both** splits, for every variant, and
   re-score the baseline after the drop. Record the counts in `data_note`.
3. Spot-check the lowest-scoring grader verdicts. More than roughly one in ten looking like grader
   errors → fix the rubric or the reference **with the user's explicit approval**, re-grade in place
   with `AUTO_EXP_REGRADE=1`, bump `grader_version`, and only then run the describers.
4. If the corpus itself is the problem, name the sibling skill that owns eval construction and stop.

---

## Gate 3 — climbability (SKILL.md Step 2.6)

### The three numbers

Put these in front of the user, together, every run:

1. **Noise floor** on the **test** split — the half-width of the paired-difference 95% CI at the
   current `n × runs`, from the paired bootstrap. (For a binary pass rate the rule of thumb is
   `≈ 1/√(n·R)`: 25 cases × 2 reps ≈ ±14 points, 100 × 2 ≈ ±7. Use the real bootstrap number, not
   the rule of thumb; quote the rule only to explain the shape.)
2. **Headroom** — `1 − baseline_mean` for a maximize goal, and the corresponding distance to the
   floor for a minimize goal. State that this is the theoretical ceiling and that the
   census-implied *reachable* ceiling is smaller.
3. **The smallest improvement the user would actually act on** — the `min_shippable_delta` intake
   field. Asked explicitly, never assumed.

**If the noise floor exceeds either of the other two, the loop cannot show the win being sought, no
matter how good the changes are.** Say so now, with the numbers, and offer the levers in cost order:
more runs (cheapest, and the paired design makes them go further), more cases, or a finer-grained
metric than a binary one. Getting this wrong is expensive in exactly the way that is hardest to
notice: every iteration returns a number, none of them mean anything.

### Saturation class

Classify the baseline from numbers the loop already computes (`score_distribution.n/zero/perfect`,
min, max, mean). The classes are the ones `agent-observability-experiment-analyzer` uses, so a
reader moving between the two skills sees the same vocabulary:

| class | condition | disposition |
|---|---|---|
| `always_zero` | `max == 0` | the harness or the metric is broken, or the task is impossible. **STOP and investigate** — never start a climb here |
| `perfect` | `min == 1` | no headroom at all. **STOP** |
| `saturated` | `mean ≥ 0.99` and `min < 1` | the remaining variance is format quirks and grader tie-breaks rather than capability. The climb can still move cost or latency — say so, and offer to switch `goal.target` |
| `struggling` | `mean < 0.70` | highest diagnostic value. Proceed |
| `interesting` | `0.70 ≤ mean < 0.99` and `min < max` | proceed |

Do not infer a metric's meaning from its name; classify from the distribution.

### Val and test are the same population

**Procedure.** Compare the two baseline means: they must agree within `2 · SE_diff`.

**On failure.** Exactly **one** re-draw is allowed, and only here — before any change exists. The
procedure (new seed, new dataset names, old ids recorded as superseded, both baselines re-run) is in
`rubrics.md` **Held-out split**. A second failure is a finding, not a second re-draw: report that
the corpus is heterogeneous and ask whether to proceed with the caveat, enlarge the corpus, or
abort.

### Selection-set disclosure

State it once, here, and again in the final report: because `test` picks the winner every iteration,
it is a selection set, and its delta overstates the true gain by an amount that grows with the
number of iterations. The end-of-run pooled confirm and the round count in the report are the
mitigations; a three-way split is the stronger one when the corpus can afford it.

### Gate verdict

Write `config.json.preflight` as one of:

- `GO` — every check passed;
- `GO with caveat: <text>` — something is imperfect and the user has seen it and accepted it;
- `STOP: <reason>` — do not start the loop; ask.

State the verdict and its evidence to the user before Step 3. A `STOP` that gets narrated and then
stepped over is the same as no gate at all.
