---
name: experiment-analzyer
description: Analyze LLM experiment results. Use when user says "analyze experiment", "experiment analysis", "evaluate experiment", "check experiment metrics", or provides an experiment ID for analysis. Requires experiment_id as argument.
---

# Experiment Analyzer

Analyze LLM experiment results to identify performance issues and recommend improvements.

## Usage

```
/analyze-experiment <experiment_id>
```

The experiment_id is: $ARGUMENTS

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

Before each action, reason about what you've learned and what to investigate next. This makes the analysis adaptive and transparent.

### Reasoning Pattern

For each phase, follow this pattern:

**Thought**: What do I know? What's surprising? What should I investigate next?
**Action**: Call the appropriate tool
**Observation**: What did it return? What does it mean?

Continue until you have enough evidence to write the final report.

---

### Phase 1: Orient

**Thought**: I need to understand the experiment's structure before diving in. What metrics exist? What dimensions can I segment by? How many events are there?

**Action**: Call `get_experiment_summary(experiment_id)`

**Observation**: Note:
- Total events and error count
- List of available metrics (classify as exact-match vs rubric/quality)
- List of available dimensions
- Any immediate red flags (high error rate, missing metrics)

**Decision point**:
- If error_count is high relative to total_events, investigate errors first
- If only 1-2 metrics exist, analysis will be simpler
- If many dimensions exist, prioritize the most meaningful ones

---

### Phase 2: Measure Overall Performance

**Thought**: Now I need baseline metrics. Which metrics are most important? Are there obvious failures?

**Action**: For each metric, call `get_metric_values(experiment_id, metric_label)`

**Observation**: For each metric, record:
- For boolean metrics: True/False/Empty counts -> calculate pass rate
- For numeric metrics: mean, min, max, distribution shape
- Flag any metric with pass rate < 90% or high variance

**Decision point**:
- If all metrics show >95% pass rate -> analysis may be brief, focus on edge cases
- If a metric has <70% pass rate -> this is a primary investigation target
- If metrics conflict (one good, one bad) -> investigate the relationship

---

### Phase 3: Segment and Discover Patterns

**Thought**: Overall metrics hide segment-level problems. Which dimensions might explain failures? Let me check each dimension's distribution first, then segment the worst-performing metric by each dimension.

**Action**: For each dimension:
1. `get_unique_dimension_values(experiment_id, dimension_key)` -> see distribution
2. `get_metric_values(experiment_id, metric_label, segment_by_dimension=dimension_key)` -> see performance by segment

**Observation**: For each dimension, note:
- Number of unique values and their frequencies
- Which segments perform worse than overall average
- Any segments with surprisingly good or bad performance

**Decision point**:
- If a segment has <50% of overall pass rate -> high priority deep dive
- If segment has high impact (many events) AND low performance -> highest priority
- If dimension has only 1-2 values -> may not be useful for segmentation
- Rank segments by: Priority = (1 - segment_pass_rate) x segment_count

---

### Phase 4: Deep Dive into Problem Segments

**Thought**: I've identified the worst segments. Now I need to understand WHY they fail. Let me pull specific failing events and examine them.

For each top problem segment (limit to top 3-5):

**Action**:
1. `get_events(experiment_id, filter_dimension_key=X, filter_dimension_value=Y, filter_metric_label=Z, filter_metric_value=false, limit=5)` -> get failing event IDs
2. `get_event_by_id(experiment_id, event_id)` -> examine 2-3 failures in detail

**Observation**: For each failing event, note:
- What was the input/context?
- What did the model output?
- What was expected?
- What specifically went wrong? (schema error, wrong value, missing field, logic error)

**Thought**: Looking across these failures, what's the pattern? Is this:
- **Prompt ambiguity**: Instructions are unclear or contradictory
- **Schema compliance**: Output format doesn't match requirements
- **Tool issue**: Tool calling or parsing problems
- **Evaluator mismatch**: Gold labels may be wrong or inconsistent
- **Data quality**: Input data has issues
- **Logic error**: Model reasoning is systematically flawed

**Decision point**:
- If pattern is clear -> formulate specific fix recommendation
- If pattern is unclear -> pull more examples or check a different angle
- If failures seem random -> may be noise or evaluator issues

---

### Phase 5: Synthesize and Recommend

**Thought**: I now have evidence for the main issues. Let me formulate actionable recommendations with specific fixes.

For each issue:
1. State the problem clearly with evidence
2. Propose a specific fix (with actual prompt/code snippet if applicable)
3. Explain why it should help (tied to evidence)
4. List validation steps
5. Note risks/tradeoffs

**Thought**: What follow-up experiments would validate these fixes? Prioritize by expected impact.

---

### Phase 6: Compile Report

**Thought**: Time to compile findings into the structured report format. Make sure to:
- Include specific numbers and event IDs
- Show the reasoning chain that led to conclusions
- Prioritize issues by severity x impact

Write the report following the Output Format template below.

---

### Phase 7: Offer to Save

After presenting the report, ask:
> "Would you like me to save this report to a markdown file?"

If yes:
- Filename: `experiment-analysis-{experiment_id_first_8_chars}-{YYYY-MM-DD}.md`
- Location: current working directory

---

## Output Format

Structure your analysis report as follows:

```markdown
# Experiment Analysis Report

[2-3 sentence executive summary including: experiment purpose, model used, total events, and key finding with specific numbers. Example: "Overall, **classification is strong** (~84% exact match), but **regression labeling is weak** (~59% exact match overall and only **25%** on cases where the model itself says it's a regression)."]

## Overall Performance Summary

**Link to experiment**: https://app.datadoghq.com/llm/experiments/{experiment_id}

**Events**: [count] (model: `[model_name]`)

### Exact-match metrics (decision-critical)

- **[metric_name]**:
  - True: [count]
  - False: [count]
  - Empty: [count]
  -> **Pass rate**: [count]/[total] = **[percentage]%**

[Repeat for each exact-match metric]

### Quality/rubric scores

- **[metric_name]**: [count] unique values / [total] events ([variance note])
  - For [specific slice]: mean **[value]**, p50 **[value]**

## Worst Segments

| Segment | Severity (key metric) | Impact | Notes |
|---------|----------------------:|-------:|-------|
| `[dimension=value]` | **[metric] pass rate = [X]% ([n]/[total])** | [count] | [Brief explanation] |

## Issue Deep Dives

### Issue 1 - [Descriptive Title]

**Segment**: `[dimension=value]` (impact: **[X] events**)
**Severity**: **[metric] pass rate = [X]%**

**What's happening**:
[Detailed explanation of the failure pattern - what the model is doing wrong and why it matters. 3-5 sentences with specific observations.]

**Representative examples**:

- [Span X](https://app.datadoghq.com/llm/experiments/{experiment_id}?selectedTab=overview&sp=%5B%7B%22p%22%3A%7B%22experimentId%22%3A%22{experiment_id}%22%2C%22spanId%22%3A%22X%22%7D%2C%22i%22%3A%22experiment-details%22%7D%5D&spanId=X): 
[input → output → expected, what went wrong]
- [Span Y](https://app.datadoghq.com/llm/experiments/{experiment_id}?selectedTab=overview&sp=%5B%7B%22p%22%3A%7B%22experimentId%22%3A%22{experiment_id}%22%2C%22spanId%22%3A%22Y%22%7D%2C%22i%22%3A%22experiment-details%22%7D%5D&spanId=Y): [input → output → expected, what went wrong]

**IMPORTANT**: Replace `{experiment_id}` with the actual experiment ID from the data, and replace `X` and `Y` with the actual span IDs from your analysis. Each span link should be clickable and point to the specific span in the Datadog UI.

**Root cause hypothesis** (categorized):
- **[Category]**: [Detailed explanation of why this is happening, tied to specific evidence from the examples above.]

**Recommended fix**:

- **What to change**: [Description of the fix]

  ```text
  [Actual prompt snippet, schema definition, or code to add/modify]
  ```

- **Why it should help**:
  - [Point 1 tied to specific evidence]
  - [Point 2 tied to specific events]

- **Validation**:
  1. [Specific test step]
  2. [Metric to track]
  3. [Events to re-evaluate]

- **Risks/tradeoffs**:
  - [Potential downside 1]
  - [Mitigation approach]

---

[Repeat for each major issue]

## Next Experiments

1. **[Experiment name]** (highest priority): [What to change]. Track [metric] on [segment/events].
2. **[Experiment name]**: [What to change]. [Expected impact].
3. **[Experiment name]**: [What to change]. [Rationale].

[Optional: Offer to pull specific event sets for further analysis]
```

## Operating Rules

- Be explicit when data is missing or ambiguous; don't guess
- Ground conclusions in specific event evidence with IDs
- Show your math: include counts and percentages, not just rates
- Describe aggregation logic clearly when computing metrics
- Focus on fixes that generalize, not one-off hacks
- Prioritize issues by severity x impact
- Include actual prompt/code snippets in recommendations
- Categorize root causes to help identify patterns across issues
- Always offer to save the report at the end
