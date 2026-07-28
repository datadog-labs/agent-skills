# Replay a trace against local code — details & rationale

Read this before generating the runner. It covers the artifacts, the correlation/polling mechanism, the
diff, and the known limitations.

## Two persistent artifacts (one-time setup, reused every iteration)

1. **In-entrypoint annotation** — the entrypoint stamps its root span so future traces self-describe:
   ```python
   LLMObs.annotate(span=span, metadata={
       "replay_entrypoint": "<stable id for this execution type>",
       "replay_input": <input extractor>,   # e.g. {"tickers": tickers}
   })
   ```
   Only `replay_input` + `replay_entrypoint` — **no `replay_output`**. The original trace already holds its
   output; the diff reads outputs from the two traces via the MCP, so storing it would be redundant.

2. **The runner** (`replay_runner.py`) — a small CLI (not a server) with an `ENTRYPOINTS` dispatch table
   keyed by `replay_entrypoint`. The skill invokes it to re-run one entrypoint on a given input. Keep the
   file; extend the table when new entrypoints appear.

Both are set up once per app. The per-iteration cost is just: edit code → run the runner → poll → diff.

## `replay_entrypoint` is the dispatch key (not a hint)

The runner routes on it: `ENTRYPOINTS[replay_entrypoint]` → the function to call. When a trace carries it,
dispatch directly. When it doesn't (an old, pre-annotation trace), **infer** the entrypoint from the root
span + code, **confirm with the user**, then add the annotation so future traces carry it.

`replay_input` is the same story: present on the trace → use it; absent → derive a **suggested** input
(prefer the code signature; the rendered prompt on the span is lossy) and have the user confirm/edit.

## How the runner works

The skill calls it with the marker set as a span tag via the environment:
```
DD_TAGS=replay_run_id:<marker> <python> replay_runner.py --entrypoint <id> --input-file <path.json>
```
The runner:
- loads `.env` with `override=True` (org-safety: ambient `DD_*` from a shell configured for the MCP can
  point at a different org),
- runs the entrypoint **directly — no wrapper span** — so the replay trace is structurally identical to a
  normal run,
- flushes and exits.

`DD_TAGS=replay_run_id:<marker>` makes ddtrace stamp the marker on every span of the run (the same channel
that carries `git_commit_sha`, `env`, etc.), so the caller can locate the new trace by that tag **without
altering the trace shape**. An earlier version wrapped the run in a `replay` workflow span; that showed up
as a visible extra root, so it was dropped in favor of the tag.

## Timeouts & finding the new trace

Two separate waits, keyed off the original trace's `total_duration_ms` (read via `get_llmobs_trace` in step 2):
- **Runner subprocess timeout** = `max(120s, ~3 × total_duration_ms)`. The replay runs the same code, so it
  takes roughly the original duration; 3× catches a hung/stuck run without tripping on a normal one.
- **Ingest poll** (after the runner returns): ingest lag is seconds-to-~2 min and does **not** scale with
  duration, so poll **every ~5s up to a flat ~2 min**. Preferred: `search_llmobs_spans` for the
  `replay_run_id` tag (from ≈ the replay launch time). Fallback: the **newest root span** for this `ml_app`
  + entrypoint created after launch. Treat "not found yet" as normal for the first attempts; on timeout
  **don't hard-fail** — tell the user it hasn't appeared yet and offer to keep waiting.

## The diff

Fetch the new trace and produce a **concise** natural-language summary of how the **new output differs from
the old output** — the meaningful differences only, not the full span trees or tool-by-tool trace. Keep it
short; the developer is iterating fast. Call out that live-world drift (time, prices, live search results)
can change the output even when the code didn't — so not every diff is attributable to the code change.

**Always lead the diff with clickable links to both traces**, so the developer can open either run in the
UI. Use the **`trace_url` field the MCP returns** (`get_llmobs_trace`) **verbatim** — it's a ready-made
deep link; do NOT hand-construct a `/llm/traces` URL (the correct query is `?query=trace_id:<id>`, not
`@trace_id:` or the APM `?traceID=` convention):
```
- [Old trace](<old trace_url from get_llmobs_trace>)
- [New trace](<new trace_url from get_llmobs_trace>)
```
The link text is just "Old trace" / "New trace".

## Interaction: selector gates, not hard stops

This is a loop, so the two decision points — (a) after proposing code changes, (b) after each diff — must be
**`AskUserQuestion` selectors**, not plain questions that end the turn. Present the choices, act on the
pick, and after every replay re-present the diff gate. Only finish when the user selects the "stop" option.
- Gate (a) — after proposing changes: **Replay now** / **Adjust the changes first** / **Cancel**.
- Gate (b) — after each diff: **Looks good — stop here** / **Make more changes** (describe the change →
  edit → replay → new diff). "Make more changes" covers the first change in diff-only mode too.

The selector always exposes a free-text option, so the user can type the refinement/adjustment **inline in
the same view** — use that text directly instead of asking a separate follow-up question.

This keeps the loop live the way plan mode stays open until you approve.

## Determining the run command

Infer it from the project (Python venv/interpreter, `package.json`, a build step for compiled languages) and
**confirm with the user** before using it — don't silently assume. Ask the user only when detection fails or
the entrypoint can't be called with just JSON input (needs live infra built first).

## Known limitations

- **Side effects.** Replaying re-runs real code: real model spend every iteration, and any real writes the
  agent performs (DB, email, billing, queues) happen again. Warn before the first replay; the safe pattern
  (test doubles / dry-run mode / read-only creds in the replay path) is the user's responsibility.
- **Ingest lag.** The "wait for the new trace" step is the loop's main latency, not the skill logic.
- **Serializable input only.** Entrypoints needing non-serializable live infra rebuilt at replay are out of
  scope — detect and ask, or skip that entrypoint.
- **Language.** The loop (fetch/edit/diff/MCP) is language-neutral, but the runner is generated in the app's
  language and the skill must know its build/run command. Python is first-class; compiled languages need a
  build step and more setup.
- **Credentials.** `DD_API_KEY` + `DD_SITE` + provider key(s). No `DD_APP_KEY` (no Experiments API here).
- **Revert.** Code edits accumulate in the working tree across iterations; the skill does not auto-revert
  (deferred) — the user reviews/keeps/restores at the end.
