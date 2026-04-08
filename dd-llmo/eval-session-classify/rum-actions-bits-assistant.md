# RUM Custom Actions — Bits AI Assistant (assistant_api)

This file documents the custom RUM actions instrumented by the Bits AI / CMD+I assistant
team (`web-ui` / `ai-experiences` package). Use it to interpret the output of the RUM
custom action query in Step 4 of the classification skill.

**This file is specific to `ml_app: assistant_api`.** If you are classifying a different
agent's sessions, create a parallel file for that agent's instrumented actions.

**Source of truth:** `packages/apps/ai-experiences/lib/assistant/tracking.ts` in `DataDog/web-ui`.
All `command-assistant.*` actions are emitted via `trackAssistantEvent(eventName, payload)` which
calls `rumShim.addAction('command-assistant.<eventName>', { tracking: { assistant: payload } })`.

---

## How to query

Custom actions are developer-instrumented events (`@action.type:custom`), as opposed to
auto-collected clicks and keypresses. This filter dramatically reduces volume (~150–200 events
per 1.5h window vs thousands of raw events).

Use `analyze_rum_events` (SQL-based, requires `rum` toolset enabled):

```python
analyze_rum_events(
  event_type    = "action",
  filter        = "@action.type:custom @usr.email:<user_handle>",
  from          = <pre_start>,
  to            = <post_end>,
  sql_query     = 'SELECT timestamp, "@action.name", view_url FROM rum ORDER BY timestamp LIMIT 200',
  extra_columns = [{"name": "@action.name", "type": "string"}]
)
```

The response is TSV with three columns: `timestamp`, `@action.name`, `view_url`. These are
directly usable — no nested attribute path parsing required.

If `is_truncated: true` appears in the metadata, paginate with `start_at=<displayed_rows>`
(same SQL, same LIMIT) until all rows are returned.

---

## Assistant panel lifecycle

| Action name | When it fires | Payload fields | Signal for classification |
|-------------|--------------|----------------|--------------------------|
| `command-assistant.panel.open` | User opened the CMD+I panel | `interaction`: `keyboard`/`navbar`/`cmd-k`/`top-nav`/`bottom-nav`; `initialQuery` if pre-filled | Note the `url_path` — this is the page context the assistant received. `interaction` tells you how they opened it. Multiple opens in a short window = prior failed attempts |
| `command-assistant.panel.close` | User closed the panel | `conversationId`, `conversationKey` | If before LLM span end → user abandoned mid-generation |
| `workbench.dock.show` | Panel docked / made visible (fires on every surface) | — | Counts assistant opens across product areas in the session window |
| `workbench.dock.click:undock` | User detached the panel into floating mode | — | |
| `trace-assistant.troubleshoot-error-button.click` | User opened via the "Troubleshoot Error" button on a trace or error | — | Strong intent signal: user wants a fix, not just information |

---

## Message send

| Action name | When it fires | Payload fields | Signal for classification |
|-------------|--------------|----------------|--------------------------|
| `command-assistant.message.send` | User sent a message (CMD+I flow) | `interaction`: `'assistant-conversation'`; `messagesLength` (turn number); `message` (text) | One per turn; count for multi-turn detection |
| `ai-experiences.chat-submit` | User submitted a message (all surfaces: CMD+I, embedded workflow/notebook chat) | — | Also fires at feedback submit — disambiguate by checking whether feedback actions preceded it |

---

## Navigation within the assistant panel

| Action name | When it fires | Payload fields | Signal for classification |
|-------------|--------------|----------------|--------------------------|
| `command-assistant.navigation` | User navigated: in-panel view change, clicked a markdown link, or clicked an entity pill | `interaction`: `'in-panel'`/`'markdown-link'`/`'entity-pill'`; `title` (link text or panel view name); `host` (`'relative'` or external hostname); `path` (URL path) | **`'markdown-link'`** = user clicked a link in the response — strong engagement signal. `host` tells you if they left to an external site (e.g. docs). `path` shows what resource they navigated to |

**Derived signals:**
- `interaction: 'in-panel'` with `title: '#conversation'` / `'#history'` etc. = user switched tabs inside the panel
- `interaction: 'markdown-link'` + `host: 'docs.datadoghq.com'` = user went to docs after the response → possible negative signal (didn't trust the answer)
- `interaction: 'entity-pill'` = user clicked an entity pill inline in the response → reading/verifying the data

---

## Context enrichment

| Action name | When it fires | Payload fields | Signal for classification |
|-------------|--------------|----------------|--------------------------|
| `command-assistant.entity.add` | User added a Datadog entity (`@asset`) as context | `interaction`: `'auto-detection'`/`'context-button'`/`'screenshot-button'`/`'upload-button'`/`'paste'`/`'drag'`; `entity.type`, `entity.label` | Methodical user; `interaction` tells you how the entity was added. Multiple `entity.add` events during a timeout wait = user actively enriching context |
| `command-assistant.entity.remove` | User removed an entity from context | `entity.type`, `entity.label` | User changed their mind about context — may indicate they realized the assistant was looking at the wrong resource |

---

## Tool call approval flow

These events cover the client tool (write-action) approval lifecycle. Write-type client tools
(notebook edits, dashboard changes, etc.) require explicit user approval before executing.

| Action name | When it fires | Payload fields | Signal for classification |
|-------------|--------------|----------------|--------------------------|
| `command-assistant.tool_call.accepted` | User approved a single tool call | `toolName`, `toolCallId`, `reason`: `'user_accepted'`/`'auto_accepted'`, `isAutoApproveEnabled`, `source`: `'chat'`/`'app'`, `interaction`: `'tool-block'`/`'prompt-bar'` | `reason: 'auto_accepted'` = read-only tool auto-approved; `'user_accepted'` = user clicked approve. `interaction: 'prompt-bar'` = used the banner, `'tool-block'` = clicked directly on the tool card |
| `command-assistant.tool_call.rejected` | User rejected a single tool call | `toolName`, `toolCallId`, `reason`: `'user_rejected'`/`'app_canceled'`, `source`, `interaction` | `reason: 'app_canceled'` = host app canceled (not user). Single reject after multiple accepts = user stopped the flow mid-way |
| `command-assistant.tool_call.accepted_all` | User clicked "Accept All" in the pending tools banner | `toolCount` (number of tools approved at once) | Bulk approval — user trusted everything in the queue |
| `command-assistant.tool_call.rejected_all` | User clicked "Reject All" in the pending tools banner | `toolCount` | Bulk rejection — user changed their mind entirely |
| `command-assistant.tool_call.result` | Client tool execution completed (success or error) | `toolName`, `toolCallId`, `status`: `'success'`/`'error'`, `durationMs`, `errorType`: `'tool_not_found'`/`'handler_exception'`/`'validation_failed'`, `isAutoApproveEnabled` | **Critical for failure analysis**: `status: 'error'` with `errorType` directly identifies what failed. `'validation_failed'` = tool args didn't pass schema check; `'handler_exception'` = the client-side handler threw; `'tool_not_found'` = tool not registered on this page |

**`tool_call.result` vs `clienttoolerror` in LLM Obs:**
- `command-assistant.tool_call.result` fires client-side immediately after execution
- `clienttoolerror` in LLM Obs appears on the agent span `stop_reason` when the error is sent back to the model
- Both signals identify the same failure; RUM gives `errorType` and `durationMs` which LLM Obs does not

---

## Tool call and thinking UI interaction

| Action name | When it fires | Payload fields | Signal for classification |
|-------------|--------------|----------------|--------------------------|
| `command-assistant.tool_call.toggled` | User expanded or collapsed a single tool call accordion | `isOpen` (bool), `title` (tool step title) | User inspecting a specific tool result; `isOpen: true` = expanded to read, `false` = collapsed after reading |
| `command-assistant.tool_group.toggled` | User expanded or collapsed the grouped tool calls accordion (the "N tool calls" collapse container) | `isOpen` (bool), `toolCount` | `isOpen: true` = user wanted to see all tool calls; absence of this event = user didn't inspect the tool calls at all |
| `command-assistant.thinking.toggled` | User expanded or collapsed the thinking/reasoning section | `isOpen` (bool), `title` | User opened the thinking block — often signals they're verifying or skeptical of the response |

**Note:** Some sessions show `click on Reasoning` as an action name instead of `thinking.toggled`.
This is the browser SDK's auto-instrumented click on the button element — both events may appear
in the same session. `thinking.toggled` is the structured event and carries `isOpen`; `click on Reasoning`
is the raw click. Treat either as "user read the thinking block."

---

## Suggestion selection

| Action name | When it fires | Payload fields | Signal for classification |
|-------------|--------------|----------------|--------------------------|
| `command-assistant.suggestion.select` | User clicked a suggested prompt card (shown on empty conversation) | `message` (the suggestion text) | User didn't know what to ask — they picked from the list. The `message` shows which suggestion was selected |

---

## Model selection

| Action name | When it fires | Payload fields | Signal for classification |
|-------------|--------------|----------------|--------------------------|
| `command-assistant.model.change` | User changed the model in the panel | `interaction` (how it was changed), `modelValue`, `modelLabel` | If a user changes model mid-session, earlier turns used a different model than later ones — cross-reference with LLM Obs `matched_model_name` per turn |

---

## Dictation (voice input)

Behind feature flag `command-assistant-dictation`. Only fires if the user has dictation enabled.

| Action name | When it fires | Payload fields | Signal for classification |
|-------------|--------------|----------------|--------------------------|
| `command-assistant.dictation.start` | User clicked the microphone button to start recording | — | User is dictating instead of typing |
| `command-assistant.dictation.stop` | User stopped recording | — | Time between start and stop = dictation duration |

---

## Rich tag interaction (entity tags inline in response)

Tags like `service:foo` or `env:prod` rendered inline in the assistant response can be clicked.

| Action name | When it fires | Payload fields | Signal for classification |
|-------------|--------------|----------------|--------------------------|
| `command-assistant.tag.click` | User clicked an inline tag in the response | `tag` (full tag string, e.g. `service:foo`), `tagKey`, `tagValue` | User engaged with a specific entity mentioned in the response — strong engagement signal. The `tagKey`/`tagValue` show which dimension they were interested in |
| `command-assistant.tag.copy` | User copied an inline tag | `tag`, `tagKey`, `tagValue` | User extracted a value from the response for use elsewhere — very strong "response was useful" signal |

---

## Content engagement

| Action name | When it fires | Signal for classification |
|-------------|--------------|--------------------------|
| `Rendered a Code block` | A code block appeared in the assistant response | Assistant gave code; check post-session navigation for evidence it was used |
| `click on Reasoning` | Auto-collected click: user expanded the thinking/reasoning section | Equivalent to `thinking.toggled` with `isOpen: true` (see above) |
| `click on Reasoning Reasoning` | User re-expanded a thinking section after collapsing it | Deep read of thinking |

---

## Feedback sequence

These fire in order when a user gives a thumbs-down. The full sequence is:
`Bad response` → reason click → `Add details` (optional) → `Submit`.

| Action name | When it fires | Signal for classification |
|-------------|--------------|--------------------------|
| `click on Bad response` | Thumbs-down button clicked | Negative feedback initiated |
| `command-assistant.message.feedback` | Feedback event (fires at initiation and at submit) | |
| `ai-experiences.chat-submit-feedback` | Same feedback event, alternate name (fires alongside the above) | |
| `click on Incorrect result` | User selected this reason | Response was factually wrong from user's perspective |
| `click on Correct but incomplete` | User selected this reason | Response was partially right |
| `click on Add details` | User clicked to type a free-text reason | Time between this and `click on Submit` = how long they typed; longer = more frustration |
| `click on Submit` | Feedback submitted | Final commit; note the `url_path` — what the user was looking at when they formed their verdict |

**Key derived signals from the feedback sequence:**
- Time from `command-assistant.message.send` → `click on Bad response`: how quickly they rejected (< 60s = near-instant; suggests the response was obviously wrong, not a slow read)
- `click on Add details` present → user typed a custom reason; retrieve it if possible via session replay
- `click on Incorrect result` vs `click on Correct but incomplete` → distinguishes `wrong_answer` from `incomplete_answer` failure modes directly from user selection

---

## Workflow Automation — action catalog

These actions fire inside the Workflow Automation editor when users interact with the step catalog.

| Action name | When it fires | Signal for classification |
|-------------|--------------|--------------------------|
| `actionPlatform.actionCatalog.openModal` | User opened the action catalog modal | User is actively building the workflow themselves (strong positive engagement signal) |
| `actionPlatform.actionCatalog.inputSearch` | User typed a search query in the catalog | Each keystroke fires; count distinct bursts, not keystrokes |
| `actionPlatform.actionCatalog.pickAction` | User selected an action from the catalog | Workflow step added |
| `actionCatalog.pickAction` | Same event, alternate namespace (both fire on the same pick) | Treat as a single pick |
| `actionPlatform.actionCatalog.closeModal` | User closed the catalog (with or without picking) | |
| `root__toggle_editor_accordion_section--click` | User expanded/collapsed an accordion section in the workflow step editor | User is inspecting a step's config; note the `view_url` fragment (`#step-<StepName>`) for which step |
| `InputWithVariables__open-autocomplete-menu--keypress` | User opened the autocomplete menu in a step's input field | User is configuring a step parameter |

**Note on `view_url` fragments in Workflow Automation:**
The URL hash encodes the currently-selected step: `/workflow/<id>#step-<StepName>`. This appears
in every RUM event fired while that step is selected, giving a per-step activity timeline at no
extra query cost.

---

## Post-session continued assistant usage

After giving feedback, users often send more messages in follow-up sessions.
Each `ai-experiences.chat-submit` event after the session end timestamp is one follow-up send.

A high count (5+) over a short window (< 30 min) = user was persistent and likely tried a
different approach. Combine with `Rendered a Code block` to confirm the assistant eventually
delivered something useful.

---

## Interpreting absence

| Absence | What it means |
|---------|--------------|
| No `command-assistant.panel.open` in the pre-window | Session was the first assistant interaction in this window; no prior browsing with the panel |
| No `command-assistant.thinking.toggled` or `click on Reasoning` | User did not read the thinking section |
| No `command-assistant.tool_call.toggled` | User did not inspect individual tool results |
| No feedback actions | User gave no explicit signal; satisfaction is inferred from navigation only |
| No `ai-experiences.chat-submit` post-session | User did not retry; either satisfied or completely gave up |
| No `command-assistant.navigation` with `interaction: 'markdown-link'` | User did not follow any links from the response |
| No `command-assistant.tag.click` or `tag.copy` | User did not interact with inline entity tags |

---

## Feature flags (bonus signal, same RUM event)

Any RUM event on the session page carries `_dd.available_feature_flags` in its attributes.
This is an array of all feature flags active on the user's browser for that page load.

Flags relevant to Bits AI / Workflow Automation:
- `ap-cloud-python-action` — experimental cloud Python execution step in Workflow Automation
- `wfa-ai-chat-diff-message` — diff-style AI response in workflow editor
- `wfa-auto-description` — AI auto-description of workflow steps
- `command-assistant-usage-indicator` — usage indicator in the CMD+I panel
- `command-assistant-dictation` — voice dictation input (gates `dictation.start/stop` events)
- `command-assistant-debug-mode` — debug mode in the assistant panel
- `assistant-skills` — assistant skills / suggestions feature

If the assistant made a platform limitation claim (e.g. "Python is not supported") and a
relevant experimental flag is enabled, the "Incorrect result" feedback may be correct —
the user's environment differs from what the assistant assumed.
