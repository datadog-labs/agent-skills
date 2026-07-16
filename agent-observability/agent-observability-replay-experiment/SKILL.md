---
name: agent-observability-replay-experiment
description: >-
  Use when a developer wants to take a REAL production trace from a Datadog-traced agent and run it
  again through the code on their own machine — reproducing one recorded run against local/checked-out
  code and scoring it as an Experiment. Signals: "make my agent replayable"; "set up replay
  experiments"; pick/grab a production trace and reproduce or re-run it on my laptop; re-run old prod
  traces after a prompt, model, or code change and compare eval scores side by side; wire up the Agent
  Observability / LLM Obs "Replay" button. Count these even when the word "replay" never appears — the
  tell is one specific production trace re-executed against local code, not a dataset. For Python
  agents traced with ddtrace / LLM Obs (pydantic-ai, LangGraph, CrewAI) with JSON-serializable entry
  input. Do NOT use for: building an experiment from a dataset/CSV, running a golden Q&A set, writing
  evaluators, root-causing failed traces, comparing already-run experiments, basic tracing setup, or
  RUM/HTTP session replay.
---

# Replay Experiment Setup

Make an Agent Observability-traced agent **replayable**: instrument the current project so its
production traces can be re-run against the developer's LOCAL code and published as Datadog
Experiments. An app usually has **several kinds of runs** (different entrypoints / trace shapes) — this
skill discovers each type from Datadog (via the MCP), instruments them all, and generates one local
server that routes each replay to the right code. It assumes nothing about the project's layout.

## The end state (what you're building)

1. **Every production run records its replay case** on the root span metadata — `replay_input`,
   `replay_output`, and `replay_entrypoint` (which execution type it is) — so any trace is replayable.
2. A **local replay server** (`replay_server.py`) on `localhost:8787` with a **dispatch table** over
   all the app's instrumented entrypoints.
3. On replay, the server reads `replay_entrypoint`, routes to that function, re-runs it as an
   **Experiment** scored by that entrypoint's evaluators, and returns the experiment URL.

## Scope — check this first

- **Python + `ddtrace`/Agent Observability** (an `ml_app` + traced entrypoints).
- Entrypoint inputs **JSON-serializable** — if an entrypoint needs non-serializable live infra
  (DB/API clients, `deps`) rebuilt at replay, that one's out of scope (a "fixtures" mechanism is
  deferred); skip it and tell the user.
- **Python only:** the Datasets/Experiments SDK is Python-only, so a non-Python agent — even if traced
  — can't produce the experiment; bail with that reason.
- **Requires the `datadog-llmo` MCP** for trace discovery — step 0 checks/installs it.

## Why it works (don't re-derive — see references/details.md)

A public product page can `fetch` `http://localhost` (mixed-content exemption + CORS + the
private-network header), and the released Experiments SDK (`create_dataset` / `pull_dataset` /
`experiment`) runs a local task + evaluators and publishes to the Experiments UI. Details, the CORS
domain set, and evaluator adaptation live in `references/details.md` — read it before generating the
server.

## Workflow

The CTA copies only the bare command — you discover everything from the MCP + code. The **MCP** is
needed up front (discovery); the **Datadog experiment keys** stay late (only to run the experiment).

### 0. Ensure the `datadog-llmo` MCP is available
Discovery reads the app's traces from Datadog via this MCP. Check whether its tools are present — look
for `mcp__datadog-llmo-mcp__*` (e.g. `search_llmobs_spans`, `get_llmobs_trace`). **If they're not
present, stop and walk the user through installing it** — point them at the setup docs
(https://docs.datadoghq.com/bits_ai/mcp_server/setup/) — and resume only once the tools appear. Do not
proceed without it.

### 1. Determine the `ml_app`
Read it from the project (`LLMObs.enable(ml_app=...)` or `DD_LLMOBS_ML_APP`) and confirm with the user.
If several apps live in the repo, ask which one this is for.

### 2. Discover the app's execution types (via MCP)
One app usually has more than one kind of run. Query **recent** root spans for the ml_app
(`search_llmobs_spans`, root-spans-only) and group them into **distinct execution types** by root-span
name + kind + I/O shape. Pull a representative trace per type (`get_llmobs_trace` / span content) to see
its input/output. Keep the query cheap and the grouping honest:
- **Scope it.** Use a narrow time window (hours/days, not 30d) and a small `limit` — an unbounded
  root-span pull can return 100Ks of chars and blow up the tool call. Count/group client-side.
- **Drop non-entrypoints.** Ignore `span_kind: llm` roots — those are auto-instrumented model/eval
  calls (e.g. an LLM-judge's `OpenAI` chat completion) that fire *outside* the agent span and are not
  execution types. Real entrypoints are `agent`/`workflow`/`task` roots.
- **Collapse versions.** The same logical entrypoint often appears under several names across code
  revisions — treat those as ONE type (favor the latest / disambiguate by time window).

**Present the list of types you found and confirm coverage** — the user may know of types outside the
window (add them from the code).

### 3. Resolve each type's entrypoint
For every type, find the function that produces its root span:
- Grep the root-span name in `@llmobs_agent`/`@workflow`/`@task` decorators and for a `def <name>`
  (span name ≠ function name when a decorator sets `name=`).
- Disambiguate by I/O shape — **weight the output** (structured, discriminating); the input is often a
  rendered prompt, so take the replayable input from the **code signature**, not the trace.
- If the root span is a **framework agent object** (`Agent(name=…)` pydantic-ai, a LangGraph graph, a
  CrewAI crew) rather than a `def`, instrument the **user function that invokes it** (`.run`/`.iter`/
  `.invoke`/`.kickoff`).
- **Echo each chosen target — "type `X` → instrument `fn` in `path`" — and confirm before editing.**
Per type, also settle: sync/async, the input extractor, the output extractor (canonical, NOT a raw
tuple/wrapper — propose + confirm), and the evaluators (code-defined or online; none → offer to
bootstrap, deferred).

### 4. Instrument each entrypoint (annotate = production capture — no DD keys)
In each entrypoint, after the run, annotate its root span with the case **and its identity**, so every
trace self-describes which entrypoint it is and how to replay it:
```python
LLMObs.annotate(span=span, metadata={
    "replay_entrypoint": "<stable id for this type>",   # what the server dispatches on
    "replay_input":  <input extractor>,                  # e.g. {"question": question}
    "replay_output": <output extractor>,                 # canonical output, not the raw wrapper
})
```
Best-effort, non-destructive, on every run. Use a stable `replay_entrypoint` id per type (reused by
the server's dispatch table).

### 5. Generate the multi-entrypoint replay server
Copy `scripts/replay_server_template.py` → `replay_server.py` and fill the **`ENTRYPOINTS` dispatch
table**: one entry per type mapping its `replay_entrypoint` id → its function, output extractor,
evaluators, and an **optional `validate(input_data)`** type/shape check. `/replay_trace` reads
`metadata.replay_entrypoint`, routes to the matching entry, and runs the experiment — returning a
**structured error contract** the web-ui surfaces off any non-2xx `{error}` body:
- **200** on success with `{status, replay_entrypoint, experiment_url}` — `experiment_url` points at
  the **Experiments page filtered to this trace's dataset**, listing **all** replays of the trace for
  side-by-side comparison (not just this run). The key name stays `experiment_url` so the web-ui needs
  no change.
- **4xx** `{error}` for bad input — unknown/missing `replay_entrypoint` (lists the valid ids), missing
  `replay_input`/`replay_output`, or a `validate` failure — validated **before** running.
- **5xx** `{error}` if the replay/experiment itself throws (caught, not a bare 500 stack trace).

The template already has `/health`, CORS/private-network handling, the stable per-trace Dataset
(pull-or-create), the experiment run, the error contract, and the credential preflight.

### 6. Prompt for the experiment env vars (the point Datadog keys are needed)
The MCP handled discovery; producing the experiment needs Datadog keys. Check the project's `.env` and
the environment and **prompt the user for anything missing** — never fill secret values yourself.

> Note: an app that emits traces does **not** necessarily have these — agent-forwarded apps don't hold
> `DD_API_KEY` in-process, and `DD_APP_KEY` is essentially never present just from tracing.
>
> Org mismatch (common): a shell configured for the `datadog-llmo` MCP often exports ambient `DD_*`
> vars for a **different org** than the project's `.env`. The template loads `.env` with
> `override=True` so those keys win — keep it. A 403 or experiments in the wrong org during verify is
> the tell that ambient keys leaked in.

| Var | Purpose | Notes |
|---|---|---|
| `DD_API_KEY` | trace ingest (agentless) | 32 chars |
| `DD_APP_KEY` | datasets/experiments API | **Application** key, ~40 chars — distinct from the API key |
| `DD_SITE` | org site | must match the org the keys belong to |
| provider key(s) | run the agent | e.g. `OPENAI_API_KEY` — whichever the agent uses |

### 7. Verify (seed one annotated trace, then replay it)
Start the server; `curl http://localhost:8787/health`.

To actually exercise the new annotation + replay end-to-end you need one trace that **carries the
`replay_*` metadata** — and the traces discovered in Step 2 predate the instrumentation, so they don't.
Bootstrap one:
1. **Get a seed input** for the entrypoint. Recovering it from a discovered trace is lossy — the root
   span often holds only the *rendered prompt*, not the structured args — so **prefer asking the user
   for one concrete example input** (or propose a minimal one and confirm); fall back to a discovered
   trace's input only if it's cleanly recoverable.
2. **Run the instrumented app once** on that seed input. This emits a fresh trace whose root span now
   carries `replay_entrypoint` / `replay_input` / `replay_output`.
3. **Replay that trace:** read its metadata and POST
   `{trace_id, metadata:{replay_entrypoint, replay_input, replay_output}}` to `/replay_trace`. Confirm
   it returns an `experiment_url` — the Experiments page filtered to this trace's dataset, listing all
   its replays — that resolves in the UI.

The server prints a **credential preflight** at boot (`DD_SITE`, masked `DD_APP_KEY`, `ml_app`) and
fails fast if the creds don't resolve to a real project — read that line first if a replay errors.

> Expect ingest lag: `experiment.run()` returns quickly, but the run's events take **~1–2 min** to
> surface — `get_llmobs_experiment_summary` may report `total_events: 0` a couple of times before it
> populates. Poll it; an immediate `0` is normal, not a failure.

The seed is only needed for this first verification — once instrumented, every real production run is
self-describing and replayable with no seed. Replaying the same trace again produces a **separate
experiment over the same dataset**; compare them in the UI's experiment-comparison view (see
`references/details.md`).

## Companion frontend change (flag to the user)
For replay to work end-to-end, the runtime **"Replay" button must forward
`metadata.replay_entrypoint`** (alongside `replay_input`/`replay_output`) so the server can dispatch to
the right entrypoint. The setup CTA itself is just the bare command.

## Reference

- `scripts/replay_server_template.py` — the multi-entrypoint server to copy + fill. Read it first.
- `references/details.md` — CORS domain set, Experiments SDK specifics, evaluator adaptation, the
  stable-dataset rationale, and known limitations. Read it before generating the server.
