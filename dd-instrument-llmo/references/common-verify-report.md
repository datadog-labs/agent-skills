# Verification and reporting

## Verify

**Confirm the `dd-trace`/`ddtrace` package you added is declared in the dependency manifest the deploy installs from** (the one identified in SKILL.md Phase 1e) — not merely present in your local environment. Grep that manifest for the package you added, or do a clean install into a throwaway env. This matters because a local `pip install ddtrace` followed by a successful `uvicorn` run passes the startup check below while the manifest is still missing the dependency — so the deploy would crash with `ModuleNotFoundError` even though local verification looked green.

**For every backend runtime you instrumented, actually execute the instrumented entrypoint** — do not rely on "the project's `start`/`dev` script." In a split frontend/backend repo there is often no single script that runs both halves, so "run the start script" can pass while never importing the backend at all. Load the module that contains your `LLMObs.enable(...)` / `.init(...)` so that init line runs:
- **Python**: from the same working directory the deploy uses (e.g. `cd backend` when the module lives under `backend/`, so `PYTHONPATH`/cwd match the deploy), run `python -c "import app.main"` (substitute the real module path), or boot the real start command (`uvicorn app.main:app`) and confirm it binds with no traceback. If you used the `ddtrace-run` / `import ddtrace.auto` alternative (no in-code `LLMObs.enable(...)` to import), boot with that wrapper instead — e.g. `ddtrace-run uvicorn app.main:app`.
- **Node.js**: `node -e "require('./<entry>')"` (or an `import` for an ESM entry), or boot the server entrypoint. If instrumentation is via `NODE_OPTIONS='--import dd-trace/initialize.mjs'`, boot with that env set so the loader actually runs. For Next.js, run `next build` and confirm it completes without errors.

This is the step that catches a wrong import path or bad `enable()`/`init()` arguments — e.g. `from ddtrace.llm_observability import LLMObs` raising `ModuleNotFoundError` — which a passing manifest check will not surface.

If the app builds/runs successfully, tell the user where to look:
- LLM Observability: `https://app.datadoghq.com/llm/traces?query=@ml_app:<mlAppName>`

## Report

```json
{
  "success": boolean,
  "mlApp": "llm-app-name",
  "message": "Human-friendly summary of what happened"
}
```

Alongside this shape, summarize goal status in the human-readable `message` — see "Reporting goal status" in `llmobs-python.md` / `llmobs-nodejs.md`.
