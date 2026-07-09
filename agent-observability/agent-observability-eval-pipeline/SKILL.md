---
name: agent-observability-eval-pipeline
description: End-to-end Agent Observability pipeline for an instrumented ml_app — classify production traces, root-cause failures, bootstrap evaluators, then (optionally) sample + publish a dataset, generate + run an experiment, and analyze results. Six narrated phases with a standardized banner and a "continue" checkpoint between each. Pure orchestration over the agent-observability sub-skills (`agent-observability-session-classify`, `agent-observability-trace-rca`, `agent-observability-eval-bootstrap`, `agent-observability-experiment-py-bootstrap`, `agent-observability-experiment-analyzer`). Use when user says "run the eval pipeline", "go from traces to evals", "bootstrap evals end to end", "classify then RCA then bootstrap", "build an eval set from scratch", "onboard me to datasets and experiments", "walk me through experiments", "I have an ml_app, now what", "Agent Observability onboarding", "guided experiment setup", "from traces to experiments", or wants a deterministic, narrated tour from production data through evaluators, datasets, and experiments. Stop early with `--stop-after <phase>` to short-circuit at evaluators or dataset, or resume mid-flow with `--start-at <phase>`.
---

## Backend

**Detection** — At the start of every invocation, before taking any action, determine which backend to use:

1. If the user passed `--backend pup` anywhere in their invocation → use **pup mode** immediately, regardless of whether MCP tools are present. Skip steps 2–4.
2. Check whether MCP tools are present in your active tool list. The canonical signal is whether `mcp__datadog-llmo-mcp__search_llmobs_spans` appears in your available tools.
3. If MCP tools are present → use **MCP mode** throughout. Call MCP tools exactly as named in the sub-skill workflow sections.
4. If MCP tools are absent → check whether `pup` is executable: run `pup --version` via Bash. A JSON response containing `"version"` confirms pup is available.
5. If pup responds → use **pup mode** throughout. Each sub-skill carries its own Tool Reference appendix with the full MCP→pup mapping.
6. If neither is available → stop and tell the user:
   > "Neither the Datadog MCP server nor the pup CLI is available. Connect the MCP server (`claude mcp add --scope user --transport http datadog-llmo-mcp 'https://mcp.datadoghq.com/api/unstable/mcp-server/mcp?toolsets=llmobs'`) or install pup."

`--backend pup` is accepted anywhere in the invocation arguments. Strip it from args before passing to sub-skills, but carry the pup-mode decision forward — every sub-skill must also operate in pup mode for the entire pipeline run.

**Sub-skill backend propagation**: The backend detected at startup applies to all sub-skills invoked across the six phases. Do not re-detect per phase. Announce once at startup:
- MCP mode: "(Running in MCP mode — all features available.)"
- pup mode: "(Running in pup mode — pup commands used throughout. All features available.)"

**Invocation ID:** At the very start of each invocation, before any MCP tool call, generate an 8-character hex invocation ID (e.g., `3a9f1c2b`). Keep it constant for the entire invocation.

**Intent tagging:** On every MCP tool call, prefix `telemetry.intent` with `skill:agent-observability-eval-pipeline[<inv_id>] — ` followed by a description of why the tool is being called. On the **first MCP tool call only**, use `skill:agent-observability-eval-pipeline:start[<inv_id>] — ` instead (note the `:start` suffix). Example first call: `skill:agent-observability-eval-pipeline:start[3a9f1c2b] — Precheck: verify ml_app has traces in the last 7 days`

---

# Agent Observability Eval Pipeline — Classify → RCA → Eval Bootstrap → Dataset → Experiment → Analyze

A deterministic, six-phase guided pipeline for an already-instrumented `ml_app` owner. Each phase has the same envelope — a banner that names the entity being produced, an explanation of its purpose, the action (a sub-skill call or a small executable step), and a checkpoint. **You always know where you are.**

```
[Precheck] verify ml_app, project, backend, credentials, output dir
   ↓
[Phase 1: Classify ml_app traces]        entity: ml_app, trace, span
   ↓
[Phase 2: Root cause analysis]           entity: failure mode, root cause
   ↓
[Phase 3: Bootstrap evaluators]          entity: evaluator, LLM judge
   ↓                                     (stop here with --stop-after eval-bootstrap
   ↓                                      for the classic eval-pipeline behavior)
[Phase 4: Create + publish dataset]      entity: dataset record, published dataset
   ↓                                     (executes: LLMObs.create_dataset)
[Phase 5: Generate + run experiment]     entity: experiment, task, evaluator, run
   ↓                                     (executes: python <generated_file>)
   ↓                                     (in-phase review beat before run)
[Phase 6: Analyze experiment]            entity: metric, comparison, recommendation
```

This skill is **pure orchestration plus pedagogy** — no new analytical logic. The work happens inside the sub-skills (`agent-observability-session-classify`, `agent-observability-trace-rca`, `agent-observability-eval-bootstrap`, `agent-observability-experiment-py-bootstrap`, `agent-observability-experiment-analyzer`). What this skill adds is the deterministic envelope: every phase has the same shape, the same checkpoint contract, and the same entity-explanation banner — so the user gets a consistent, narrated experience regardless of how they phrased the original request.

## Usage

```
/agent-observability-eval-pipeline <ml_app> [--project-name <name>] [--timeframe <window>] [--trace-limit <N>]
                                [--trace-export <path>]
                                [--format py|ipynb] [--evaluator-style function|class|remote]
                                [--offline-evaluators | --online-evaluators | --data-only]
                                [--start-at classify|rca|eval-bootstrap|dataset|experiment|analyze]
                                [--stop-after classify|rca|eval-bootstrap|dataset|experiment|analyze]
                                [--classification-summary <path>] [--rca-report <path>]
                                [--dataset-file <path>] [--dataset-name <name>]
                                [--experiment-file <path>] [--experiment-id <uuid> | --experiment-url <url>]
                                [--app-root <path>] [--env-file <path>] [--output-dir <dir>]
                                [--backend pup]
```

Arguments: $ARGUMENTS

### Inputs

| Input | Required | Default | Description |
|-------|----------|---------|-------------|
| `ml_app` | Yes | — | The instrumented LLM app to onboard / evaluate against. The precheck verifies it has recent traces. |
| `--project-name` | No | derived from `pyproject.toml` / `setup.cfg` / `setup.py` / `package.json` / cwd (same order as `agent-observability-experiment-py-bootstrap`); falls back to `experiment-sdk-default` | The Datadog **project** the pipeline writes datasets and experiments into. The SDK lazily creates the project on first use via `LLMObs.enable(project_name=...)`. Surface this in the Precheck so the user can confirm before anything is created. |
| `--timeframe` | No | `now-7d` | Lookback window for Phase 1 classification and Phase 4 dataset sampling. **Ignored** when `--trace-export` is set (the curated set is already fixed). |
| `--trace-limit` | No | `20` | Sampling cap for Phase 4. Phase 1 internally uses `min(20, --trace-limit)` for the classification sample. **Ignored** when `--trace-export` is set. |
| `--trace-export <path>` | No | none | Path to an export file of curated trace IDs (e.g. a Datadog **Annotation Queue** export). Format is inferred from the file extension: **`.csv`** is supported today; **`.json`** is planned as a follow-up. When set, the pipeline skips its own sampling — Phase 1 classifies only the listed traces, Phase 2 RCAs the failing subset, and Phase 4 reuses the export's `Input` / `Output` fields directly as dataset records (no re-fetch, no span-walking). See the **Precheck** section below for the expected schema and field aliases. Mutually exclusive with `--dataset-file` (both supply Phase 4's input). |
| `--format` | No | `py` | Passed to `agent-observability-experiment-py-bootstrap` in Phase 5: `py` (script) or `ipynb` (Jupyter notebook). |
| `--evaluator-style` | No | `function` | Passed to `agent-observability-eval-bootstrap` (Phase 3) and `agent-observability-experiment-py-bootstrap` (Phase 5): `function`, `class`, or `remote`. |
| `--offline-evaluators` | No | on (default) | Phase 3: emit a Python SDK evaluator suite (BaseEvaluator / LLMJudge classes) that runs inside an experiment against a dataset. Maps internally to `agent-observability-eval-bootstrap` `sdk_code` mode. |
| `--online-evaluators` | No | off | Phase 3: publish online LLM-judge evaluators directly to Datadog (created as disabled drafts; enable in the UI). Online evaluators run on production spans as they're emitted. Maps internally to `agent-observability-eval-bootstrap` `publish` mode (was `--publish`). |
| `--data-only` | No | off | Phase 3: emit a local data blob only — no executable evaluator code or online publish. At Phase 3 entry the skill **prompts** the user to pick one of: (a) a `DatasetRecordRaw[]` JSON suitable for experiment use (maps internally to `agent-observability-eval-bootstrap --emit-dataset`), or (b) a framework-agnostic JSON evaluator spec for local analysis (maps internally to `agent-observability-eval-bootstrap` `data_only` mode). |
| `--stop-after <phase>` | No | `analyze` (run everything) | Stop after the named phase completes. `classify` = Phase 1 only. `rca` = through Phase 2. `eval-bootstrap` = through Phase 3 (matches the classic eval-pipeline). `dataset` = through Phase 4 (dataset created + published). `experiment` = through Phase 5 (experiment generated + run). `analyze` = all six phases (default). |
| `--start-at <phase>` | No | `classify` (start at the top) | Skip earlier phases and start at the named phase. Same vocabulary as `--stop-after`. The skill auto-loads any required prior-phase artifacts from `<output-dir>/state/` (see "State persistence and entry/exit" section). For phases that need an artifact the auto-load can't find, supply it via one of the override flags below. Combinable with `--stop-after` to run a contiguous slice of the pipeline. |
| `--classification-summary <path>` | No | auto-loaded from `<output-dir>/state/01-classification.md` if `--start-at rca` or later | Override the Phase 1 output that Phase 2 consumes. Useful when the prior state file is missing or you want to point at a hand-edited version. |
| `--rca-report <path>` | No | auto-loaded from `<output-dir>/state/02-rca-report.md` if `--start-at eval-bootstrap` or later | Override the Phase 2 output that Phase 3 consumes. |
| `--dataset-file <path>` | No | auto-loaded from `<output-dir>/state/04-published-dataset.json`'s `dataset_file` field (or the most recent `<output-dir>/dataset_<ml_app>_*.json`) | The local `DatasetRecordRaw[]` JSON. Used by Phase 4's publish sub-step when re-publishing without re-sampling. |
| `--dataset-name <name>` | No | auto-loaded from `<output-dir>/state/04-published-dataset.json` if `--start-at experiment` | The name of a published Datadog dataset that Phase 5 wires the experiment to. |
| `--experiment-file <path>` | No | auto-loaded from `<output-dir>/state/05-experiment-run.json`'s `experiment_file` field if `--start-at experiment` and the file already exists | The generated experiment file. When present, Phase 5 skips the codegen sub-step (5a) and goes straight to the review beat (5b) → run (5c). |
| `--experiment-id <uuid>` | No | auto-loaded from `<output-dir>/state/05-experiment-run.json` if `--start-at analyze` | The Datadog experiment ID Phase 6 analyzes. Mutually exclusive with `--experiment-url`. |
| `--experiment-url <url>` | No | auto-loaded as above | Alternative to `--experiment-id`. The skill parses the trailing UUID out of the URL. |
| `--app-root` | No | resolved from cwd / `pyproject.toml` etc. | Restricts `agent-observability-experiment-py-bootstrap`'s task-function introspection to this directory tree. |
| `--env-file` | No | none (auto-discovery walks standard locations) | Explicit `.env` path for credential loading. Surfaced in the Precheck and baked into the generated experiment as `ENV_FILE_OVERRIDE`. |
| `--output-dir` | No | `./experiments` | Where the dataset JSON, publish script, and generated experiment file are written. |
| `--backend` | No | auto-detect | `pup` forces pup mode regardless of MCP availability. |

If `ml_app` is not provided, ask the user before proceeding. The three evaluator-output flags (`--offline-evaluators`, `--online-evaluators`, `--data-only`) are mutually exclusive — error out if more than one is set. If none are set, `--offline-evaluators` is the default. The legacy flag names `--publish` and `--sdk-code` are still accepted as aliases for backward compatibility but map to the new names in all output. `--trace-export` and `--dataset-file` are mutually exclusive — both supply Phase 4 input but at different layers; error out at argument parse time if both are set.

---

## Precheck

Before Phase 1, run a single short verification pass — do **not** announce a "Phase" banner for this; it's plumbing. Output a one-block precheck summary, then move directly to Phase 1.

1. **Backend** — already detected at the top of this skill. Note the chosen backend in the precheck output so the user can confirm.

2. **ml_app has recent traces** — call `search_llmobs_spans(query="@ml_app:\"<ml_app>\"", root_spans_only=true, limit=1, from="<timeframe>")` (MCP) or the pup equivalent. If the result is empty, stop and tell the user the precheck failed — there is nothing to evaluate against — and suggest widening `--timeframe` or confirming the ml_app name.

3. **Resolve `project_name`** — if `--project-name <name>` was supplied, use it verbatim. Otherwise derive using the same resolution order as `agent-observability-experiment-py-bootstrap` (Workflow step 1): `pyproject.toml` → `setup.cfg` → `setup.py` → `package.json` → cwd basename (slugified). Final value is `experiment-<service-name>`; fall back to `experiment-sdk-default` if nothing resolves and emit a warning telling the user to set `--project-name` explicitly.

   **Project creation semantics**: the project is created lazily by the Datadog SDK the first time `LLMObs.enable(project_name=...)` is called against the org (in Phase 4's publish script, and again in Phase 5's generated experiment). The user does not need to pre-create anything in the UI. Surface the chosen project name in the Precheck output so the user can override before Phase 4 if it isn't what they wanted.

4. **Ensure `--output-dir` and `<output-dir>/state/` exist** — `mkdir -p <output-dir>/state` via Bash. Cheap. The `state/` subdirectory is where phase outputs get persisted (see "State persistence and entry/exit" below).

5. **Resolve credentials.** Walk the discovery order below to find Datadog credentials before Phase 4 needs them — failing late at the publish step is bad UX. Read-only at this stage: do NOT write any new files. Do NOT print secret values to the user; only report which file was loaded and which keys were resolved.

   **Discovery order** (first hit per variable wins; shell env vars always override files):
   1. **`--env-file <path>`** override if supplied — always tried first.
   2. **Current shell environment** (`os.environ`) — already-exported `DD_API_KEY` / `DD_APPLICATION_KEY` / `DD_APP_KEY` / `DD_SITE` take precedence over file values. If all required keys are already present, skip file loading entirely.
   3. **`<output-dir>/.env`** — if the user previously ran the skill and dropped a `.env` next to past artifacts, prefer that.
   4. **`<app-root>/.env`** — the resolved `pyproject.toml` / `setup.cfg` / `setup.py` / `package.json` directory (or cwd if none).
   5. **`<app-root>/.env.local`** — git-ignored local override convention.
   6. **`<cwd>/.env`** — fallback if cwd differs from app-root.
   7. **Parent walk**: from cwd, walk up directory by directory looking for `.env` until reaching `/` or the user's home directory. Stop at the first hit.
   8. **`~/.datadog/credentials`** — Datadog's well-known per-user credentials file, if present.

   For each file checked, parse line-by-line: skip blanks / comment lines (`#`) / malformed lines (no `=`). Strip a leading `export ` if present (so `.envrc`-style files work). Split on the first `=`. Strip surrounding quotes on the value. Only set a variable that is not already in `os.environ` — never overwrite the shell.

   **Required keys**: `DD_API_KEY` AND (`DD_APPLICATION_KEY` OR `DD_APP_KEY`). `DD_SITE` is optional (defaults to `datadoghq.com`). Provider keys (`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, etc.) are validated later in Phases 6/7 against the introspected task — not here.

   **If all required keys resolved** — record which file(s) were loaded and emit a single-line summary in the Precheck block. Continue.

   **If required keys NOT found after walking every location** — stop and prompt the user with a clear, actionable message:

   > "Datadog credentials were not found in your shell env or any discovered `.env` file. Two options before continuing:
   > - **Export in your shell**: `export DD_API_KEY=…` and `export DD_APPLICATION_KEY=…` (also `DD_SITE=…` if non-default), then re-invoke this skill.
   > - **Drop a `.env`** at `<app-root>/.env` with `DD_API_KEY=…` and `DD_APPLICATION_KEY=…` on separate lines, then re-invoke.
   >
   > Make sure `.env` is in your `.gitignore` before committing."

   Do **not** offer to create the `.env` file for the user — secrets-on-disk decisions belong to the user, not the skill.

6. **Trace-export resolution and validation** — resolve where the curated trace list is coming from, then validate it. Skip this step entirely only if none of the sources below match.

   **Source resolution** (first hit wins):

   a. **`--trace-export <path>` flag explicitly passed** → use that path.

   b. **Context auto-detection** *(only if the flag was NOT passed)* — scan the current Claude Code conversation context (the user's invocation message plus any attached files) for signals of a curated trace list. **Detect and confirm; never silently adopt.** Look for any of:

      - An attached file with a `.csv` or `.json` extension whose content includes 32-character lowercase hex strings that look like `trace_id`s (10+ occurrences is a strong signal; treat any file whose name matches `*annotation*queue*` as a strong signal regardless of content sniff).
      - A fenced code block in the invocation message whose first line looks like a CSV header (`Content ID`, `content_id`, `trace_id`, or any of the accepted `input` aliases) followed by rows of hex + text.
      - A markdown table in the invocation message with a column of 32-char hex trace IDs.
      - A plain list of 10+ 32-char hex strings in the invocation message (one per line or comma-separated).

      For each candidate, materialize the content to `<output-dir>/state/00-trace-export-detected.csv` (write the raw block verbatim; the existing schema resolver in the sub-steps below handles the parsing). Then **ask the user via `AskUserQuestion`** — do not proceed without explicit confirmation:

      > "I detected what looks like a curated trace list in your context — `<one-line description: e.g. "an attached CSV named annotation-queue-export.csv (16 rows)"` / "a fenced CSV block in your message (3 rows)" / "a table of 8 trace IDs">`. Should I use it as `--trace-export`?"
      >
      > Options:
      > - **Use it** — resolves the source, continues with validation below.
      > - **Ignore and sample fresh** — proceeds without `--trace-export` (Phase 1 and Phase 4 sample from the ml_app as normal).
      > - **Show me what I detected** — surface the first 5 rows of the detected content, then re-ask.

      If confirmed, set `<resolved-path>` to `<output-dir>/state/00-trace-export-detected.csv` (or `.json`) and continue with validation using that path. Surface a Precheck line: `Trace export: detected from context, awaiting your confirmation` if the user hasn't answered yet, then `Trace export: <path> (auto-detected, confirmed by user)` after.

      **Never** auto-detect more than one candidate. If multiple sources are visible (e.g., an attached file *and* a pasted block), list both in the `AskUserQuestion` prompt and let the user pick.

   c. **No flag and no context match** — skip the whole trace-export step. Phase 1 and Phase 4 sample fresh from the `ml_app` as they would without the flag. Surface a Precheck line: `Trace export: (not set — pipeline will sample fresh traces in Phase 1 and Phase 4)`.

   After resolution, proceed with validation on `<resolved-path>` regardless of how it was resolved.

   **Format support**. Infer the format from the file extension:

   - **`.csv`** *(supported today)* — the canonical shape of a Datadog Annotation Queue export. Parse with Python's `csv.DictReader` via Bash. See the schema table below.
   - **`.json`** *(planned)* — a JSON export shape will be supported in a follow-up. When encountered today, stop with: *"`.json` export format is not yet supported. Convert to CSV matching the schema below, or wait for the JSON follow-up."*
   - **Anything else** — stop with: *"Unrecognized export format `<ext>`. Supported: `.csv` (today), `.json` (planned)."*

   **CSV schema**. Column names are matched case-insensitively, ignoring spaces and underscores, so `Content ID`, `content_id`, `contentid`, `trace_id`, and `id` are all the same column. Accept these column-name aliases:

   | Canonical | Required | Aliases (any case, spaces/underscores ignored) |
   |---|---|---|
   | `trace_id` | **yes** | `Content ID`, `content_id`, `trace_id`, `id` |
   | `input` | **yes** | `Input`, `input_data`, `prompt`, `query`, `question` |
   | `output` | no | `Output`, `actual_output`, `response`, `answer` |
   | `expected_output` | no | `Expected Output`, `expected_output`, `expected`, `gold`, `truth` |
   | `type` | no | `Type`, `kind` — when present, **filter to rows where the value equals `trace`** (case-insensitive); skip `session` / `span` / other types with a one-line warning per skipped row |

   If `expected_output` is present, use it as the dataset's `expected_output`. If only `output` is present (the common annotation-queue export case), use `output` as `expected_output` and surface this decision in the Precheck output so the user can override at the Phase 4 checkpoint.

   If neither `input` nor `output` columns are found, stop with: *"The CSV at `<path>` is missing required columns. Need at least one of `Input`/`input_data`/`prompt` and (`Output`/`response` or `Expected Output`/`expected`). Found columns: `<comma-separated list>`."*

   **trace_id format**. Each `trace_id` should be a 32-character lowercase hex string. Strip leading/trailing whitespace. Reject empty rows with a one-line warning and skip them.

   **Existence check**. For each `trace_id`, verify the trace exists in Datadog via a single MCP / pup call:

   - **MCP mode**: `mcp__datadog-llmo-mcp__search_llmobs_spans(query="trace_id:<id>", limit=1, root_spans_only=true)`. Result of zero spans = trace not found.
   - **pup mode**: `pup llm-obs spans search --query "trace_id:<id>" --root-spans-only --limit 1 --summary`. Empty `spans` array = trace not found.

   Issue these calls in parallel — one tool call per row, all in a single message — since the CSV is typically small (annotation queue exports are 10–100 rows). For each missing trace, emit a one-line warning, drop the row, and continue. **Never fail the whole precheck just because one trace is missing** — surface a summary count instead. If **all** rows are missing or invalid, stop the run and tell the user to verify the CSV and the `ml_app` are in the same Datadog org / site.

   **Cache the validated set**. Write the kept rows to `<output-dir>/state/00-trace-export.json` with this shape so later phases consume it without re-reading the CSV:

   ```json
   {
     "source_path": "<absolute path to the export file>",
     "ml_app": "<ml_app>",
     "row_count_total": <int>,
     "row_count_kept": <int>,
     "row_count_dropped": <int>,
     "drop_reasons": {"missing_in_datadog": <int>, "non_trace_type": <int>, "empty_trace_id": <int>, "bad_input_output": <int>},
     "records": [
       {"trace_id": "<id>", "input": "<input text>", "output": "<output text or null>", "expected_output": "<expected_output text or null>"},
       ...
     ]
   }
   ```

   Surface a one-line summary in the Precheck output: `Trace export: <kept>/<total> rows loaded from <path> (<dropped> dropped: <one-line reason breakdown>)`.

Output the precheck summary, then start Phase 1:

```
## Precheck

- Backend: <MCP | pup>
- ml_app `<ml_app>` has traces in <timeframe>: yes (<sample_count> root spans found)
- Project name: `<project_name>` (created lazily on first LLMObs.enable() call in Phase 4)
- Output dir: `<output-dir>` (created)
- Credentials: <one of:
    "loaded from shell env (DD_API_KEY, DD_APPLICATION_KEY, DD_SITE)"
  | "loaded from <relative path to .env file> (DD_API_KEY, DD_APPLICATION_KEY[, DD_SITE])"
  | "shell env + <relative path>: keys resolved from both (shell overrode file for <list>)"
  >
- Trace export: <one of:
    "(not set — pipeline will sample fresh traces in Phase 1 and Phase 4)"
  | "<kept>/<total> rows loaded from <relative path> (<dropped> dropped: <reasons>); using `output` column as expected_output"
  >
- Stop-after: <phase from --stop-after, default `analyze`>

Starting Phase 1 of 6.
```

The exact list of keys in the credentials parenthetical reflects what was actually discovered (so the user can verify nothing surprising was loaded). Never print the values.

---

## Phase Template

Every phase below uses this exact template. Do not deviate — the deterministic envelope is what makes the pipeline experience consistent across invocations.

```
## Phase N of 6: <Title>

**You are here.** Phase N of 6 — <one-line position summary>.

**What this phase produces**: <Entity name>
**What a <entity> is**: <2-3 sentence definition tailored to this phase>
**Why it matters**: <1 sentence on why the user needs this>

→ Action: <invoke <sub-skill> | execute <script>>

<full sub-skill output OR execution log reproduced here — do NOT summarize or truncate>

<after the action completes successfully, Write the phase output to
`<output-dir>/state/0N-<name>.{md,json}` per the State persistence contract — see
"State persistence and entry/exit" section. This happens BEFORE the checkpoint
so the file is on disk even if the user types `stop` next.>

---

### Checkpoint <N>

<concise summary of what was produced, where it lives (path / Datadog URL), and any caveats>

Before I continue to Phase N+1 (<next title>):
- <2–3 phase-specific review prompts the user can answer>

Type `continue` to proceed, `stop` to exit cleanly (state is saved), `redo` to re-run this phase, or give me adjustments.
```

**Never auto-advance.** Always pause at the checkpoint and wait for explicit user input. The whole point of this skill is determinism — that includes determinism over *when* the user moves on.

If the current phase matches the value of `--stop-after`, replace the checkpoint prompt with a **Stop summary** (see "Stop-after handling" at the bottom of this file).

---

## Phase 1: Classify ml_app traces

**Entity**: `ml_app`, `trace`, `span`.

**Pedagogy banner** (use verbatim, adapted only to the actual ml_app name):

> **What an ml_app is**: a logical LLM application — a name you tag spans with when instrumenting (`ml_app=<name>`). It groups all production traces and evaluator runs that belong to the same product surface. Every dataset, experiment, and evaluator you create later targets this scope.
>
> **What a trace is**: one end-to-end execution of your ml_app — typically the agent loop for a single user request, made up of one or more spans (LLM calls, tool calls, retrievals).
>
> **Why this phase matters**: before you root-cause failures (Phase 2), bootstrap evaluators (Phase 3), or curate a dataset (Phase 4), you want a quick read on what your app actually does in production and where its current failure modes are. Classification gives you that signal in one pass.

**Trace pool preview (MANDATORY).** Before invoking the sub-skill, build and surface a Datadog Traces UI link that opens the **exact set of root spans Phase 4 will sample from**. This lets the user eyeball the pool, spot outliers, or adjust `--timeframe` before any classification work runs. The link must match the filter that `agent-observability-eval-bootstrap --emit-dataset` uses in Phase 4 (see `agent-observability/agent-observability-eval-bootstrap/SKILL.md` → Phase 3D → Sampling): `@ml_app:"<ml_app>" @status:ok`, root spans only.

URL construction rules:

1. **Host**: prepend `app.` to `DD_SITE`. If `DD_SITE` is unset, default to `app.datadoghq.com`.
   - `datadoghq.com` → `app.datadoghq.com`
   - `datadoghq.eu` → `app.datadoghq.eu`
   - `us3.datadoghq.com` → `app.us3.datadoghq.com`
   - `us5.datadoghq.com` → `app.us5.datadoghq.com`
   - `ap1.datadoghq.com` → `app.ap1.datadoghq.com`
   - `ap2.datadoghq.com` → `app.ap2.datadoghq.com`
   - `datad0g.com` → `app.datad0g.com` (staging)
2. **Path**: `/llm/traces` (the Agent Observability Traces explorer).
3. **Query string**: URL-encode `@ml_app:"<ml_app>" @status:ok @parent_id:undefined` and bind to `query=`.
4. **Time window**: convert `--timeframe` to absolute epoch milliseconds (`now - <duration in ms>` for the start, `now` for the end) and bind to `start=` / `end=`.
5. **Optional**: append `&paused=true` so the UI does not live-stream the result on open.

Surface the link immediately before the Action block:

```
**Trace pool being analyzed** ({timeframe}, filter `@ml_app:"<ml_app>" @status:ok`, root spans only):

  <full URL>

Phase 1 will classify the first {min(20, trace-limit)} of these for orientation. Phase 4 will sample up to {trace-limit} from the same pool for the dataset. Click through if you want to see the pool before either runs, or adjust `--timeframe` and re-invoke if the window looks off.
```

**Action — two paths depending on `--trace-export`**:

#### Path A — default (sample from ml_app)

Follow the **`agent-observability-session-classify`** skill in **ml_app mode**, using:
- `ml_app` = the provided ml_app
- `timeframe` = the provided timeframe
- `sample_limit` = `min(20, trace-limit)` — keep this fast; Phase 4 will do the bigger sample

Run the complete ml_app mode workflow as defined in that skill (Steps M1 through M3). **Output the full classification output** (all compact per-unit blocks plus the final `# Session Classification Summary`) — do not summarize or truncate. Downstream Phase 2's RCA depends on the full text being in context.

#### Path B — `--trace-export` set (curated trace set)

Skip session-classify's own sampling. The trace pool is **exactly** the validated rows in `<output-dir>/state/00-trace-export.json` (cached by the Precheck — every row's `trace_id` already exists in Datadog at this point).

For each `trace_id` in the cached set, fetch the trace's root span in parallel — one tool call per trace, all in a single message:

- **MCP mode**: `mcp__datadog-llmo-mcp__search_llmobs_spans(query="trace_id:<id>", limit=1, root_spans_only=true)`. Take the single returned span.
- **pup mode**: `pup llm-obs spans search --query "trace_id:<id>" --root-spans-only --limit 1`. Take the single span from the `spans` array.

Then invoke `agent-observability-session-classify` in **ml_app mode**, but **pass the pre-fetched root spans inline as the sample** rather than letting it issue its own `search_llmobs_spans` call. The sub-skill's classification loop (Steps M2–M3) consumes whatever sample is in scope — give it the curated set and it will classify exactly those. **Output the full classification output** verbatim, same rule as Path A.

When emitting the **Trace pool preview** link above this Action, replace it with a **Curated trace set** preview instead — a Datadog UI link that opens just the listed traces:

```
**Curated trace set** ({N} traces from `<csv path>`):

  <https://app.<DD_SITE>/llm/traces?query=trace_id%3A(<id1>%20OR%20<id2>%20OR%20...)>

Phase 1 will classify all {N} of these. Phase 4 will use their `Input` / `Output` pairs directly as dataset records — no re-sampling, no span-walking.
```

URL construction is the same as Path A's host/path/time-window rules, except the `query=` parameter is built as `trace_id:(<id1> OR <id2> OR ... OR <idN>)` (URL-encoded). If the resulting URL would exceed 8000 characters (rough browser limit), truncate to the first 50 IDs and surface a one-line note: *"link shows first 50 of N traces; full curated set is processed by the pipeline."*

### Checkpoint 1

After the `# Session Classification Summary` is output, present:

```
## Phase 1 complete — you've seen what `<ml_app>` does in production

[verdict distribution table from session-classify]
[failure mode frequency table from session-classify]

**Trace pool for Phase 4's dataset sample**: <full URL from the Trace pool preview above>
(same filter / same timeframe — open it now if you want to scan for traces you'd rather exclude)

Next up — Phase 2 will diagnose *why* the failing traces are failing.

Before I continue:
- Do these failure patterns look right?
- Any traces you'd like to exclude from the dataset sample in Phase 4? (Paste trace IDs from the link above and I'll drop them.)
- Any quality dimension you already know you want to measure later?

Type `continue` to proceed, `stop` to exit cleanly (state is saved), `redo` to re-run this phase, or give me adjustments.
```

Wait for explicit user confirmation. If the user excludes specific traces, mark them as "excluded by user" — drop them from Phase 2's failure bucket and from Phase 4's sampling. Do NOT re-classify.

---

## Phase 2: Root cause analysis

**Entity**: `failure mode`, `root cause`.

**Pedagogy banner**:

> **What a failure mode is**: a recurring pattern in *how* your app fails — e.g. "the model hallucinates citation URLs", "the agent forgets state across tool calls", "the retrieval returns irrelevant chunks". Each failure mode has one or more *root causes* (system prompt deficiency, tool gap, retrieval miss, etc.).
>
> **Why this phase matters**: an evaluator that scores generic "is this response good?" misses the specific things going wrong in your app. RCA lets Phase 3 propose evaluators that target your *actual* failure modes — sharper signal, fewer false alarms.

**Action**: Follow the **`agent-observability-trace-rca`** skill.

The `# Session Classification Summary` from Phase 1 is in context. The skill detects it automatically via its Phase 0 Step 0S check and enters the "from classifications" path — it extracts the failure bucket, presents the Classification Overview, and proceeds directly to Phase 2 (open coding) without running its own Phase 1 span search.

Run the full workflow through Phase 6 (the compiled RCA report). **Output the full RCA report** — do not summarize. The full report must be in context for Phase 3's detection to work.

### Checkpoint 2

```
## Phase 2 complete — root causes identified

[the Phase 6 RCA report is above]

Next up — Phase 3 will bootstrap evaluators that target these failure modes.

Before I continue:
- Do these root causes look accurate?
- Any failure modes to add, remove, or reframe?
- Which root causes should the evaluators target? (Default: all of them.)

Type `continue` to proceed, `stop` to exit cleanly (state is saved), `redo` to re-run this phase, or give me adjustments.
```

Wait for explicit user confirmation. If the user adjusts the taxonomy, incorporate the changes before continuing.

---

## Phase 3: Bootstrap evaluators

**Entity**: `evaluator`, `LLM judge`.

**Pedagogy banner**:

> **What an evaluator is**: a function that grades one record's output. Returns `bool` / `float` / a structured `EvaluatorResult`. Two flavors: **code evaluators** (deterministic checks — JSON validity, regex match, length, custom Python) and **LLM-as-judge evaluators** (a model graded against a rubric — e.g. "is this response grounded in the retrieved documents?").
>
> **Operating modes** (mutually exclusive; default is `--offline-evaluators`):
>
> - **`--offline-evaluators`** *(default)*: emit a Python SDK evaluator suite (BaseEvaluator / LLMJudge classes) that runs **inside an experiment against a dataset**. Use this when you'll run experiments locally.
> - **`--online-evaluators`**: publish LLM-judge evaluators to Datadog as disabled drafts. They run **on production spans as they're emitted** once enabled. Use this when you want continuous evaluation in prod.
> - **`--data-only`**: emit a local data blob only — no executable evaluator code or online publish. Prompts you to pick between a dataset for experiment use or a local analysis blob (see "Operating mode resolution" below).
>
> **Why this phase matters**: evaluators are the contract between "this output looks fine" and "this output meets our quality bar." The bootstrapped suite is grounded in the failure taxonomy from Phase 2 — sharper than generic evaluators you'd otherwise hand-write.

**Action**: Follow the **`agent-observability-eval-bootstrap`** skill.

The RCA report from Phase 2 is in context. The skill detects the `## Failure Taxonomy` heading automatically and enters its "from RCA" path in Phase 0.

### Operating mode resolution

Before invoking the sub-skill, resolve the operating mode:

1. **`--offline-evaluators`** (default) → call `agent-observability-eval-bootstrap` with no mode flag (its `sdk_code` default).
2. **`--online-evaluators`** → call `agent-observability-eval-bootstrap --publish`.
3. **`--data-only`** → prompt the user via `AskUserQuestion`:

   > "You picked `--data-only` for Phase 3. What kind of local data blob do you want?
   >
   > - **Dataset for experiment use** — a `DatasetRecordRaw[]` JSON suitable for `LLMObs.create_dataset(records=...)`. Useful when you want to seed an experiment with the records this skill samples. (Internally calls `agent-observability-eval-bootstrap --emit-dataset <path>`.)
   > - **Local blob for analysis** — a framework-agnostic JSON evaluator spec describing what evaluators *would* be generated, without emitting Python code. Useful for inspecting evaluator coverage without running anything. (Internally calls `agent-observability-eval-bootstrap --data-only`.)"

   If the user picks "Dataset for experiment use" and the pipeline is running through Phase 4 (i.e. `--stop-after` is not `eval-bootstrap` or earlier), tell the user that Phase 4's sample step will be **skipped** in favor of this Phase 3 output — the dataset they produce here will be the one Phase 4 publishes.

Pass `--evaluator-style` through unchanged.

**The agent-observability-eval-bootstrap skill has its own mandatory proposal checkpoint** (the evaluator suite proposal before code generation). Honor it — do not skip or auto-confirm it.

### Checkpoint 3

```
## Phase 3 complete — evaluator suite ready

- Mode: `<offline-evaluators | online-evaluators | data-only (dataset-for-experiment) | data-only (analysis-blob)>`
- Output: `<path to .py / .json / dataset-record JSON / "published as drafts to Datadog">`
- Evaluators emitted: <list of names>
- Coverage: <one-liner: which failure-mode categories are now covered>

Next up — Phase 4 will sample production traces into a dataset you can run experiments against (using these evaluators or the placeholders the experiment template ships with).

If you only wanted evaluators (the classic eval-pipeline flow), this is the natural stopping point: re-invoke with `--stop-after eval-bootstrap` to formalize that as the exit.

Before I continue:
- Do the generated evaluators look right?
- Any to drop or rename before they're referenced in the experiment?

Type `continue` to proceed, `stop` to exit cleanly (state is saved), `redo` to re-run this phase, or give me adjustments.
```

Wait for explicit user confirmation. If `--stop-after eval-bootstrap` is set, this is where the pipeline ends — emit the Stop summary instead of the Checkpoint and exit.

---

## Phase 4: Create and publish dataset

**Entity**: `dataset`, `dataset record`, published dataset (Datadog-side), `dataset_name`, version.

**Pedagogy banner**:

> **What a dataset is**: a named collection of records that an experiment runs against. Each record has `input_data` (what the task receives) and optionally `expected_output` (what you expect back). Datasets live in Datadog under your project and have a version — every push that changes records produces a new version.
>
> **What a record is**: a single `(input_data, expected_output)` pair, optionally with `metadata` and `tags`. One record = one experiment row.
>
> **What "publishing" means**: pushing the local records to Datadog so the dataset becomes addressable by name across all subsequent experiments. After publish, anyone in your org (with access) can pull it with `LLMObs.pull_dataset(dataset_name="…")` — including the experiment code we generate in Phase 5.
>
> **Why this phase matters**: experiments need a stable, addressable input set. Sampling production traces gives you a realistic starting dataset (the inputs your app actually sees), and publishing it under your project makes it the contract between dataset curation and the experiments that consume it.

**This phase executes code on your machine** (the publish step writes to Datadog via `LLMObs.create_dataset(...)`). The dataset-create step is read-only; the publish step is the one that changes state.

**Action — two sub-steps, no intermediate checkpoint:**

**4a — Sample traces into a `DatasetRecordRaw[]` JSON.** Follow the **`agent-observability-eval-bootstrap`** skill in **`--emit-dataset` mode**:

```
/eval-bootstrap <ml_app> --timeframe <timeframe> --trace-limit <trace-limit> --emit-dataset <output-dir>/dataset_<ml_app>_<YYYYMMDD>.json
```

This mode samples root spans, extracts `(input_data, expected_output)` pairs, applies a PII scrub, and writes the JSON. **It does not propose or generate evaluators** — the dataset is the sole artifact. See `agent-observability/agent-observability-eval-bootstrap/SKILL.md` → Phase 3D for the full spec.

If the user excluded specific traces in Checkpoint 1, pass that exclusion list along (sub-skill drops them during sampling — do NOT re-classify).

Reproduce the sub-skill's `## Generated Dataset` summary verbatim.

**Skip 4a if Phase 3 already produced a dataset.** When the user picked `--data-only` with the "Dataset for experiment use" sub-mode in Phase 3, the Phase 3 state file (`state/03-evaluators.json`) has `mode: data_only_dataset` and an `output_path` pointing at the dataset JSON. In that case, **skip sub-step 4a entirely** and feed the Phase 3 output to 4b's publish helper directly. Surface a one-line note: "Reusing dataset from Phase 3 (`<path>`); skipping fresh sampling."

**Skip 4a if `--trace-export` is set.** When the Precheck cached a curated trace set at `<output-dir>/state/00-trace-export.json`, do **not** call `eval-bootstrap --emit-dataset` — its sampling logic is the very thing the export is replacing. Instead, build the `DatasetRecordRaw[]` JSON directly from the cached set by invoking the pre-shipped helper at `<this-skill-dir>/scripts/build_dataset_from_export.py` via Bash. **Do not** inline the script content into this SKILL.md or re-write it from scratch — the helper is the source of truth for the record-shape, PII scrub, and provenance tagging.

```bash
python <skill-dir>/scripts/build_dataset_from_export.py \
  --state <output-dir>/state/00-trace-export.json \
  --output <output-dir>/dataset_<ml_app>_<YYYYMMDD>.json \
  --ml-app <ml_app>
```

`<skill-dir>` resolves to wherever the skill is installed (e.g., `~/.claude/skills/agent-observability-eval-pipeline/`). The script prints:

- `Wrote <N> records to <path>` — success
- `PII redactions: <K>` — number of records where email / SSN / phone / api-key patterns were scrubbed
- `Records with expected_output: <M>/<N>` — how many records carry an `expected_output` (from either the `expected_output` or fallback `output` field in the cached export)
- `ERROR: ...` on stderr with a non-zero exit if the state file is missing or malformed

After the script runs, emit a sub-skill-style summary so the user sees what was produced without scrolling back to the CSV:

```
## Generated Dataset (from curated CSV)

- Source: `<csv path>` (annotation queue export)
- Record count: <N>
- expected_output present on: <M>/<N> records (sourced from <`expected_output` | `output`> column)
- PII redactions: <K>
- Per-record tags: `ml_app:<ml_app>`, `source:annotation-queue`, `trace_id:<id>`
- Output JSON: `<absolute path to dataset_<ml_app>_<YYYYMMDD>.json>`
```

Then surface a **mandatory review checkpoint** before 4b (since CSV records reflect the actual model output the user saw — they may want to edit those values into the *desired* output before publishing the dataset):

```
**Before I publish this dataset to Datadog** — quick review:

1. The dataset file is at `<output-dir>/dataset_<ml_app>_<YYYYMMDD>.json`. Open it now if you want to inspect or edit any records (the `Output` column from your CSV is currently being used as `expected_output` — edit it in place to mark the *desired* output if the captured output is wrong).
2. The publish step will create the dataset in Datadog under project `<project_name>` with name `<ml_app>_seed_<YYYYMMDD>`. Pass `--dataset-name <name>` to override.

Type `publish` to push to Datadog, `stop` to exit cleanly (the dataset JSON is saved on disk and can be re-published later), or paste an absolute path to a hand-edited file and I'll publish that one instead.
```

Once the user confirms, proceed to 4b using `<output-dir>/dataset_<ml_app>_<YYYYMMDD>.json` (or the override path) as 4b's `--records` argument.

**4b — Publish to Datadog.** Immediately invoke the pre-shipped publish helper at `<this-skill-dir>/scripts/publish_dataset.py` via Bash. **Do not** inline the script content into this SKILL.md or re-write it from scratch — the helper is the source of truth for the publish flow (credential discovery, tag normalization, project creation, error handling). It accepts CLI args, so no placeholder substitution is needed.

```bash
python <skill-dir>/scripts/publish_dataset.py \
  --records <absolute path to JSON from 4a> \
  --dataset-name <chosen dataset_name, default <ml_app>_seed_<YYYYMMDD>> \
  --project-name <resolved project_name from Precheck> \
  [--env-file <path>]   # repeatable; takes precedence over auto-discovery
```

`<skill-dir>` resolves to wherever the skill is installed (e.g., `~/.claude/skills/agent-observability-eval-pipeline/`). The script prints either:

- `Loaded credentials from: <file paths>` (if any `.env` files contributed values), then
- `OK dataset_name=<name> record_count=<N> url=<url>` (on success), or
- `ERROR: <message>` on stderr with a non-zero exit (auth, missing keys, ddtrace import failure, etc.).

**Notes for the orchestrator:**

- Before invoking the publish helper, do an import-availability precheck: `python -c "import ddtrace.llmobs"` via Bash. If it fails, stop and tell the user:
  > "`ddtrace` is not installed in the active Python environment. Run `pip install 'ddtrace>=4.7'` and re-invoke this skill (re-run from the top — Phases 1–3 outputs are idempotent)."
- `LLMObs.enable(project_name=...)` inside the script is where the `--project-name` from the Precheck actually materializes — the Datadog project is created lazily on first call.
- If the script prints a `WARNING:` line about tag normalization, surface it in Checkpoint 4 so the user knows their upstream dataset had malformed tags.
- If the script prints `Loaded credentials from: ...`, include that file path in Checkpoint 4.
- If the script exits non-zero with an auth error (401/403), surface the stderr and stop — do not retry. Tell the user the most likely cause is a stale `.env` value, and that `export DD_API_KEY=... DD_APPLICATION_KEY=...` in their shell takes precedence and can be used to override.
- On success, capture the printed `dataset_name` and `url` and carry them into Phase 5. Write both to the Phase 4 state file (see State persistence section).

**Why no intermediate checkpoint between 4a and 4b?** The local JSON is auto-extracted from traces and goes straight into `LLMObs.create_dataset(records=...)` — there is essentially no editable surface between the two steps in the common case. Users who want to inspect or edit the JSON before publish should run `eval-bootstrap --emit-dataset` standalone, edit the JSON, then re-enter this pipeline with `--start-at experiment --dataset-name <name>` once they've published manually.

### Checkpoint 4

```
## Phase 4 complete — dataset created and published

- Local file: `<path>` (kept for inspection / re-publish)
- Records emitted: <N> (skipped: <M> with no usable output)
- PII redactions: <P>
- Tag normalizations: <T>
- Published as: `<dataset_name>` in project `<project_name>` <(created if it did not exist)>
- Datadog UI: <url or "open Agent Observability → Datasets to confirm">
- Caveat: `expected_output` is the **current production behavior baseline**, not ground truth. Treat the dataset as a regression-style baseline before promoting it to a labelled gold set.

Next up — Phase 5 will generate a Python experiment script that pulls `<dataset_name>` and runs your task code (auto-discovered) against it.

Before I continue:
- Confirm you can see the dataset in the Datadog UI (Agent Observability → Datasets → search `<dataset_name>`)?
- Any second thoughts on the records (we can re-emit and re-publish before generating the experiment)?

Type `continue` to proceed, `stop` to exit cleanly (state is saved), `redo` to re-run this phase, or give me adjustments.
```

Wait for confirmation.

---

## Phase 5: Generate and run experiment

**Entity**: `experiment`, `task` function, `evaluator`, experiment run, `experiment.url`, metric stream.

**Pedagogy banner**:

> **What an experiment is**: a programmatic harness that, for each record in a dataset, calls a `task` function (your code under test — typically an LLM call), then runs one or more `evaluators` against the task's output. Datadog collects all those results into a single experiment view you can compare across runs.
>
> **What a task function is**: a Python callable that receives one record's `input_data` and a `config` dict, and returns whatever your app would have returned for that input. **The sub-skill introspects your project to find this function automatically** — no `# TODO(user)` placeholder unless nothing was found.
>
> **What an evaluator is**: covered in Phase 3 above. The generated experiment ships placeholder evaluators by default; if you ran Phase 3 with `--evaluator-style remote`, you can wire those names in here.
>
> **What `experiment.url` is**: the deep link to the run in the Datadog Experiments UI. Phase 6 uses this to analyze results.
>
> **Why this phase matters**: this is where your code actually executes against the dataset and produces measurements. Generation is the cheap part; running is what costs provider tokens and produces signal.

**This phase executes code on your machine** (Python file is run end-to-end after an in-phase review beat).

**Action — three sub-steps with an in-phase review beat between codegen and run:**

**5a — Generate the experiment file.** Follow the **`agent-observability-experiment-py-bootstrap`** skill:

```
/agent-observability-experiment-py-bootstrap \
  --dataset-name <dataset_name> \
  --project-name <project_name> \
  --format <format> \
  --evaluator-style <evaluator-style> \
  --app-root <app-root> \
  --env-file <env-file if set> \
  --output <output-dir>/experiment_<ml_app>_<YYYYMMDD>.<py|ipynb>
```

Reproduce the sub-skill's full output (including the generated SDK calls summary, the "Task function source" block, the credential discovery section, and the Next steps block) verbatim. Do not summarize. The "Task function source" block tells the user which `module:function` was auto-wired — that's load-bearing for the next sub-step.

**5b — In-phase review beat (MANDATORY).** After the sub-skill output, **pause** with a brief inline prompt (this is NOT a full checkpoint with a new banner — it's a single-line review beat *inside* Phase 5):

```
## Phase 5 — review the generated experiment before running

- File: `<path>`
- Wired to dataset: `<dataset_name>` (pulled at runtime via LLMObs.pull_dataset)
- Task function source: <line lifted from the sub-skill output — module:function, or "placeholder fallback">
- Evaluators: <2–3 evaluator names>

**Open the file and check three things before I run it:**
1. The wired `task_fn` (section 4) — confirm the sub-skill picked the right entry point. If it picked a helper or deprecated path, edit the import to point at the right function. If it fell back to a placeholder, replace it with a real call.
2. The placeholder evaluators (section 5) — these are starting points. If Phase 3 produced online evaluators via `--publish`, swap one of the placeholders for a `RemoteEvaluator(eval_name="...")`.
3. The `experiment.run(jobs=<N>)` parallelism (section 7) — defaults to 10; lower it if you're worried about rate limits.

Type **`run`** to execute the file as-is, **`edit`** to pause here so you can edit and re-run this skill, or **`stop`** to exit cleanly. State for Phase 4 and earlier is preserved.
```

Wait for the user's reply.

- If `run`: proceed to 5c.
- If `edit`: tell the user the file is at `<path>` and they can re-invoke this skill with `--start-at experiment` once they're satisfied. End the run cleanly. The generated file path is written to the state file so `--start-at experiment` can resume codegen-free (just re-run sub-step 5c).
- If `stop`: emit the Stop summary and end. State for completed phases (including the generated experiment file) is preserved on disk.
- Anything else: treat as adjustment / question. Reason about it, answer, then re-show the review prompt.

**5c — Execute the file.**

- For `--format py`: `python <generated_path>` via Bash. Stream output to the user.
- For `--format ipynb`: tell the user the generated file is a notebook and ask whether to (a) execute it via `jupyter nbconvert --to notebook --execute --inplace <path>` (requires `jupyter` installed), or (b) hand off — the user opens it in JupyterLab and runs cells manually. Default to (a) if `jupyter` is on PATH; otherwise (b).
- Capture the printed `experiment.url` from the run's stdout — the generated file always ends with `print(experiment.url)`. If you can't find it, parse stdout for the substring `https://app.datadoghq.com/llm/experiments/` (account for non-default `DD_SITE` hosts).
- If the run fails: do NOT retry automatically. Surface the full traceback, identify the failure category (auth, missing dep, dataset not found, task function raised, evaluator raised) in a one-line diagnosis, and ask the user whether to fix and re-run.

### Checkpoint 5

```
## Phase 5 complete — experiment generated and run

- File: `<path>`
- Experiment URL: <experiment.url>
- Records processed: <N>
- Duration: <wall-clock seconds>
- Task function: <module:function>
- Evaluator score summary (from stdout, if printed): <table or "open the UI">

Next up — Phase 6 will pull the experiment results back from Datadog and produce an analysis report (struggling metrics, qualitative examples, root-cause hypotheses).

Before I continue:
- Take a look at the experiment in the UI (link above). Do the per-record scores roughly match your expectations?
- Any specific question you want Phase 6 to focus on? (Optional — leaving it open runs an exploratory analysis.)

Type `continue` to proceed, `stop` to exit cleanly (state is saved), `redo` to re-run this phase, or give me adjustments.
```

Wait for confirmation. If the user provides a focus question, carry it to Phase 6 as the analyzer's `question` argument.

---

## Phase 6: Analyze experiment

**Entity**: experiment `metric`, segment comparison, recommendation.

**Pedagogy banner**:

> **What an experiment metric is**: a per-record score produced by one of your evaluators, aggregated into a pass-rate / score-distribution across the dataset. The Datadog Experiments UI shows these as columns and lets you slice by `metadata` fields you attached to each record.
>
> **What a recommendation looks like**: based on which metrics underperformed and on patterns in the failing records, the analyzer surfaces hypotheses for what to change next (system prompt, retrieval, task code, the dataset itself, or the evaluator).
>
> **Why this phase matters**: this closes the loop. You started by looking at production behavior; you now have an evidence-backed read on where the experiment exposes gaps and what to try next.

**Action**: Follow the **`agent-observability-experiment-analyzer`** skill in **single-exploratory** (or **single-Q&A** if the user supplied a focus question in Checkpoint 7):

```
/agent-observability-experiment-analyzer <experiment_id_from_url> [<focus question if any>] --output agent
```

Extract the `<experiment_id>` from the URL captured in Phase 5 (the trailing UUID after `/llm/experiments/`).

Reproduce the analyzer's full report verbatim.

### Final Summary

After the analyzer report, emit the closing summary — this replaces the per-phase checkpoint:

```markdown
# Agent Observability Eval Pipeline complete

**ml_app**: `<ml_app>` | **Project**: `<project_name>` | **Timeframe**: <timeframe>

| Phase | Output |
|---|---|
| 1. Classify ml_app | <N> traces classified (<F> failures) |
| 2. Root cause analysis | <K> failure modes, <M> root causes |
| 3. Bootstrap evaluators | <J> evaluators (`<offline-evaluators | online-evaluators | data-only>`) → `<path>` (or "<N> drafts published to Datadog") |
| 4. Create + publish dataset | <K> records → `<dataset_path>`, published as `<dataset_name>` (v1) in project `<project_name>` |
| 5. Generate + run experiment | `<experiment_file_path>` → <experiment.url> (<N> records, <duration>s) |
| 6. Analyze experiment | <2–3 bullet headline findings from the analyzer> |

## What you learned

- The five core entities you touched: **ml_app**, **failure mode**, **evaluator**, **dataset**, **experiment**. Each has a dedicated docs page — see Datadog Documentation below.
- The loop you can now repeat: **edit dataset → re-run experiment → compare in the UI**. Pull-by-name + auto-versioning makes the loop cheap.
- The reusable artifacts you produced: an evaluator suite (Phase 3), a published dataset (Phase 4), and an experiment script (Phase 5). All three survive beyond this pipeline run.

## Recommended next steps

1. Open the experiment in the Datadog UI: <experiment.url>
2. Replace the placeholder evaluators in `<experiment_file_path>` with the ones bootstrapped in Phase 3 (swap the function refs / `RemoteEvaluator` names).
3. Re-run the experiment after every meaningful change to your task code. Datadog will keep the run history under the same project.
4. If you published draft evaluators via `--online-evaluators`, review and enable them in the UI (Agent Observability → Evaluations).

## Datadog Documentation

- Agent Observability overview: <https://docs.datadoghq.com/llm_observability/>
- Datasets: <https://docs.datadoghq.com/llm_observability/experiments/datasets_and_experiments/>
- Experiments: <https://docs.datadoghq.com/llm_observability/experiments/>
- Evaluations: <https://docs.datadoghq.com/llm_observability/evaluations/>
- Python SDK reference: <https://docs.datadoghq.com/llm_observability/instrumentation/sdk/>
```

---

## Stop-after handling

`--stop-after <phase>` lets the user exit cleanly before the full six-phase pipeline completes. Valid values map to the phase numbers:

| Value | Stop after | Use case |
|---|---|---|
| `classify` | Phase 1 | "I just want to see what's going on in my ml_app." |
| `rca` | Phase 2 | "I want to understand failure modes — I'll write evaluators myself." |
| `eval-bootstrap` | Phase 3 | **Matches the classic `agent-observability-eval-pipeline` behavior.** Use for "I want evaluators, not experiments." |
| `dataset` | Phase 4 | "I want the dataset created and published, but I'll generate / run / analyze the experiment myself." |
| `experiment` | Phase 5 | "Generate and run the experiment; I'll analyze the results myself." |
| `analyze` | Phase 6 (default) | Full pipeline. |

When the current phase matches the stop value, **replace the Checkpoint at the bottom of that phase with a Stop summary**:

```
## Pipeline stopped — `--stop-after <phase>`

Completed phases: <list 1..stop>
Skipped: <list stop+1..6 with one-line descriptions>

Artifacts produced: <list with paths / URLs>

Re-invoke with a later `--stop-after` (or no flag for the full run) when you're ready to continue. State from completed phases is idempotent — re-running them will just re-derive the same outputs.
```

This makes `--stop-after eval-bootstrap` a drop-in replacement for the old `agent-observability-eval-pipeline` behavior without losing the orchestrator's pedagogy banners.

---

## State persistence and entry/exit

The pipeline persists every phase's primary output to `<output-dir>/state/` so that subsequent invocations can resume mid-flow with `--start-at <phase>` instead of starting from Phase 1 every time.

### State file contract

After every successful phase completion (i.e. after the user types `continue` past its checkpoint, OR before the Stop summary if `--stop-after` matches), **write the phase output to `<output-dir>/state/0N-<name>.{md,json}`**. Schema is fixed per phase:

| Phase | State file | Schema |
|---|---|---|
| 0 trace-export *(only when `--trace-export` is set)* | `state/00-trace-export.json` | `{"source_path": "<export file path>", "source_format": "csv\|json", "ml_app": "<ml_app>", "row_count_total": <int>, "row_count_kept": <int>, "row_count_dropped": <int>, "drop_reasons": {...}, "records": [{"trace_id": "<id>", "input": "<...>", "output": "<...>", "expected_output": "<...>"}]}`. Written by the Precheck's `--trace-export` validation step and consumed by Phase 1 (curated classification set) and Phase 4 (skips its own sampling). `source_format` is inferred from the file extension. |
| 1 classify | `state/01-classification.md` | The full `# Session Classification Summary` block plus all per-unit compact blocks, verbatim from `agent-observability-session-classify`. Markdown. |
| 2 rca | `state/02-rca-report.md` | The full Phase 6 RCA report from `agent-observability-trace-rca`, verbatim. Markdown. |
| 3 eval-bootstrap | `state/03-evaluators.json` | `{"mode": "offline_evaluators\|online_evaluators\|data_only_dataset\|data_only_analysis", "output_path": "<path>", "evaluator_names": [...], "ml_app": "<ml_app>", "generated_at": "<ISO 8601>"}`. The actual evaluator code/JSON/dataset stays where the sub-skill wrote it. The `data_only_dataset` mode produces a `DatasetRecordRaw[]` JSON that Phase 4 then consumes directly. |
| 4 dataset | `state/04-published-dataset.json` | `{"dataset_file": "<path to local DatasetRecordRaw[] JSON>", "dataset_name": "<published name>", "project_name": "<name>", "version": <int>, "url": "<datadog url>", "record_count": <int>, "skipped_count": <int>, "pii_redactions": <int>, "tag_normalizations": <int>, "published_at": "<ISO 8601>"}` — combines what was previously two state files (`04-dataset.json` and `05-published-dataset.json`) because Phase 4 now creates and publishes in one step. |
| 5 experiment | `state/05-experiment-run.json` | `{"experiment_file": "<path>", "format": "py\|ipynb", "dataset_name": "<name>", "task_source": "<module:function>\|placeholder", "purpose": "<text>", "experiment_id": "<uuid>", "experiment_url": "<datadog url>", "records_processed": <int>, "duration_seconds": <float>, "generated_at": "<ISO 8601>", "ran_at": "<ISO 8601>"}` — combines what was previously two state files (`06-experiment.json` and `07-experiment-run.json`) because Phase 5 now generates and runs in one step. If the user halts at the in-phase review beat (5b) with `edit`, only the codegen fields are populated; re-entering with `--start-at experiment` reads the file path from here and resumes at 5c. |
| 6 analyze | `state/06-analysis.md` | The full analyzer report from `agent-observability-experiment-analyzer`. Markdown. |

`<output-dir>/state/` should be created via `mkdir -p` at the top of the Precheck (alongside the existing `<output-dir>` creation). Never write state files outside this directory.

### `--start-at` resolution

At the top of the run, after the Precheck, branch on `--start-at`:

1. If `--start-at` is not set or equals `classify`, proceed normally with Phase 1.
2. Otherwise, for every phase strictly before the start phase, **load the state file** corresponding to that phase. If an override flag was passed (`--classification-summary`, `--rca-report`, `--dataset-file`, `--dataset-name`, `--experiment-file`, `--experiment-id`, `--experiment-url`), it takes precedence over the state file.
3. If a required state file is missing AND no override flag was supplied, **fail fast** with a clear message:

   > "Can't `--start-at <phase>` — Phase <N-1>'s state file at `<output-dir>/state/0(N-1)-<name>.{md,json}` is missing and no `--<prior-phase>-override <path>` flag was supplied. Either re-run from an earlier phase or pass the override flag explicitly."

4. For each loaded state file or override, print a one-line "Loaded prior state: <phase> from <path>" in the Precheck output so the user can see what's being reused.
5. Skip directly to the start phase. The first phase to actually run prints its full banner; the earlier phases get a one-line note in the Precheck.

The Precheck output gets an extra line at the bottom when `--start-at` is in effect:

```
- Resumed from: --start-at <phase> (loaded state for phases 1..N-1)
```

### Mid-run exit at any checkpoint

Every Checkpoint accepts these inputs (case-insensitive, leading/trailing whitespace OK):

| User types | Behavior |
|---|---|
| `continue`, `c`, `go`, `yes`, `y`, `next`, or just `<Enter>` with no text | Advance to the next phase. |
| `stop`, `exit`, `done`, `quit`, `q`, `cancel` | Emit the **Stop summary** here and end the run. Same shape as if `--stop-after <current-phase>` had been set from the top. State for completed phases is preserved on disk, so the user can `--start-at <next-phase>` later. |
| `redo` (optionally followed by adjustment notes like `redo with --trace-limit 50`) | Re-run the current phase only. Do **not** re-run earlier phases — their state on disk is unchanged. Apply any adjustment notes the user appended. |
| `back` (optionally followed by a phase name) | Move backward one phase (or to the named phase) and re-run from there. Discard state for the affected phases on disk so they're regenerated. |
| anything else | Treat as adjustment / question. Reason about it in context; if the user is asking a clarifying question, answer and re-show the checkpoint prompt. If the user is requesting a phase modification, apply it and re-run the phase. |

The Phase Template's "Type 'continue' to proceed" line should be updated to:

> Type `continue` to proceed, `stop` to exit cleanly (state is saved — you can resume with `--start-at <next-phase>` later), `redo` to re-run this phase, or give me adjustments.

### Practical re-entry examples

```bash
# First-time, full run end-to-end
/agent-observability-eval-pipeline lux --project-name lux

# (user typed 'stop' at Checkpoint 4)
# Later, pick up where they left off:
/agent-observability-eval-pipeline lux --project-name lux --start-at experiment

# Already have a dataset published; want to scaffold + run an experiment around it
/agent-observability-eval-pipeline lux --project-name lux --start-at experiment --dataset-name lux_seed_v3

# Re-analyze a previous experiment without re-running it
/agent-observability-eval-pipeline lux --start-at analyze --experiment-id 8a3f9c2b-...

# Slice — just classify + RCA, leave the rest for later
/agent-observability-eval-pipeline lux --stop-after rca

# Continue from the slice above without redoing classify
/agent-observability-eval-pipeline lux --start-at eval-bootstrap --stop-after eval-bootstrap

# Run the full pipeline against a curated set from a Datadog Annotation Queue export.
# Phase 1 classifies exactly those traces, Phase 4 builds the dataset from the
# export's Input/Output fields (no re-sampling, no span-walking), and Phase 5 runs
# the experiment against the resulting dataset. Format is inferred from the file
# extension (`.csv` supported today; `.json` planned).
/agent-observability-eval-pipeline lux --project-name lux \
  --trace-export ~/Downloads/annotation-queue-export-1782834681602.csv

# Same curated set, but stop after publishing the dataset (no experiment yet)
/agent-observability-eval-pipeline lux --project-name lux \
  --trace-export ~/Downloads/annotation-queue-export.csv \
  --stop-after dataset

# Column-name aliases like `content_id` / `trace_id` / `Input` / `Output` are accepted.
# See the Precheck section's schema table for the full alias list.
```

`--start-at` and `--stop-after` compose freely. Internally, `--start-at X --stop-after Y` runs exactly phases X through Y inclusive (and the run is invalid if Y < X — error out at argument parse time).

---

## Orchestration Rules

- **Always run the precheck, even on re-invocations.** It's cheap and it catches a stale `ml_app` argument, an expired auth token, or a typo in `--project-name` before you waste a sub-skill call.
- **Always emit the precheck block.** Even though it isn't a "phase", users have learned to look for it as the first output.
- **Never auto-advance between phases.** Every checkpoint waits for explicit user input. The recognized checkpoint vocabulary is `continue` / `stop` / `redo` / `back` plus free-text adjustments — see "State persistence and entry/exit → Mid-run exit at any checkpoint" for the full table.
- **Persist phase output before showing the checkpoint.** Every phase writes its primary output to `<output-dir>/state/0N-<name>.{md,json}` *before* the checkpoint prompt is rendered. This way `stop` at any point leaves a re-enterable artifact on disk — the user can resume later with `--start-at <next-phase>` and the skill will load the prior state without re-running anything.
- **Honor `--start-at` precisely.** When `--start-at <phase>` is set, load every prior phase's state file (or its override flag if supplied), print a one-line confirmation per loaded phase in the Precheck, and begin from the named phase. If a required state file is missing and no override was passed, fail fast — do not silently re-run the missing phase.
- **Never truncate sub-skill output.** The user is here to learn what the sub-skills do; if you summarize their output, you defeat the pedagogical purpose. Reproduce verbatim. Downstream phases also depend on the full text being in context (Phase 2 detects Phase 1's classification summary; Phase 3 detects Phase 2's failure taxonomy).
- **The phase envelope is invariant.** The banner ("You are here. Phase N of 6…"), the entity block, the action label, and the checkpoint header must appear identically across every phase. The *content inside* may differ; the envelope must not. This is the determinism the skill promises.
- **Execute only at Phases 4 and 5.** No other phase runs code on the user's machine. Phase 4 runs the publish script (writes the dataset to Datadog); Phase 5 runs the generated experiment file (calls provider APIs against the dataset). All other phases are read-only or write generated files to `--output-dir`. If a sub-skill output suggests the user should run something themselves, hand it off — don't quietly execute it.
- **Phase 5 has a mandatory in-phase review beat.** Between sub-step 5a (codegen) and 5c (execute), pause with the in-phase review prompt. Wait for the user to type `run`, `edit`, or `stop`. Never auto-run after codegen.
- **One backend for the whole run.** Detected at startup, propagated to all sub-skill calls. Do not re-detect mid-run.
- **`--project-name` is sticky.** Whatever the user picked at Precheck flows unchanged into Phases 4 and 5 and into the final summary. If the user changes their mind at Checkpoint 4, re-run Phase 4 (and only Phase 4) with the new name — do NOT silently rewrite earlier outputs.
- **Phase re-entry**: if the user types something like "redo phase 4 with --trace-limit 30", re-run that phase only (and clearly say so — "Re-running Phase 4 with the new trace limit. Phases 1–3 outputs are unchanged."). After it completes, fall through to Phase 5 just like a fresh run would.

---

## What this skill does NOT do

This list exists so reviewers can spot scope creep:

- **Does not instrument your app.** Audience assumption: the user already has `ml_app` traces flowing into Datadog. If the precheck finds zero traces, the skill stops and points the user at the instrumentation docs — it does not attempt to bootstrap instrumentation.
- **Does not push code or commit anything.** All generated files land in `<output-dir>`; the user owns version control.
- **Does not run any phase's code without an explicit checkpoint or review confirmation.** Phase 4 advances from the dataset-create sub-step to the publish sub-step automatically (no user-editable surface between them), but Phase 5 pauses at the in-phase review beat between codegen and run — the user types `run` to proceed.
- **Does not deeply modify your app.** Phase 5's experiment file *imports* your task function; it does not refactor it. If you want prompt / model variants without editing your app, inline the call inside `task_fn` in the generated file.
- **Does not auto-create `.env` files.** Credential files are discovered, not generated — secrets-on-disk decisions belong to the user.

---

## Tool Reference

This skill itself does almost no direct tool calls — the only direct calls are:

1. The **precheck** `search_llmobs_spans` (to confirm the ml_app has traces).
2. `Bash` for **Phase 4** (running the publish script) and **Phase 5** (running the generated experiment).
3. **No** Write for Phase 4's publish — the publish helper ships at `scripts/publish_dataset.py` alongside this SKILL.md; the orchestrator invokes it by path with CLI args. The skill does not generate the script on the fly.

Everything else routes through sub-skills, which carry their own MCP-to-pup mappings:

| Sub-skill | When invoked | Where its tool reference lives |
|---|---|---|
| `agent-observability-session-classify` | Phase 1 | `agent-observability/agent-observability-session-classify/SKILL.md` (Tool Reference appendix) |
| `agent-observability-trace-rca` | Phase 2 | `agent-observability/agent-observability-trace-rca/SKILL.md` (Tool Reference appendix) |
| `agent-observability-eval-bootstrap` (offline-evaluators / online-evaluators / data-only) | Phase 3 | `agent-observability/agent-observability-eval-bootstrap/SKILL.md` (Tool Reference appendix) |
| `agent-observability-eval-bootstrap` (`--emit-dataset` mode) | Phase 4 (sub-step 4a) | `agent-observability/agent-observability-eval-bootstrap/SKILL.md` (Phase 3D + Tool Reference appendix) |
| `agent-observability-experiment-py-bootstrap` | Phase 5 (sub-step 5a) | `agent-observability/agent-observability-experiment-py-bootstrap/SKILL.md` |
| `agent-observability-experiment-analyzer` | Phase 6 | `agent-observability/agent-observability-experiment-analyzer/SKILL.md` (Tool Reference appendix) |

### Precheck `search_llmobs_spans` ↔ pup

| MCP Tool | pup Command |
|---|---|
| `search_llmobs_spans(query="@ml_app:\"<ml_app>\"", root_spans_only=true, limit=1, from="<timeframe>")` | `pup llm-obs spans search --query "@ml_app:\"<ml_app>\"" --root-spans-only --limit 1 --from <stripped-timeframe> --summary` (strip the `now-` prefix from timeframe per the pup invocation rules above). |

- **MCP result parsing safety**: Before writing any script (Python, jq, etc.) that iterates over or accesses fields in an MCP tool result, inspect the raw structure first — check `type(result)`, top-level keys, and whether the payload is nested inside a content block (e.g. `[{'type': 'text', 'text': '<json>'}]`). Extract and `json.loads()` the inner payload if needed. Never assume MCP results are bare dicts or lists.
