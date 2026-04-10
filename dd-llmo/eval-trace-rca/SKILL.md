---
name: eval-trace-rca
description: Root cause analysis on production LLM traces using eval judge results as signal. Diagnoses why the user's application is failing. Use when user says "eval RCA", "root cause analysis", "analyze eval failures", "why is my eval failing", "why is my app failing", "failure analysis", "diagnose eval", "what's wrong with my app", or wants to understand production failure patterns. Works with ml_app or eval name.
---

# Eval RCA — Root Cause Analysis from Production Eval Signal

Perform structured root cause analysis on production LLM traces using LLM judge verdicts and reasoning. The goal is to diagnose **why the user's application is failing** — not to evaluate the judge itself. The judge is the signal; the app is the patient.

## Methodology

**Context → Observe → Open Coding → Axial Coding → Root Cause Analysis → Recommendations**

## Usage

```
What's wrong with <ml_app> based on its evals over the last <timeframe>
Analyze eval failures for <eval_name> over the last <timeframe>
```

### Inputs

| Input | Required | Default | Description |
|-------|----------|---------|-------------|
| `ml_app` | One of these | — | The application to analyze. The skill discovers all configured evals via `list_llmobs_evals`. |
| `eval_name` | One of these | — | A specific evaluator to focus on. Eval names are unique per org — each eval belongs to exactly one `ml_app`. |
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

### Phase 0: Resolve Inputs

**First: check for classification context.** Scan the conversation for a `# Session Classification Summary` header OR three or more `## Trace:` / `## Session:` blocks each containing a `**Verdict:**` line. If found → **"from classifications" path**: enter Step 0S below and skip steps 1–5.

#### Step 0S — Extract Failure Bucket from Classification Output

For each `## Trace: <id>` or `## Session: <id>` block in the conversation, extract:

| Field | Source line |
|-------|-------------|
| `trace_id` | `## Trace: <trace_id>` header, or `**Trace ID:**` line |
| `span_id` | `**Span ID:**` or `**Agent span ID:**` line |
| `verdict` | `**Verdict:**` line |
| `failure_mode` | `**Failure mode:**` line |
| `reasoning_text` | `**Failure mode detail:**` line (construct as "verdict=no, failure_mode=X" if absent) |
| `app_type` | `**App type:**` line (if present; default to `LLM` if absent) |

**Failure bucket** = all traces/sessions where verdict is `no` or `partial` (exclude `yes` and `error`).

If failure bucket has < 5 entries → note low confidence, proceed anyway.
If failure bucket is empty → report "No failures found in the provided classification output" and stop.

Synthesize and present this overview before continuing:

```
## Classification Overview (from eval-session-classify)

**Source**: eval-session-classify  |  **ml_app**: <from summary header if present>
**Traces/sessions classified**: N  |  **Failures (no+partial)**: F  |  **Pass rate**: X%

Failure modes in failure bucket:
| Mode | Count |
...

Proceeding to Phase 2 (open coding) using F failure traces as the corpus.
No eval judge configured — classification verdict is the signal.
```

After presenting, **skip Phase 1 entirely and jump to Phase 2.**

Carry forward:
- **For Phase 2**: failure bucket = `[(trace_id, span_id, reasoning_text)]` tuples — identical structure to Step 1b output.
- **For Phase 4**: evaluated span is the root span. If `app_type: Agent` is visible in any trace block, use `get_agent_loop` + child-span investigation; otherwise treat as `llm` span.
- **Phases 2–7**: all run unchanged — the failure bucket structure is the same regardless of origin.

---

**Standard resolution (no classification context):**

1. If neither `ml_app` nor `eval_name` provided → ask the user.
2. If `timeframe` not provided → default to `now-24h`.
3. **ml_app entry point**: If `ml_app` provided without `eval_name`, call `list_llmobs_evals(ml_app)` to discover all configured evals. Then call `get_llmobs_eval_aggregate_stats` for each eval (in parallel) to get a quick health snapshot. Present the overview to the user and proceed to analyze ALL evals with issues (don't ask the user to pick one).
4. **eval_name entry point**: Proceed directly to Phase 1 for the specified eval.
5. Note any additional filters (tags, span_kind) for all subsequent queries.

---

### Phase 1: Gather Context & Collect Evidence

> **Skip this entire phase if entering from the "from classifications" path (Step 0S).** Jump directly to Phase 2.

**Goal**: Get the big picture, sample failure spans, and determine the app profile.

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

---

## Output Format

```markdown
# Eval RCA Report: `{eval_name or ml_app}`

**Date**: {YYYY-MM-DD}  |  **Timeframe**: {from} → {to}
**App profile**: {LLM | RAG | Agent | Multi-agent}

{If single eval:
**Eval**: {Custom/OOTB}  |  **Metric**: {boolean/score/categorical}
**Total spans**: {total_count}  |  **Pass rate**: {pass_rate}%
**Sample**: {sample_size} ({pass_count} pass / {fail_count} fail)
}

{If multiple evals (ml_app entry):
## Eval Health Summary

| Eval | Type | Total | Pass Rate | Status |
|------|------|------:|:---------:|--------|
| ... | ... | ... | ... | ... |
}

{If from classifications (Step 0S path):
## Classification Signal Summary

**Source**: eval-session-classify output (not eval judges)

| Metric | Value |
|--------|-------|
| Traces/sessions classified | N |
| Failures in RCA corpus (no+partial) | F |
| Failure modes present | list |
| Classification signal | content-only / content+evals / trace+rum |

Note: Root cause analysis is based on per-trace classification verdicts, not automated eval judge reasoning.
}

{If custom eval:
## Eval Definition
**Measures**: {summary}  |  **Criteria**: {pass_when/threshold}
}

[2-3 sentence executive summary: overall health, most important finding with numbers.]

## Failure Taxonomy

| # | Failure Mode | Count | % | Severity | Root Cause |
|---|-------------|------:|:-:|:--------:|-----------|
| 1 | ... | ... | ... | ... | ... |

## Detailed Analysis

### Failure Mode 1: [Name]

**Count**: {n} ({pct}%)  |  **Severity**: High  |  **Root Cause**: [Category]

**What's happening**: [3-5 sentences. What goes wrong, when, what triggers it.]

**Evidence**:
- [Trace {id_short}](https://app.datadoghq.com/llm/traces?query=trace_id:{full_id}): [what happened]

**Judge reasoning**: > "{quoted reasoning}"

**Trace deep-dive**: [What the trace investigation revealed — system prompt content, tool calls, agent decisions, errors found, retrieved documents]

**Root cause**: [WHY this happens, tied to trace evidence]

**Recommendation**:
- **Type**: [System Prompt Edit | Tool Gap | Routing Fix | Retrieval Fix | Evaluator Prompt Edit | Other]
- **What to change**:
  ```text
  BEFORE: [actual text from trace]
  AFTER: [proposed replacement]
  ```

- **Why**: [causal link]
- **Impact**: ~{n} failures ({pct}%)

---

[Repeat for top 3-5 failure modes]

## Prioritized Action Plan

1. **[Title]** ({pct}%) — [concrete change] — Confidence: High/Medium/Low

## Remaining / Low-Confidence

| Mode | Count | Notes |
|------|------:|-------|
| ... | ... | ... |

## Limitations & Follow-ups

- [What needs more data]
- [Suggested follow-ups]

```

## Operating Rules

- **Ground in evidence**: Every claim references span IDs with clickable trace links: `[Trace {first_8}...](https://app.datadoghq.com/llm/traces?query=trace_id:{full_32_char_id})`.
- **Root cause over symptom**: "System prompt doesn't specify date format" not "model gave wrong answer."
- **Show your math**: "47 failures (34%)" not "many failures."
- **Honest about uncertainty**: < 5 examples = tentative. Flag it.
- **Anonymize PII**: No emails or names. User/org IDs are fine.
