---
name: experiment-analyzer
description: Analyze LLM experiment results. Handles single or comparative experiments, exploratory or Q&A modes. Use when user says "analyze experiment", "compare experiments", "analyze against baseline", or provides one or two experiment IDs for analysis.
---

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
/experiment-analyzer <experiment_id_1> [experiment_id_2] [question text] [--output agent|file|notebook]
```

Arguments: $ARGUMENTS

## Available Tools

| Tool | Purpose |
|------|---------|
| `mcp__datadog-llmo-mcp__get_llmobs_experiment_summary` | Get total events, error count, metrics stats, available dimensions |
| `mcp__datadog-llmo-mcp__list_llmobs_experiment_events` | Query events with filters, sorting, pagination |
| `mcp__datadog-llmo-mcp__get_llmobs_experiment_event` | Get full event details (input, output, expected_output, metrics) |
| `mcp__datadog-llmo-mcp__get_llmobs_experiment_metric_values` | Get metric stats overall and segmented by dimension |
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

Otherwise, ask **one combined clarification message** before proceeding. Cover only what is genuinely unclear:

- If mode is ambiguous (e.g., user asked a question but only provided IDs in surrounding context), ask in plain language: "Did you have a specific question in mind, or would you like an exploratory analysis?"
- Always ask about output destination if not specified: "Would you like me to save this to a file, export it to a Datadog notebook, or is displaying it here in chat fine?"

Never ask multiple rounds of clarifications. One message covers everything unresolved.

**Output modes:**
1. **Agent (default):** Display the full report in the conversation.
2. **File:** Before starting, propose a path:
   `evals/reports/YYYY-MM-DD-<experiment-slug>-analysis.md`
   Present it to the user and let them confirm or adjust. Then proceed.
3. **Notebook:** Use `mcp__datadog-mcp-core__create_datadog_notebook` at the end.
   If the tool is unavailable, output these setup instructions instead of failing:
   ```
   To enable Datadog notebook export, add the MCP server:
     claude mcp add --transport http datadog-mcp https://mcp.datadoghq.com/api/unstable/mcp-server
   See: https://docs.datadoghq.com/bits_ai/mcp_server/setup/
   ```
   Then ask: "Would you like to fall back to file or agent output instead?"
   See Phase 5 for full notebook call details.

After resolving mode and output, proceed fully automatically through Phases 1–5 with no further user interaction.

---

## Phase 1 — Orient

**Comparative:** Call `get_llmobs_experiment_summary` for both experiments. Produce a side-by-side comparison:
- Scale: total events and error rate for each
- Metrics: which metrics exist in each; which are shared
- Dimensions: which dimensions exist in each; which are shared
- Immediate red flags (high error rate, missing metrics, sparse data)
- Obvious improvements or regressions visible at the summary level

**Single:** Call `get_llmobs_experiment_summary` for the experiment. Determine:
- Total events, error count, error rate
- Available metrics (classify as exact-match vs. rubric/quality)
- Available dimensions for segmentation
- Any immediate red flags

---

## Phase 2 — Signal Discovery + UI Links

**Comparative:** Using only shared metrics and dimensions, identify:
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

**Single:** Measure per-metric performance across all dimensions. Identify:
- Worst-performing segments (by metric × dimension)
- Any segments with surprising pass rates
- Overall pass rates and variance

Generate Datadog experiment UI link:
- `https://app.datadoghq.com/llm/experiments/{experiment_id}`

---

## Phase 3 — Deep Dives

Run all necessary deep dives automatically. Do not ask for approval or pause.

**Q&A modes:** Focus deep dives on what is needed to answer the question directly. Pull specific events, segment by relevant dimensions, inspect examples.

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

For each Deep Dive segment, generate a direct link to view those events in the (candidate) experiment:
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

All modes: use quantified deltas/rates wherever possible. Redact PII.

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

- **`cells`**: the full report as a single markdown cell — `[{ "type": "markdown", "text": "<full report markdown>" }]`. Omit the `# Experiment Analysis Report` top-level heading from the cell content — it is already shown as the notebook title.
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

[Comparative: **Baseline**: [`{baseline_short}`]({baseline_url}) | **Candidate**: [`{candidate_short}`]({candidate_url}) | **Compare**: [`{baseline_short}-{candidate_short}`](https://app.datadoghq.com/llm/experiment-comparison?baselineExperimentId={baseline_id}&experimentIds={candidate_id}%2C{baseline_id}&tableView=all&selectedEvaluation=pass) — Single: **Experiment**: [`{experiment_short}`]({experiment_url}). Always the first line of this section.]

[2–3 sentence executive summary directly below the links line. Open with "This is a **{Mode}** analysis..." where {Mode} is one of: Comparative Exploratory, Comparative Q&A, Single Exploratory, Single Q&A. Include experiment(s) purpose, scale, and key finding with specific numbers.]

[If the report uses opaque dimension values (e.g. category labels like b1/b2/b3/bx), add a `### Dataset Categories` subsection here. Include: one sentence explaining where the categories come from (i.e. labels from the evaluation dataset grouping questions by required tools/data sources), then a bullet per value with its name bolded and a brief description. Infer descriptions from input question patterns, capability tags, and expected tool calls. Omit this subsection if all dimension values are self-explanatory.]

[Wins, regressions, neutral areas, prioritized actions. For Q&A: verdict + rationale.]

## Orientation

[Side-by-side table for comparative; summary table for single. Include: events, error rate, metrics, dimensions. Experiment IDs in column headers must be Markdown links.]


## What Changed

[Comparative modes only. Table of differences between baseline and candidate: model, toolset/skill profile,
dataset, evaluator schema, and any other metadata differences detectable from the summary data.
If no differences are detectable, write: "No configuration differences detected between experiments."]

## [Signals | Answer to Question]

[For exploratory: ranked table of signals/segments with metric deltas and impact counts.]
[For Q&A: direct answer with verdict, then supporting evidence.]

## Deep Dive Findings

### [Issue/Finding Title]

**Segment**: `[dimension=value]` | **Impact**: N events | **Severity**: metric pass rate = X% | [View events](https://app.datadoghq.com/llm/experiments/{experiment_id}?selectedTab=overview&filter[{dimension}]={value})

**What's happening**: [1–2 sentences: key observation and metric impact only]

**Representative examples**:
- [Span link]: [input → output → expected, what went wrong]

**Root cause hypothesis**: [Category]: [Explanation tied to evidence]

**Recommendation**: [Specific, actionable next step]

---
[Repeat for each major issue]

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
- Never show internal tool calls, schemas, or implementation details to the user.
