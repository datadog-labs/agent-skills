---
name: agent-observability-replay-trace
description: >-
  Use when a developer wants to iterate on ONE specific Agent Observability / LLM Obs trace whose output
  they didn't like — re-running that trace against their LOCAL code, seeing a concise diff of the old vs
  new output, and looping (change code → replay → diff) until satisfied. Invoked as
  /agent-observability-replay-trace <trace-id> [what to change]. Signals: "replay this trace"; "iterate on
  a trace"; "this trace's output is wrong, fix it and re-run"; "re-run trace <id> with <change>"; pasting a
  trace id from the Agent Observability UI with a description of what to fix. It fetches the trace via the
  datadog-llmo MCP, edits code, re-runs the app to emit a NEW trace, and diffs the two — no local server,
  no browser. For agents traced with ddtrace / LLM Obs (Python first-class), with JSON-serializable entry
  input. Do NOT use for: scored Experiments or the browser "Replay" button (that's
  agent-observability-replay-experiment), building an experiment from a dataset/CSV, writing evaluators,
  root-causing failed traces, or RUM/HTTP session replay.
---

# Replay a trace against local code

A fast **iteration loop** on a single production trace: take a trace whose output a developer didn't like,
optionally change the code to fix it, **re-run that trace against their LOCAL code**, and show a concise
diff of old vs new output — repeating until they're happy. It assumes nothing about the project's layout.

Invoked from the developer's coding agent (they paste a CTA from the Agent Observability UI):
`/agent-observability-replay-trace <trace-id> [<what they didn't like>]`.

## The loop (what you're building each run)

1. Fetch the trace and read its output (the baseline).
2. If a change was requested, edit the local code to address it — **show the changes and get an OK before replaying**.
3. **Replay**: re-run the entrypoint locally so it emits a **new trace**.
4. Wait for the new trace, then show a **concise diff** of old vs new output.
5. Satisfied → done. Not satisfied → the developer says what's still wrong → back to step 2. Iterate.

With **no** modification (`/agent-observability-replay-trace <trace-id>`): do the replay + diff only (a
reproduce/regression check), then offer to enter the edit loop.

## Interaction model — selector gates, never a hard stop

This is a live loop. At every decision point, present the choices as an **interactive selector** (the
`AskUserQuestion` tool — the same menu style as plan mode), **not** a plain question that ends your turn.
There are two gates: (a) after you **propose code changes**, before replaying; and (b) after **each diff
view**. Keep re-presenting the selector after every replay until the user explicitly chooses to finish —
do not stop mid-loop. Only end when they pick "Looks good — stop here".

The selector always offers a **free-text option**, so when a choice needs detail (what to refine, what to
adjust), the user types it **right in the selector** — you get their description in the same view. Treat
that free-text as the instruction and act on it directly; don't follow up with a separate question.

## Scope — check this first

- **Traced with `ddtrace` / LLM Obs**, with an `ml_app` and a discoverable entrypoint. **Python is
  first-class**; other languages work in principle (the loop is language-neutral) but you must learn that
  language's build/run command and generate the runner in it.
- **Entrypoint input JSON-serializable.** If the entrypoint needs non-serializable live infra rebuilt at
  replay (DB/API clients, a `deps`/context object), the runner can't manufacture it — ask the user how, or
  declare that entrypoint out of scope.
- **Requires the `datadog-llmo` MCP** (step 0).
- **Credentials:** `DD_API_KEY` + `DD_SITE` + the agent's provider key(s). **Not** `DD_APP_KEY` — this
  replays into a plain trace, not an Experiment.
- **Side effects:** replaying re-runs real code (real model spend + any real writes the agent does). See
  step 6 — warn before the first replay.

## Why trace-only (not an Experiment)

This is deliberately **not** the Experiments path (that's `agent-observability-replay-experiment`). Re-running
the app just emits a normal new trace; the comparison is an LLM diff of the two traces' outputs. This keeps
it lightweight, drops the `DD_APP_KEY` requirement, and isn't limited to Python's Experiments SDK. Details in
`references/details.md` — read it before generating the runner.

## Workflow

### 0. Ensure the `datadog-llmo` MCP is available
Discovery + diffing read traces via this MCP. Check for `mcp__datadog-llmo-mcp__*` (e.g.
`get_llmobs_trace`, `search_llmobs_spans`). **If absent, stop and walk the user through installing it**
(https://docs.datadoghq.com/bits_ai/mcp_server/setup/) and resume only once the tools appear.

### 1. Parse the command
`<trace-id>` (required) and an optional free-text modification (everything after the id). No modification →
reproduce/diff-only mode. Determine the `ml_app` from the project (`LLMObs.enable(ml_app=…)` /
`DD_LLMOBS_ML_APP`) or the trace; confirm if ambiguous.

### 2. Fetch the trace
`get_llmobs_trace` (and span content as needed). Read the **root span's output** — this is the baseline for
the diff — and its `metadata.replay_input` / `metadata.replay_entrypoint` if present.

### 3. Resolve the entrypoint + input
- **Entrypoint:** if `metadata.replay_entrypoint` is present, use it as the dispatch id. If absent, **infer**
  the entrypoint from the root span (name/kind) + code and **ask the user to confirm** before proceeding.
- **Input:** if `metadata.replay_input` is present, use it. If absent, derive a **suggested** input from the
  trace (best-effort — the rendered prompt is lossy, so prefer the code signature) and have the user
  **confirm or edit** it.

### 4. Ensure the two persistent artifacts (one-time setup, reused every iteration)
- **a) In-entrypoint annotation** — so future traces self-describe. If the entrypoint doesn't already
  annotate its root span, add it (best-effort, non-destructive):
  ```python
  LLMObs.annotate(span=span, metadata={
      "replay_entrypoint": "<stable id for this type>",
      "replay_input": <input extractor>,   # e.g. {"tickers": tickers}
  })
  ```
  (No `replay_output` — the original trace is the baseline; the diff reads outputs from the traces.)
- **b) The runner** — copy `scripts/replay_runner_template.py` → `replay_runner.py` and fill its
  **`ENTRYPOINTS` dispatch table** (one entry per type, keyed by `replay_entrypoint` → its function +
  sync/async). Extend the table when new entrypoints appear; keep the file. **Infer the run command**
  (venv/interpreter/build) from the project and **confirm it** with the user. If the entrypoint needs
  non-serializable live infra, ask how to build it or skip it.

### 5. (If a change was requested) edit the code, then gate on a selector
Analyze the trace + the request, make the code changes, show the developer the diff of your changes, then
present an `AskUserQuestion` **selector** (not a plain question) — e.g.:
- **Replay now** — proceed to step 6.
- **Adjust the changes first** — the user says what to adjust; edit again and re-present this gate.
- **Cancel** — stop without replaying.
Only replay on the "Replay now" choice.

### 6. Replay
**Before the first replay, warn:** re-running executes the agent for real — model calls cost tokens and any
external writes (DB/email/billing/queues) happen again. On confirmation, record the launch time `t0`, then
invoke the runner with the entrypoint id + input (as a JSON file), passing a unique correlation marker as a
span tag via the environment:
```
DD_TAGS=replay_run_id:<unique-id> <python> replay_runner.py --entrypoint <id> --input-file <path>
```
The runner runs the entrypoint **directly — no wrapper span — so the replay trace looks identical to a
normal run**, and the marker rides along as a tag on the emitted spans.

### 7. Wait for the new trace
Two waits, keyed off the original trace's duration (`total_duration_ms`, read in step 2):
- **Runner run:** give the runner subprocess a timeout of `max(120s, ~3 × total_duration_ms)` — the replay
  runs the same code, so it takes roughly the original duration; 3× catches a hung/stuck run without
  tripping on a normal one.
- **Ingest:** once the runner returns, tell the user **"waiting for the new trace to appear in Datadog…"**
  and poll the MCP **every ~5s for up to ~2 min**: `search_llmobs_spans` for the `replay_run_id` tag (from
  ≈ `t0`). If that tag isn't queryable, fall back to the **newest root span** for this `ml_app` +
  entrypoint created after `t0`. Ingest lag is seconds-to-~2 min and does **not** scale with duration.
  **Don't hard-fail** on timeout: say it hasn't appeared yet and offer to keep waiting.

### 8. Summarize the diff (with links to both traces)
Fetch the new trace and give a **concise** summary of how the **new output differs from the old** — just the
meaningful output differences, not the full span trees. Note that live-world drift (time, prices, search
results) can differ even with unchanged code.

**Every diff view must start with clickable links to BOTH traces** so the developer can open either in the
UI. Use the **`trace_url` the MCP returns** for each trace (from `get_llmobs_trace`) **verbatim** — do NOT
hand-construct the URL (the correct query is `?query=trace_id:<id>`, not `@trace_id:` or the APM `?traceID=`
convention, so building it yourself gets it wrong):
```
- [Old trace](<old trace_url from get_llmobs_trace>)
- [New trace](<new trace_url from get_llmobs_trace>)
```
The link text is just "Old trace" / "New trace". Then the diff summary.

### 9. Iterate — gate on a selector (never a hard stop)
After the diff, present an `AskUserQuestion` **selector** with two options (the tool also offers a free-text
"Other"):
- **Looks good — stop here** — finish; leave the code changes in the working tree for the user to review.
- **Make more changes** — the user describes what to change **inline in the selector** (free-text); use
  that description and go to step 5 (edit → gate → replay → diff). In diff-only mode, this is where the
  first change is made.
Re-present this gate after every replay until the user picks "stop here". Do not end your turn between iterations.

## Reference
- `scripts/replay_runner_template.py` — the runner to copy + fill. Read it first.
- `references/details.md` — the annotation + runner contract, correlation-marker/polling, concise-diff
  guidance, and scope/limitations. Read before generating the runner.
