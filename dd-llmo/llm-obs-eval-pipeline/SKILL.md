---
name: llm-obs-eval-pipeline
description: End-to-end pipeline from unlabeled ml_app traces to a bootstrapped evaluator suite. Runs trace classification → root cause analysis → eval bootstrap in sequence with user checkpoints. Use when user says "run the eval pipeline", "go from traces to evals", "bootstrap evals end to end", "classify then RCA then bootstrap", "build an eval set from scratch", or wants a guided walkthrough from production data to evaluator code.
---

## Backend

**Detection** — At the start of every invocation, before taking any action, determine which backend to use:

1. If the user passed `--backend pup` anywhere in their invocation → use **pup mode** immediately, regardless of whether MCP tools are present. Skip steps 2–4.
2. Check whether MCP tools are present in your active tool list. The canonical signal is whether `mcp__datadog-llmo-mcp__search_llmobs_spans` appears in your available tools.
3. If MCP tools are present → use **MCP mode** throughout. Call MCP tools exactly as named in the sub-skill workflow sections.
4. If MCP tools are absent → check whether `pup` is executable: run `pup --version` via Bash. A JSON response containing `"version"` confirms pup is available.
5. If pup responds → use **pup mode** throughout. Each sub-skill carries its own Tool Reference appendix with the full MCP→pup mapping.
6. If neither is available → stop and tell the user:
   > "Neither the Datadog MCP server nor the pup CLI is available. Connect the MCP server (`claude mcp add --scope user --transport http datadog-llmo-mcp 'https://mcp.datadoghq.com/api/unstable/mcp-server/mcp?toolsets=llmobs'`) or install pup."

`--backend pup` is accepted anywhere in the invocation arguments. Strip it from args before passing to sub-skills, but carry the pup-mode decision forward — sub-skills must also operate in pup mode for the entire pipeline run.

**Sub-skill backend propagation**: The backend detected at pipeline startup applies to all three sub-skills (session-classify → trace-rca → eval-bootstrap). Do not re-detect per phase. Announce once at startup:
- MCP mode: "(Running in MCP mode — all features available.)"
- pup mode: "(Running in pup mode — pup commands used throughout. RUM signals use `pup rum aggregate`. Notebooks use `pup notebooks create/edit`. All features available.)"

**pup invocation rules:**
- Invoke via Bash: `pup llm-obs <subcommand> [flags]`
- pup always outputs JSON. Parse directly — no content-block unwrapping (unlike MCP results).
- If pup returns an auth error, tell the user to run `pup auth login` and stop.
- Parallelization: issue multiple Bash tool calls in a single message (one pup command per call).
- Time flags: pup accepts bare duration strings (`1h`, `7d`, `30m`) and RFC3339 timestamps. Do **not** use `now-`-prefixed strings — strip the prefix when converting from a skill `--timeframe` argument: `now-7d` → `7d`, `now-24h` → `24h`, `now-30d` → `30d`.

**Intent tagging:** On every MCP tool call, prefix `telemetry.intent` with `skill:llm-obs-eval-pipeline — ` followed by a description of why the tool is being called.

# LLM Obs Eval Pipeline — Classify → RCA → Bootstrap

Walks from unlabeled production LLM trace data to a ready-to-use evaluator suite in three phases, with user checkpoints between each. No pre-existing evals or labeled data required.

```
llm-obs-session-classify  (ml_app mode)
           ↓
  llm-obs-trace-rca  (from classifications)
           ↓
  llm-obs-eval-bootstrap  (from RCA output)
```

## Usage

```
/llm-obs-eval-pipeline <ml_app> [--timeframe <window>] [--trace-limit <N>] [--data-only] [--publish]
```

Arguments: $ARGUMENTS

### Inputs

| Input | Required | Default | Description |
|-------|----------|---------|-------------|
| `ml_app` | Yes | — | LLM app to analyze end to end |
| `--timeframe` | No | `now-7d` | Lookback window for trace sampling and RCA |
| `--trace-limit` | No | `20` | Max traces to classify in Phase 1 |
| `--data-only` | No | off | Pass through to llm-obs-eval-bootstrap: emit JSON spec instead of Python SDK code |
| `--publish` | No | off | Pass through to llm-obs-eval-bootstrap: publish online LLM-judge evaluators to Datadog |

If `ml_app` is not provided, ask the user before proceeding.

---

## Phase 1: Trace Classification

Follow the **llm-obs-session-classify** skill in **ml_app mode**, using:
- `ml_app` = the provided ml_app
- `timeframe` = the provided timeframe
- `sample_limit` = the provided trace-limit

Run the complete ml_app mode workflow as defined in that skill (Steps M1 through M3):
sample spans → classify each → emit per-unit compact blocks → emit summary.

**Output the full classification output**, including all compact per-unit blocks and the final
`# Session Classification Summary` section. Do not summarize or truncate this output —
downstream phase detection depends on the full text being present in context.

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

- If the user excludes specific traces: mark them as "excluded by user" and drop them from the failure bucket. Do NOT re-classify.
- If the user asks to re-run with different parameters: re-run Phase 1 with the new parameters.
- If Phase 1 yielded zero failures: surface this explicitly and offer to retry with a wider timeframe or stop.

---

## Phase 2: Root Cause Analysis

Follow the **llm-obs-trace-rca** skill.

The `# Session Classification Summary` from Phase 1 is in context. The skill detects it automatically via its Phase 0 Step 0S check and enters the "from classifications" path — it extracts the failure bucket, presents the Classification Overview, and proceeds directly to Phase 2 (open coding) without running its own Phase 1 span search.

Run the full workflow through Phase 6 (the compiled RCA report). **Output the full RCA report** — do not summarize. The full report must be in context for Phase 3's detection to work.

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

If the user adjusts the taxonomy: incorporate the changes before continuing to Phase 3.

---

## Phase 3: Eval Bootstrap

Follow the **llm-obs-eval-bootstrap** skill.

The RCA report from Phase 2 is in context. The skill detects the `## Failure Taxonomy` heading automatically and enters its "from RCA" path in Phase 0.

Pass through any flags:
- `--data-only` → emit a JSON spec instead of Python SDK code
- `--publish` → publish online LLM-judge evaluators directly to Datadog

**The llm-obs-eval-bootstrap skill has its own mandatory proposal checkpoint** (the evaluator suite proposal before code generation). Honor it — do not skip or auto-confirm it.

---

## Final Summary

After Phase 3 completes, present:

```markdown
# LLM Obs Eval Pipeline Complete

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

- **Always checkpoint before advancing.** Never auto-proceed between phases.
- **Never truncate sub-skill outputs.** Downstream phase detection depends on the full text being in context.
- **Phase isolation**: if the user wants to re-run a single phase, re-run only that phase and its downstream phases.
- **Carry context forward**: the output of each phase is the input for the next. Present the full output of each sub-skill before showing the checkpoint prompt.

---

## Tool Reference

This appendix applies only in **pup mode**. Each sub-skill also carries its own Tool Reference with the same mappings — consult them for full parameter details. The tables below are a quick reference for pipeline-level orientation.

### Spans and traces

| MCP Tool | pup Command |
|---|---|
| `search_llmobs_spans(...)` | `pup llm-obs spans search --query "@ml_app:A [other_filters]" [--from F] [--to T] [--limit N] [--summary]` — **always use `--query "@ml_app:A"`**; `--ml-app A` is unreliable. |
| `get_llmobs_span_details(...)` | `pup llm-obs spans get-details --trace-id T --span-ids S1,S2,...` |
| `get_llmobs_span_content(...)` | `pup llm-obs spans get-content --trace-id T --span-id S --field F [--path P]` |
| `get_llmobs_trace(...)` | `pup llm-obs spans get-trace --trace-id T [--include-tree]` |
| `get_llmobs_agent_loop(...)` | `pup llm-obs spans get-agent-loop --trace-id T [--span-id S]` |
| `find_llmobs_error_spans(...)` | `pup llm-obs spans find-errors --trace-id T` |
| `expand_llmobs_spans(...)` | `pup llm-obs spans expand --trace-id T --span-ids S1,S2,...` |

### Evaluators

| MCP Tool | pup Command |
|---|---|
| `list_llmobs_evals()` | `pup llm-obs evals list` |
| `get_llmobs_evaluator(eval_name)` | `pup llm-obs evals get-evaluator EVAL_NAME` |
| `get_llmobs_eval_aggregate_stats(...)` | `pup llm-obs evals get-aggregate-stats EVAL_NAME [--ml-app A] [--from F] [--to T]` |
| `create_or_update_llmobs_evaluator(...)` | `pup llm-obs evals create-or-update EVAL_NAME --file /tmp/eval_EVAL_NAME.json` (see eval-bootstrap Tool Reference for flat schema details) |

### RUM

| MCP Tool | pup Command |
|---|---|
| `analyze_rum_events(event_type="view", filter="@usr.email:EMAIL", ...)` | `pup rum aggregate --user-email EMAIL --from F --to T --compute count --group-by @session.id` |
| `analyze_rum_events(event_type="action", filter="@action.type:custom ...", ...)` | `pup rum aggregate --user-email EMAIL --query "@action.type:custom" --from F --to T --compute count --group-by @evt.name` |

### Notebooks

| MCP Tool | pup Command |
|---|---|
| `create_datadog_notebook(name, cells, ...)` | `pup notebooks create --title "TITLE" --file /tmp/nb_cells.json` |
| `edit_datadog_notebook(id, cells, append_only=true)` | `pup notebooks edit NOTEBOOK_ID --file /tmp/nb_cells.json` |
- **MCP result parsing safety**: Before writing any script (Python, jq, etc.) that iterates over or accesses fields in an MCP tool result, inspect the raw structure first — check `type(result)`, top-level keys, and whether the payload is nested inside a content block (e.g. `[{'type': 'text', 'text': '<json>'}]`). Extract and `json.loads()` the inner payload if needed. Never assume MCP results are bare dicts or lists.
