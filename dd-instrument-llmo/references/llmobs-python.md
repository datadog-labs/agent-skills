# LLM Observability: Python

Provision `DD_API_KEY` first via `common-credentials.md` if you haven't already.

Install — and **persist `ddtrace` into the dependency manifest the deploy installs from** (the one identified in SKILL.md Phase 1e), not just the local environment. A local-only install makes the app run for you, but a clean deploy install reads the manifest and will crash with `ModuleNotFoundError: No module named 'ddtrace'` if the package isn't declared there.

**Persisting installs (preferred).** These commands record `ddtrace` in the manifest automatically — nothing else needed:
```sh
poetry add ddtrace      # writes pyproject.toml [tool.poetry.dependencies]
uv add ddtrace          # writes pyproject.toml [project.dependencies] + uv.lock
pdm add ddtrace         # writes pyproject.toml
pipenv install ddtrace  # writes Pipfile
```

**Non-persisting installs (you must also hand-edit the manifest).** These commands touch the current environment **only** and do NOT write any manifest — after running one, add `ddtrace` to the manifest by hand:
```sh
pip install ddtrace              # env only — does not edit requirements.txt/pyproject.toml
uv pip install ddtrace           # env only — prefer `uv add`
conda install -c conda-forge ddtrace
```
Where to hand-edit: a `ddtrace` line in `requirements.txt`, or `"ddtrace"` in the `[project.dependencies]` (or `[tool.poetry.dependencies]`) array of `pyproject.toml` — matching whichever manifest Phase 1e identified.

## In-code init

> **Copy the import and `LLMObs.enable(...)` call verbatim.** The module is `ddtrace.llmobs` — there is **no** `ddtrace.llm_observability` module, and "correcting" the terse name to the spelled-out one is the most common way this instrumentation crashes on deploy (`ModuleNotFoundError: No module named 'ddtrace.llm_observability'`). Use exactly the kwargs shown below (`ml_app`, `api_key`, `site`, `agentless_enabled`); don't substitute `env`/`service` or add a `from ddtrace import tracer, config` line from memory.

Must run before any other application imports:
```python
# any top-level imports, mainly os or dotenv
import os
import dotenv

# import and enable LLM Observability _before_ any other module imports
from ddtrace.llmobs import LLMObs

LLMObs.enable(
    ml_app=os.environ["DD_LLMOBS_ML_APP"],
    api_key=os.environ["DD_API_KEY"],
    site=os.environ.get("DD_SITE", "datadoghq.com"),
    agentless_enabled=True,
)

# all other application-specific imports, and remaining application logic
from openai import OpenAI
# ...
```

## Alternative: no code change

Wrap the run command with `ddtrace-run` and pass config via env vars:
```sh
DD_SITE=datadoghq.com DD_LLMOBS_ENABLED=1 DD_LLMOBS_ML_APP=<app-name> DD_API_KEY=<key> DD_LLMOBS_AGENTLESS_ENABLED=1 ddtrace-run app.py
```
For FastAPI/uvicorn: `ddtrace-run uvicorn main:app --reload`.

## Beyond SDK init: a well-formed trace

**Run this section even if `LLMObs.enable()` already exists in the project.** SDK init alone only turns on auto-instrumented `llm` spans — it does not by itself produce a session ID, a root span, or a RUM/APM link. Don't let Phase 1d's "SDK already present" finding stand in for this checklist.

Enabling the SDK produces auto-instrumented `llm` spans for supported providers, but a useful trace also needs a root span, session ID, and annotations. Work toward these four goals; the first two are always achievable, the last two depend on the environment:

| # | Goal | Always achievable? | What it requires |
|---|------|-------------------|-------------------|
| 1 | Well-formed LLMObs trace | Yes | SDK initialized; a root span with the right kind; nested child spans; `input_data`/`output_data` annotated |
| 2 | Agent Session ID | Yes | A stable `session_id` on the root span — it propagates to children automatically |
| 3 | RUM session linking | Only with RUM SDK | RUM session ID (`datadogRum.getInternalContext().session_id`) passed to the backend as the LLMObs `session_id`. Without RUM, fall back to a UUID — linking can be added later without restructuring |
| 4 | APM trace linking | Partial — always | `apm_trace_id` is set on every span automatically. Full navigable linking in the UI needs a dd-agent; without one the tag is present but the APM trace won't exist |

Detect what's achievable before instrumenting: check for `@datadog/browser-rum` on the frontend (goal 3), and for `DD_AGENT_HOST`/`DD_TRACE_AGENT_URL`/`ddtrace-run`/an agent sidecar (goal 4). Never promise RUM or navigable APM linking that the detected environment doesn't support — state the gap and the upgrade path instead.

**Goal 4 config knob:** the init snippet above hardcodes `agentless_enabled=True`. Flip it to `False` whenever a dd-agent is detected — leave it `True` otherwise (the `apm_trace_id` tag is still set either way, but only becomes a navigable APM trace with an agent present).

## Adding spans

All imports come from `ddtrace.llmobs` (public package) — never import from underscore-prefixed submodules.

**Decorators** (preferred for child functions):
```python
from ddtrace.llmobs.decorators import agent, workflow, task, tool, retrieval, embedding

@agent(name="my_agent", session_id=session_id)   # orchestrates LLM calls — set session_id here only
@workflow(name="my_workflow")                    # multi-step, no direct LLM calls — inherits session_id from root
@task(name="my_task")                            # pure computation
@tool(name="my_tool")                            # wraps a capability
@retrieval(name="my_retrieval")                  # RAG / vector search
@embedding(name="my_embedding")                  # embedding generation
```

**Context manager** (preferred for the root span when `session_id` is a runtime value):
```python
with LLMObs.agent(name="my_agent", session_id=session_id) as span:
    LLMObs.annotate(span=span, input_data=user_message, output_data=response)
```
`LLMObs.agent(...)` returns a `Span`, which only supports the sync context manager protocol — always use `with`, even inside an `async def`. Using `async with` raises `'Span' object does not support the asynchronous context manager protocol`.

**Annotation:**
```python
LLMObs.annotate(
    span=span,                  # None = current active span
    input_data="user message",  # str or messages list
    output_data="agent response",
    metadata={"key": "value"},
)
```

**Session ID rule:** set `session_id` only on whichever decorator/context-manager is the *root* span of the trace — typically `@agent`/`LLMObs.agent(...)`, but a `@workflow` can be the root if the entry point has no direct LLM orchestration (see the LangChain/LangGraph note below). Descendants inherit it via `ddtrace` context propagation — never pass `session_id` to a non-root decorator.

## Session name (optional display title)

`session_id` has no companion "display name" field in the SDK — the UI otherwise shows a truncated session ID. If the application already has (or can cheaply derive) a human-readable label for the operation — a job type, a conversation topic, a ticket title — set it as a `session_name` tag on the same root span that carries `session_id`, via `LLMObs.annotate(tags=...)`:

```python
with LLMObs.agent(name="my_agent", session_id=session_id) as span:
    LLMObs.annotate(span=span, tags={"session_name": "Playlist for Milo (cat)"})
```

Only set it once, on the root span — same rule as `session_id`. Skip this if there's no natural label available; don't fabricate one from generic defaults (e.g. `f"session-{session_id}"`) since that's no more useful than the raw ID.

## Session ID intake by environment

**RUM present** — default the LLMO session ID to the RUM session ID. The frontend forwards it to the backend alongside the rest of the request payload, under whatever key name fits this project's existing request-body naming convention; read that value on the backend using the same request-parsing accessor the project already uses for the rest of that request's fields — do not assume one framework's access pattern (FastAPI's Pydantic model gives attribute access like `request.session_id`; Flask needs `request.get_json()["session_id"]` or a schema's `.load()`; see Framework-specific notes below):
```python
# Shown with a FastAPI Pydantic request model — substitute the accessor your framework/route actually uses.
session_id = request.session_id
with LLMObs.agent(name="my_agent", session_id=session_id) as span:
    LLMObs.annotate(span=span, input_data=request.message)
    result = do_work(request.message)
    LLMObs.annotate(span=span, output_data=result)
```
Frontend side (show as instructions, never edit frontend files yourself in this reference — setting up the RUM SDK itself is out of scope for this LLM Observability skill):
```javascript
body: JSON.stringify({
  message: userMessage,
  session_id: datadogRum.getInternalContext()?.session_id ?? crypto.randomUUID(),
})
```

**Web framework, no RUM** — accept from caller, UUID fallback (same caveat: use the project's actual request accessor, not necessarily attribute access):
```python
session_id = request.session_id or request.headers.get("x-session-id") or str(uuid.uuid4())
```

**No web frontend (CLI / background job)** — reuse an existing per-operation identifier if the code already generates one before the pipeline runs (`job_id`, `request_id`, `task_id`, etc.) instead of minting a second ID; otherwise one UUID per invocation:
```python
session_id = job_id or str(uuid.uuid4())
```

## Multi-agent pipelines and sub-agent orchestration

When one function invokes multiple distinct agents (or the same agent multiple times) as sequential stages of a single logical operation — a background job that runs a profiler agent, then a theme agent, then a scout agent, then a curator agent, for example — wrap the **entire orchestrating function once**. One root span per logical operation, not one per `agent.run()`/`agent.iter()` call:

```python
async def run_pipeline(job_id: str) -> None:
    with LLMObs.workflow(name="playlist_pipeline", session_id=job_id) as span:
        vibe = (await profiler_agent.run(...)).output
        theme = (await theme_agent.run(...)).output
        candidates = await scout_agent.run(...)
        selections = (await curator_agent.run(...)).output
        LLMObs.annotate(span, input_data=job_id, output_data=selections)
```

Use `workflow` here, not `agent` — `run_pipeline` doesn't call an LLM directly, it orchestrates other agents that do (see the decision table below). Each `agent.run()` call auto-instruments as a nested `agent` span under this one root; all four land on a single trace and inherit `session_id` through ddtrace's context propagation. This applies to any auto-patched agent framework (Pydantic AI, LangChain/LangGraph, OpenAI Agents SDK) — the rule is about *where* you put the one enclosing span, not which framework is calling the LLM.

**Anti-pattern — produces one trace/session per stage instead of one per operation:** calling `agent.run()` for each stage directly inside the pipeline function with no enclosing span. Without an active parent span, there's nothing for the auto-instrumentation to attach each call to, so every `agent.run()` gets its own root span — a separate trace, and (with no explicit `session_id`) a separate session in the UI. This is easy to miss because the SDK is "enabled" and traces do show up — they're just fragmented one-per-agent instead of grouped one-per-operation. Symptom in the trace list: what should be one conversation instead shows up as N separate single-agent conversations (e.g. `curator_agent`, `scout_agent`, `theme_agent`, `profiler_agent` each listed on their own, instead of one session containing all four).

**Detecting this while verifying:** if a single logical operation (one job, one request, one pipeline run) produces more than one root span/trace in the LLM Obs UI, the fix is to add one enclosing `workflow`/`agent` span around the whole orchestrating function — not to sprinkle the same `session_id` across each stage's own root span. Matching `session_id`s across separate traces groups them into one *session*, but a single trace with nested children is the stronger, simpler, and intended fix; prefer it whenever the stages share one caller.

## Span kind decision table

| Code pattern | Span kind |
|---|---|
| Top-level function orchestrating LLM calls and tools | `agent` |
| Multi-step pipeline not directly calling LLMs | `workflow` |
| Function calling an LLM directly (only if not auto-instrumented) | `llm` (manual) |
| Function wrapping a capability (web search, calculator, DB) | `tool` |
| Pure data transformation / business logic | `task` |
| RAG / vector search | `retrieval` |
| Text embedding generation | `embedding` |

**Auto-instrumented providers** (no manual `llm` span needed): OpenAI, Anthropic, LangChain/LangGraph, AWS Bedrock, Google Vertex/Gemini, LiteLLM, CohereAI. Decorate the *calling* function with `tool`/`agent` instead of the provider call itself.

**Never add spans to:** web routing/middleware, data model/config classes, DB/cache clients, logging/metrics utilities, test files, pure utility functions.

## Framework-specific notes

- **OpenAI Agents SDK:** coexists with its own `trace()`; `Runner.run()` is auto-instrumented — wrap the calling function with `@tool`/`@agent`; align `session_id` via `group_id` in `trace()` and `session_id` in the LLMObs context manager.
- **Pydantic AI:** auto-patched on `LLMObs.enable()`; wrap the function calling `agent.run()`/`agent.iter()` with a context manager passing `session_id`. If that function calls more than one `Agent` (a multi-stage pipeline), wrap it **once**, enclosing every stage — see "Multi-agent pipelines and sub-agent orchestration" above; don't wrap each stage's `.run()` call separately.
- **LangChain/LangGraph:** fully auto-instrumented; wrap the top-level invoking function with `@workflow(session_id=...)` or a context manager — `session_id` is valid here only because this `@workflow` *is* the root span; don't add it to any nested `@workflow`.
- **FastAPI:** `LLMObs.enable()` where `app = FastAPI()` is created; session ID from request body or `x-session-id` header with UUID fallback.
- **Flask:** `LLMObs.enable()` before `app.run()` or in `create_app()`; session ID from `request.get_json()["session_id"]` (or a schema's `.load()`), not attribute access — Flask's `request` has no Pydantic-style fields.
- **CLI / background job:** prefer an existing per-operation ID if the code already tracks one (`session_id = job_id or str(uuid.uuid4())`); otherwise `str(uuid.uuid4())` per run. RUM not applicable.

## Reporting goal status

Alongside the standard report shape in `common-verify-report.md`, summarize goal status in the human-readable message, e.g.:
```
✓ Well-formed LLMObs trace
✓ Agent Session ID (UUID fallback — RUM upgrade path noted)
~ RUM session linking: not available (no RUM SDK detected)
~ APM trace linking: apm_trace_id set, but not navigable (no dd-agent detected)
```
