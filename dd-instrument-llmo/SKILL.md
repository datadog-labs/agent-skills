---
name: dd-instrument-llmo
description: Instrument the current project with Datadog LLM Observability for Python or Node.js/Next.js backends that call LLMs or run AI agents. Detects the runtime and LLM framework, provisions credentials, adds SDK init (ddtrace/dd-trace) with the correct kwargs, persists the dependency into the deploy manifest, and audits session-ID plumbing for gaps — fixing them when found. Use when the user says "instrument this project with LLM Observability", "add LLM Observability", "monitor my AI app in Datadog", "add LLM spans", "add agent session tracking", or "verify/repair my LLM Observability setup".
metadata:
  version: "0.2.0"
  author: datadog-labs
  repository: https://github.com/datadog-labs/agent-skills
  tags: datadog,llm-observability,llmobs,instrumentation,python,nodejs,nextjs,ddtrace,dd-trace,agents
  alwaysApply: "false"
---

# Datadog LLM Observability Instrumentation

This skill assesses the current project (backend runtime, LLM/agent framework, existing instrumentation) and routes you to the right reference file under `references/` for the actual setup steps. It is fully self-contained — it does not call any Datadog MCP server. It reuses the API key that `dd-account-setup` or the task environment provides (see `references/common-credentials.md`), and all code changes are made by you, directly, using your own file-editing tools.

Stay within LLM Observability scope. Do not add RUM, APM application instrumentation, or unrelated Datadog products. RUM and a Datadog Agent are relevant only because they change what session/trace linking is achievable (see the "Beyond SDK init" section in the reference files).

**Do NOT invent tool names.** Every step is done with your normal file-editing, search and shell tools. The one exception is a tool that your environment provides for writing the API key, as described in `references/common-credentials.md`.

**Do NOT write anything to memory during or after this skill.** Project paths, frameworks, credentials, and ML app names are project-specific and must not be stored in persistent memory.

## Ground rules while instrumenting

- **Verify before you assert.** If you're not sure about file content or codebase structure, read the files — do not guess.
- **Check for existing instrumentation first.** Before touching the backend, check whether `ddtrace`/`dd-trace` already starts (for APM or for LLM Observability) and whether LLM Observability is on (Phase 1d). If the tracer already starts, never add a second, competing start or `init`/`enable` call; add only what is missing. **This only means skip re-init — it does not mean skip the work.** SDK presence is not the same as a well-formed trace or a session ID that actually flows; run the session-ID plumbing audit in Phase 1d and close any gap it finds, even when the SDK is already present.
- **Start the tracer before any LLM library loads**, or it cannot patch the LLM client and the app sends no LLM spans. Prefer a preload in the persisted start command, because it does not depend on import order. Use in-code init only when the start command cannot be edited: above every LLM import, and never in an ES module. Next.js uses `instrumentation.ts`. See "Start the tracer" in the runtime reference.
- **Load the env file before the tracer starts, in the persisted start path**, because neither tracer reads `.env` files. Verify with the persisted start command, not a one-off command that passes the variables inline.
- **Only use packages/features you've explicitly been told to add.** Don't decide on your own to enable an additional Datadog product or SDK feature beyond what this skill specifies.
- **Persist added dependencies into the deploy's manifest.** Any Datadog package you add (`ddtrace`, `dd-trace`) must be written into the dependency manifest the build/deploy installs from — the one identified in Phase 1e — not just installed into the local environment. A clean deploy install reads only the manifest and will crash with a missing-module error (e.g. `ModuleNotFoundError: No module named 'ddtrace'`) if the package isn't declared there.
- **Don't make stylistic changes** to code you're not otherwise touching.
- **No package aliases** when importing Datadog packages.
- **Copy Datadog SDK package names, import paths, and init keyword arguments verbatim from the applicable reference section — do not paraphrase them from memory.** The Python LLM Observability import is exactly `from ddtrace.llmobs import LLMObs` — the module is `ddtrace.llmobs`, **not** `ddtrace.llm_observability` (that module does not exist and produces a deploy-time `ModuleNotFoundError`). The Node package is `dd-trace`, started through `dd-trace/initialize.mjs` (ESM preload, Next.js), `dd-trace/init` (CommonJS preload) or `require('dd-trace').init(...)` (CommonJS fallback) — all shown in `references/llmobs-nodejs.md`; never force `require(...)` into an ESM project. Use the exact `LLMObs.enable(...)` / `.init({...})` keyword arguments shown; do not add, drop, or rename kwargs based on general Datadog knowledge.
- **Use a real edit tool for existing files** (`Edit`/`Write`, not `sed -i`, `awk`, or scripted find/replace via Bash). If an edit-by-text-match fails, re-read the file first rather than retrying the identical edit. Env files are the exception: append to them without reading them, as `references/common-credentials.md` shows, so a key never enters your context.
- **Checklist discipline.** Before starting the instrumentation steps, post a short checklist of the steps you're about to take. Check items off as you go, and review the checklist at the end of the run.
- **After any code change**, check `package.json` for a `lint:fix`, `fix`, or `format` script (or `eslint --fix` / `prettier --write` config) and run it automatically — no need to ask permission.
- **Verify the app still builds/runs** before declaring success (see `references/common-verify-report.md`).
- **LLM Observability session IDs propagate from the root span only.** Never pass a session ID to a child span/decorator — see the "Adding spans" section in `references/llmobs-python.md` / `references/llmobs-nodejs.md`.
- **Don't promise what the environment doesn't support.** If no RUM SDK is detected, don't claim RUM session linking; if no dd-agent is detected, don't claim navigable APM trace linking. State the gap and the upgrade path instead (see the "Beyond SDK init" section in the reference files).

---
## Phase 1: Analysis

Inspect the relevant application directory before asking questions or editing files.

### 1a. Detecting the backend LLM/agent runtime

- Python signals: `requirements.txt`, `pyproject.toml`, `Pipfile`, or `*.py` files → runtime **`python`**
- Node.js signals: a backend entry point (`server.js`, `index.js`, an Express/Next.js/Fastify app) → runtime **`nodejs`**
- If both exist (e.g. a Next.js app with a Python worker), instrument each backend runtime independently.
- If neither is present, there is nothing to instrument — stop and tell the user.

### 1b. Detecting the LLM framework/SDK (optional, informational)

Check dependency files for signals of: `openai`, `@anthropic-ai/sdk` / `anthropic`, `langchain`, `langgraph`, `ai` (Vercel AI SDK), `boto3` + Bedrock usage, `google-generativeai` / `google-genai`, `crewai`, `litellm`, `pydantic-ai`, an MCP SDK, `google-adk`. This doesn't change the init code (ddtrace/dd-trace auto-instruments these SDKs once the tracer is initialized) — it's only used for confirming the setup with the user and for special-cased frameworks noted in the reference files (Next.js, Vercel AI SDK).

### 1c. Detecting the backend application framework

- Python: `fastapi`, `flask`, or `django` dependency
- Node.js: `express` dependency, or `next` (Next.js API routes / server actions)

### 1d. Detecting existing instrumentation, and auditing session-ID plumbing

- For each backend runtime, search the code, the start commands (`package.json` scripts, `Dockerfile`, compose, `Procfile`, `NODE_OPTIONS`) and the env and deploy files, but skip templates and docs such as `.env.example`, `.env.sample` and READMEs. Answer two questions:
  - **Does the tracer start?** Yes for a preload (`ddtrace-run`, `import ddtrace.auto`, `dd-trace/init`, `dd-trace/initialize`) or an in-code init (`LLMObs.enable(`, any `dd-trace` `.init(`).
  - **Is LLM Observability on?** Yes for `LLMObs.enable(`, an `llmobs` option in a `dd-trace` init, or `DD_LLMOBS_ENABLED` set to `1`/`true`. In Node.js, a non-empty `DD_LLMOBS_ML_APP` or `DD_LLMOBS_AGENTLESS_ENABLED` also turns it on; in Python it does not.
- Act on the answers:
  - **Both yes:** the runtime is set up; add no start or init. If the start path loads the settings after the tracer reads them (for example a preload plus `require('dotenv').config()` in the app), fix only the loading.
  - **The tracer starts, but LLM Observability is off** (for example `ddtrace-run` or `require('dd-trace').init({ service: 'checkout-api' })` for APM): keep that start unchanged, because a second start is ignored or drops the app's own tracer options, such as its service name. Add only the LLM Observability settings and the key ("Tracer already started" in the runtime reference).
  - **Only the variables match:** add no start, because the platform may start the tracer. Report the setup as unverified, not as successful, and say that the repository itself does not start the tracer.
  - **Neither:** follow the full setup in the runtime reference.
- Also grep for a frontend RUM SDK (`datadogRum.init(`, `@datadog/browser-rum`) — not to set it up, but because its presence determines whether the opt-in RUM↔LLMObs pivot is available (that pivot reuses the RUM session ID as the LLMObs session ID).
- Whatever the answers, **do not stop there.** SDK presence only means "don't re-init"; it says nothing about whether a session ID actually flows. Run this audit whenever its prerequisite surfaces are present:
  - **Session-ID plumbing** — applicable whenever Phase 1a found an LLM/agent backend. Check whether a stable, conversation/operation-scoped session ID flows from the appropriate source: for web apps, the frontend sends a per-conversation ID and the backend request model/route reads it; for CLI/background jobs, the backend reuses an existing job/request/task ID or mints one UUID per invocation. A root `agent`/`workflow` span must then set it as `session_id`/`sessionId` (see "Beyond SDK init" and "Session ID intake by environment" in `references/llmobs-python.md` / `references/llmobs-nodejs.md`). `LLMObs.enable()`/`dd-trace().init()` alone does not establish this. If a RUM SDK is also present, the RUM↔LLMObs pivot is available as an opt-in — reusing the RUM session ID as the `session_id` — but that is a deliberate trade-off (the LLMObs session then spans the whole browser session), not the default; don't flag its absence as a gap.
  - If the check finds a gap, tell the user what's missing and fix it (same reference files, same ground rules) even though the SDK itself doesn't need re-initializing. If it passes, say so explicitly. Note the RUM pivot as not applicable rather than a gap when there's no RUM SDK.

### 1e. Detecting the dependency manifest to persist into

For each backend runtime found in 1a, identify the **dependency manifest the build/deploy actually installs from**. This is where any Datadog package you add (`ddtrace`/`dd-trace`) must be persisted so a clean deploy install includes it. It is *not* necessarily "whichever manifest file happens to exist": a repo can have several (e.g. an empty `requirements.txt` alongside a `pyproject.toml`, or multiple `package.json` files where only one is the deployed workspace), and editing a non-authoritative one is a silent no-op at deploy time.

Resolve it in this order:

1. **Deploy/build install command first (authoritative).** Read the project's build/deploy configuration and use the install source it names: `render.yaml` (`buildCommand`), `Procfile`, `Dockerfile` (`RUN … install …`), `Makefile`, CI workflows, `package.json` scripts. Examples: `pip install -r <file>` → that requirements file; `poetry install` / `uv sync` / `pdm install` → `pyproject.toml` (+ its lockfile); `pipenv install` → `Pipfile`; `npm ci` / `yarn install` / `pnpm i` → `package.json`.
2. **Else, the highest-priority manifest present.** Python: a lockfile-backed manager (`uv.lock` or `poetry.lock` → `pyproject.toml`) > `pyproject.toml` `[project.dependencies]`/`[tool.poetry.dependencies]` > `requirements*.txt` > `Pipfile` > `setup.py`/`setup.cfg`. Node.js: `package.json` (always).

Record the **manifest path** and the **manager** that owns it. Persisting commands (`poetry add`, `uv add`, `pdm add`, `pipenv install`, `npm`/`yarn`/`pnpm`/`bun add`) write the manifest. Non-persisting commands (`pip install`, `uv pip install`, `conda install`) touch only the current environment — with those you must **also** hand-edit the manifest. If `requirements.txt` is generated from a `requirements.in` (pip-tools), edit the `.in` and recompile. After hand-editing a manifest that has a lockfile, regenerate the lock so a frozen deploy install picks up the new package.

---
## Phase 2: Instrumentation — routing table

Pick the env file with `references/common-credentials.md`, follow the reference for each backend runtime found in Phase 1a, and write `DD_API_KEY` last, as `common-credentials.md` describes:

| Need | Read |
|---|---|
| LLM Observability — Python (SDK init, spans, session ID, RUM/APM linking) | `references/llmobs-python.md` |
| LLM Observability — Node.js / Next.js (SDK init, spans, session ID, RUM/APM linking) | `references/llmobs-nodejs.md` |

If Phase 1d found that the tracer already starts on a runtime, keep that start and add only what is missing. Credential provisioning keeps an existing key, so run it whenever the start path may lack one. Still act on any gap the Phase 1d session-ID plumbing audit found, using the same reference files.

---
## Phase 3: Verification and Reporting

See `references/common-verify-report.md` for the verify step and the JSON report shape.
