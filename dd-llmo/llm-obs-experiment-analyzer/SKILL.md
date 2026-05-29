---
name: llm-obs-experiment-analyzer
description: Analyze LLM experiment results. Handles single or comparative experiments, exploratory or Q&A modes. Use when user says "analyze experiment", "compare experiments", "analyze against baseline", or provides one or two experiment IDs for analysis.
---

## Backend

**Detection** — At the start of every invocation, before taking any action, determine which backend to use:

1. If the user passed `--backend pup` anywhere in their invocation → use **pup mode** immediately, regardless of whether MCP tools are present. Skip steps 2–4.
2. Check whether MCP tools are present in your active tool list. The canonical signal is whether `mcp__datadog-llmo-mcp__get_llmobs_experiment_summary` appears in your available tools.
3. If MCP tools are present → use **MCP mode** throughout. Call MCP tools exactly as named in this skill's workflow sections.
4. If MCP tools are absent → check whether `pup` is executable: run `pup --version` via Bash. A JSON response containing `"version"` confirms pup is available.
5. If pup responds → use **pup mode** throughout. Translate every MCP tool call to its pup equivalent using the Tool Reference appendix at the bottom of this file.
6. If neither is available → stop and tell the user:
   > "Neither the Datadog MCP server nor the pup CLI is available. Connect the MCP server (`claude mcp add --scope user --transport http datadog-llmo-mcp 'https://mcp.datadoghq.com/api/unstable/mcp-server/mcp?toolsets=llmobs'`) or install pup."

`--backend pup` is accepted anywhere in the invocation arguments and is stripped before passing remaining args to the skill logic.

**pup invocation rules:**
- Invoke via Bash: `pup llm-obs <subcommand> [flags]`
- pup always outputs JSON. Parse directly — no content-block unwrapping (unlike MCP results, which may wrap JSON in `[{"type": "text", "text": "<json>"}]`).
- If pup returns an auth error, tell the user to run `pup auth login` and stop.
- Parallelization: issue multiple Bash tool calls in a single message (one pup command per call).

**Invocation ID:** At the very start of each invocation, before any MCP tool call, generate an 8-character hex invocation ID (e.g., `3a9f1c2b`). Keep it constant for the entire invocation.

**Intent tagging:** On every MCP tool call, prefix `telemetry.intent` with `skill:llm-obs-experiment-analyzer[<inv_id>] — ` followed by a description of why the tool is being called. On the **first MCP tool call only**, use `skill:llm-obs-experiment-analyzer:start[<inv_id>] — ` instead (note the `:start` suffix). Example first call: `skill:llm-obs-experiment-analyzer:start[3a9f1c2b] — Phase 1: get experiment summary to orient analysis`

# Unified Experiment Analyzer

Analyzes one or two LLM experiments. Supports four modes based on inputs:

| Inputs | Mode |
|--------|------|
| 2 IDs, no question | Comparative Exploratory |
| 2 IDs + question | Comparative Q&A |
| 1 ID, no question | Single Exploratory |
| 1 ID + question | Single Q&A |

## Usage

```
/llm-obs-experiment-analyzer <experiment_id_1> [experiment_id_2] [question text] [--output agent|file|notebook]
```

Arguments: $ARGUMENTS

## Available Tools

| Tool | Purpose |
|------|---------|
| `mcp__datadog-llmo-mcp__get_llmobs_experiment_summary` | Get total events, error count, metrics stats, available dimensions |
| `mcp__datadog-llmo-mcp__list_llmobs_experiment_events` | Query events with filters, sorting, pagination |
| `mcp__datadog-llmo-mcp__get_llmobs_experiment_event` | Get full event details (input, output, expected_output, metrics) |
| `mcp__datadog-llmo-mcp__get_llmobs_experiment_metric_values` | Get metric stats overall and segmented by dimension. Use `segment_by_dimension` (not `segment_dimension`) to segment; optionally `segment_dimension_value` to filter to a specific value. |
| `mcp__datadog-llmo-mcp__get_llmobs_experiment_dimension_values` | List unique values for a dimension with counts |
| `mcp__datadog-mcp-core__create_datadog_notebook` | Export report as a Datadog notebook |

---

## Phase 0 — Mode & Output Resolution

Parse $ARGUMENTS:
1. Extract one or two UUID-format strings as experiment IDs (first = baseline/primary, second = candidate).
2. Extract `--output agent|file|notebook` flag if present.
3. The remaining text (after IDs and flags) is the question, if any.

**Mode determination:**
- 2 IDs + question → Comparative Q&A
- 2 IDs, no question → Comparative Exploratory
- 1 ID + question → Single Q&A
- 1 ID, no question → Single Exploratory

**Output mode determination:**

If `--output` was provided in arguments, use that mode and skip asking.

Otherwise, ask two **separate sequential** `AskUserQuestion` calls before proceeding — never combined into a single call:

1. **Analysis type**: If no question text was provided in the arguments, ask whether the user wants exploratory analysis or has a specific question. Skip this call only if the user's intent is already clear from context (e.g. they typed a question alongside the IDs).
2. **Output destination**: If `--output` was not specified, ask where to deliver the report (chat, file, or Datadog notebook). Always ask this as its own standalone call.

**Output modes:**
1. **Agent (default):** Display the full report in the conversation.
2. **File:** Before starting, propose a path:
   `evals/reports/YYYY-MM-DD-<experiment-slug>-analysis.md`
   Present it to the user and let them confirm or adjust. Then proceed.
3. **Notebook:** Use `mcp__datadog-mcp-core__create_datadog_notebook` at the end. In pup mode, use `pup notebooks create --title "TITLE" --file /tmp/nb_cells.json` instead (see Tool Reference). If neither MCP nor pup is available, output these setup instructions instead of failing:
   ```
   To enable Datadog notebook export, add the MCP server:
     claude mcp add --transport http datadog-mcp https://mcp.datadoghq.com/api/unstable/mcp-server
   See: https://docs.datadoghq.com/bits_ai/mcp_server/setup/
   ```
   Then ask: "Would you like to fall back to file or agent output instead?"
   See Phase 5 for full notebook call details.

After resolving mode and output, proceed to Phase 1. There will be one additional `AskUserQuestion` interaction at Phase 1.5 before the deep analysis begins.

---

## Phase 1 — Orient

**Comparative:** Call `get_llmobs_experiment_summary` for both experiments. Produce a side-by-side comparison:
- Scale: total samples and error count for each
- Metrics: which metrics exist in each; which are shared
- Dimensions: which dimensions exist in each; which are shared
- Immediate red flags (errors present, missing metrics, sparse data)
- Obvious improvements or regressions visible at the summary level

When `error_count > 0`, call `get_llmobs_experiment_dimension_values` for `error_type` and report the breakdown by exception class (e.g. "2 errors: `asyncio.exceptions.cancellederror`"). Errors mean the executor threw an unhandled exception — no eval scores were produced for those samples. Do not report a percentage; report the count and type(s).

**Single:** Call `get_llmobs_experiment_summary` for the experiment. Determine:
- Total samples, and error count (with `error_type` breakdown if non-zero)
- Available metrics grouped by `metric_type` as returned by the summary (`score`, `boolean`, `categorical`). Do not infer semantic groupings or categories from label name patterns or prefixes — the label string is not a reliable signal for what a metric measures.
- Classify each metric using the statistics already returned by the summary (mean, min, max). Do not infer metric meaning from label names or prefixes. Use the classifications defined in Phase 1.5 when referencing metrics throughout the report.
- Available dimensions for segmentation
- Any immediate red flags

---

## Phase 1.5 — Metrics Selection

After completing Phase 1, run the following three steps before any `AskUserQuestion`.

**Step 1 — Classify every metric** using summary statistics only (no additional tool calls):

| Class | Condition | Meaning |
|---|---|---|
| `always_zero` | `max == 0` | Feature disabled or not implemented — no signal |
| `perfect` | `min == 1` | Always passes — no diagnostic signal |
| `saturated` | `mean ≥ 0.99` and `min < 1` | Rarely fails — low diagnostic value |
| `struggling` | `mean < 0.70` | Meaningful failure rate — highest diagnostic value |
| `interesting` | `0.70 ≤ mean < 0.99` and `min < max` | Partial failures — moderate diagnostic value |

**Step 2 — Print the full metric table to chat** before asking any question. This gives the user complete visibility — never truncated by option limits. Format:

```
Found N metrics. Full breakdown:

| Metric | Mean | Class |
|--------|------|-------|
| <label> | <mean> | Struggling |
| <label> | <mean> | Interesting |
| <label> | <mean> | Saturated |
| <label> | 1.000 | Perfect (no signal) |
| <label> | 0.000 | Always zero (disabled?) |
```

Flag any `always_zero` metrics with a note — e.g. "N metrics always score 0 and appear to be disabled features; they will be excluded from suggested groupings."

**Step 3 — AskUserQuestion** with options built entirely from the computed classes:

Generate options dynamically based on what is actually present in the data. Do **not** invent option names from label prefixes.

- **"Struggling metrics (N) — Recommended"**: only shown if N ≥ 1. Description explicitly lists each metric label and its mean (e.g. "`open_answer` 0.33, `c_permanence` 0.68"). This is the grounded suggestion — based on observed pass rates, not label names. If there are no struggling metrics, replace this option with **"Lowest-performing metrics (N)"** covering the bottom N by mean.
- **"Interesting + struggling (N)"**: shown only if there are interesting-class metrics in addition to struggling ones. Description lists them with means.
- **"All metrics (N)"**: always shown. Note in the description that always-zero and perfect metrics add noise but are included.
- **"A specific metric"**: always shown. Description says: *"Choose one from the table printed above."*

**If the user selects "A specific metric"**, ask a second `AskUserQuestion` that shows the **4 metrics with the lowest mean** as labeled options (label = metric name, description = `mean: X.XX — class`). In the question text, explicitly say: *"Or type any metric name from the table above into 'Other'."* The `always_zero` and `perfect` metrics must not appear in the 4 options (they have no diagnostic value); restrict the 4 to `struggling` and `interesting` classes only. After the user picks one, restrict all analysis in Phases 2–4 to that single metric only.

**Scope enforcement:**
- If the user accepts "all", proceed with all metrics (including constant ones, but note their low signal value).
- If the user selects a grouping or a specific metric, restrict all analysis in Phases 2–4 strictly to that selection. Do not call `get_llmobs_experiment_metric_values` for any metric outside the selection.

---

## Phase 2 — Signal Discovery + UI Links

**Comparative:** Using only the metrics selected in Phase 1.5 (intersected with shared metrics) and shared dimensions, identify:
- Segments where the candidate outperforms the baseline
- Segments where the candidate regresses
- Error types present in one but rare in the other
- Distribution shifts or coverage gaps
- Tradeoffs (e.g., higher recall, lower precision)

Generate Datadog comparison UI links:
- Base URL: `https://app.datadoghq.com/llm/experiment-comparison`
- Required params: `baselineExperimentId`, `experimentIds` (candidate%2Cbaseline), `tableView=all`
- Optional (include if discoverable): `project`, `compareDatasetId`, `selectedEvaluation`
- `selectedEvaluation` priority: overall/overall_score/rubric metric → primary metric → first shared metric
- Generate 2–4 links: primary comparison, regression view, calibration view (if applicable), worst-segment view (only if supported — never fabricate filters)

**Single:** Measure per-metric performance across all dimensions for only the metrics selected in Phase 1.5. Identify:
- Worst-performing segments (by metric × dimension)
- Any segments with surprising pass rates
- Overall pass rates and variance

Generate Datadog experiment UI link:
- `https://app.datadoghq.com/llm/experiments/{experiment_id}`

---

## Phase 3 — Deep Dives

Run all necessary deep dives automatically. Do not ask for approval or pause. Scope all deep dives strictly to the metrics selected in Phase 1.5 — do not call `get_llmobs_experiment_metric_values` for any metric outside the selection.

**Q&A modes:** Focus deep dives on what is needed to answer the question directly. Pull specific samples, segment by relevant dimensions, inspect examples.

**Exploratory modes:** Investigate the most interesting signals broadly:
- Per-segment and per-class delta analysis (comparative) or pass-rate analysis (single)
- Error overlap vs. unique failure mode analysis
- Sampling and qualitative inspection of representative failures (2–5 per issue)
- Clustered error theme analysis

Rules:
- Prefer cheap, high-signal analyses first; do not stop early.
- Mask or redact PII in all outputs.
- Avoid destructive actions.

For each sampled event, generate a direct span link:
`https://app.datadoghq.com/llm/experiments/{experiment_id}?selectedTab=overview&sp=[{"p":{"experimentId":"{experiment_id}","spanId":"{span_id}"},"i":"experiment-details"}]&spanId={span_id}`

For each Deep Dive segment, generate a direct link to view those samples in the (candidate) experiment:
`https://app.datadoghq.com/llm/experiments/{experiment_id}?selectedTab=overview&filter[{dimension}]={value}`
If you are not confident the filter URL format works for this dimension, omit the filter params and link to the experiment root instead. Never fabricate filter URLs.

---

## Phase 4 — Synthesis

**Comparative Exploratory:**
- Clear wins where the candidate improves on the baseline
- Clear regressions or risks the candidate introduces
- Neutral or unchanged areas
- Root-cause hypotheses (1–4), tied to evidence
- Prioritized recommendations: ship as-is / block / gate by segment / combine behaviors

**Comparative Q&A:**
- Direct answer to the question with a clear verdict
- Supporting evidence (metrics, percentages, event examples)
- Relevant context (e.g., caveats, data limitations)

**Single Exploratory:**
- Overall performance assessment
- Worst-performing segments and root causes
- Hypotheses for why failures occur
- Recommended next experiments

**Single Q&A:**
- Direct answer to the question with a clear verdict
- Supporting evidence from the experiment data

All modes: open with a one-line issue type tally — e.g. "3 agent issues, 1 evaluator/dataset issue, 1 ambiguous" — before the detailed findings. Use quantified deltas/rates wherever possible. Redact PII.

**Always produce both `## Summary & Recommendations` and `## Synthesis` sections regardless of experiment complexity, how many metrics exist, or how quickly the answer is apparent.** Do not skip Summary because the findings are simple or obvious. Do not skip Synthesis because you've already covered the findings in Deep Dives. These two sections are the most portable output of the analysis — they are what a reader encounters first and last.

---

## Phase 5 — Output Delivery

**Agent:** Present the full report in the conversation using the report format below.

**File:** Write the report to the pre-confirmed path. Confirm with: "Report saved to `<path>`."

**Notebook:** Call `mcp__datadog-mcp-core__create_datadog_notebook` with the following parameters:

- **`name`** (by mode):
  | Mode | Name |
  |------|------|
  | Comparative Exploratory | `Experiment Analysis: {baseline_short} (Baseline) vs {candidate_short} (Candidate) — YYYY-MM-DD` |
  | Comparative Q&A | `Experiment Q&A: {baseline_short} vs {candidate_short} — YYYY-MM-DD` |
  | Single Exploratory | `Experiment Analysis: {experiment_short} — YYYY-MM-DD` |
  | Single Q&A | `Experiment Q&A: {experiment_short} — YYYY-MM-DD` |
  where `short` = first 8 characters of the UUID.

- **`cells`**: one cell per report section — do NOT put the entire report in a single cell. Structure:
  - Cell 1 — **Summary & Recommendations** containing three `###` subheaders: **Experiment** (link + executive summary), **Key Findings** (bullets), **Recommendations** (numbered list) — **always present, always first, never skipped regardless of experiment complexity**
  - Cell 2 — Orientation table
  - Cell 3 — What Changed (comparative modes only; omit for single)
  - Cell 4 — Signals / Answer to Question
  - Cells 5…N — one cell per Deep Dive Finding
  - Cell N+1 — **Synthesis** (issue tally, Overall Performance Assessment, Worst-Performing Segments, Root Cause Hypothesis, Recommended Next Experiments) — **always present, always second-to-last**
  - Cell N+2 — UI Links

  Omit the `# Experiment Analysis Report` top-level heading from all cells — it is already shown as the notebook title.
- **`time`**: `{ "live_span": "1h" }`

After the notebook is created, output the URL in chat: `"Report exported to notebook: <url>"`

If the tool is unavailable, follow the fallback instructions in Phase 0.

---

## Phase 6 — Conversational Follow-up

After delivering the report, append a follow-up section:

```
---
## Want to explore further?

Here are a few directions based on the findings:

1. [Specific question derived from actual findings — e.g., "Want me to dig deeper into why the SQL scenarios regressed in the candidate?"]
2. [Another specific follow-up — e.g., "Should I compare error patterns between the two failing clusters?"]
3. [A third option if relevant]

Do you have any other questions about this analysis?
```

Stay active after the report. Answer follow-up questions using the same MCP tools, referencing findings already gathered. Do not re-run analyses you've already performed unless new questions require it.

---

## Report Format

Link rules:
- **Experiment IDs**: Wherever a full experiment UUID appears, render it as a Markdown link to `https://app.datadoghq.com/llm/experiments/{full_uuid}`.
- **Comparative table column headers**: In the Orientation table and in every subsequent table that has Baseline/Candidate columns, wrap the *entire* column header as a link — not just the short ID. Format: `[Baseline \`{short_id}\`]({baseline_url})` and `[Candidate \`{short_id}\`]({candidate_url})`. This makes the full header cell clickable, not just the ID portion.

```markdown
# Experiment Analysis Report

> **Question:** {original question text}
> _(Q&A modes only — omit for Exploratory modes)_

## Summary & Recommendations

### Experiment

[Comparative: [`{baseline_short}`]({baseline_url}) (Baseline) vs [`{candidate_short}`]({candidate_url}) (Candidate) — [Compare]({compare_url}) — Single: [`{experiment_short}`]({experiment_url})]

[2–3 sentence executive summary. Open with "This is a **{Mode}** analysis..." where {Mode} is one of: Comparative Exploratory, Comparative Q&A, Single Exploratory, Single Q&A. Include experiment(s) purpose, scale, and the headline finding with specific numbers.]

[If the report uses opaque dimension values (e.g. category labels like b1/b2/b3/bx), add a `#### Dataset Categories` sub-subsection here — one bullet per value with name bolded and a brief description. Omit if all dimension values are self-explanatory.]

### Key Findings

- **{Finding 1}**: one-line description with numbers (e.g. "+4.2pp on `tool_accuracy` across all segments")
- **{Finding 2}**: one-line description
- **{Finding 3}** (if present): one-line description
[For Q&A modes: one-line verdict bullet + one-line rationale bullet]

### Recommendations

1. **{Recommendation 1}**: specific, actionable next step tied to a finding
2. **{Recommendation 2}**: specific, actionable next step
3. **{Recommendation 3}** (if present): specific, actionable next step
[Omit this subsection for Q&A modes unless a clear action follows from the answer.]

## Orientation

[Side-by-side table for comparative; summary table for single. Include: samples, errors (count + `error_type` breakdown if non-zero, otherwise "none"), metrics, dimensions. Experiment IDs in column headers must be Markdown links.]


## What Changed

[Comparative modes only. Table of differences between baseline and candidate: model, toolset/skill profile,
dataset, evaluator schema, and any other metadata differences detectable from the summary data.
If no differences are detectable, write: "No configuration differences detected between experiments."]

## [Signals | Answer to Question]

[For exploratory: ranked table of signals/segments with metric deltas and impact counts.]
[For Q&A: direct answer with verdict, then supporting evidence.]

## Deep Dive Findings

### [Issue/Finding Title]

**Segment**: `[dimension=value]` | **Impact**: N samples | **Severity**: metric pass rate = X% | [View samples](https://app.datadoghq.com/llm/experiments/{experiment_id}?selectedTab=overview&filter[{dimension}]={value})

**Issue type**: `Agent` — the evaluator is sound; the agent output is the problem. | `Evaluator/Dataset` — the agent output may be correct; the rubric, ground truth labels, or scoring logic is suspect. | `Ambiguous` — cannot determine from available evidence whether the agent or evaluator is at fault; flag for manual inspection.

**What's happening**: [1–2 sentences: key observation and metric impact only]

**Representative examples**:
- [Span link]: [input → output → expected, what went wrong]

**Root cause hypothesis**: [Category]: [Explanation tied to evidence]

**Recommendation**: [Specific, actionable next step]

---
[Repeat for each major issue]

## Synthesis

[Required in all modes. Comes after all Deep Dive Findings, before UI Links.]

**Issue tally**: [N agent issues, N evaluator/dataset issues, N ambiguous]

### Overall Performance Assessment
[2–4 sentences on overall quality: what the experiment shows, whether the app/model is production-ready on this task, key numbers.]

### Worst-Performing Segments
[Bullet list: which dimension values or conditions most reliably predict failure. Include metric values.]

### Root Cause Hypothesis
[The single most likely root cause across all findings. If multiple independent root causes, list them ranked by impact. Each hypothesis must be tied to specific evidence, not to label names or general reasoning.]

### Recommended Next Experiments
[2–4 concrete, specific follow-up experiments. Each should be actionable: e.g. "Re-run with `max_turns=40` to test whether turn exhaustion is the primary driver, not model quality" not "Investigate turn limits further."]

## UI Links

[All generated Datadog UI links with labels]
```

---

## Operating Rules

- Do not assume anything about the experiment (model, task, metrics, schema, dimensions). Infer everything by inspecting the data.
- Ground all conclusions in specific evidence: event IDs, counts, percentages.
- Show math: include counts and rates, not just qualitative claims.
- Avoid speculative explanations not supported by observed evidence.
- Mask or redact PII in all user-visible output.

---

## Tool Reference

This appendix applies only in **pup mode**. In MCP mode, use the tool names in the workflow sections directly.

### Experiments

| MCP Tool | pup Command |
|---|---|
| `get_llmobs_experiment_summary(experiment_id)` | `pup llm-obs experiments summary EXPERIMENT_ID` |
| `list_llmobs_experiment_events(experiment_id, ...)` | `pup llm-obs experiments events list EXPERIMENT_ID [--filter-metric-label L] [--sort-by-metric M] [--sort-direction asc\|desc] [--limit N]` — confirm filter/sort flag names with `pup llm-obs experiments events list --help` before use |
| `get_llmobs_experiment_event(experiment_id, event_id)` | `pup llm-obs experiments events get EXPERIMENT_ID EVENT_ID` |
| `get_llmobs_experiment_metric_values(experiment_id, metric_label, ...)` | `pup llm-obs experiments metric-values EXPERIMENT_ID --metric-label L [--segment-by-dimension D] [--segment-dimension-value V]` |
| `get_llmobs_experiment_dimension_values(experiment_id, dimension_key)` | `pup llm-obs experiments dimension-values EXPERIMENT_ID --dimension-key K` |

### Notebooks

| MCP Tool | pup Command |
|---|---|
| `create_datadog_notebook(name, cells, ...)` | `pup notebooks create --title "TITLE" --file /tmp/nb_cells.json` — confirm exact flags with `pup notebooks create --help` |

The cells file is a JSON array of cell objects:
```json
[{"attributes": {"definition": {"type": "markdown", "text": "## Section\n\nContent."}}, "type": "notebook_cells"}]
```
- Never show internal tool calls, schemas, or implementation details to the user.
