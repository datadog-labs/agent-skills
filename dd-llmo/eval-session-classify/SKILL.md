---
name: eval-session-classify
description: Classify whether a user's intent was satisfied in a Datadog assistant session. Use when given a session_id and asked to evaluate satisfaction, intent classification, or RUM-based session quality.
---

# Skill: eval-session-classify

Given a Datadog assistant session ID, classify whether the user's intent was satisfied.
Uses the Datadog LLM Obs and core MCP servers.

---

## Input

A single `session_id` UUID, e.g. `a1b2c3d4-e5f6-7890-abcd-ef1234567890`.

---

## What the Datadog MCP LLM Obs toolset gives you

Starting from only a `session_id`, three MCP calls reconstruct everything:

| Call | What you get |
|------|-------------|
| `search_llmobs_spans(session_id:<id>)` | `trace_id`, agent `span_id`, all span tags: `user_handle`, `user_id`, `org_id`, `product_area`, `message_id`, `iteration` counts, `stop_reason`, `matched_model_name`, tool names |
| `get_llmobs_span_details(agent_span_id)` | All evaluations on the span with full reasoning (any judges that ran — built-in, user-uploaded, or external), `content_info` map (shows available metadata fields: `query_string`, `referrer_path`, `referrer_url`, `entities_json`, `user_info_json`) |
| `get_llmobs_agent_loop(trace_id, agent_span_id)` | **Full conversation**: system prompt, user message + ROUTE_CONTEXT (recent pages with popularity scores), assistant thinking blocks, all tool call arguments + results, final response |

The agent loop tool reconstructs the full conversation from
the child LLM spans, which do carry content — the null inputs/outputs are only on the agent
span itself, not on the underlying `anthropic.request` spans.

---

## Step 1 — Get trace identity and span structure

```
search_llmobs_spans(
  query  = "session_id:<SESSION_ID>",
  from   = "<reasonable window, e.g. now-30d>",
  to     = "now",
  limit  = 50
)
```

From the results, extract:
- `trace_id` (same on all spans)
- `span_id` of the span with `span_kind=agent` and `name=assistant` → this is the **agent span**
- From agent span tags: `user_handle`, `user_id`, `org_id`, `product_area`, `message_id`, `start_ms`
- Tool span names (all `span_kind=tool` entries) → what tools were called
- Iteration count from `iteration` tags on `get_answer_from_model_step` spans (0-indexed, so max+1 = total iterations)
- `stop_reason` on the last iteration's LLM span (`end_turn` = clean finish, `tool_use` = ended mid-tool)

**Key metadata available from tags alone — before reading any content:**
- `product_area` → the pod (`workflow`, `rca`, `dashboard`, `monitor`, etc.)
- `user_handle` → email, needed for RUM queries
- `matched_model_name` → which model served the session
- `mcp:true/false` on tool spans → whether tools were MCP-backed
- `response_truncated:true/false` → whether the response was cut off

**If `search_llmobs_spans` returns no results** → stop immediately, output error `llmobs_not_found`.

---

## Step 2 — Get evaluations and metadata fields

```
get_llmobs_span_details(
  trace_id  = "<TRACE_ID>",
  span_ids  = ["<AGENT_SPAN_ID>"],
  from/to   = <same window>
)
```

### Evaluations

The `evaluations` map contains every judge verdict that ran against this span. Evaluations in
LLM Observability are simply named key-value results attached to a span — any party can upload
them. There are two common sources:

**Platform-run judges** (run server-side by Datadog automatically on sampled spans):
these show up with names like `tribunal_*` or `prompt-injection`. Each has a `.value` (the
label or score) and usually a `.reasoning` (prose explanation from the judge LLM). Always
read the reasoning, not just the value — it often contains the most useful signal.

**User-uploaded evaluations** (run externally and pushed back via the LLM Obs SDK or API):
these appear under whatever name the team chose. They follow the same structure. Treat them
with the same weight — a user-defined judge for e.g. `groundedness` or `helpfulness` is as
authoritative as a platform judge.

**How to read the evaluations map:**
- Iterate over all keys — don't assume a fixed set of judge names
- For each evaluation: note `.value` (the verdict), `.reasoning` (if present), and `.tags`
  (which can carry experiment names, judge versions, billing plan, etc.)
- Categorical evaluations have a string `.value`; score evaluations have a numeric `.value`
- A missing evaluation key means the judge didn't run on this span (sampling, not failure)

Treat whatever evaluations are present as the authoritative set for that span.

### Metadata fields

From `content_info.metadata`:
- `query_string` → the raw text the user typed
- `referrer_path` → the Datadog page the user was on when they opened the assistant
- `referrer_url` → full URL
- `entities_json` → any Datadog entities the user added as context (`@asset`)
- `user_info_json` → user profile info

**These metadata fields are available via `get_llmobs_span_content(field="metadata")` if you
need the actual values.** The `content_info` map only confirms they exist and their type.

**Known bug:** `get_llmobs_span_details` `span_ids` parameter consistently fails with a Go type
unmarshal error regardless of how the array is passed. This is a known platform bug affecting all
sessions — skip step 2 silently and proceed to step 3. This is not a classification error.

---

## Step 3 — Read the full conversation

```
get_llmobs_agent_loop(
  trace_id           = "<TRACE_ID>",
  span_id            = "<AGENT_SPAN_ID>",
  from/to            = <same window>,
  max_content_length = 3000   ← increase for full tool results
)
```

**If this call returns a 404 or any error** → stop immediately, output error `llmobs_content_expired`.

**If the response contains `<REDACTED_INPUT>` or `<MASKED_STREAMING_RESPONSE>`** → stop immediately,
output error `llmobs_content_masked`. Do not attempt to classify from tool names or structural signals.

The response has two parallel structures:

**`iterations[]`** — one entry per LLM call in order:
- `iteration` (1-indexed)
- `content` — the assistant's output for this iteration (thinking block JSON or final text)
- `tool_calls[]` — array of `{name, arguments, result}` for any tool calls made
- `input_tokens`, `output_tokens`, `cache_read_input_tokens` — token economics
- `stop_reason` implicit from whether there are more iterations

**`timeline[]`** — flat chronological message list:
- `role: system` at iteration 1 — the full system prompt
- `role: user` at iteration 1 — the user's message + full `ROUTE_CONTEXT` (recent pages with
  popularity scores) + any custom context
- `role: assistant` at each iteration — thinking blocks and tool calls
- Final assistant response as the last `role: ""` entry (the actual text sent to the user)

**From the user message's ROUTE_CONTEXT you get:**
- Which pages the user visited recently and how often (popularity score)
- The current page they were on when they sent the message
- What Datadog products/features they were working in before the session

This is pre-session behavioral context embedded in the LLM Obs trace — no RUM query needed
to understand what the user was doing.

---

## Step 4 — Get RUM behavioral signals

With `user_handle` (from step 1) and `start_ms` (agent span start), define the time window:

- **pre**: `[start_ms − 30min, start_ms]`
- **during**: `[start_ms, start_ms + session_duration_ms]`
- **post**: `[start_ms + session_duration_ms, start_ms + session_duration_ms + 60min]`

Run two RUM queries in parallel via `analyze_rum_events` (SQL-based, `rum` toolset).

**If either RUM query returns 0 rows**, query a wider window (last 30 days) to check whether the
user has any web RUM data at all. If they have data on other days but not on the session date
(web RUM gap), or if they have no RUM data at all → stop, output error `rum_unavailable`.
Do not fall back to trace-only classification.

### RUM Query A — Page view timeline

```
analyze_rum_events(
  event_type  = "view",
  filter      = "@usr.email:<user_handle>",
  from        = <pre_start>,
  to          = <post_end>,
  sql_query   = "SELECT timestamp, view_url, \"@view.time_spent\" FROM rum ORDER BY timestamp LIMIT 200",
  extra_columns = [{"name": "@view.time_spent", "type": "int64"}]
)
```

`@view.time_spent` is in nanoseconds — divide by 1e9 for seconds. Sort ascending for navigation arc.

### RUM Query B — Custom actions only

```
analyze_rum_events(
  event_type    = "action",
  filter        = "@action.type:custom @usr.email:<user_handle>",
  from          = <pre_start>,
  to            = <post_end>,
  sql_query     = "SELECT timestamp, \"@action.name\", view_url FROM rum ORDER BY timestamp LIMIT 200",
  extra_columns = [{"name": "@action.name", "type": "string"}]
)
```

`@action.type:custom` filters to developer-instrumented events only, excluding auto-collected clicks and keypresses. 
A typical 1.5h session window yields ~150–200 custom events — manageable in 1–2 calls.

From each row: `@action.name`, `view_url`, `timestamp`. Sort ascending.

**Pagination**: if `is_truncated: true`, re-call with `start_at=<displayed_rows>` and the same
`LIMIT`. `start_at` is a row offset into the SQL result, not a cursor. Use `max_tokens` to tune
response size.

**External customer RUM noise**: Some sessions generate far more framework telemetry per page load. APM service pages can emit 200+ custom events in 2 minutes, exhausting the LIMIT before reaching the session time. When this happens, use SQL `WHERE` filters to reduce noise. Each agent team will have their own set of actions to filter out or target — the examples below are illustrative, not prescriptive.

**Query 1 — Navigation & non-assistant actions (framework noise filtered):**

```python
analyze_rum_events(
    event_type    = "action",
    filter        = "@action.type:custom @usr.email:<user_handle>",
    from          = <pre_start>,
    to            = <post_end>,
    sql_query     = """
        SELECT timestamp, "@action.name", view_url
        FROM rum
        WHERE "@action.name" NOT IN (
            'dataviz.first_significant_render',
            'perf.scroll.dashboard',
            'perf.trafficTelemetry.initialLoad',
            'getInitialContrastMode',
            'DSM__root__Widget-Map--view',
            'DataStreamsRelationGraph__ErrorBoundary--view',
            'DataStreamsRelationGraphWrapper__ErrorBoundary--view',
            'dsm-topology-map-fetch-finish',
            'apm_autopilot_notification_stats',
            'noEventInCache',
            'useEventPlatformQuery without query',
            'Experiments explicit fetch completed',
            'discussions.discussionCountFetched',
            'Feature Flags Provider'
        )
        ORDER BY timestamp LIMIT 200
    """,
    extra_columns = [{"name": "@action.name", "type": "string"}]
)
```

**Query 2 — Assistant-specific signals only:**

```python
analyze_rum_events(
    event_type    = "action",
    filter        = "@action.type:custom @usr.email:<user_handle>",
    from          = <pre_start>,
    to            = <post_end>,
    sql_query     = """
        SELECT timestamp, "@action.name", view_url
        FROM rum
        WHERE (
            "@action.name" LIKE 'command-assistant%'
            OR "@action.name" LIKE 'workbench%'
            OR "@action.name" LIKE 'ai-experiences%'
            OR "@action.name" = 'click on Bad response'
            OR "@action.name" = 'click on Incorrect result'
            OR "@action.name" = 'click on Submit'
            OR "@action.name" = 'click on Reasoning'
            OR "@action.name" = 'Rendered a Code block'
        )
        ORDER BY timestamp LIMIT 200
    """,
    extra_columns = [{"name": "@action.name", "type": "string"}]
)
```

These two complementary queries — one filtering out known noise, one targeting known signal — typically reduce thousands of events to the handful that matter for classification. Your agent team's RUM instrumentation will differ; adapt the action names accordingly.

**To interpret what the action names mean for your agent, consult the agent-specific RUM
action reference file:**

@rum-actions-bits-assistant.md

Each LLM agent team instruments their own custom actions. If you are classifying a different
agent, you need the equivalent reference file for that agent's RUM instrumentation. The
classification logic (what constitutes a positive vs negative post-session signal) depends
entirely on which actions are tracked and what they mean in that product's context.

---

## Step 5 — Classify

With the conversation (step 3), evaluations (step 2), and RUM signals (step 4), apply
the following classification schema.

### User intent (1 sentence)
From the user message in the agent loop timeline. What did they want to achieve?

### Pod
Primary source: `product_area` tag from step 1.
Secondary confirmation: any pod/area classifier evaluation present in step 2 (e.g. `tribunal_pod_classifier.value`).
If they disagree, note the discrepancy — the tag reflects where the user was in the UI,
the evaluation reflects what the conversation was actually about.

### What the assistant did (2–4 bullets)
From the agent loop: which tools were called (with arguments), what docs/data was retrieved, what the final response said.

### Failure mode taxonomy

| Code | Meaning |
|------|---------|
| `wrong_answer` | Factually incorrect claim (check feature flags for platform limitation claims) |
| `incomplete_answer` | Correct as far as it went, but missed important paths |
| `broke_existing_state` | Assistant damaged something the user had |
| `excessive_turns` | Goal achieved but took too many round-trips |
| `context_loss` | Assistant forgot earlier context or repeated mistakes |
| `wrong_tool_use` | Called wrong tool or with wrong parameters |
| `hallucination` | Invented IDs, URLs, or facts not in tool results |
| `other: <describe>` | |

### Satisfaction verdict
`yes` / `partial` / `no`

**From the trace alone:**
- `yes`: Final response directly answers the user's intent, no negative feedback, no abandon signals
- `partial`: Response was partially right or user got unblocked through continued effort
- `no`: Negative feedback given, user abandoned, or core intent structurally unachievable with the response given

---

## Error output schema

When any required data source fails, stop classification and emit:

```json
{
  "session_id": "<id>",
  "classification_with_rum": "error",
  "error": "<error_code>: <detail>"
}
```

Error codes:
- `llmobs_not_found` — `search_llmobs_spans` returned no spans for this session_id
- `llmobs_content_expired` — `get_llmobs_agent_loop` returned 404 (trace past retention window)
- `llmobs_content_masked` — agent loop returned `<REDACTED_INPUT>` / `<MASKED_STREAMING_RESPONSE>`
- `rum_unavailable` — no web RUM data found for this user on or around the session date

---

## Classification output schema

```markdown
# Classification: <session_id>

## Session metadata
- **Trace ID:** <trace_id>
- **Agent span ID:** <span_id>
- **Start:** <UTC timestamp>
- **Duration:** <seconds>s
- **User:** <user_handle>
- **Product area:** <tag value>
- **Model:** <matched_model_name>
- **Iterations:** <N> (stop reason: <end_turn|tool_use>)
- **Tools called:** <tool names, counts>
- **Evaluations:** <name: value — "reasoning excerpt"> for each judge that ran
- **Referrer page:** <referrer_path>

## User intent
One sentence.

## What the assistant did
- Bullet 1
- Bullet 2

## Why the user gave negative feedback
(skip if no feedback)

## Was the core intent satisfied?
**yes / partial / no** — one sentence justification.

## Failure mode
- `code`: explanation

## RUM behavioral signals

### Pre-session context (from ROUTE_CONTEXT in agent loop)
What the user was working on before the session, from the popularity-scored page list.

### Assistant panel actions
| Time | Action | Page |
|------|--------|------|

### Post-session navigation
| Time | URL | Dwell |
|------|-----|-------|

### Feature flags (from RUM event on session page)
List any product-area flags that are relevant to the classification.

### RUM verdict
One sentence: does behavioral evidence support or contradict the trace-only verdict?

## Revised satisfaction verdict (with RUM)
yes / partial / no
```

---

## Notes on limitations

**What LLM Obs MCP gives that nothing else does:**
- The `ROUTE_CONTEXT` in the user message is pre-session behavioral context embedded in the
  trace — the assistant received this as part of the conversation. It lists pages the user
  visited recently with popularity scores. This is more accurate than RUM page views for
  understanding pre-session intent because it's what the assistant actually saw.
- Evaluation reasoning fields are full prose explanations, not just labels — always read
  `.reasoning`, not just `.value`. This applies to any judge, platform-run or user-uploaded.
- Token economics per iteration (`input_tokens`, `cache_read_input_tokens`) reveal whether
  the model was working with a hot cache (cost-efficient, but also means the conversation
  context was being reused from prior sessions — relevant for context_loss analysis).

**What LLM Obs MCP does NOT give:**
- Post-session behavior: what did the user do after getting the response? RUM required.
- Feature flags active on the user's browser: RUM required.
- Whether the user continued with more sessions after giving negative feedback: RUM required
  (count of `ai-experiences.chat-submit` events after `start_ms + duration`).
- Other concurrent sessions by the same user in the same time window: requires a second
  `search_llmobs_spans` call filtered by `user_handle` across a broader window.
