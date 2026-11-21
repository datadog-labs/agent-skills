# Verification and reporting

## Verify

**Confirm the `dd-trace`/`ddtrace` package you added is declared in the dependency manifest the deploy installs from** (the one identified in SKILL.md Phase 1e) — not merely present in your local environment. Grep that manifest for the package you added, or do a clean install into a throwaway env. This matters because a local `pip install ddtrace` followed by a successful `uvicorn` run passes the startup check below while the manifest is still missing the dependency — so the deploy would crash with `ModuleNotFoundError` even though local verification looked green.

**Check the load order and the settings.** Confirm that the tracer loads before the LLM library: the persisted start command has the preload (`ddtrace-run`, `--import dd-trace/initialize.mjs` or `--require dd-trace/init`), Next.js imports `dd-trace/initialize.mjs` in `register()`, or the in-code init sits above every LLM import. An init placed after the LLM import produces no LLM spans. Then confirm that the env file or service environment that the start path loads sets every variable that the setup reads (for a preload: `DD_LLMOBS_ENABLED`, `DD_LLMOBS_AGENTLESS_ENABLED`, `DD_LLMOBS_ML_APP`, `DD_SITE` and `DD_API_KEY`). Check names only, with the environment's scan tool or with `grep -c '^DD_LLMOBS_ML_APP=.' .env`, and never print the values.

**For every backend runtime you instrumented, boot the persisted start command.** Run the exact command that the deploy or run script uses, with its env loading, and pass no variables inline. A one-off command with inline variables can pass while the persisted start path never loads the settings. In a split frontend/backend repo, use the backend's own start command, because a root `start`/`dev` script can pass without ever importing the backend.
- **Python**: from the same working directory the deploy uses (e.g. `cd backend` when the module lives under `backend/`, so `PYTHONPATH`/cwd match the deploy), boot the start command (for example `ddtrace-run uvicorn app.main:app`) and confirm it binds with no traceback. For in-code init, `python -c "import app.main"` (substitute the real module path) also runs the `LLMObs.enable(...)` line.
- **Node.js**: boot the start command (for example `npm start`) and confirm the server starts without errors. For in-code init, `node -e "require('./<entry>')"` also runs the `.init(...)` line. For Next.js, run `next build` and confirm it completes without errors.

This is the step that catches a wrong import path or bad `enable()`/`init()` arguments — e.g. `from ddtrace.llm_observability import LLMObs` raising `ModuleNotFoundError` — which a passing manifest check will not surface.

If the app builds/runs successfully, tell the user where to look:
- LLM Observability: `https://<app host>/llm/traces?query=@ml_app:<mlAppName>`, with the app host for the user's site from `common-credentials.md`.

## Report

```json
{
  "success": boolean,
  "mlApp": "llm-app-name",
  "message": "Human-friendly summary of what happened"
}
```

Alongside this shape, summarize goal status in the human-readable `message` — see "Reporting goal status" in `llmobs-python.md` / `llmobs-nodejs.md`.
