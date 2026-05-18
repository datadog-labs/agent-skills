---
name: llm-obs-session-classify
description: Classify whether user intent was satisfied in a Datadog LLM Obs trace or session. Three modes: (1) session_id — classify a single CMD+I assistant session with RUM; (2) trace_id — classify a single LLM Obs trace without RUM; (3) ml_app — sample and classify multiple sessions or traces from a given LLM app. Output is compact by default (verdict + one-sentence reason). Use when evaluating satisfaction, classifying sessions/traces, labeling data, or generating signal for llm-obs-eval-pipeline or llm-obs-trace-rca.
---

## Backend

**Detection** — At the start of every invocation, before taking any action, determine which backend to use:

1. If the user passed `--backend pup` anywhere in their invocation → use **pup mode** immediately, regardless of whether MCP tools are present. Skip steps 2–4.
2. Check whether MCP tools are present in your active tool list. The canonical signal is whether `mcp__datadog-llmo-mcp__search_llmobs_spans` appears in your available tools.
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
- Time flags: pup accepts bare duration strings (`1h`, `7d`, `30m`) and RFC3339 timestamps. Do **not** use `now-`-prefixed strings — strip the prefix when converting from a skill `--timeframe` argument: `now-7d` → `7d`, `now-24h` → `24h`, `now-30d` → `30d`.
- `--summary` on `pup llm-obs spans search` strips payload fields to essential metadata only. Use it in bulk/search phases where content is not needed.

**pup mode notes by entry mode:**
- `session_id` mode: Steps 1–3 and Step 5 work fully. Step 4 (RUM) uses `pup rum aggregate --user-email EMAIL` instead of `analyze_rum_events` — see Tool Reference. Step 4b (audit trail) is pup-native and queries the active user's own org via OAuth.
- `trace_id` mode: Full parity with MCP mode.
- `ml_app` mode: Option A (`aggregate_spans`) is unavailable in pup — skip it and proceed directly to Option B.

**Intent tagging:** On every MCP tool call, prefix `telemetry.intent` with `skill:llm-obs-session-classify — ` followed by a description of why the tool is being called.

# Skill: eval-session-classify

Classification skill for Datadog LLM Obs sessions and traces. Produces a satisfaction verdict (`yes` / `partial` / `no`) with a brief reasoning string. Designed to be called standalone or by `eval-pipeline`.

---

## Inputs

| Input | Mode | Required | Description |
|-------|------|----------|-------------|
| `session_id` | session_id mode | Yes | UUID of a Datadog CMD+I assistant session |
| `trace_id` | trace_id mode | Yes | Trace ID from LLM Observability |
| `ml_app` | ml_app mode | Yes | LLM app name to sample from |
| `timeframe` | ml_app mode | No (default: `now-7d`) | How far back to sample |
| `sample_limit` | ml_app mode | No (default: `20`, cap: `50`) | Number of sessions or traces to classify |

**If none of `session_id`, `trace_id`, or `ml_app` is provided → stop immediately and return:**

```json
{
  "error": "missing_input",
  "detail": "Provide one of: session_id, trace_id, or ml_app."
}
```

---

## Phase 0 — Mode Detection

- `session_id` provided → **session_id mode** → go to [Session Mode](#session-mode)
- `trace_id` provided (no `session_id`) → **trace_id mode** → go to [Trace Mode](#trace-mode)
- `ml_app` provided (no `session_id`, no `trace_id`) → **ml_app mode** → go to [ML App Mode](#ml-app-mode)
- Nothing provided → emit error (see above)

---

## Output Format

**Default (compact) — used in all modes unless the caller requests verbose:**

```
verdict: yes | partial | no
reason: <one sentence>
```

**Verbose** — full markdown report (see end of each mode section). Request verbose by including `verbose: true` in input or asking for a detailed report.

In ml_app mode, a summary table is always appended after the per-trace compact blocks.

---

## Content Retrieval Cascade

Reading conversation content follows this cascade across all modes. Run the cascade for every turn root span identified in Step 1 (session mode) or Step T1 (trace mode) before forming a verdict.

> **Completeness gate**: do not proceed to RUM (session mode) or classification (any mode) until the cascade has run for every turn. Previews from `search_llmobs_spans` (`input.preview` / `output.preview`, truncated to ~200 chars) do NOT satisfy this gate — they identify turn boundaries, nothing more. Only actual content from `get_llmobs_agent_loop`, `expand_llmobs_spans` + `get_llmobs_span_content`, or `get_llmobs_span_content(field="input"/"output")` counts.
>
> **Short sessions** (≤ 20 turns): run C1/C2 for every turn — no selection, no "key turns" heuristic.
>
> **Long sessions** (> 20 turns): run C1/C2 for the **first 5** and **last 5** turns, plus any turn whose `input.preview` or `output.preview` flags an anomaly (error, correction loop, repeated intent, unexpected tool). Scan all remaining turns' previews for anomalies before skipping them.

**Parallelism**: issue all N `get_llmobs_agent_loop` calls in a single message — one call per turn, all in the same batch. For an N-turn session this means N simultaneous C1 calls. Do not process turns sequentially and do not form partial verdicts before all results are in hand. Move to C2 for a turn only when C1 returns `iterations: []` for that turn.

### C1 — `get_llmobs_agent_loop(trace_id, agent_span_id)`

The richest source: full system prompt, user message, tool call arguments + results, assistant response, and token economics per iteration. Attempt this first for every agent span.

```
get_llmobs_agent_loop(
  trace_id           = "<TRACE_ID>",
  span_id            = "<AGENT_SPAN_ID>",
  from               = "now-90d",
  to                 = "now",
  max_content_length = 2000
)
```

- **`iterations: []` and `timeline: null`** → the app's LLM spans go through an intermediate workflow layer (e.g. `get_answer_from_model_step`) rather than as direct children of the agent span. Fall through to C2.
- **404** → span ID does not resolve in the trace store. Fall through to C2.
- **Content fields contain `<REDACTED_INPUT>` or `<MASKED_STREAMING_RESPONSE>`** → IO tracing is disabled by a feature flag. Structure (iteration count, tool names, token usage) is still useful — note it. Fall through to C2 for actual content.

**When a C1 result is too large for context and gets persisted to disk** (common on long sessions): do NOT try to Read the full file — it will exceed the token limit. Run a targeted Python extraction to build a structured per-turn summary:

```python
import json
with open('<persisted_path>') as f:
    data = json.loads(json.load(f)[0]['text'])
iters = data['iterations']
for it in iters:
    tcs = [tc['name'] for tc in it.get('tool_calls', [])]
    content = it.get('content', '') or ''
    # Skip the system prompt body — it's verbose and not what we're classifying.
    if len(content) > 4000 and content.lstrip().startswith('#'):
        content = '[system prompt]'
    print(f"iter {it['iteration']} [{it.get('status')}] tools={tcs}"
          f" err={it.get('error_message','')} content={content[:300]}")
```

A large-but-parseable C1 result is real content; only fall through to C2 if C1 returned `iterations: []`.

### C2 — `get_llmobs_span_content(field="messages")` on LLM child spans

When C1 returns `iterations: []`, the LLM spans typically sit 2 levels below the root agent span — under a workflow wrapper (e.g. `get_answer_from_model_step`) that the agent-loop API does not descend into. The concrete tree per turn:

```
<root agent span>                          ← one per turn
  <workflow wrapper>                       ← one per LLM round-trip
    <llm span, e.g. anthropic.request>     ← call `field="messages"` here
```

Use `expand_llmobs_spans` to navigate to the LLM span IDs — **not** `get_llmobs_trace`, which only returns depth-1 children and cannot reach LLM spans nested under a workflow:

```python
expand_llmobs_spans(
  trace_id  = "<TRACE_ID>",
  span_ids  = ["<ROOT_AGENT_SPAN_ID>"],
  max_depth = 2,          # root → workflow → llm span
  from      = "now-90d",  # required: default is now-1d, silently returns empty for older spans
  to        = "now"
)
```

From the returned tree, collect all nodes with `span_kind=llm` and `has_input=true` (commonly named `anthropic.request`, `openai.request`, `chat_completion-call`, `messages-call`). Call `get_llmobs_span_content(field="messages")` on each.

**JSONPath + truncation trap**: `path` is applied *after* `max_tokens` truncation. Each LLM-call span typically starts with a multi-KB system prompt, so a low `max_tokens` means the JSONPath filter operates on system-prompt-only content and silently returns the wrong messages.

To extract the meaningful tail (user query, tool calls, final answer):
1. Call once without `path` to read `total_tokens_approx` from the response.
2. Re-call with `path = "$.[-5:]"` and `max_tokens = total_tokens_approx + 500`.

```
get_llmobs_span_content(
  trace_id   = "<TRACE_ID>",
  span_id    = "<LLM_SPAN_ID>",
  field      = "messages",
  path       = "$.[-5:]",
  max_tokens = <total_tokens_approx + 500>
)
```

This returns the last 5 messages: typically [user context+query, reasoning, assistant text, assistant tool_call, tool result] — enough to understand what the turn did.

- **Messages are `"REDACTED"`** → IO tracing disabled. Fall through to C3.
- **`content_info` map does not include `messages`** → not a chat span. Fall through to C3.

### C3 — `get_llmobs_span_content(field="input")` and `field="output"` on the root span

The root span often carries synthetic summaries written by the app (e.g. the raw user query as `input`, the final response as `output`, or `"Investigate error for issue: <id>"` / `"Investigation completed with status: completed"`). Minimal signal, but enough to confirm what the task was and whether it completed.

### C4 — Structural signals only

When all content is inaccessible, classify from span metadata alone:
- `status` (ok / error), `stop_reason`, `response_truncated`
- Child span names (tool names, workflow step names like `classify`, `generate-summary`, `suggest-action`)
- `iteration` tag count (total LLM rounds)
- Duration and token counts if available

### Drill-down — targeted extraction for suspicious tool results

After the summary pass, drill into any turn whose tool list includes a client-rendered tool, a write operation, or an iteration flagged with `error_message`. These checks surface silent failures the summary loop cannot detect.

**Triggers:**

- **Client-rendered tools** (browser-handled, e.g. `show_content`, `create_datadog_notebook`, `edit_datadog_notebook`) — **always mandatory** when present. The server fires an event; the client handles display. The result tells you whether rendering was delegated or confirmed.
  - `"This tool is handled by the client application..."` → delegated to browser, **no server-side confirmation the user saw anything**. Check for a `client_tool_response` in the next turn's input. If absent, or if the next turn shows user confusion, treat as invisible output — strong negative signal.
  - Any other result → executed server-side; output was in the response stream.

- **Write operations** (e.g. `call_datadog_api`, `upsert_datadog_dashboard`, `upsert_widget`, monitor/notebook create-or-update) — the assistant often narrates "I updated X" regardless of the actual HTTP outcome. Check the result for:
  - `403` / `"not allowlisted"` / `"PUT not allowlisted"` → write blocked; the change was **never applied**.
  - `404` → asset not found; editing something that doesn't exist.
  - `500` / `"internal server error"` → write uncertain, may not have landed.
  - `"Session not found"` → resource expired before the tool ran.
  - `200` / `201` with payload → confirmed success.

- **Data queries returning empty** (e.g. `search_datadog_metrics`, `get_datadog_metric`, `search_datadog_logs`, `search_datadog_spans`) — when the summary shows many iterations (> 5) of the same query tool, check whether results were empty. An assistant reasoning from `NO_DATA` / empty arrays may produce plausible-sounding but fabricated analysis (`hallucination` failure mode).

- **Error iterations** — any iteration where `error_message` is set: confirm the exact error, which tool triggered it, and whether the assistant retried successfully or gave up. A transient retry is neutral; a permanent API restriction or repeated identical failure is negative.

```python
CLIENT_TOOLS = {'show_content', 'create_datadog_notebook', 'edit_datadog_notebook'}
WRITE_TOOLS  = {'call_datadog_api', 'upsert_datadog_dashboard', 'upsert_widget'}
FAIL_STRINGS = {'403', '404', '500', 'not allowlisted', 'not found', 'session not found', 'error'}

for it in iters:
    if it.get('error_message'):
        print(f"ERROR  iter={it['iteration']}: {it['error_message'][:200]}")
    for tc in it.get('tool_calls', []):
        name   = tc['name']
        result = (tc.get('result') or '')
        if name in CLIENT_TOOLS:
            print(f"CLIENT iter={it['iteration']} {name}: {result[:300]}")
        elif name in WRITE_TOOLS or any(s in result.lower() for s in FAIL_STRINGS):
            print(f"WRITE  iter={it['iteration']} {name}: {result[:300]}")
```

**Interpretation cheat sheet:**

| Result pattern | Verdict signal |
|---|---|
| `"This tool is handled by the client application"` | Unconfirmed rendering → check next-turn `client_tool_response` |
| `403` / `"not allowlisted"` | Write blocked — core intent may be unachievable via assistant |
| `"Session not found"` | Resource expired — tool call had no effect |
| `NO_DATA` / empty list on data query | No real signal — check for hallucination in assistant's answer |
| `500` on write op | Uncertain — may or may not have landed |
| `200` / `201` with payload | Confirmed success |

The tool-name lists above are examples calibrated for Datadog assistant apps. For other apps, look at the tool names that appear in C1's `tool_calls` and reason about their semantics (client rendering vs server side-effect vs read-only query).

---

## Session Mode

Classifies a single Datadog CMD+I assistant session. Uses RUM behavioral signals to confirm the trace-based verdict.

### Step 1 — Enumerate turn root spans

```
search_llmobs_spans(
  tags            = {"session_id": "<SESSION_ID>"},
  root_spans_only = True,
  from            = "now-90d",
  to              = "now",
  limit           = 500
)
```

> **pup mode**: `pup llm-obs spans search --query "@session.id:<SESSION_ID>" --root-spans-only --limit 500 --from 90d`

Use `tags={...}` (not the `query` string form). The free-form `query="session_id:<id>"` triggers full-text behavior that returns the entire trace bundle for every turn (5× the spans, structural filters silently ignored) and exhausts the row budget on long sessions. `tags={"session_id": "<id>"}` hits the indexed tag filter and respects the other parameters.

`root_spans_only=True` returns one span per user turn — typically the root agent span — rather than every workflow/LLM/tool descendant. Paginate via `next_cursor` if the session has more than 500 turns.

For each returned root span, record:
- `trace_id`, `span_id`
- `start_ms`, `duration_ms`
- Tags: `user_handle`, `user_id`, `org_id`, `product_area`, `message_id`
- `name` and `span_kind` of the root (usually `agent` / `assistant`, but app-dependent)
- `input.preview`, `output.preview` (for turn-boundary identification only — see warning below)

Sort the turns ascending by `start_ms`. From the first turn's tags, capture session-level identity: `user_handle`, `user_id`, `org_id`, `product_area`, session `start_ms`.

> **Preview-truncation warning**: `input.preview` and `output.preview` are truncated to ~200 characters. They identify turn boundaries and surface obvious anomalies — they are NOT sufficient for verdict formation. Never skip the [Content Retrieval Cascade](#content-retrieval-cascade) because the previews already "look conclusive."

**If `search_llmobs_spans` returns no results** → stop, return error `llmobs_not_found`.

Per-turn structural details (iteration count, tool names, `stop_reason`, `mcp` flag, `response_truncated`) are recovered later from each turn's `get_llmobs_agent_loop` result in Step 3 (C1).

### Step 2 — Get evaluations and metadata

```
get_llmobs_span_details(
  trace_id  = "<TRACE_ID>",
  span_ids  = ["<AGENT_SPAN_ID>"],
  from/to   = <same window>
)
```

From the `evaluations` map: iterate all keys, note `.value`, `.reasoning`, `.tags` for each judge.
From `content_info.metadata`: `query_string`, `referrer_path`, `referrer_url`, `entities_json`, `user_info_json`.

If `get_llmobs_span_details` fails or returns empty → skip silently, proceed to step 3.

If `content_info` shows `metadata` is present, fetch it via `get_llmobs_span_content(field="metadata")` to get the actual `query_string` and `referrer_path` — these give the user's question and the page they were on without needing the agent loop.

### Step 3 — Read the full conversation

For every turn root span enumerated in Step 1, follow the [Content Retrieval Cascade](#content-retrieval-cascade). Issue all per-turn C1 calls (`get_llmobs_agent_loop`) in a single parallel batch per the cascade's parallelism rule, then fall through to C2 for any turns where C1 returns `iterations: []`.

For apps where LLM spans sit under a workflow wrapper (e.g. `assistant_api`, where `anthropic.request` is a grandchild via `get_answer_from_model_step`), C1 will routinely return empty iterations — this is expected and the cascade handles it by routing those turns through C2's `expand_llmobs_spans` navigation. C3 (metadata + output on the root span) is the last resort when no LLM-call content is available.

### Step 4a — Resolve RUM session ID(s)

Before the main RUM queries, run a narrow ±2min lookup around the first turn's `start_ms` to find the exact browser session(s) the user was on. This isolates the RUM `session_id`(s) and avoids cross-session bleed that occurs when filtering only by `@usr.email` over a wide time window (a user with multiple tabs across the day will return events from all of them).

```
analyze_rum_events(
  event_type    = "action",
  filter        = "@usr.email:<user_handle> @action.type:custom",
  from          = <start_ms - 120000>,   # 2 minutes before first turn's start_ms (epoch ms)
  to            = <start_ms + 120000>,   # 2 minutes after
  sql_query     = 'SELECT DISTINCT session_id FROM rum LIMIT 10'
)
```

Extract all distinct `session_id` values → store as `rum_session_ids`. The RUM filter for all subsequent queries (`<rum_filter>` below) is determined by the result:

- **0 results** → `<rum_filter>` = `@usr.email:<user_handle>` (fallback to email + time window).
- **1 result** → `<rum_filter>` = `@session.id:<rum_session_id>`.
- **Multiple results** → `<rum_filter>` = `@session.id:(<id1> OR <id2>)`.

> **pup mode**: `pup rum aggregate --user-email <user_handle> --query "@action.type:custom" --from <start_ms - 120000> --to <start_ms + 120000> --compute count --group-by @session.id`. Confirm the `--group-by` flag name with `pup rum aggregate --help`.

### Step 4 — Get RUM behavioral signals

> **pup mode**: Replace `analyze_rum_events` calls with `pup rum aggregate` calls (see Tool Reference). Page views: `pup rum aggregate --user-email USER_HANDLE --from START_MS --to END_MS --compute count --group-by @session.id`. Custom actions: `pup rum aggregate --user-email USER_HANDLE --query "@action.type:custom" --from START_MS --to END_MS --compute count --group-by @evt.name`. Confirm event-type filtering flag with `pup rum aggregate --help` if the API rejects the query.

With `start_ms` and `<rum_filter>` (from Step 4a), define the window:
- **pre**: `[start_ms − 30min, start_ms]`
- **during**: `[start_ms, start_ms + session_duration_ms]`
- **post**: `[start_ms + session_duration_ms, start_ms + session_duration_ms + 60min]`

Run in parallel:

**RUM Query A — Page view timeline:**
```
analyze_rum_events(
  event_type    = "view",
  filter        = "<rum_filter>",
  from/to       = <pre to post>,
  sql_query     = "SELECT timestamp, view_url, \"@view.time_spent\" FROM rum ORDER BY timestamp LIMIT 200",
  extra_columns = [{"name": "@view.time_spent", "type": "int64"}]
)
```
`@view.time_spent` is in nanoseconds.

**RUM Query B — Custom actions (product-area narrowed):**
```
analyze_rum_events(
  event_type    = "action",
  filter        = "@action.type:custom <rum_filter>",
  from/to       = <pre to post>,
  sql_query     = """
    SELECT timestamp, "@action.name", view_url FROM rum
    WHERE (
      "@action.name" LIKE 'command-assistant%'
      OR "@action.name" LIKE 'workbench%'
      OR "@action.name" LIKE 'ai-experiences%'
      OR "@action.name" = 'click on Bad response'
      OR "@action.name" = 'click on Incorrect result'
      OR "@action.name" = 'click on Submit'
    )
    ORDER BY timestamp LIMIT 200
  """,
  extra_columns = [{"name": "@action.name", "type": "string"}]
)
```

The `LIKE` filters above are a Datadog assistant example — narrow to whatever action-name prefix is meaningful for the app under classification, or drop the filter to read all custom actions on the session.

**If either query returns 0 rows or hits a permission/auth error**, do NOT abort the classification. Missing or partial RUM is a coverage gap, not behavioral evidence — treating it as negative is a documented failure mode.

| Condition | Action |
|---|---|
| 0 RUM rows on the session AND user has SOME RUM data in a wider 30-day window (web RUM gap on the session date) | Degraded. Proceed without RUM; rely on trace evidence (Step 3) and audit signals (Step 4b, if applicable). Set `rum_coverage: partial — web RUM gap on session date` in the output. |
| `analyze_rum_events` / `pup rum` returns permission or auth errors on one or more queries (common for external orgs, SOC2-isolated tenants) | Degraded. Use whichever RUM queries returned rows; fall back to trace + audit for the missing pieces. Set `rum_coverage: partial — auth error on <query>` in the output. |
| 0 RUM rows on the session AND no RUM data anywhere for the user in 30 days | Degraded. Proceed with trace + audit. Set `rum_coverage: unavailable — no RUM data for user` in the output. Do NOT emit `rum_unavailable` as a terminal error. |

**Hard rule:** never use the absence of RUM as evidence for a negative verdict. If trace and audit signals point to satisfaction, the absence of RUM does not override them; if trace signals are ambiguous, default to the trace-only verdict rather than penalizing for missing behavioral data.

The `rum_unavailable` error code is reserved for the unrecoverable case where RUM was *required* by the classification protocol (e.g., the trace is so content-redacted that RUM is the only remaining signal) AND no RUM exists. Otherwise, proceed in degraded mode.

#### Signal interpretation framework

RUM actions are not equal evidence. Stratify them into three tiers and let the tier control how much weight each carries in Step 5. The categories below are **agent-agnostic** — map your specific app's RUM action names onto them. Treat unrecognized actions as Ambiguous until you have evidence for how they correlate with satisfaction.

**Tier-A — sufficient on its own (when present, can flip or confirm the trace verdict)**

| Abstract category | Direction | Example action-name patterns |
|---|---|---|
| Stream-abort fired before the response completed | negative | `*.chat-cancel`, `*.stream-abort`, `*.cancel-generation` |
| Panel/window closed mid-stream with no reopen within 1s | negative | `*.panel.close` while the agent's LLM span is still active |
| Navigation to a URL containing an asset ID the agent explicitly named in its response | positive | View on the exact resource path the agent referenced |
| Artifact action within 60s of session end on the agent's output | positive | Export, save, modify, share targeting the produced asset |
| New session within 15 min with the same intent (retry per Step 1b classifier) | negative | Detected by neighbor-session analysis |
| Wholesale rejection of the agent's plan (`reject_all` or equivalent) | negative | Bulk-reject affordance, "stop" action mid-stream |

**Tier-B — corroborating, never decisive on its own**

| Abstract category | Direction |
|---|---|
| Short dwell on the agent's view (< 30s total) | negative |
| Post-session navigation to external help (docs, support, community) | negative |
| Repeated panel/view opens before any response | negative |
| Long dwell time (> 60s) on the agent's view | positive |
| Click on a markdown link the agent rendered | positive |
| Post-session navigation to a topic-adjacent resource (not exact-asset match) | positive |

**Ambiguous — DO NOT use as sole evidence**

| Abstract category | Why ambiguous | Rule |
|---|---|---|
| Reveal/expand interactions on intermediate state (tool-call panels, reasoning blocks) | Fires in both engaged-reading and pre-thumbs-down-scrutiny patterns; no principled rule discriminates | Pair with a Tier-A or matching Tier-B signal of the same direction |
| Single approval of a tool call (`accept` on one of N proposals) | The expected UX step — users click accept even on mediocre responses | Treat as WEAK positive; never primary evidence for `yes` |
| Single rejection of a tool call (`reject` on one of N proposals) | Could be "user redirected to a better path", not "user dissatisfied" | Count as negative ONLY if user also stopped sending messages OR all subsequent calls were rejected |
| Panel/view close without mid-stream context | "Session ended naturally" indistinguishable from "abandoned" | Need stream-active timing — see Tier-A |

**For your specific agent:** before classifying, build a mapping from your app's RUM action names onto these categories. A reference mapping for the Datadog CMD+I assistant is in `@rum-actions-bits-assistant.md` — use it as a template for what to look up in your own app's RUM event taxonomy. Unrecognized action names default to Ambiguous; promote to Tier-B or Tier-A only after you have evidence (from labeled sessions or product knowledge) for how the action correlates with satisfaction.

For interpreting Datadog assistant action names specifically, consult @rum-actions-bits-assistant.md.

### Step 4b — Get audit-trail signals (when an asset was created or edited)

The audit trail surfaces server-confirmed effects of the assistant's actions (writes that landed, modifications that stuck, deletions, follow-up edits) and post-session user behavior on the same asset. For sessions where the assistant created or edited a Datadog asset (dashboard, notebook, monitor, SLO, etc.), this is the most authoritative behavioral signal — more direct than RUM clicks.

Primary tool: `pup audit-logs search`, which queries the active user's own org via OAuth (`pup auth login`).

**When to run this step:**
- The trace's tool calls include any write op (`upsert_*`, `call_datadog_api` with `PUT`/`POST`, monitor/notebook create-or-update) → run it.
- The session has a known asset ID (from a tool-call argument, the `dashboard_id` tag, the session's `referrer_path`, or the user query) → run it.
- The trace is pure read-only (no writes, no asset ID) → skip; nothing for audit to add over Step 4.

**Query priority** (most signal first, least noise last):

1. **`@asset.id`** — modification/deletion history with full diff. Most authoritative. Required when an asset ID is known.
2. **Q3 (targeted HTTP path)** — write count and timing for the same asset. Required when an asset ID is known.
3. **Q1 / Q2 (broad-signal HTTP audit)** — fallback only. Noisy on active users.

#### `@asset.id` — modification history

```bash
pup audit-logs search \
  --query '@asset.id:<asset_id>' \
  --from "<session_start - 30d, ISO>" --to now --limit 50
```

Each row's body lives at `row["attributes"]["attributes"]`. Fields:
- `action` — `created` / `modified` / `deleted` / `accessed`
- `usr.email`
- `asset.type` (`dashboard`, `notebook`, `monitor`, …), `asset.id`, `asset.name`
- `asset.new_value` / `asset.prev_value` — full before/after state as **nested dicts** (use directly; no JSON parsing needed)
- `timestamp` — ISO8601 string

Pup returns descending order by default. Sort ascending for the chronological session arc:

```python
rows = sorted(d["data"], key=lambda r: r["attributes"]["attributes"]["timestamp"])
```

**Signals:**

| Event after `session_end` | Verdict signal |
|---|---|
| `deleted` event | Strong negative — asset was torn down |
| `modified` by the user, fewer widgets/cells/items than `prev_value` | Negative — user undid the assistant's changes |
| `modified` by the user, more items than `prev_value` | Positive — user extended what the assistant built |
| `modified` with query / threshold change (monitor) | Negative — user disagreed with the assistant's values |
| `modified` with message / tag change only | Neutral — cosmetic |
| No events | Neutral-to-positive — asset untouched |

> **Do not over-interpret audit signals.** Audit measures backend correctness, not user satisfaction — a missing write or absent event is not by itself proof of dissatisfaction. The exception is **explicit user actions**: a `deleted` event, a manual `modified` with widget/cell removal, or a manual write that overrides what the agent produced. Those are direct user intent and can flip a verdict. Backend-symptom signals (zero writes despite acceptance, missing PUTs, etc.) should only corroborate trace + RUM, never flip them alone.

For dashboards, the most useful diff is widget count:
```python
nv = row["attributes"]["attributes"]["asset"]["new_value"]
pv = row["attributes"]["attributes"]["asset"]["prev_value"]
delta = len(nv["dashboard_definition"]["widgets"]) - len(pv["dashboard_definition"]["widgets"])
```

#### Q3 — targeted HTTP path (write count + timing)

While `@asset.id` shows the diff, Q3 shows the **HTTP event count** for writes to the resource — useful for cross-checking the trace's tool calls and finding manual post-session edits.

```bash
# Dashboard:
pup audit-logs search \
  --query '@usr.email:<user_handle> @http.url_details.path:/api/v1/dashboard/<dashboard_id>' \
  --from "<session_start - 30min, ISO>" --to "<session_end + 2h, ISO>" --limit 200

# Notebook:
pup audit-logs search \
  --query '@usr.email:<user_handle> @http.url_details.path:/api/v2/notebook/<notebook_id>' \
  --from ... --to ... --limit 200
```

**Phase-bucket the events** to see what happened during vs after the session:

```python
from collections import Counter
phase_method = Counter()
rows = sorted(d["data"], key=lambda r: r["attributes"]["attributes"]["timestamp"])
for r in rows:
    a = r["attributes"]["attributes"]
    ts = a["timestamp"]; m = a["http"]["method"]; s = a["http"]["status_code"]
    if   ts < session_start_iso:  phase = "pre-session"
    elif ts < session_end_iso:    phase = "during"
    elif ts < feedback_iso:       phase = "session_end->feedback"  # only when feedback time is known
    else:                         phase = "post"
    phase_method[(phase, m, s)] += 1
```

Each `PUT` is one write. `200`/`201` = confirmed success. `status_code: 0` means the browser sent the request but navigated away before the response landed — server almost certainly processed it; count it alongside the 200s.

**Interpretation:**
- `during PUT 200` count should match the trace's upsert tool-call count.
- Writes timestamped 10–30s after `session_end` and whose count matches the last turn's expected writes are async tool execution lag — not user-initiated.
- Writes beyond 30s after `session_end` are user manual edits. Count them post-session: a small clustered burst (e.g., 5–10 PUTs in ~1 min) = fine-tuning (mildly positive — user kept the result and improved it); zero post-session PUTs = clean acceptance.
- Confirmed cancelled sessions have zero `during PUT` events.

#### Q1 / Q2 — broad-signal HTTP audit (fallback)

Use only when no asset ID is known, or to look for retry / support-ticket signals after the targeted queries.

```bash
# Q1: broad signal, session ± window
pup audit-logs search \
  --query '@usr.email:<user_handle>' \
  --from "<session_start - 15min, ISO>" --to "<session_end + 30min, ISO>" --limit 200

# Q2: post-session, strip the highest-volume noise paths
pup audit-logs search \
  --query '@usr.email:<user_handle> -@http.url_details.path:/api/ui/query/scalar -@http.url_details.path:/api/ui/query/timeseries' \
  --from "<session_end, ISO>" --to "<session_end + 30min, ISO>" --limit 200
```

> **Noise dominates on active users.** The 200-row cap is real and there is no pagination cursor. Other high-volume paths to strip when needed: `/api/ui/frontend_telemetry/metrics`, `/api/v1/logs-analytics/aggregate`, `/api/v1/logs-analytics/list`, `/api/v1/logs-analytics/facet_info`, `/api/v2/watchdog/insights/search/*`, `/api/v2/notifications/notify/*`, `/api/v2/notifications/presence/*`. If you still hit the cap after stripping, narrow the time window.

Look for: support ticket creation (`POST /api/v2/cases`), retry assistant calls (`POST /api/v2/assistant`), navigation to docs (visible in RUM Step 4, not here).

#### Response-extraction helper

The doubly-nested envelope (`row["attributes"]["attributes"]`) is verbose. Normalize once per script:

```python
import json, subprocess

def pup_audit(query, frm, to, limit=200):
    res = subprocess.run([
        "pup", "audit-logs", "search",
        "--query", query, "--from", frm, "--to", to, "--limit", str(limit),
    ], capture_output=True, text=True, check=True)
    d = json.loads(res.stdout)
    rows = []
    for r in d.get("data", []):
        a = r["attributes"]["attributes"]
        rows.append({
            "ts":     a.get("timestamp"),
            "method": a.get("http", {}).get("method"),
            "path":   a.get("http", {}).get("url_details", {}).get("path"),
            "status": a.get("http", {}).get("status_code"),
            "action": a.get("action"),
            "asset":  a.get("asset", {}),
            "usr":    a.get("usr", {}).get("email"),
        })
    rows.sort(key=lambda r: r["ts"])
    return rows
```

#### Graceful degradation

If a query returns 0 rows **and** the session is in an external customer org, do not error — flag `audit_unavailable` in the verbose output and continue with trace + RUM. Some external customer audit trails (notably AP1 / SOC2-isolated orgs) are not reachable via pup. Trace + RUM alone is still sufficient for a verdict.

### Step 4b — Get audit-trail signals (when an asset was created or edited)

The audit trail surfaces server-confirmed effects of the assistant's actions (writes that landed, modifications that stuck, deletions, follow-up edits) and post-session user behavior on the same asset. For sessions where the assistant created or edited a Datadog asset (dashboard, notebook, monitor, SLO, etc.), this is the most authoritative behavioral signal — more direct than RUM clicks.

Primary tool: `pup audit-logs search`, which queries the active user's own org via OAuth (`pup auth login`).

**When to run this step:**
- The trace's tool calls include any write op (`upsert_*`, `call_datadog_api` with `PUT`/`POST`, monitor/notebook create-or-update) → run it.
- The session has a known asset ID (from a tool-call argument, the `dashboard_id` tag, the session's `referrer_path`, or the user query) → run it.
- The trace is pure read-only (no writes, no asset ID) → skip; nothing for audit to add over Step 4.

**Query priority** (most signal first, least noise last):

1. **`@asset.id`** — modification/deletion history with full diff. Most authoritative. Required when an asset ID is known.
2. **Q3 (targeted HTTP path)** — write count and timing for the same asset. Required when an asset ID is known.
3. **Q1 / Q2 (broad-signal HTTP audit)** — fallback only. Noisy on active users.

#### `@asset.id` — modification history

```bash
pup audit-logs search \
  --query '@asset.id:<asset_id>' \
  --from "<session_start - 30d, ISO>" --to now --limit 50
```

Each row's body lives at `row["attributes"]["attributes"]`. Fields:
- `action` — `created` / `modified` / `deleted` / `accessed`
- `usr.email`
- `asset.type` (`dashboard`, `notebook`, `monitor`, …), `asset.id`, `asset.name`
- `asset.new_value` / `asset.prev_value` — full before/after state as **nested dicts** (use directly; no JSON parsing needed)
- `timestamp` — ISO8601 string

Pup returns descending order by default. Sort ascending for the chronological session arc:

```python
rows = sorted(d["data"], key=lambda r: r["attributes"]["attributes"]["timestamp"])
```

**Signals:**

| Event after `session_end` | Verdict signal |
|---|---|
| `deleted` event | Strong negative — asset was torn down |
| `modified` by the user, fewer widgets/cells/items than `prev_value` | Negative — user undid the assistant's changes |
| `modified` by the user, more items than `prev_value` | Positive — user extended what the assistant built |
| `modified` with query / threshold change (monitor) | Negative — user disagreed with the assistant's values |
| `modified` with message / tag change only | Neutral — cosmetic |
| No events | Neutral-to-positive — asset untouched |

For dashboards, the most useful diff is widget count:
```python
nv = row["attributes"]["attributes"]["asset"]["new_value"]
pv = row["attributes"]["attributes"]["asset"]["prev_value"]
delta = len(nv["dashboard_definition"]["widgets"]) - len(pv["dashboard_definition"]["widgets"])
```

#### Q3 — targeted HTTP path (write count + timing)

While `@asset.id` shows the diff, Q3 shows the **HTTP event count** for writes to the resource — useful for cross-checking the trace's tool calls and finding manual post-session edits.

```bash
# Dashboard:
pup audit-logs search \
  --query '@usr.email:<user_handle> @http.url_details.path:/api/v1/dashboard/<dashboard_id>' \
  --from "<session_start - 30min, ISO>" --to "<session_end + 2h, ISO>" --limit 200

# Notebook:
pup audit-logs search \
  --query '@usr.email:<user_handle> @http.url_details.path:/api/v2/notebook/<notebook_id>' \
  --from ... --to ... --limit 200
```

**Phase-bucket the events** to see what happened during vs after the session:

```python
from collections import Counter
phase_method = Counter()
rows = sorted(d["data"], key=lambda r: r["attributes"]["attributes"]["timestamp"])
for r in rows:
    a = r["attributes"]["attributes"]
    ts = a["timestamp"]; m = a["http"]["method"]; s = a["http"]["status_code"]
    if   ts < session_start_iso:  phase = "pre-session"
    elif ts < session_end_iso:    phase = "during"
    elif ts < feedback_iso:       phase = "session_end->feedback"  # only when feedback time is known
    else:                         phase = "post"
    phase_method[(phase, m, s)] += 1
```

Each `PUT` is one write. `200`/`201` = confirmed success. `status_code: 0` means the browser sent the request but navigated away before the response landed — server almost certainly processed it; count it alongside the 200s.

**Interpretation:**
- `during PUT 200` count should match the trace's upsert tool-call count.
- Writes timestamped 10–30s after `session_end` and whose count matches the last turn's expected writes are async tool execution lag — not user-initiated.
- Writes beyond 30s after `session_end` are user manual edits. Count them post-session: a small clustered burst (e.g., 5–10 PUTs in ~1 min) = fine-tuning (mildly positive — user kept the result and improved it); zero post-session PUTs = clean acceptance.
- Confirmed cancelled sessions have zero `during PUT` events.

#### Q1 / Q2 — broad-signal HTTP audit (fallback)

Use only when no asset ID is known, or to look for retry / support-ticket signals after the targeted queries.

```bash
# Q1: broad signal, session ± window
pup audit-logs search \
  --query '@usr.email:<user_handle>' \
  --from "<session_start - 15min, ISO>" --to "<session_end + 30min, ISO>" --limit 200

# Q2: post-session, strip the highest-volume noise paths
pup audit-logs search \
  --query '@usr.email:<user_handle> -@http.url_details.path:/api/ui/query/scalar -@http.url_details.path:/api/ui/query/timeseries' \
  --from "<session_end, ISO>" --to "<session_end + 30min, ISO>" --limit 200
```

> **Noise dominates on active users.** The 200-row cap is real and there is no pagination cursor. Other high-volume paths to strip when needed: `/api/ui/frontend_telemetry/metrics`, `/api/v1/logs-analytics/aggregate`, `/api/v1/logs-analytics/list`, `/api/v1/logs-analytics/facet_info`, `/api/v2/watchdog/insights/search/*`, `/api/v2/notifications/notify/*`, `/api/v2/notifications/presence/*`. If you still hit the cap after stripping, narrow the time window.

Look for: support ticket creation (`POST /api/v2/cases`), retry assistant calls (`POST /api/v2/assistant`), navigation to docs (visible in RUM Step 4, not here).

#### Response-extraction helper

The doubly-nested envelope (`row["attributes"]["attributes"]`) is verbose. Normalize once per script:

```python
import json, subprocess

def pup_audit(query, frm, to, limit=200):
    res = subprocess.run([
        "pup", "audit-logs", "search",
        "--query", query, "--from", frm, "--to", to, "--limit", str(limit),
    ], capture_output=True, text=True, check=True)
    d = json.loads(res.stdout)
    rows = []
    for r in d.get("data", []):
        a = r["attributes"]["attributes"]
        rows.append({
            "ts":     a.get("timestamp"),
            "method": a.get("http", {}).get("method"),
            "path":   a.get("http", {}).get("url_details", {}).get("path"),
            "status": a.get("http", {}).get("status_code"),
            "action": a.get("action"),
            "asset":  a.get("asset", {}),
            "usr":    a.get("usr", {}).get("email"),
        })
    rows.sort(key=lambda r: r["ts"])
    return rows
```

#### Graceful degradation

If a query returns 0 rows **and** the session is in an external customer org, do not error — flag `audit_unavailable` in the verbose output and continue with trace + RUM. Some external customer audit trails (notably AP1 / SOC2-isolated orgs) are not reachable via pup. Trace + RUM alone is still sufficient for a verdict.

### Step 5 — Classify

Using the conversation (step 3), evaluations (step 2), RUM signals (step 4), and audit signals (step 4b, when run):

**Satisfaction verdict:**
- `yes`: Final response directly answers user's intent, no negative feedback, no abandon signals
- `partial`: Partially correct, or user got unblocked only through additional effort
- `no`: Negative feedback given, user abandoned, or core intent structurally unachievable

**Failure mode codes** (omit if `yes`):

| Code | Meaning |
|------|---------|
| `wrong_answer` | Factually incorrect claim |
| `incomplete_answer` | Correct but missed important paths |
| `broke_existing_state` | Damaged something the user had |
| `excessive_turns` | Goal achieved but too many round-trips |
| `context_loss` | Forgot earlier context or repeated mistakes |
| `wrong_tool_use` | Wrong tool or wrong parameters |
| `hallucination` | Invented IDs, URLs, or facts not in tool results |
| `other: <describe>` | |

**Compact output** (default):
```
verdict: yes | partial | no
reason: <one sentence>
```

**Verbose output** (only when requested):

```markdown
# Classification: <session_id>

## Session metadata
- **Trace ID:** <trace_id>
- **Agent span ID:** <span_id>
- **RUM session ID(s):** <resolved in Step 4a, or "fallback to email filter" if Step 4a returned 0>
- **Start:** <UTC timestamp>
- **Duration:** <seconds>s
- **Turns:** <N>
- **User:** <user_handle>
- **Product area:** <tag value>
- **Model:** <matched_model_name>
- **Iterations:** <N> (stop reason: <end_turn|tool_use>)
- **Tools called:** <names, counts>
- **Evaluations:** <name: value — "reasoning excerpt"> for each judge
- **Referrer page:** <referrer_path>
- **Content source:** agent_loop | span_content_messages | span_content_io | structural

## User intent
One sentence.

## What the assistant did
- Bullet 1
- Bullet 2

## Was the core intent satisfied?
**yes / partial / no** — one sentence justification.

## Failure mode
- `code`: explanation  (omit if yes)

## RUM behavioral signals

### Pre-session context (from ROUTE_CONTEXT or referrer_path)
What the user was working on before the session.

### Assistant panel actions
| Time | Action | Page |
|------|--------|------|

### Post-session navigation
| Time | URL | Dwell |
|------|-----|-------|

### RUM verdict
One sentence: does behavioral evidence support or contradict the trace-only verdict?

## Audit-trail findings (if Step 4b ran)
- **Asset:** <asset_id> (<type>)
- **In-session writes:** <PUT count> (<status breakdown>)
- **Post-session manual edits:** <count, with timing>
- **Asset deleted or torn down:** yes / no
- **Diff signal:** <e.g. "widget count: 8 → 11 (user extended)" or "audit_unavailable">

## Revised satisfaction verdict (with RUM + audit)
yes / partial / no
```

---

## Trace Mode

Classifies a single LLM Obs trace by trace_id. No RUM. Works for any LLM app.

### Step T1 — Get span structure

```
search_llmobs_spans(
  query  = "trace_id:<TRACE_ID>",
  from   = "now-30d",
  to     = "now",
  limit  = 50
)
```

From the results, map the full span hierarchy:
- Identify the **root span** (the one with `parent_id: undefined`)
- Note the root span's `span_kind` — it may be `agent`, `workflow`, `llm`, or other
- Extract all span kinds present: `agent`, `llm`, `tool`, `workflow`, `retrieval`, `embedding`
- Extract tags: `ml_app`, `service`, `session_id`, `investigation.id`, `issue_id`, `user_handle`, `org_id`, `matched_model_name`, `stop_reason`, `current_turn`, `iteration`

**Determine app type from span structure:**

| Signal | App type |
|--------|----------|
| Root is `embedding` only | Embedding pipeline — see unclassifiable guard below |
| `span_kind=agent` present anywhere | Agent app |
| Only `span_kind=llm` spans | LLM/chat app |
| `span_kind=retrieval` or `span_kind=workflow` at root, with LLM children | Pipeline app |

**Find the agent span (if any):**
The agent span is not always the root. Search all spans for `span_kind=agent` — it may be a child or grandchild of a `workflow` root (e.g. `investigate` → `Agent workflow` → `generate-summary`). Use the deepest / most meaningful agent span for the agent loop call.

**If no spans found** → stop, return error `llmobs_not_found`.

**Unclassifiable guard:** If all spans are `span_kind=embedding` with no `messages` content, stop and return:
```json
{
  "error": "unclassifiable_app_type",
  "detail": "All spans are embedding kind with no conversational content. Use status:ok/error rates instead."
}
```

### Step T2 — Get span details and evaluations

Call `get_llmobs_span_details` on the root span (and on the agent span if different from root). Extract:
- `children_ids` → confirms the hierarchy
- `evaluations` map → any judge verdicts (iterate all keys; treat WARN-status evals as not applicable)
- `content_info` → which fields exist and their size

### Step T3 — Read content

Follow the [Content Retrieval Cascade](#content-retrieval-cascade) using the agent span (C1), then LLM child spans (C2), then the root span (C3), then structural signals (C4).

**Notes on specific patterns observed:**
- **`service:ai_gateway` LLM spans** (bits-copilot, assistant_api): standalone `chat_completion-call` or `messages-call` spans from the AI Gateway proxy — `get_llmobs_span_content(messages)` works reliably on these.
- **`generate-summary` agent spans** (error-tracking-investigator): agent loop works but content is often REDACTED when IO tracing is gated by a feature flag. Fall back to structural signals: child span names (`classify`, `suggest-action`, `add-markdown-links`), tool calls, turn count from `current_turn` tag, overall `status`.
- **`assistant` agent spans** (assistant_api): agent loop returns empty iterations because LLM spans are grandchildren via `get_answer_from_model_step` workflow. Use `get_llmobs_span_content(metadata)` for `query_string` and `get_llmobs_span_content(output)` for the response.

### Step T4 — Classify

Using content and evaluations:

**Satisfaction criteria:**
- `yes`: Output directly and completely addresses the task. No errors, truncation, or refusals.
- `partial`: Addresses part of the task, correct but incomplete, or shows degraded quality.
- `no`: Fails to address the task — hallucinations, errors, empty response, wrong tool use, structurally unachievable.
- When content is fully REDACTED, classify from structural signals: `status:error` → `no`; clean completion with expected child span names → lean `yes`; note low confidence.

**Failure mode codes:** same taxonomy as session mode (see above).

**Compact output** (default):
```
verdict: yes | partial | no
reason: <one sentence>
confidence: high | medium | low   ← include when content was partially or fully unavailable
```

**Verbose output** (when requested):

```markdown
# Classification: <trace_id>

## Trace metadata
- **Trace ID:** <trace_id>
- **Root span:** <name> (<kind>)
- **Agent span:** <name> (<span_id>) — or "none"
- **ML app:** <ml_app>
- **Service:** <service>
- **Start:** <UTC timestamp>
- **Duration:** <seconds>s
- **Status:** ok | error
- **Session/business ID:** <session_id or investigation.id or issue_id if present>
- **Org:** <org_id if present>
- **Models:** <matched_model_name values>
- **Evaluations:** <name: value — "reasoning excerpt"> or "none"
- **Content source:** agent_loop | span_content_messages | span_content_io | structural

## Task / User query
One sentence.

## What the app produced
- Bullet 1
- Bullet 2

## Was the task completed?
**yes / partial / no** — one sentence justification.

## Failure mode
- `code`: explanation  (omit if yes)
```

---

## ML App Mode

Samples and classifies multiple sessions or traces from a given LLM app. Handles diverse instrumentation patterns across teams.

### Step M1 — Sample spans and understand the app

```
search_llmobs_spans(
  query           = "@ml_app:\"<ml_app>\" @status:ok",
  root_spans_only = true,
  from            = "<timeframe>",
  to              = "now",
  limit           = 100,
)
```

**If no spans found** → stop, return:
```json
{
  "error": "no_traces_found",
  "detail": "No spans found for ml_app '<ml_app>' in the given timeframe. Check the app name and timeframe."
}
```

**Unclassifiable guard:** If all root spans are `span_kind=embedding` → stop and return:
```json
{
  "error": "unclassifiable_app_type",
  "detail": "<ml_app> only contains embedding spans. No conversational content to classify. Use status:ok/error rates instead."
}
```

**Sanity check:** Skim the sampled root spans before classifying. Confirm they represent the app you expect — check `service:`, span names, and tags. Discard outliers that look unrelated before proceeding.

**Root span kind analysis:**

| Root span kind | What it means | Action |
|---------------|---------------|--------|
| `agent` | Agent span is the root | Use directly as classification unit |
| `workflow` / `task` | Pipeline wrapper — agent may be nested | Call `search_llmobs_spans(trace_id=<id>)` on a sample trace to find nested agent spans |
| `llm` | Flat LLM app, no orchestration | Use LLM span directly |
| `embedding` only | Embedding pipeline | Unclassifiable (see guard above) |
| Mixed across traces | Multiple services or modes | Group by `service:` tag and treat separately |

> **LLM Experiments traces**: If root spans have `span_kind: experiment` and carry `input`, `output`, and `expected_output` structured fields, you are looking at a [Datadog LLM Experiments](https://docs.datadoghq.com/llm_observability/experiments/) trace. Each experiment span represents one dataset record run. Content is in the root span's structured I/O fields — use `get_llmobs_span_content(field="input"/"output"/"expected_output")` on the root span rather than following the LLM sub-span message cascade (C1/C2). LLM sub-spans in experiment traces may contain stub or placeholder content.

**Grouping granularity — session vs trace:**

First, determine what business ID tag to group by. Use this cascade:

**Option A — `aggregate_spans` (preferred):** If the app emits APM spans (most Go/Java/Python services do), use APM aggregation to get all unique IDs in one call. *In pup mode, `aggregate_spans` is not available — skip Option A and proceed directly to Option B.*

```
aggregate_spans(
  query    = "service:<service_name> resource_name:<root_span_name>",
  computes = [{ field: "*", aggregation: COUNT, output: "count" }],
  group_by = { fields: ["@session_id"], limit: 1000 },   # or @investigation.id, @chatid, etc.
  from     = "<timeframe>",
  to       = "now"
)
```

Try `@session_id` first. If buckets are empty, try the business ID tag you observed on the LLM Obs spans (`@investigation.id`, `@chatid`, `@correlation_id`, etc.). This returns up to 1000 unique IDs with a count per ID in a single call — far more efficient than paginating LLM Obs search results.

**Option B — `search_llmobs_spans` fallback:** If the app has no APM instrumentation or `aggregate_spans` returns nothing, paginate LLM Obs root spans and deduplicate the business ID tag client-side:

```
search_llmobs_spans(
  query           = "@ml_app:\"<ml_app>\"",
  root_spans_only = true,
  from            = "<timeframe>",
  to              = "now",
  limit           = 50          # max per page; use cursor to paginate
)
```

Collect unique values of `session_id`, `investigation.id`, `chatid`, or whichever tag is consistent across spans.

**Granularity decision (after collecting IDs):**

1. **`session_id` (UUID format)** present → **session granularity**, group by `session_id`.
2. **A consistent business ID** (`investigation.id`, `chatid`, `correlation_id`, etc.) present → use as session equivalent.
3. **Neither** → **trace granularity**: one root span = one classification unit.

Sample up to `sample_limit` unique IDs from the collected set. The fallback cascade is automatic — no user input needed.

### Step M2 — Classify each unit (parallel)

Issue all content retrieval calls in a single message. For each sampled unit:

**Session granularity:** For each `session_id`, run the [Session Mode](#session-mode) steps 1–5. Include RUM only if `user_handle` is available; skip RUM gracefully if not.

**Trace granularity:** For each trace, run the [Trace Mode](#trace-mode) steps T1–T4 including the content retrieval cascade.

In both cases, the content retrieval cascade (C1→C4) applies. Emit results as they complete — do not wait for all units.

Compact block per unit:
```
[session|trace]: <id_short>  verdict: yes | partial | no  reason: <one sentence>  content: <agent_loop|messages|io|structural>
```

### Step M3 — Emit summary

After all units are classified, emit the summary. The `# Session Classification Summary` header is the **detection sentinel** for downstream skills (`eval-trace-rca`, `eval-pipeline`) — emit it exactly as shown:

```markdown
# Session Classification Summary

**App:** `<ml_app>`  |  **Timeframe:** <from> → now  |  **Granularity:** session | trace  |  **Sampled:** <N>
**Root span kind:** <kind>  |  **Content source:** <dominant source across units>

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

## Per-Unit Details

| Trace/Session ID | Type | Verdict | Failure Mode | Reason | Content source |
|-----------------|------|---------|-------------|--------|----------------|
| [<first8>...<last8>](<url>) | session\|trace | yes\|partial\|no | none\|<mode> | <one sentence> | agent_loop\|messages\|io\|structural |
```

- **ID column**: display as `<first8>...<last8>` (16 visible chars), linked to the full trace/session URL.
  - Session link: `https://app.datadoghq.com/llm/traces?query=session_id:<full_id>`
  - Trace link: `https://app.datadoghq.com/llm/traces?query=trace_id:<full_id>`
- **Reason column**: the one-sentence classification rationale from the compact block. Use `none` for `yes` verdicts.

---

## Error Output Schema

```json
{
  "mode": "session_id | trace_id | ml_app",
  "id": "<the input id or app name>",
  "error": "<error_code>",
  "detail": "<human-readable explanation>"
}
```

Error codes:

| Code | Trigger |
|------|---------|
| `missing_input` | No `session_id`, `trace_id`, or `ml_app` provided |
| `llmobs_not_found` | `search_llmobs_spans` returned no spans |
| `llmobs_content_expired` | `get_llmobs_agent_loop` returned 404 and all other content sources also failed |
| `llmobs_content_masked` | All content fields returned `<REDACTED_INPUT>` / `<MASKED_STREAMING_RESPONSE>` and no structural signals are available |
| `rum_unavailable` | Reserved — emit only when RUM was *required* by the protocol (e.g., trace is fully redacted and RUM is the sole remaining signal) AND no RUM exists. **For the common 0-rows or auth-error case, do NOT emit this error** — proceed in degraded mode with trace + audit and set `rum_coverage: partial \| unavailable` in the output instead (see Step 4). |
| `audit_unavailable` | Step 4b returned 0 rows in an external-customer org. **Not a hard error** — flag in the verbose output and continue classifying from trace + RUM. |
| `no_traces_found` | ml_app mode: no spans found for the given app and timeframe |
| `unclassifiable_app_type` | App only has embedding spans or is an automated pipeline with no conversational content |

---

## Tool Reference

This appendix applies only in **pup mode**. In MCP mode, use the tool names in the workflow sections directly.

### Spans and traces

| MCP Tool | pup Command |
|---|---|
| `search_llmobs_spans(query, ml_app, from, to, limit, cursor, root_spans_only, span_kind, summary)` | `pup llm-obs spans search --query "@ml_app:A [other_filters]" [--from F] [--to T] [--limit N] [--cursor C] [--root-spans-only] [--span-kind K] [--summary]` — **always use `--query "@ml_app:A"` to filter by ml_app**; the `--ml-app A` flag is unreliable and silently returns spans from other apps. |
| `get_llmobs_span_details(trace_id, span_ids, from, to)` | `pup llm-obs spans get-details --trace-id T --span-ids S1,S2,...` |
| `get_llmobs_span_content(trace_id, span_id, field, path)` | `pup llm-obs spans get-content --trace-id T --span-id S --field F [--path P]` |
| `get_llmobs_trace(trace_id, include_tree)` | `pup llm-obs spans get-trace --trace-id T [--include-tree]` |
| `get_llmobs_agent_loop(trace_id, span_id)` | `pup llm-obs spans get-agent-loop --trace-id T [--span-id S]` |
| `find_llmobs_error_spans(trace_id)` | `pup llm-obs spans find-errors --trace-id T` |
| `expand_llmobs_spans(trace_id, span_ids, max_depth, filter_kind)` | `pup llm-obs spans expand --trace-id T --span-ids S1,S2,... [--max-depth N] [--filter-kind K]` |

### RUM

| MCP Tool | pup Command |
|---|---|
| `analyze_rum_events(event_type="view", filter="@usr.email:EMAIL", ...)` | `pup rum aggregate --user-email EMAIL --from F --to T --compute count --group-by @session.id` |
| `analyze_rum_events(event_type="action", filter="@action.type:custom @usr.email:EMAIL", ...)` | `pup rum aggregate --user-email EMAIL --query "@action.type:custom" --from F --to T --compute count --group-by @evt.name` |

`--user-email EMAIL` prepends `@usr.email:EMAIL` to the query, composing cleanly with any `--query` already set. Confirm event-type filtering flag with `pup rum aggregate --help` if needed.

### Audit trail (Step 4b)

Audit trail is pup-native in both backends — the MCP server does not expose an `analyze_audit_events` equivalent. Always shell out to `pup audit-logs search` via Bash, regardless of MCP availability.

| Use case | pup Command |
|---|---|
| `@asset.id` modification history | `pup audit-logs search --query '@asset.id:<id>' --from <iso\|duration> --to <iso\|now> --limit 50` |
| Q3 targeted HTTP path | `pup audit-logs search --query '@usr.email:<u> @http.url_details.path:<path>' --from F --to T --limit 200` |
| Q1 broad signal | `pup audit-logs search --query '@usr.email:<u>' --from F --to T --limit 200` |
| Q2 post-session noise-filtered | `pup audit-logs search --query '@usr.email:<u> -@http.url_details.path:/api/ui/query/scalar -@http.url_details.path:/api/ui/query/timeseries' --from F --to T --limit 200` |

Response shape: `data[].attributes.attributes.{action,asset,http,usr,timestamp,...}` — doubly nested under `attributes`. `timestamp` is ISO8601. `asset.new_value` / `asset.prev_value` are nested dicts. Pup returns descending order by default — `sorted()` ascending in code. No pagination cursor; cap is `--limit` (default 100, set 200 for broad queries).

### Tools with no pup equivalent

| MCP Tool | pup mode behavior |
|---|---|
| `get_llmobs_bits_session` | Skip — not needed for non-bits sessions. |
| `aggregate_spans` (Step M1 Option A) | Not available. Skip Option A and proceed directly to Option B. |

---

## Operating Rules

- **MCP result parsing safety**: Before writing any script (Python, jq, etc.) that iterates over or accesses fields in an MCP tool result, inspect the raw structure first — check `type(result)`, top-level keys, and whether the payload is nested inside a content block (e.g. `[{'type': 'text', 'text': '<json>'}]`). Extract and `json.loads()` the inner payload if needed before parsing. Never assume MCP results are bare dicts or lists.
