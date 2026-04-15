---
name: eval-trace-rca
description: Root cause analysis on production LLM traces using eval judge results as signal. Diagnoses why the user's application is failing. Use when user says "eval RCA", "root cause analysis", "analyze eval failures", "why is my eval failing", "why is my app failing", "failure analysis", "diagnose eval", "what's wrong with my app", or wants to understand production failure patterns. Works with ml_app or eval name.
---

# Eval RCA — Root Cause Analysis from Production Trace Signal

Perform structured root cause analysis on production LLM traces. Supports two modes depending on what signal is available:

| Mode | Signal used | When to use |
|------|-------------|-------------|
| **Eval Signal** | LLM judge verdicts and reasoning (pass/fail rates, scoring) | App has evaluators configured; goal is to understand *why* evals are failing |
| **Error Signal** | Runtime errors in traces (`@status:error`, error types, stack traces) | No evals configured, or user explicitly wants to analyze crashes/exceptions/tool failures |

If the mode cannot be inferred from context, ask **one clarifying question** before proceeding: "Would you like me to analyze eval pass/fail patterns, or look at runtime errors and exceptions in traces?"

## Methodology

**Context → Observe → Open Coding → Axial Coding → Root Cause Analysis → Recommendations**

## Usage

```
What's wrong with <ml_app> based on its evals over the last <timeframe>
Analyze eval failures for <eval_name> over the last <timeframe>
Look at the errors on <ml_app> over the last <timeframe>
```

### Inputs

| Input | Required | Default | Description |
|-------|----------|---------|-------------|
| `ml_app` | One of these | — | The application to analyze. |
| `eval_name` | One of these | — | A specific evaluator to focus on (always Eval Signal mode). |
| `timeframe` | No | `now-24h` | How far back to look |

Either `ml_app` or `eval_name` must be provided. If neither is given, ask the user.

## Available Tools

### Eval Discovery & Overview

| Tool | Purpose |
|------|---------|
| `list_llmobs_evals` | Discover all configured evals for an `ml_app`. Returns eval names and types. Use when starting from `ml_app`. |
| `get_llmobs_eval_aggregate_stats` | Pass/fail rate, score distribution, or categorical breakdown for an eval over a time window. |
| `get_llmobs_eval_config` | Custom eval's prompt template, assessment criteria, output schema. Returns nil for OOTB evals. |

### Trace & Span Exploration

| Tool | Purpose |
|------|---------|
| `search_llmobs_spans` | Find spans by eval presence, tags, span kind, query syntax. Paginate with cursor. |
| `get_llmobs_span_details` | Metadata, evaluations (scores, labels, reasoning), and **`content_info`** map showing available fields + sizes. |
| `get_llmobs_span_content` | Actual content for a span field. Supports JSONPath via `path` param for targeted extraction. |
| `get_llmobs_trace` | Full trace hierarchy as span tree with span counts by kind. |
| `find_llmobs_error_spans` | All error spans in a trace with error type, message, stack, and propagation context. |
| `expand_llmobs_spans` | Load children of collapsed trace nodes. |
| `get_llmobs_agent_loop` | Chronological agent execution timeline (LLM calls, tool invocations, decisions). |

### Key `get_llmobs_span_content` Patterns

Use the `path` parameter to extract targeted data without fetching full payloads:

| Field | Path | What you get |
|-------|------|-------------|
| `messages` | `$.messages[0]` | System prompt (first message, usually `system` role) |
| `messages` | `$.messages[-1]` | Last assistant response |
| `messages` | *(no path)* | Full conversation including tool calls |
| `input` / `output` | — | Span I/O |
| `documents` | — | Retrieved documents (RAG apps) |
| `metadata` | — | Custom metadata (prompt versions, feature flags, user segments) |

### How to Use `search_llmobs_spans`

**Primary query pattern** — eval presence:

```
@evaluations.custom.<eval_name>:*
```

You can only query for eval *presence*, not specific results (e.g., `@evaluation_assessments.custom.<name>:fail` will NOT work). To determine pass vs fail, read the verdict from `get_llmobs_span_details`.

Additional filters combine with space (AND): `@evaluations.custom.<name>:* @status:ok`. Dedicated params (`span_kind`, `root_spans_only`, `ml_app`) work alongside `query`, but `query` takes precedence over `tags`.

### Parallelization Rules

1. **`get_llmobs_span_details`**: Group span_ids by trace_id. One call per trace_id with ALL its span_ids. Issue ALL calls for a page in a **single message**.
2. **`get_llmobs_span_content`**: Each call is independent — always issue ALL in a single message.
3. **`get_llmobs_trace` / `find_llmobs_error_spans` / `get_llmobs_agent_loop`**: Parallelize across different traces in a single message.
4. **Pipeline parallelism**: Start `get_llmobs_span_details` for page 1 results immediately — don't wait to collect all pages.

---

## Analysis Workflow

**Output discipline**: Phases 0–5 are internal analysis. The only user-facing outputs during these phases are the brief checkpoint updates (Phases 2, 3) and the Eval Overview (Phase 1a). Do NOT narrate your reasoning, summarize intermediate findings, or output Phase 4 deep-dive results as prose. All detailed findings go exclusively into the Phase 6 report.

---

### Phase 0: Resolve Inputs & Mode

1. If neither `ml_app` nor `eval_name` provided → ask the user.
2. If `timeframe` not provided → default to `now-24h`.
3. **Resolve mode**:
   - `eval_name` provided → **Eval Signal**. Proceed to Phase 1 for the specified eval.
   - User explicitly mentions errors, exceptions, crashes, runtime failures, or "look at errors" → **Error Signal**.
   - `ml_app` provided, no explicit signal → call `list_llmobs_evals(ml_app)`:
     - Evals exist → **Eval Signal** (default). Get aggregate stats for each eval in parallel.
     - No evals configured → **Error Signal** automatically; inform the user.
     - Evals exist but context is ambiguous → ask one question: "Would you like me to analyze eval pass/fail patterns, or look at runtime errors and exceptions in traces?"
4. Note any additional filters (tags, span_kind) for all subsequent queries.

---

### Phase 1: Gather Context & Collect Evidence

**Goal**: Get the big picture, sample failure spans, and determine the app profile.

> **If mode = Error Signal**, replace Steps 1a–1b below with the Error Signal path:
>
> **Step 1a (Error Signal): Sample error spans**
> Call `search_llmobs_spans(query="@ml_app:{ml_app} @status:error", from=timeframe, limit=50)`. Paginate until ≥ 30 error spans or no more pages. Group spans by `error_type` tag to build an initial frequency table. Report: `Found {N} error spans across {K} distinct error types: {type1} ({count}), {type2} ({count}), ...`
>
> **Step 1b (Error Signal): Fetch stack traces per error type**
> For the top 3–4 error types, pick 2–3 representative trace IDs and call `find_llmobs_error_spans(trace_id)` in parallel. Extract the error message, stack trace, and the span kind/name where the error originated. Note whether errors propagate from children to parents (cascade vs. isolated).
>
> **Step 1c (Error Signal): Determine app profile**
> Inspect span names, kinds, and tags from the error spans to understand the app structure (same as Eval Signal Step 1c). Then proceed to Phase 2 (Open Coding) treating each distinct error pattern as a failure category — the error_type + origin span + triggering condition is the "judge reasoning" equivalent.
>
> The Output Format for Error Signal mode uses the same structure but replaces **Eval Overview** / **Judge reasoning** fields with **Error type counts** / **Stack trace excerpts**.

#### Step 1a: Eval overview (parallel)

For each eval being analyzed, call both in parallel:

- `get_llmobs_eval_aggregate_stats(eval_name, from, to)`
- `get_llmobs_eval_config(eval_name)`

When analyzing multiple evals (ml_app entry point), issue ALL calls for all evals in a single parallel batch.

**Interpret aggregate stats:**

- **`total_count == 0`** → Note "no data." Skip this eval.
- **Boolean with `pass_rate == 1.0`** → Note "100% pass." Skip unless it's the only eval.
- **Boolean with failures** → Note counts and pass_rate. Continue.
- **Score eval with assessment criteria** → Note distribution and pass/fail counts. Continue.
- **Score eval WITHOUT assessment criteria** → Still analyze. Use score distribution to identify low-performers: treat bottom quartile as "failures," or scores below the median if the distribution is bimodal. Note that the threshold is inferred, not configured.
- **Categorical with assessment criteria** → Note top_values and pass/fail. Continue.
- **Categorical WITHOUT assessment criteria** → Still analyze. Examine the value distribution. Ask user which categories represent failures if ambiguous, or infer from context (e.g., "error", "incomplete", "off_topic" are likely failures).

**Interpret eval config:**

- **Config returned** (custom/BYOP) → Store `prompt_template`, `assessment_criteria`, `parsing_type`, `output_schema`.
- **Config nil** (OOTB) → Note prompt is not inspectable.

**Report to user:**

```
## Eval Overview: `{eval_name}`

**Timeframe**: {from} → {to}  |  **Type**: {boolean/score/categorical}  |  **Eval**: {Custom/OOTB}
**Total spans**: {total_count}  |  **Pass rate**: {pass_rate}% ({pass_count}/{fail_count})
{If no assessment criteria: **Note**: No pass/fail criteria configured. Using inferred threshold: {threshold_description}.}

{If custom: 1-2 sentence summary of what the eval measures.}
```

When multiple evals are being analyzed, present a combined overview table first:

```
## Eval Overview: `{ml_app}`

| Eval | Type | Total | Pass Rate | Status |
|------|------|------:|:---------:|--------|
| eval_1 | boolean | 4,891 | 37.3% | ⚠ Investigating |
| eval_2 | score | 1,200 | — (no criteria) | ⚠ Investigating (inferred threshold) |
| eval_3 | boolean | 500 | 99.2% | ✓ Healthy |
```

#### Step 1b: Collect failure spans

For each eval being analyzed:

1. `search_llmobs_spans(query="@evaluations.custom.<eval_name>:*", from, limit=50)` + user filters.
   - Paginate until ≥15–20 failures OR no more pages. Cap at 200 spans total per eval.

2. `get_llmobs_span_details` per trace_id batch (follow Parallelization Rules).
   - Extract: **assessment**, **value**, **reasoning**, **span_id**, **trace_id**, **span_kind**, **content_info**.

3. Separate into pass/fail buckets.
   - **With assessment criteria**: Use configured pass/fail threshold.
   - **Without assessment criteria**: Use the inferred threshold from Step 1a (bottom quartile for scores, inferred categories for categorical). Label these as "inferred failures" in the report.

4. Report: `Collected {N} spans for {eval_name}: {pass_count} passing, {fail_count} failing.`

5. Edge cases: 0 failures in sample → try different time slice. < 10 failures → proceed, note low confidence.

#### Step 1c: Determine App Profile & Eval Scope

Inspect `content_info` and `span_kind` across collected spans. This drives the Phase 4 investigation strategy.

**App profile** (from content_info):

| Signal | App profile | Phase 4 strategy |
|--------|------------|-----------------|
| `content_info` has `messages` | LLM/chat app | Extract system prompt via `messages[0]`, check conversation flow |
| `content_info` has `documents` | RAG app | Check retrieval quality alongside LLM output |
| Trace contains `agent` span kind | Agent app | Use `get_llmobs_agent_loop` for decision tracing, analyze tool usage |
| `messages.count > 10` | Long conversation | Check for context overflow |
| `content_info` has `metadata` | Has custom metadata | Check for clustering by metadata values (prompt version, etc.) |

**Evaluated span kind** — determines where the root cause likely lives:

| Eval runs on... | Symptom surfaces here | But root cause is often in... |
|----------------|----------------------|------------------------------|
| `llm` span | Bad LLM response | **Parent** agent (bad instructions), **sibling** retrieval (bad context), **sibling** tool (bad data fed to LLM) |
| `agent` span | Bad orchestration | **Child** spans (wrong tool calls, bad routing), full agent loop |
| `tool` span | Bad tool result | **Parent** LLM (passed wrong parameters), tool implementation |
| `workflow` span | Bad overall output | **Child** sub-spans (which step first deviated?) |
| `retrieval` span | Bad retrieval | Query construction (parent), index/embedding config (outside trace) |

**Key insight**: The eval judges a single span in isolation. The judge reasoning is a *symptom report*, not a diagnosis. The root cause often lives in a different span in the same trace — a parent that gave bad instructions, a sibling that provided bad context, or a child that made a wrong decision. Phase 4 must navigate the trace tree to find it.

---

### Phase 2: Open Coding — Initial Failure Categorization

**Goal**: Read failure evidence and propose initial, concrete failure categories. When analyzing multiple evals, pool all failures together — categories should describe app behaviors, not which eval caught them. Note which eval(s) flagged each category.

**Shortcuts**:

- **< 15 failures**: Combine Phases 2 and 3 into one pass. Still produce the checkpoint output.
- **> 80% share the same reasoning**: Skip to Phase 4 with dominant pattern. Still output checkpoint.
- **> 50 failures**: Sample ~50, build taxonomy, then spot-check 10–15 more.

1. **Use reasoning from Step 1b** — do NOT re-fetch.
   - Only call `get_span_content(field="input"/"output")` for spans where reasoning is insufficient (generic or empty).

2. **If eval config loaded**, distinguish early:
   - **App failures**: Output genuinely violates the eval's criteria
   - **Eval failures**: Output seems reasonable but eval criteria are too strict/ambiguous

3. **Each pattern must be specific**: "Agent called search instead of calculator for price computation" — NOT "tool issue."

#### MANDATORY CHECKPOINT — Brief Progress Update

**You MUST output a brief summary before proceeding.** Keep it short — the full report in Phase 6 is the real deliverable. No descriptions or examples here.

```
**Open coding**: {N} failures → {K} initial categories: {Category1} ({count}), {Category2} ({count}), ...
```

---

### Phase 3: Axial Coding — Refine Failure Taxonomy

**Goal**: 3–8 final categories, ranked by impact.

1. **Merge**: Categories with < 3 occurrences → parent category or drop as noise.
2. **Split**: Categories with > 30% of failures → more specific sub-categories. Pull additional span content if needed.
3. **Validate**: 2–3 representative examples per category confirm the label fits.
4. **Rank**: `priority = count × severity` (severity: high / medium / low).

#### MANDATORY CHECKPOINT — Brief Progress Update

**You MUST output the refined taxonomy before proceeding.** Keep it short — show what changed and the ranked list. Details go in the final report.

```
**Axial coding**: {merges/splits/drops}. Final categories:
1. {Category} ({count}, {pct}%) — {severity}
2. ...
```

---

### Phase 4: Root Cause Analysis — Navigate from Symptom to Root Cause

**Goal**: The eval flagged a span. That's the symptom. Now find the actual root cause by navigating the trace tree — it's often in a different span.

For each of the top 3 categories, pick 2–3 representative traces:

#### Step 4a: Trace structure + errors (parallel)

For each representative trace, call in a single message:

- `get_trace(trace_id)` — get span hierarchy; locate the evaluated span and its parent/siblings/children
- `find_error_spans(trace_id)` — check for runtime errors anywhere in the trace

**Runtime vs behavioral**: If `find_llmobs_error_spans` returns errors on the evaluated span or nearby spans, the root cause may be a runtime failure (timeout, API error) rather than a behavioral one. Check this first.

#### Step 4b: Navigate to the root cause (parallel)

Use the evaluated span kind (from Step 1c) to decide where to look. Issue ALL calls in a single message.

**If eval is on an `llm` span** (most common):

- `get_span_content(field="messages", path="$.messages[0]")` on the **evaluated span** — get its system prompt
- `get_span_content(field="messages")` on the **evaluated span** — check what context it received
- `get_span_content(field="documents")` on **sibling retrieval spans** (if any) — was the retrieved context relevant?
- `get_span_content(field="input")` on **sibling tool spans** (if any) — did tools provide good data?
- `get_span_content(field="messages", path="$.messages[0]")` on **parent agent/workflow span** — did the parent give clear instructions?

**If eval is on an `agent` span**:

- `get_agent_loop(trace_id, span_id)` — full decision timeline
- `get_llmobs_span_details` on **child spans** — which tool/LLM calls did the agent make?
- `get_span_content(field="input"/"output")` on **child spans that look wrong** — what went off track?

**If eval is on a `tool` span**:

- `get_span_content(field="input")` on the **evaluated span** — what parameters was it called with?
- `get_span_content(field="messages")` on the **parent LLM span** — did the LLM construct the call correctly?

**If eval is on a `workflow` span**:

- `get_llmobs_span_details` on **all child spans** — find which step first deviated from expected behavior
- `get_span_content(field="input"/"output")` on the **deviating child** — what went wrong?

**Always also fetch**:

- `get_span_content(field="metadata")` on the evaluated span — check for clustering signals (prompt version, feature flags)

#### Step 4c: Diagnose — from symptom to root cause

For each category, trace the causal chain:

1. **Symptom** — what the judge flagged (reasoning from Phase 1b). Remember: the judge only saw one span's input/output, so its reasoning may be shallow.
2. **Trace context** — what the surrounding spans reveal (parent instructions, sibling data, child decisions)
3. **Root cause** — the specific span and decision point where the failure originated. This is often NOT the evaluated span itself.

**For suspected eval issues** (if config loaded): Compare the eval's criteria against evidence. Is the prompt ambiguous? Criteria too strict?

**Root cause categories:**

| Category | Description |
|----------|-------------|
| **System Prompt Deficiency** | Instructions unclear, missing, or contradictory — in evaluated span OR its parent |
| **Tool Gap** | Needed tool doesn't exist or parameters too coarse |
| **Tool Misuse** | Wrong tool called or wrong parameters — often visible in agent loop or parent LLM |
| **Routing/Handoff Error** | Wrong sub-agent selected (multi-agent systems) |
| **Retrieval Failure** | RAG returned irrelevant or missing context — check sibling retrieval spans |
| **Context Overflow** | Critical info lost due to context length |
| **Upstream Data Issue** | A sibling or parent span provided bad data that cascaded to the evaluated span |
| **Runtime Error** | Tool/API failure, timeout, exception — from `find_llmobs_error_spans` |
| **Evaluator Miscalibration** | Eval criteria produce false positives/negatives |

---

### Phase 5: Generate Recommendations

**Goal**: Concrete, actionable recommendations grounded in trace evidence. Not generic advice — actual text/code changes with before/after quotes from the trace.

Recommendation types: **System Prompt Edit** (quote actual prompt, provide before/after), **Tool Gap/Misuse** (reference agent loop steps), **Routing/Handoff Fix**, **Retrieval Fix** (show retrieved vs needed), **Evaluator Prompt Edit** (flag that eval changes need re-validation), **Other**.

**When run in Claude Code with codebase access**: Search the codebase for system prompt, tool definitions, or routing logic. Propose specific diffs. Always ask before modifying files.

---

### Phase 6: Compile RCA Report

Write the full report following the Output Format below. **This is the primary deliverable — output it directly in the chat.**

---

### Phase 7: Post-Analysis Actions

**Do NOT take any action automatically.** After presenting the report, ask the user what they'd like to do next. Suggest options:

1. Save the report to `eval-rca-{eval_name}-{date}.md`
2. Apply fixes (if codebase is available)
3. Deeper investigation of remaining categories
4. Export the report to a Datadog notebook
5. Run on an expanded time range (re-run the full analysis from Phase 1 with a wider `timeframe`, e.g. `now-7d` if the current window was `now-24h`)

**If the user chooses option 4**, call `mcp__datadog-mcp-core__create_datadog_notebook` with:
- **`name`**: `Eval RCA: {eval_name or ml_app} — YYYY-MM-DD`
- **`type`**: `report`
- **`time_span`**: `1w`
- **`cells`**: **one cell per section** (see Notebook Cell Structure below) — do NOT put the entire report in a single cell

After creation, output the URL on its own line:
`RCA report exported to notebook: <url>`

Print the URL prominently — if `/eval-bootstrap` runs next in the same session, it will detect this URL and offer to append the evaluator suite to the same notebook.

#### Notebook Cell Structure

Split the report into separate cells — one per major section. This renders far better than a single large cell.

**Cell 1 — Overview**
```
**Date**: YYYY-MM-DD | **Timeframe**: {from} → {to} | **App**: {ml_app} | **Signal**: {Eval | Error}
**App profile**: {description}

{2-3 sentence executive summary}
```

**Cell 2 — Error/Eval Health Summary** (table)

**Cell 3 — Failure Taxonomy** (table)

**Cells 4…N — one cell per Failure Mode**

**Cell N+1 — Prioritized Action Plan + Limitations**

**Notebook formatting rules** (apply to every cell):
- **No triple-backtick code blocks** — they render as separate plaintext blocks in Datadog notebooks. Use blockquotes (`>`) for prompts/rubrics, and inline code (`` ` ``) for short values.
- **Evidence as tables** — not bullet lists. See Output Format.
- **Tool inputs as tables** — Argument | Wrong value passed | Correct approach.
- **Action plan as a table** — Priority | Action | Confidence | Impact.

---

## Output Format

The in-chat report (Phase 6) and the notebook export (Phase 7) use the same structure. Differences are noted inline.

---

### Header

```
# Eval RCA Report: `{eval_name or ml_app}`

**Date**: YYYY-MM-DD | **Timeframe**: {from} → {to} | **App**: {ml_app} | **Signal**: {Eval | Error}
**App profile**: {LLM | RAG | Agent | Multi-agent, with brief description}

{2-3 sentence executive summary: overall health, most important finding with numbers.}
```

---

### Error / Eval Health Summary

Table — one row per error type or eval:

```
| Error Type | Spans | Traces | Versions Affected | Status |
|---|:---:|:---:|---|:---:|
| `monitor_groups_search` 400 | ~21 | 4+ | All | ⚠️ Active |
| `CancelledError` | ~25 | 12+ | All | ⚠️ Active |
| `load_datadog_skill` | ~7 | 7 | v1.0–v1.3 only | ✅ Resolved |
```

---

### Failure Taxonomy

```
| # | Failure Mode | Traces | % | Severity | Root Cause |
|---|---|:---:|:---:|:---:|---|
| 1 | Short description | 4+ | ~20% | **High** | Tool Misuse |
```

---

### Failure Mode Sections (one per mode)

```
## Failure Mode N: [Name]

**Count**: {n} spans, {t} traces | **Severity**: High/Medium/Low | **Root Cause**: [Category]

[3-5 sentences: what goes wrong, when, what triggers it, causal chain.]

**Evidence**

| Trace | Behavior | Version |
|---|---|---|
| [69de86a7...](https://app.datadoghq.com/llm/traces?query=trace_id:{full_id}) | 7 parallel calls, all 400 | v107624932 |
| [69de473f...](https://app.datadoghq.com/llm/traces?query=trace_id:{full_id}) | 7x 400 + CancelledError after 153s | v107574104 |

{For tool misuse: add a tool inputs table}
**Tool inputs (100% of sampled calls have this pattern)**

| Argument | Value passed (wrong) | Correct approach |
|---|---|---|
| `query` | `"monitor_id:123 group_status:alert"` | `"monitor_id:123"` (name/tag only) |
| `group_states` | *(not passed)* | `["alert"]` (separate param) |

{For eval signal: add judge reasoning as a blockquote}
> "{quoted judge reasoning}"

**Root cause**: [WHY this happens, tied to trace evidence — specific span, parameter, or prompt.]

**Fix**: [Concrete action. For schema/prompt changes, show before/after inline — no code blocks:
  BEFORE: query: string  # "Search query"
  AFTER: query: string  # Name/tag filters only — do not embed state filters here
         group_states: array[enum]  # ["alert", "warn", "no data", "ignored", "ok"]
]

**Impact**: Eliminates ~{n} spans/timeframe{; also describe secondary effects if any}.
```

**Evidence table columns** — use the most informative subset:
- **Error Signal**: Trace | Behavior | Version
- **Eval Signal**: Trace | Judge verdict | What the trace revealed
- Always omit columns that add no information for a given mode.

---

### Prioritized Action Plan

Table — not a numbered list:

```
| Priority | Action | Confidence | Impact |
|:---:|---|:---:|---|
| 1 | Fix `monitor_groups_search` schema — add `group_states` param | High | Eliminates ~21 spans/7d |
| 2 | Cap `max_retries` + handle `CancelledError` gracefully | High | Reduces retry storms |
| 3 | Upgrade pydantic-ai for cross-task cancel scope fix | Medium | Fixes ~8 spans/7d |
```

---

### Limitations & Follow-ups

Bullet list — unchanged from before:

```
- **{Topic}** — {what needs more data or follow-up action}
```

## Operating Rules

- **Ground in evidence**: Every claim references span IDs with clickable trace links: `[Trace {first_8}...](https://app.datadoghq.com/llm/traces?query=trace_id:{full_32_char_id})`.
- **Root cause over symptom**: "System prompt doesn't specify date format" not "model gave wrong answer."
- **Show your math**: "47 failures (34%)" not "many failures."
- **Honest about uncertainty**: < 5 examples = tentative. Flag it.
- **Anonymize PII**: No emails or names. User/org IDs are fine.
