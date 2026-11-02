# Replay a trace against local code — details & rationale

Read this before generating the runner. It covers the trace-access backend, the artifacts, the
correlation/polling mechanism, the diff, and the known limitations.

## Trace-access backend (MCP or pup)

The skill reads traces through one of two backends; every other step is backend-agnostic:

| operation | MCP backend | pup backend |
|---|---|---|
| fetch a trace | `get_llmobs_trace` | `pup llm-obs spans get-trace --trace-id <id>` |
| read span output | `get_llmobs_span_content` | `pup llm-obs spans get-content` |
| poll for the replay | `search_llmobs_spans` | `pup llm-obs spans search` |

Use the **MCP** when it's present — the default; slightly richer for reads (structured tree + `content_info`,
plus a ready `trace_url`). Fall back to **pup** when the MCP isn't installed (`pup auth` must point at the
app's org); pup returns everything this skill needs too — `trace_url`, span `output`, and a tree via
`--include-tree`. If neither is available, guide the **pup install** — it's easier to set up than the MCP
(and the other Agent Observability skills fall back to pup): `brew tap datadog-labs/pack && brew install
datadog-labs/pack/pup`, then `pup auth login`. MCP alternative:
`claude mcp add --scope user --transport http "datadog-llmo-mcp" "https://mcp.datadoghq.com/v1/mcp?toolsets=llmobs"`
(see https://docs.datadoghq.com/bits_ai/mcp_server/setup/). Both the MCP and pup return a ready **`trace_url`** — use it verbatim (no construction).

**pup exact usage** (flags are non-obvious, and this copy has drifted from reality before — treat
`pup <cmd> --help` and pup's own error text as the source of truth; keep only the non-obvious bits here):
- **Response shape (parse defensively — it has drifted between versions):** in AI-agent mode pup wraps the
  payload in `{status, data, metadata}` with spans under **`data.spans[]`**; with `--no-agent` (pup 1.8.0)
  they have come back at **top-level `spans[]`**. **Read whichever of `data.spans[]` / `spans[]` is
  present.** Parsing the wrong level returns **zero hits on an already-ingested trace** — a silent false
  negative (see polling). Prefer `--no-agent` for a stable script path.
- `pup llm-obs spans get-trace --trace-id <id> --from 30d` — `--trace-id` is a **flag**, not positional;
  the default window is **1h**, so pass `--from` as a **bare duration** (`30d`/`7d`/`1h`) — `now-30d` is
  rejected. Returns `total_duration_ms` and `trace_url`. **Carry `trace_id` forward, not `apm_trace_id`**
  (also present in results) — feeding `apm_trace_id` into get-trace 404s.
- `pup llm-obs spans get-content --trace-id <id> --span-id <root-span-id> --field output --from 30d` —
  span-id from get-trace; `--field` is required (`input`/`output`/`messages`/…). **Same 1h default window as
  get-trace**, so pass `--from` for older traces — a 404 `"span not found: <id>"` here is usually the
  **window**, not a bad span id.
- `pup llm-obs spans search --ml-app <app> --root-spans-only --from <t0> --query "replay_run_id:<id>"` — the
  tag filter is a plain `key:value` in `--query`; the MCP-style `@replay_run_id:` matches **nothing**. Full
  results already include each span's `output`, `tags`, and `trace_url`, so the poll can read the new
  trace's output straight from the hit (no separate get-content needed).
- Multi-org: add `--org <name>`, or make sure `pup auth` selected the app's org — a mismatched org returns a
  bare `404 "no spans found"`, not an auth error.
- **Check token expiry, not just presence.** Read `pup auth status` for the remaining token time before a
  loop; expiry **mid-loop** surfaces as `404 "no spans found"` and feeds the false-negative trap. Re-auth if
  it's short.

## Two persistent artifacts (one-time setup, reused every iteration)

1. **In-entrypoint annotation** — the entrypoint stamps its root span so future traces self-describe:
   ```python
   LLMObs.annotate(span=span, metadata={
       "replay_entrypoint": "<stable id for this execution type>",
       "replay_input": <input extractor>,   # e.g. {"tickers": tickers}
   })
   ```
   Only `replay_input` + `replay_entrypoint` — **no `replay_output`**. The original trace already holds its
   output; the diff reads outputs from the two traces via the backend, so storing it would be redundant.
   **Stamp it at span START, not on the success/deferred-finish path** — otherwise a run that errors carries
   no `replay_input`, and failed runs are exactly the ones you most want to replay.

2. **The runner** — a small CLI (not a server) with an `ENTRYPOINTS` dispatch table keyed by
   `replay_entrypoint`. The skill invokes it to re-run one entrypoint on a given input. Satisfies the runner
   contract below. Keep the file; extend the table when new entrypoints appear.

3. **A run wrapper (only where the language has no in-process dotenv)** — e.g. a shell script for Go. It is
   the natural home for the env work the Python template does inline: **source the project's env file**
   (never inline `DD_API_KEY=<value>` on the command — the permission classifier blocks it and it leaks into
   shell history + the transcript; see contract point 2), **unset ambient provider vars**, and **export the
   `<ml_app>-local` override** before invoking the runner. Where in-process dotenv exists (Python), the
   runner covers this and no wrapper is needed.

The annotation + runner are set up once per app. The per-iteration cost is just: edit → run → poll → diff.

## `replay_entrypoint` is the dispatch key (not a hint)

The runner routes on it: `ENTRYPOINTS[replay_entrypoint]` → the function to call. When a trace carries it,
dispatch directly. When it doesn't (an old, pre-annotation trace), **infer** the entrypoint from the root
span + code, **confirm with the user**, then add the annotation so future traces carry it.

`replay_input` is the same story: present on the trace → use it; absent → derive a **suggested** input
(prefer the code signature; the rendered prompt on the span is lossy) and have the user confirm/edit.

## The runner contract (language-independent)

The runner is generated in the **app's language**; the Python template is the reference implementation, not
the definition. Any runner — Python, Go, TS, … — must satisfy this contract; don't assume the Python API
shape carries over:

1. **Inputs:** a `--entrypoint <id>` + `--input-file <path.json>` (JSON kwargs) CLI, invoked with the marker
   in the environment: `DD_TAGS=replay_run_id:<marker> <run cmd> --entrypoint <id> --input-file <path>`.
2. **Env:** make the project's env authoritative over ambient vars (Python: `load_dotenv(override=True)`;
   languages without in-process dotenv → do it in the run wrapper, artifact 3). A shell configured for the
   MCP often exports `DD_*` for a **different org**, and the coding agent itself exports provider vars.
   **Never inline secret values on the command** — source the env file (the permission classifier blocks
   `DD_API_KEY=<value> …`, and it leaks into history + the transcript).
3. **ml_app derivation:** enable LLM Obs under **`<ml_app>-local`** (idempotent `+ "-local"`) so replay/test
   traces never pollute production. **Tracer/init-level ml_app is NOT proof of isolation:** a
   **per-span/per-call override** (Go `llmobs.WithMLApp(...)`; Python `ml_app=` on a decorator or in
   `LLMObs.annotate`) beats the init setting, so the app's spans can still land in production even with the
   runner set to `-local`. Before writing the runner, **grep the entrypoint's call path for such overrides**;
   if any exist, the app's ml_app constant must resolve from env so the `-local` override wins — configuring
   the runner alone won't do it.
4. **Dispatch:** route `<id>` → the entrypoint function and call it **directly — no wrapper span** — so the
   replay trace is structurally identical to a normal run.
5. **Flush on EVERY exit path, including errors** (`try/finally`). A failed replay that exits without
   flushing leaves **no diffable partial trace** — the worst case, because the developer sees nothing.
6. **On exit, print the `-local` ml_app** the caller should poll under.
7. **Fail-fast interlock:** refuse to start unless the resolved ml_app ends in `-local` (one line). Cheaper
   to trip here than to discover a permanently-polluted production ml_app afterward — LLM Obs traces **cannot
   be deleted** (see limitations).

**Per-language notes (this is where the template does *not* transfer):**
- **Go** (`dd-trace-go`) — the Python `LLMObs.*` shape does not carry over; the actual APIs:
  - init: `tracer.Start(WithLLMObsEnabled(true), WithLLMObsMLApp("<ml_app>-local"), WithLLMObsAgentlessEnabled(true))`.
  - flush/stop: `defer tracer.Stop()` (calls `llmobs.Stop()`) **and** `tracer.Flush()` (flushes APM + LLM
    Obs) on **every** exit incl. the error path — in a `defer`.
  - annotate: `span.Annotate(llmobs.WithAnnotatedMetadata(map[string]any{...}))` (or the option to
    `AnnotateTextIO`) — **not** `LLMObs.annotate(span=…, metadata=…)`.
  - common first failure: `WithLLMObsAgentlessEnabled(true)` **errors hard without `DD_API_KEY`** — name it.
- Other stacks: learn the SDK's enable/flush/annotate API and build/run command; keep the seven contract points.

**Export mode (agentless vs Agent sidecar).** The template enables **agentless** (`agentless_enabled=True`),
which is correct for local replay, but a service may be wired to a local Agent sidecar — check before
replaying and prefer agentless locally. Known benign gotcha: with agentless LLM Obs on, the APM tracer may
still dial `localhost:8126` and log `ERROR: lost N traces … connection refused`. **Harmless** — LLM Obs
spans ship independently and arrive fine — but it looks like a failed replay, so flag it pre-emptively (it
compounds the false-negative failure mode in the polling step).

`DD_TAGS=replay_run_id:<marker>` makes ddtrace stamp the marker on every span of the run (the same channel
that carries `git_commit_sha`, `env`, etc.), so the caller can locate the new trace by that tag **without
altering the trace shape**. An earlier version wrapped the run in a `replay` workflow span; that showed up
as a visible extra root, so it was dropped in favor of the tag.

## Timeouts & finding the new trace

Two separate waits, keyed off the original trace's `total_duration_ms` (read via `get_llmobs_trace` in step 2):
- **Runner subprocess timeout** = `max(120s, ~3 × total_duration_ms)`. The replay runs the same code, so it
  takes roughly the original duration; 3× catches a hung/stuck run without tripping on a normal one.
- **Ingest poll** (after the runner returns): ingest lag is seconds-to-~2 min and does **not** scale with
  duration, so poll **every ~5s up to a flat ~2 min**. Preferred: search the backend for the `replay_run_id`
  tag (from ≈ the replay launch time) — MCP `search_llmobs_spans` (`tags: {replay_run_id: <id>}`) or pup
  `spans search --ml-app <ml_app>-local --root-spans-only --from <t0> --query "replay_run_id:<id>"`.
  Fallback: the **newest root span** for this `ml_app`
  + entrypoint created after launch. Treat "not found yet" as normal for the first attempts; on timeout
  **don't hard-fail** — tell the user it hasn't appeared yet and offer to keep waiting.
- **Before ever reporting "not found," re-query with no tag filter** (just `<ml_app>-local` + window). This
  distinguishes "nothing ingested" from "my filter/parse/scope is wrong" (e.g. reading the wrong spans array,
  or polling the production ml_app). A parse/scope bug produces a **confident false negative** — the worst
  outcome the skill can produce, because it looks like a normal "no trace" and invites a wasteful re-run. If
  the unfiltered query returns spans, the problem is the filter, not ingest.
- **False-positive twin — verify the ml_app on each hit, don't trust the query.** `pup … search --ml-app
  <app> --query "<tag>:<val>"` has returned a span whose **actual `ml_app` was a different app** — the
  `--query` tag did the matching and `--ml-app` was effectively ignored. So a hit is **not** proof of
  isolation. Read `ml_app` (or the `ml_app:` tag) off every returned span and **assert it ends in `-local`**
  before reporting a clean replay. This is worse than the false negative because it produces false
  *confidence*: "clean replay under `-local`" while the trace is actually sitting in production (which you
  can't undo — traces don't delete).

## The diff

Fetch the new trace and produce a **concise** natural-language summary of how the **new output differs from
the old output** — the meaningful differences only, not the full span trees or tool-by-tool trace. Keep it
short; the developer is iterating fast. Call out that live-world drift (time, prices, live search results)
can change the output even when the code didn't — so not every diff is attributable to the code change.

**Sampling variance is a separate confound from drift — n=1 can't isolate it.** Any nondeterministic agent
needs a control, not just prompt edits: **default to two replays** (in diff-only mode too, not only when the
edit targets model-facing text). The technique that makes a run conclusive is **replay-to-replay
comparison** — if two local runs differ from each other about as much as either differs from production, the
wording delta is sampling variance while the decision layer is stable. One replay can't produce that signal.
Deterministic-code edits are the only case where a single replay suffices.

**Exclude disabled-integration subtrees before comparing structure.** If the replay disables a downstream
side-effecting integration (dry-run/stub/nil-adapter), that integration's whole subtree is absent from the
new trace — so raw span-count / tool-count comparisons against production are **junk** (they mostly measure
the skipped subtree, not your change). Identify the subtree the dry-run skips and exclude it from **both**
sides before any structural diff.

**Always lead the diff with clickable links to both traces**, so the developer can open either run in the
UI. Both backends return a ready `trace_url`, with one scoping trap on the replay link:
- **Old trace** → use its `trace_url` **verbatim**; do NOT hand-construct a `/llm/traces` URL. **Under
  fan-out** (you replayed one branch — step 2.5 / below), the old `trace_url` opens the *whole root*; instead
  link the **specific old branch span** (or tell the user which child to open) so old and new line up.
- **New (replay) trace** → the `trace_url` has **no `ml_app`**, so as-is it opens scoped to whatever app the
  user's picker last had (usually production) and renders **empty**; it needs `ml_app=<ml_app>-local`.
  **The trap:** the returned URL is an org-switch wrapper —
  `…/switch_to_user/<id>?next=<URL-encoded /llm/traces …>&flow=org_switch` — with the real `/llm/traces`
  query encoded inside `next`. Appending `&ml_app=…` to the outer URL is a **no-op** (it sits after
  `&flow=org_switch`; the `switch_to_user` endpoint drops it and redirects to the decoded `next` without
  ml_app). Correct fix: URL-**decode** `next`, add `ml_app=<ml_app>-local` to its `/llm/traces` query,
  re-encode `next` (keep `&flow=org_switch`). Bare `/llm/traces?…` URLs (no wrapper) → append directly.
  Browser-unverifiable from here — confirm once that the replay link opens non-empty.
```
- [Old trace](<old trace_url — verbatim>)
- [New trace](<new trace_url with ml_app=<ml_app>-local injected into `next`>)
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
the entrypoint can't be called with just JSON input (needs live infra built first). On a **transient
dependency-fetch failure**, retry the build **once** before reporting a break.

**Monorepo / generated build systems.** Follow the host repo's build-file conventions for the new runner
(and wrapper): repos with generated metadata (Bazel/Gazelle, Pants, Buck, lockfiles) need the regeneration
step or the file won't build. For **Bazel**: put the runner at `cmd/<name>/` under the app, run **Gazelle**
on the touched dirs, **build before replaying**, and prefer executing the built binary from `bazel-bin/…`
directly over `bazel run` (cleaner env control — matters for the `-local` override and unset provider vars).

## Baseline field & fan-out (get these right or the diff means less than it looks)

- **The baseline is not necessarily the root output.** The value the developer is dissatisfied with can be a
  tool-call input or an intermediate output several levels deep; the root output may be a summary that
  carries nothing about the change. Locate the field(s) that actually express the complaint. Also check for
  **post-processing** between what the model produced and what the span records — diff the value the code
  change can move, not the boilerplate-appended one.
- **Fan-out roots.** If the root dispatches N repeated sibling subtrees (a batch/map), the change is usually
  visible in one representative branch; replaying the whole root costs ~N× spend/time for no extra signal.
  Offer a single-branch replay and log what was skipped. **Pick the branch deliberately:** prefer the
  **cheapest branch that reached the terminal / side-effecting tool** — most branches are no-ops that would
  reproduce trivially and prove nothing. **Reconstruct that branch's input from the child span's input, not
  the root's.**

## Known limitations

- **Side effects, and traces are permanent.** Replaying re-runs real code: real model spend every iteration,
  and any real writes the agent performs (DB, email, billing, queues) happen again — warn before the first
  replay (safe pattern: test doubles / dry-run / read-only creds). **And LLM Obs traces cannot be deleted** —
  a mis-scoped replay (wrong ml_app) **permanently** contaminates the production app's dashboards and eval
  sets. That irreversibility is what makes the `-local` isolation load-bearing, not tidy: contract points
  3 + 7, the per-span-override pre-flight, and the poll ml_app-verification all exist to prevent it.
- **Dry-run changes what the model sees.** The "safe pattern" isn't free: a stubbed/dry-run tool returns
  *different* text to the model mid-loop, so every turn after the first side-effecting call diverges from
  production. Treat post-stub turns as lower fidelity, and prefer the app's **own** dry-run affordance over a
  hand-written stub (closer to the real return).
- **Ambient environment — the coding agent is the likely contaminant; unset by default.** The agent's own
  env (e.g. `ANTHROPIC_API_KEY` / `ANTHROPIC_BASE_URL` set by Claude Code) can make the app's SDK **bypass
  its configured model gateway** — invisible in the diff — and many SDKs log a warning about exactly this.
  So **unset ambient provider vars by default and report that you did** (not just "surface and let the user
  decide"), and grep the app for its own guards against ambient keys. Also **verify the credential's org
  matches the trace's org before replaying** — a mismatch ships the replay somewhere you can't query, which
  again looks like ingest lag. (`override=True` / the wrapper handles ambient `DD_*`.)
- **Ingest lag.** The "wait for the new trace" step is the loop's main latency, not the skill logic.
- **Not locally runnable → local setup.** If the app can't be invoked locally with a JSON input
  (deployed-only service, HTTP/gRPC handler, live-infra deps), the skill sets up a local testing flow first
  — see `references/local-setup.md` (detect → propose → build; hands back for secrets + the stub-vs-real
  decision). Input still has to be JSON-serializable once the local path exists.
- **Language — Python is the only templated path.** The loop (fetch/edit/diff/backend) is language-neutral,
  but only Python ships a runner template (`scripts/replay_runner_template.py`); other languages are **not**
  at parity — write the runner (+ wrapper) to the contract from scratch using their SDK/build tooling (Go
  APIs named above). Treat non-Python as "supported via the contract," not "templated."
- **Credentials.** `DD_API_KEY` + `DD_SITE` + provider key(s). No `DD_APP_KEY` (no Experiments API here).
- **Revert.** Code edits accumulate in the working tree across iterations; the skill does not auto-revert
  (deferred) — the user reviews/keeps/restores at the end.
