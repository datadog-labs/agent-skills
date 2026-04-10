---
name: eval-session-classify
description: Classify a sample of LLM Obs traces from an ml_app to produce labeled verdicts (yes/partial/no) for downstream RCA and eval bootstrap. Use when user says "classify traces", "label traces", "classify sessions", "generate eval signal", "classify my app", or wants to produce verdict signal from production data before running eval-trace-rca or eval-pipeline.
---

# Skill: eval-session-classify

Given an `ml_app`, samples production traces from LLM Observability and classifies each one: did the app accomplish the user's intent? Produces labeled verdicts that feed directly into `eval-trace-rca` and `eval-pipeline`.

No pre-existing evaluators required. Classification is based on reading span content (input/output/messages) and any eval scores already on the span.

---

## Inputs

| Input | Required | Default | Description |
|-------|----------|---------|-------------|
| `ml_app` | Yes | — | LLM app to sample traces from |
| `timeframe` | No | `now-7d` | How far back to sample |
| `trace_limit` | No | `20` | Number of traces to classify (cap: 50) |

If `ml_app` is not provided, ask the user.

---

## Available Tools

| Tool | Purpose |
|------|---------|
| `search_llmobs_spans` | Sample root spans from an ml_app |
| `get_llmobs_span_details` | Get content_info map and any existing eval scores |
| `get_llmobs_span_content` | Read span input, output, messages, documents, metadata |
| `get_llmobs_agent_loop` | Full agent execution timeline for agent apps |

---

## Workflow

### Step M1 — Sample traces

```
search_llmobs_spans(
  ml_app          = "<ml_app>",
  root_spans_only = true,
  from            = "<timeframe>",
  to              = "now",
  limit           = 100,
  query           = "@status:ok"
)
```

From the results, randomly sample up to `trace_limit`. Then fetch span details in parallel — one call per trace:

```
get_llmobs_span_details(trace_id=<id>, span_ids=[<root_span_id>])
```

From `content_info`, determine **app type**:

| Signal | App type |
|--------|----------|
| `content_info` has `messages` | LLM/chat |
| `content_info` has `documents` | RAG |
| Spans include `agent` kind | Agent |

Note any existing eval scores in the `evaluations` map — these are additional signal for classification.

If no spans found → stop, report `no_traces_found` and suggest checking the `ml_app` name and timeframe.

### Step M2 — Read trace content (parallel)

For each sampled trace, fetch content based on app type. Issue all calls in a single message.

| App type | Calls to make |
|----------|--------------|
| LLM/chat | `get_llmobs_span_content(field="messages", path="$.messages[-1]")` — final response; `path="$.messages[0]"` — system prompt |
| RAG | `get_llmobs_span_content(field="documents")` + `field="output"` |
| Agent | `get_llmobs_agent_loop(trace_id, root_span_id)` |

Also fetch `field="metadata"` if `content_info` shows it present — may contain task type, user segment, feature flags, or prompt versions.

### Step M3 — Classify each trace

For each trace, determine:

1. **What the task was** — from the span input (user query, task description, or first user message)
2. **What the app produced** — from span output or final assistant message
3. **Whether it succeeded** — using the criteria below

**Satisfaction criteria:**
- `yes`: Output directly and completely addresses the task. No errors, truncation, or refusals. Content is coherent and accurate.
- `partial`: Output addresses part of the task, is correct but incomplete, or shows evidence of degraded quality (vague answers, partial completions, excessive hedging).
- `no`: Output fails to address the task — contains hallucinations, errors, empty responses, wrong tool use, or the task was structurally unachievable.

If existing eval scores are present: treat low scores (bottom quartile or failed assessment) as additional evidence for `partial` or `no`.

**Failure mode codes:**

| Code | Meaning |
|------|---------|
| `wrong_answer` | Factually incorrect claim |
| `incomplete_answer` | Correct but missed important paths |
| `hallucination` | Invented facts, IDs, or URLs not in the data |
| `wrong_tool_use` | Called wrong tool or with wrong parameters |
| `excessive_turns` | Goal achieved but took too many round-trips |
| `context_loss` | Forgot earlier context or repeated mistakes |
| `broke_existing_state` | Damaged something the user had |
| `other: <describe>` | |

### Step M4 — Emit per-trace blocks and summary

Emit a compact block for each trace as it is classified (do not wait for all to finish):

```markdown
## Trace: <trace_id>

- **Span ID:** <root_span_id>
- **Verdict:** yes | partial | no
- **Failure mode:** <code> | none
- **Failure mode detail:** <one sentence>
- **App type:** LLM | RAG | Agent
- **Signal:** content-only | content+evals
```

After all traces are classified, emit the summary. The `# Session Classification Summary` header is the **detection sentinel** for downstream skills (`eval-trace-rca`, `eval-pipeline`) — emit it exactly as shown:

```markdown
# Session Classification Summary

**App:** `<ml_app>`  |  **Timeframe:** <from> → now  |  **Traces sampled:** <N>

## Verdict Distribution

| Verdict | Count | % |
|---------|------:|:-:|
| yes     | N     | % |
| partial | N     | % |
| no      | N     | % |

## Failure Mode Frequency

| Failure Mode | Count | % of failures |
|-------------|------:|:-------------:|
| <mode>      | N     | %             |

## Per-Trace Details

| Trace ID | Verdict | Failure Mode | Signal | Link |
|----------|---------|-------------|--------|------|
| <id_short> | yes | none | content-only | [link](https://app.datadoghq.com/llm/traces?query=trace_id:<full_id>) |
```
