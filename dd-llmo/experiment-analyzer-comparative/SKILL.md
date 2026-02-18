---
name: experiment-analzyer-comparative
description: Analyze two llmo experiements in a comparative manner. Use when user says "analyze two experiments", "comparative experiment analysis", "evaluate experiments" "analyze against baseline". Requires two experiment_id as arguments to perform a comparative analysis, first is the baseline, second the candidate. 
---

# Comparative Experiment Analyzer

Analyzes a candidate LLMO experiments compared against a baseline. 

## Usage

```
/analyze-experiment-comparative <experiment_id_1> <experiment_id_2>
```

## Available Tools

Use these llm-obs-mcp MCP tools for analysis:

| Tool | Purpose |
|------|---------|
| `get_experiment_summary` | Get total events, error count, metrics stats, available dimensions |
| `get_events` | Query events with filters, sorting, pagination |
| `get_event_by_id` | Get full event details (input, output, expected_output, metrics) |
| `get_metric_values` | Get metric stats overall and segmented by dimension |
| `get_unique_dimension_values` | List unique values for a dimension with counts |

## Analysis Workflow

You are an agent that performs one-shot comparative error analysis for ML experiments.

The ONLY required inputs are two experiment IDs:
- a BASELINE experiment (the current or reference behavior)
- a CANDIDATE experiment (the proposed or new behavior)

Do not assume anything else (model type, task, metrics, dataset, schema, dimensions, project, or UI parameters).
All understanding must be inferred by inspecting the experiments themselves.

CRITICAL RULES:
- This is a ONE-SHOT run. Do not ask the user for clarification, approval, or follow-up input.
- Internally generate plans, run all necessary analyses, and save results automatically.
- Internal plans, tool calls, schemas, queries, and action-level details must NEVER be shown to the user.
- The user only sees the final report, UI links, and a confirmation that results were saved.

Your primary goal is to definitively answer:
“What does the candidate experiment do better or worse than the baseline — and why?”

You must follow the phases below, in order, automatically.

────────────────────────────────────────
PHASE 1 — ORIENT (BASELINE & CANDIDATE)
────────────────────────────────────────
Internally retrieve summaries for BOTH experiments using only their experiment IDs.

For each experiment (Baseline and Candidate), determine:
- Total events and total errors (and error rate if computable)
- What metrics exist (describe them in plain English, e.g. exact match, rubric-based, quality score)
- What dimensions are available for segmentation
  (e.g., label/class, confidence, time, source, annotator, device, etc.)
- Any immediate red flags
  (e.g., unusually high error rate, missing expected metrics, sparse or skewed data)

Produce a user-facing ORIENT COMPARISON that includes:
- A clear side-by-side summary of baseline vs candidate:
  scale, error rate, and available metrics
- Key differences in metrics or segmentation dimensions
- Any obvious improvements or regressions in the candidate relative to the baseline
- Explicit assumptions if any information was missing and had to be inferred

────────────────────────────────────────────
PHASE 2 — COMPARATIVE SIGNAL DISCOVERY + UI LINKS
────────────────────────────────────────────
Using ONLY shared metrics and dimensions between the baseline and candidate, identify meaningful differences.

Analyze:
- Segments where the candidate outperforms the baseline
- Segments where the candidate regresses relative to the baseline
- Error types or behaviors present in one experiment but rare or absent in the other
- Differences in confidence behavior or calibration (if applicable)
- Distribution shifts or coverage gaps introduced or resolved by the candidate
- Tradeoffs (e.g., higher recall but lower precision)

Summarize these as observed comparative signals.

Additionally, generate Datadog UI comparison links to allow visual inspection:

UI LINK GENERATION RULES:
- Base URL (must be exact):
  https://app.datadoghq.com/llm/experiment-comparison

- Required parameters:
  * baselineExperimentId = <baseline_experiment_id>
  * experimentIds = <candidate_experiment_id>%2C<baseline_experiment_id>
  * tableView = all

- Optional parameters (include ONLY if discoverable from experiment metadata):
  * project
  * compareDatasetId
  * selectedEvaluation

- selectedEvaluation selection priority:
  1) Shared “overall” / “overall_score” / rubric / quality metric
  2) Shared primary metric (f1, accuracy, loss, etc.)
  3) First shared metric, explicitly labeled as default

Generate and present 2–4 links:
- Primary comparison (default metric)
- Candidate regressions (alternative metric or same view with explanatory label)
- Calibration/confidence view (if applicable)
- Worst-performing segment view (ONLY if supported; never fabricate filters)

If optional parameters cannot be inferred, still generate valid links and note limitations clearly.

────────────────────────────────────────────
PHASE 3 — AUTOMATIC DEEP DIVES (NO APPROVAL)
────────────────────────────────────────────
Based on the comparative signals, automatically perform ALL necessary deep dives to explain observed differences.

Deep dives may include (as applicable):
- Per-segment and per-class delta analysis
- Confusion matrix comparisons
- Error overlap vs unique failure mode analysis
- Confidence bucket analysis
- Calibration comparisons
- Sampling and qualitative inspection of representative errors
- Clustered error theme analysis

Rules:
- Prefer cheap, high-signal analyses first, but do not stop early.
- Run all deep dives needed to support conclusions.
- Avoid destructive actions (e.g., retraining, annotation job creation).
- Mask or redact PII in all outputs.

Summarize results progressively, but do not require user interaction.

────────────────────────────
PHASE 4 — FINAL SYNTHESIS
────────────────────────────
Produce a comprehensive comparative summary that answers:
“What does the candidate get right or wrong relative to the baseline — and why?”

Include:
- Clear wins where the candidate improves on the baseline
- Clear regressions or risks introduced by the candidate
- Neutral or unchanged areas (where relevant)
- Root-cause hypotheses (1–4), explicitly tied to evidence
- Concrete, prioritized recommendations, such as:
  • ship the candidate as-is
  • block the candidate pending fixes
  • gate rollout by segment
  • combine behaviors from baseline and candidate
- A list of all produced artifacts and UI links

Use quantified comparisons whenever possible.

────────────────────────────
PHASE 5 — SAVE RESULTS (AUTOMATIC)
────────────────────────────
Automatically save the full baseline-vs-candidate analysis to a file.

The saved report must include:
- Experiment IDs (baseline and candidate)
- Timestamp of analysis
- Orientation comparison
- Comparative signals
- Deep dive findings
- Final synthesis and recommendations
- All generated UI links
- References to produced artifacts

Saving rules:
- Save automatically; do NOT ask the user.
- Choose a sensible default format (e.g., Markdown or HTML).
- Persist the file and return a confirmation with a human-readable reference or link.
- Do not expose internal file paths, tool calls, or implementation details.

────────────────────
SAFETY & QUALITY
────────────────────
- Mask or redact any PII in all user-visible outputs and artifacts.
- If metrics or dimensions differ between baseline and candidate, clearly explain limitations.
- Avoid speculative explanations unless supported by observed evidence.
- Prefer quantified deltas, ratios, or percentages over qualitative claims.
- Use clear, baseline-vs-candidate language suitable for rollout and decision-making.
