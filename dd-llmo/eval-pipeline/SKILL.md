---
name: eval-pipeline
description: End-to-end pipeline from unlabeled ml_app traces to a bootstrapped evaluator suite. Runs trace classification → root cause analysis → eval bootstrap in sequence with user checkpoints. Use when user says "run the eval pipeline", "go from traces to evals", "bootstrap evals end to end", "classify then RCA then bootstrap", "build an eval set from scratch", or wants a guided walkthrough from production data to evaluator code.
---

# Eval Pipeline — Classify → RCA → Bootstrap

Walks from unlabeled production LLM trace data to a ready-to-use evaluator suite in three phases, with user checkpoints between each. No pre-existing evals or labeled data required.

```
eval-session-classify (ml_app mode)
         ↓
   eval-trace-rca (from classifications)
         ↓
   eval-bootstrap (from RCA output)
```

## Usage

```
/eval-pipeline <ml_app> [--timeframe <window>] [--trace-limit <N>] [--data-only]
```

Arguments: $ARGUMENTS

### Inputs

| Input | Required | Default | Description |
|-------|----------|---------|-------------|
| `ml_app` | Yes | — | LLM app to analyze end to end |
| `--timeframe` | No | `now-7d` | Lookback window for trace sampling and RCA |
| `--trace-limit` | No | `20` | Max traces to classify in Phase 1 |
| `--data-only` | No | off | Pass through to eval-bootstrap: emit JSON spec instead of Python SDK code |

If `ml_app` is not provided, ask the user before proceeding.

---

## Phase 1: Trace Classification

Follow the **eval-session-classify** skill in **ml_app mode**, using:
- `ml_app` = the provided ml_app
- `timeframe` = the provided timeframe
- `trace_limit` = the provided trace_limit

Run the complete ml_app mode workflow as defined in that skill (Steps M1 through M4):
sample traces → read content → classify each → emit per-trace blocks → emit summary.

**Output the full classification output**, including all `## Trace: <id>` blocks and the final
`# Session Classification Summary` section. Do not summarize or truncate this output —
the downstream phase detection depends on the full text being present in context.

---

### CHECKPOINT 1

After the `# Session Classification Summary` is output, pause and present:

```
## Phase 1 Complete

[verdict distribution table]
[failure mode frequency table]

Before I continue to root cause analysis:
- Do these failure patterns look right?
- Any traces you'd like to exclude from the RCA?
- Any failure modes to focus on or ignore?

Type "continue" to proceed, or give me adjustments.
```

**Wait for explicit user confirmation before proceeding.**

If the user excludes specific traces: remove them from the failure bucket by noting "excluded by user" — do NOT re-classify.
If the user asks to re-run with different parameters: re-run Phase 1 with the new parameters.
If Phase 1 yielded zero failures: surface this explicitly and offer to retry with a wider timeframe or stop.

---

## Phase 2: Root Cause Analysis

Follow the **eval-trace-rca** skill in **"from classifications"** mode.

The `# Session Classification Summary` from Phase 1 is in context. The skill detects it
automatically via its Phase 0 check and enters Step 0S (failure bucket extraction).
Run the full workflow through Phase 6 (the compiled RCA report).

**Output the full RCA report** as defined in eval-trace-rca's Output Format section.
Do not summarize — the full report must be in context for Phase 3's detection to work.

---

### CHECKPOINT 2

After the RCA report is output, pause and present:

```
## Phase 2 Complete

[the Phase 6 RCA report is above]

Before I generate evaluators:
- Do these root causes look accurate?
- Any failure modes to add, remove, or reframe?
- Which root causes should the evaluators target?

Type "continue" to proceed, or give me adjustments.
```

**Wait for explicit user confirmation before proceeding.**

If the user adjusts the taxonomy: note the changes and apply them before continuing.

---

## Phase 3: Eval Bootstrap

Follow the **eval-bootstrap** skill in **"from RCA"** mode.

The RCA report from Phase 2 is in context. The skill detects the `Failure Taxonomy` heading
automatically and enters its "from RCA" path in Phase 0.

If `--data-only` was specified: pass it through — the skill will emit a JSON spec instead
of Python SDK code.

**The eval-bootstrap skill has its own mandatory proposal checkpoint** (the evaluator suite
proposal before code generation). Honor it — do not skip or auto-confirm it.

---

## Final Summary

After Phase 3 completes, present:

```markdown
# Eval Pipeline Complete

**App**: `<ml_app>`  |  **Timeframe**: <timeframe>

| Phase | Output |
|-------|--------|
| 1. Classification | <N> traces sampled, <F> failures identified |
| 2. Root Cause Analysis | <K> failure modes, <M> root causes diagnosed |
| 3. Eval Bootstrap | <J> evaluators → `<output_path>` |

## Key findings

[3–5 bullets: most important failure patterns and what the evaluators capture]

## Next steps

1. Review the generated evaluators at `<output_path>`
2. Run an offline experiment to validate eval quality
3. Once validated, configure as production evals in Datadog
```

---

## Orchestration Rules

- **Always checkpoint before advancing** between phases. Never auto-proceed.
- **Never truncate sub-skill outputs** — downstream phase detection depends on the full text being in context.
- **Phase isolation**: if the user wants to re-run a single phase, re-run only that phase and its downstream phases.
- **Carry context forward**: the output of each phase is the input for the next. Present the full output of each sub-skill before showing the checkpoint prompt.
