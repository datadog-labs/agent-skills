# Replay Experiment Setup — details & rationale

Read this before generating the server. It covers the settled mechanics, how to adapt evaluators, and
the known limitations.

## Why browser → localhost works (settled — don't re-derive)

A public product page (e.g. `https://app.datadoghq.com/llm`) can `fetch` `http://localhost:8787`:
- **Mixed content:** `localhost`/`127.0.0.1` are "potentially trustworthy" secure origins (W3C
  spec), so HTTPS→http-localhost is exempt from mixed-content blocking in modern Chromium + Firefox.
- **CORS:** the local server echoes the requesting Datadog origin (see the `_DD_ORIGIN` regex in the
  template) and allows `Content-Type`.
- **Chrome Local Network Access / Private Network Access:** public→private (localhost) requests need
  the server to return `Access-Control-Allow-Private-Network: true` on the preflight (the template
  does). Newer Chrome (141+) may also show an "allow local network" **permission prompt** the user
  clicks once; enterprise-managed Chrome can block local-network access entirely (out of our control).
- **CSP:** the Agent Observability SPA currently has no `connect-src`/`default-src`, so page scripts can fetch
  localhost. This is a deployment-level property (same for all users of a build), not per-laptop.

## CORS domain coverage

Use the full Datadog site set, not just `.datadoghq.com` — otherwise EU (`datadoghq.eu`) and gov
(`ddog-gov.com`) users are blocked. The template's regex mirrors the shipping `dd-apm-test-agent`:
```
^https?://([\w.-]+\.datadoghq\.(com|eu)|[\w.-]+\.ddog-gov\.com|[\w.-]+\.datad0g\.com|[\w.-]+\.static-app\.us1\.staging\.dog)$
```

## The Experiments SDK (what the server uses)

Released `ddtrace` Agent Observability exposes:
- `LLMObs.create_dataset(name, project_name=, description=, records=[{input_data, expected_output}])`
- `LLMObs.pull_dataset(name, project_name=)` — fetch an existing (versioned) dataset
- `LLMObs.experiment(name, task, dataset, evaluators=[...], project_name=, tags=)` → `.run()`, `.url`

**Task signature:** `task(input_data, config)` → returns the new output. The task runs **locally**
(this is the "replay against local code" step) and publishes results to the Experiments UI.

**Evaluator signature:** `fn(input_data, output_data, expected_output) -> value` (bool / number /
JSON). `output_data` is the task's new output; `expected_output` is the record's recorded output. The
Experiments UI shows the two side-by-side plus the eval metrics — that IS the old-vs-new comparison.

## Adapting the project's evaluators

Most existing evaluators are objects with `.evaluate(EvaluatorContext) -> EvaluatorResult`. Wrap each
as an experiment-signature function that builds an `EvaluatorContext` from the record + new output and
returns `.value`. The function's `__name__` becomes the eval-metric label in the UI:
```python
from ddtrace.llmobs._evaluators import EvaluatorContext

def _wrap(app_ev):
    def fn(input_data, output_data, expected_output):
        ctx = EvaluatorContext(input_data=input_data, output_data=output_data, span_id="", trace_id="")
        return app_ev.evaluate(ctx).value
    fn.__name__ = app_ev.name
    return fn
```
If an evaluator needs the input at **construction** (e.g. it takes the expected tickers/args at init),
build it per-record inside the function from `input_data` instead of wrapping a fixed instance.

If the project has **no** evaluators, the experiment still runs (comparison via `expected_output` in
the UI); optionally offer to generate evaluators (e.g. via an eval-bootstrap skill) — deferred.

## Stable per-trace dataset (the dataset is stable; the experiment is not)

Name the **dataset** `replay-{trace_id}` and pull-or-create it, so every replay of the same trace runs
over the **identical input case** — and the resulting experiments are directly comparable in the UI's
**experiment-comparison** view (they line up because they share this dataset). This also avoids spawning
an orphan dataset per replay, and a dataset is mandatory anyway (the experiment API requires a
`dataset_id`). Keep the experiment name **unique per run** (`replay-{trace_id}-{timestamp}`).

Because comparison lives on the dataset, the server returns a single `experiment_url` that — despite
the name (kept stable so the web-ui needs no change) — points at the **Experiments page filtered to
this trace's dataset**, listing every replay of the trace together for side-by-side comparison, not
just the one run. It's built from the SDK URLs (so it inherits site/base handling), not hand-assembled:
```
<base>/llm/experiments?query=@event_type:span @parent_id:undefined
    &dataset=<dataset_uuid>&project=<ml_app>&refresh_mode=sliding&start=<ms>&end=<ms>&paused=false
```
The base comes from `experiment.url` (`.../llm/experiments/<id>` → strip the id) and the dataset UUID
from `dataset.url` (`.../llm/datasets/<uuid>`). If the Datadog UI changes its experiments-list query
params, this is the one spot to update.

Do **not** try to stack replays as multiple runs of one experiment. The SDK only supports multiple runs
*within a single* `experiment.run()` call (the `runs=N` param), not appending a run to an
already-finished experiment. An earlier version forced `ensure_unique=False` to create-or-get one
experiment by name; **empirical testing showed replays still landed as separate experiments**, so that
was reverted. Cross-replay comparison is the shared **dataset's** job, not the experiment's.

## Recording the replay case (annotate = production capture)

Each entrypoint annotates its root span with the case **and its identity** —
`metadata.replay_entrypoint` (a stable id for the execution type), `metadata.replay_input`, and
`metadata.replay_output` (the **extracted** canonical output, not a raw wrapped return). So any
production trace self-describes which entrypoint it is and how to replay it.

## Multi-entrypoint dispatch (why `replay_entrypoint`)

One app usually has several execution types (different traced entrypoints). The setup discovers them
via the `datadog-llmo` MCP and instruments each; the generated server holds a dispatch table keyed by
`replay_entrypoint`. At replay time the runtime "Replay" button forwards `metadata.replay_entrypoint`
alongside the input/output, and the server routes to that entrypoint's function + evaluators. That's
how one local server replays all of an app's trace types.

## Known limitations

- **App key on the laptop.** Producing experiments needs `DD_APP_KEY` (write-scoped Application key)
  locally — a heavier credential + onboarding/security consideration than the ingest API key. Watch
  for **shadowing**: ambient `DD_*` shell vars (common once the `datadog-llmo` MCP is set up, often a
  different org) silently win over `.env` under a plain `load_dotenv()`, so the server auths against
  the wrong org and the Experiments API returns a bare 403/500. The template defends with
  `load_dotenv(override=True)` **and** a boot preflight that logs the loaded site + masked key and
  fails fast — keep both.
- **Serializable input only.** Agents needing non-serializable live infra rebuilt at replay
  (a "fixtures" mechanism) are out of scope; detect and bail with a clear message.
- **Output extractor is agent-specific.** Deriving the canonical output from an arbitrary return
  can't be fully automated — propose and confirm with the user.
- **Side effects.** Replaying real code can hit prod DBs / billing / email / queues. Warn the user;
  the safe pattern is dependency injection / test doubles / transaction rollback in the replay path.
- **Browser variance.** Chrome LNA may prompt (click Allow) or be blocked by enterprise policy;
  Safari is untested.
- **Language/framework — Python only.** Datadog's Datasets/Experiments SDK is Python-only; Node.js
  and Java can trace + annotate but cannot create experiments (no documented HTTP API either), so a
  non-Python agent can be made replay-*ready* (the annotation) but can't produce the experiment
  in-language — out of scope until the Experiments SDK ships in those languages.
- **Async job shape.** The server blocks ~60–120s per replay; a productized version should return a
  job id + poll rather than block the request.
- **Local daemon / auto-start.** A browser page can't spawn a local process; "auto-start" requires a
  persistent local daemon (the Lapdog / dd-apm-test-agent model). For now the dev runs the server.
