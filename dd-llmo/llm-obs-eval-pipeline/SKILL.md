---
name: llm-obs-eval-pipeline
description: End-to-end LLM Observability pipeline for an instrumented ml_app — classify production traces, root-cause failures, bootstrap evaluators, then (optionally) sample a dataset, publish it, generate an experiment, run it, and analyze results. Eight narrated phases with a standardized banner and a "continue" checkpoint between each. Pure orchestration over the dd-llmo sub-skills (`llm-obs-session-classify`, `llm-obs-trace-rca`, `llm-obs-eval-bootstrap`, `llm-obs-experiment-py-bootstrap`, `llm-obs-experiment-analyzer`). Use when user says "run the eval pipeline", "go from traces to evals", "bootstrap evals end to end", "classify then RCA then bootstrap", "build an eval set from scratch", "onboard me to datasets and experiments", "walk me through experiments", "I have an ml_app, now what", "LLM Obs onboarding", "guided experiment setup", "from traces to experiments", or wants a deterministic, narrated tour from production data through evaluators, datasets, and experiments. Stop early with `--stop-after <phase>` to short-circuit at evaluators / dataset / experiment.
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

**Sub-skill backend propagation**: The backend detected at startup applies to all sub-skills invoked across the eight phases. Do not re-detect per phase. Announce once at startup:
- MCP mode: "(Running in MCP mode — all features available.)"
- pup mode: "(Running in pup mode — pup commands used throughout. All features available.)"

**Invocation ID:** At the very start of each invocation, before any MCP tool call, generate an 8-character hex invocation ID (e.g., `3a9f1c2b`). Keep it constant for the entire invocation.

**Intent tagging:** On every MCP tool call, prefix `telemetry.intent` with `skill:llm-obs-eval-pipeline[<inv_id>] — ` followed by a description of why the tool is being called. On the **first MCP tool call only**, use `skill:llm-obs-eval-pipeline:start[<inv_id>] — ` instead (note the `:start` suffix). Example first call: `skill:llm-obs-eval-pipeline:start[3a9f1c2b] — Precheck: verify ml_app has traces in the last 7 days`

---

# LLM Obs Eval Pipeline — Classify → RCA → Eval Bootstrap → Dataset → Experiment → Analyze

A deterministic, eight-phase guided pipeline for an already-instrumented `ml_app` owner. Each phase has the same envelope — a banner that names the entity being produced, an explanation of its purpose, the action (a sub-skill call or a small executable step), and a checkpoint. **You always know where you are.**

```
[Precheck] verify ml_app, project, backend, credentials, output dir
   ↓
[Phase 1: Classify ml_app traces]      entity: ml_app, trace, span
   ↓
[Phase 2: Root cause analysis]         entity: failure mode, root cause
   ↓
[Phase 3: Bootstrap evaluators]        entity: evaluator, LLM judge
   ↓                                   (stop here with --stop-after eval-bootstrap
   ↓                                    for the classic eval-pipeline behavior)
[Phase 4: Create dataset from traces]  entity: dataset record
   ↓
[Phase 5: Publish dataset]             entity: published dataset, dataset_name
   ↓
[Phase 6: Generate experiment code]    entity: experiment, task, evaluator
   ↓
[Phase 7: Run experiment]              entity: experiment run, experiment.url
   ↓
[Phase 8: Analyze experiment]          entity: metric, comparison, recommendation
```

This skill is **pure orchestration plus pedagogy** — no new analytical logic. The work happens inside the sub-skills (`llm-obs-session-classify`, `llm-obs-trace-rca`, `llm-obs-eval-bootstrap`, `llm-obs-experiment-py-bootstrap`, `llm-obs-experiment-analyzer`). What this skill adds is the deterministic envelope: every phase has the same shape, the same checkpoint contract, and the same entity-explanation banner — so the user gets a consistent, narrated experience regardless of how they phrased the original request.

## Usage

```
/llm-obs-eval-pipeline <ml_app> [--project-name <name>] [--timeframe <window>] [--trace-limit <N>]
                                [--format py|ipynb] [--evaluator-style function|class|remote]
                                [--data-only | --publish]
                                [--stop-after classify|rca|eval-bootstrap|dataset|publish|experiment|run|analyze]
                                [--app-root <path>] [--env-file <path>] [--output-dir <dir>]
                                [--backend pup]
```

Arguments: $ARGUMENTS

### Inputs

| Input | Required | Default | Description |
|-------|----------|---------|-------------|
| `ml_app` | Yes | — | The instrumented LLM app to onboard / evaluate against. The precheck verifies it has recent traces. |
| `--project-name` | No | derived from `pyproject.toml` / `setup.cfg` / `setup.py` / `package.json` / cwd (same order as `llm-obs-experiment-py-bootstrap`); falls back to `experiment-sdk-default` | The Datadog **project** the pipeline writes datasets and experiments into. The SDK lazily creates the project on first use via `LLMObs.enable(project_name=...)`. Surface this in the Precheck so the user can confirm before anything is created. |
| `--timeframe` | No | `now-7d` | Lookback window for Phase 1 classification and Phase 4 dataset sampling. |
| `--trace-limit` | No | `20` | Sampling cap for Phase 4. Phase 1 internally uses `min(20, --trace-limit)` for the classification sample. |
| `--format` | No | `py` | Passed to `llm-obs-experiment-py-bootstrap` in Phase 6: `py` (script) or `ipynb` (Jupyter notebook). |
| `--evaluator-style` | No | `function` | Passed to `llm-obs-eval-bootstrap` (Phase 3) and `llm-obs-experiment-py-bootstrap` (Phase 6): `function`, `class`, or `remote`. |
| `--data-only` | No | off | Phase 3 pass-through to `llm-obs-eval-bootstrap`: emit JSON spec instead of Python SDK code. |
| `--publish` | No | off | Phase 3 pass-through to `llm-obs-eval-bootstrap`: publish online LLM-judge evaluators to Datadog. |
| `--stop-after <phase>` | No | `analyze` (run everything) | Stop after the named phase completes. `classify` = Phase 1 only. `rca` = through Phase 2. `eval-bootstrap` = through Phase 3 (matches the classic eval-pipeline). `dataset` = through Phase 4. `publish` = through Phase 5. `experiment` = through Phase 6 (file generated, not run). `run` = through Phase 7 (skip analyzer). `analyze` = all eight phases (default). |
| `--app-root` | No | resolved from cwd / `pyproject.toml` etc. | Restricts `llm-obs-experiment-py-bootstrap`'s task-function introspection to this directory tree. |
| `--env-file` | No | none (auto-discovery walks standard locations) | Explicit `.env` path for credential loading. Surfaced in the Precheck and baked into the generated experiment as `ENV_FILE_OVERRIDE`. |
| `--output-dir` | No | `./experiments` | Where the dataset JSON, publish script, and generated experiment file are written. |
| `--backend` | No | auto-detect | `pup` forces pup mode regardless of MCP availability. |

If `ml_app` is not provided, ask the user before proceeding. If `--data-only` and `--publish` are both set, error out — they're mutually exclusive (per `llm-obs-eval-bootstrap`'s rules).

---

## Precheck

Before Phase 1, run a single short verification pass — do **not** announce a "Phase" banner for this; it's plumbing. Output a one-block precheck summary, then move directly to Phase 1.

1. **Backend** — already detected at the top of this skill. Note the chosen backend in the precheck output so the user can confirm.

2. **ml_app has recent traces** — call `search_llmobs_spans(query="@ml_app:\"<ml_app>\"", root_spans_only=true, limit=1, from="<timeframe>")` (MCP) or the pup equivalent. If the result is empty, stop and tell the user the precheck failed — there is nothing to evaluate against — and suggest widening `--timeframe` or confirming the ml_app name.

3. **Resolve `project_name`** — if `--project-name <name>` was supplied, use it verbatim. Otherwise derive using the same resolution order as `llm-obs-experiment-py-bootstrap` (Workflow step 1): `pyproject.toml` → `setup.cfg` → `setup.py` → `package.json` → cwd basename (slugified). Final value is `experiment-<service-name>`; fall back to `experiment-sdk-default` if nothing resolves and emit a warning telling the user to set `--project-name` explicitly.

   **Project creation semantics**: the project is created lazily by the Datadog SDK the first time `LLMObs.enable(project_name=...)` is called against the org (in Phase 5's publish script and Phase 6's generated experiment). The user does not need to pre-create anything in the UI. Surface the chosen project name in the Precheck output so the user can override before Phase 5 if it isn't what they wanted.

4. **Ensure `--output-dir` exists** — `mkdir -p <output-dir>` via Bash. Cheap.

5. **Resolve credentials.** Walk the discovery order below to find Datadog credentials before Phase 5 needs them — failing late at the publish step is bad UX. Read-only at this stage: do NOT write any new files. Do NOT print secret values to the user; only report which file was loaded and which keys were resolved.

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

Output the precheck summary, then start Phase 1:

```
## Precheck

- Backend: <MCP | pup>
- ml_app `<ml_app>` has traces in <timeframe>: yes (<sample_count> root spans found)
- Project name: `<project_name>` (created lazily on first LLMObs.enable() call in Phase 5)
- Output dir: `<output-dir>` (created)
- Credentials: <one of:
    "loaded from shell env (DD_API_KEY, DD_APPLICATION_KEY, DD_SITE)"
  | "loaded from <relative path to .env file> (DD_API_KEY, DD_APPLICATION_KEY[, DD_SITE])"
  | "shell env + <relative path>: keys resolved from both (shell overrode file for <list>)"
  >
- Stop-after: <phase from --stop-after, default `analyze`>

Starting Phase 1 of 8.
```

The exact list of keys in the credentials parenthetical reflects what was actually discovered (so the user can verify nothing surprising was loaded). Never print the values.

---

## Phase Template

Every phase below uses this exact template. Do not deviate — the deterministic envelope is what makes the pipeline experience consistent across invocations.

```
## Phase N of 8: <Title>

**You are here.** Phase N of 8 — <one-line position summary>.

**What this phase produces**: <Entity name>
**What a <entity> is**: <2-3 sentence definition tailored to this phase>
**Why it matters**: <1 sentence on why the user needs this>

→ Action: <invoke <sub-skill> | execute <script>>

<full sub-skill output OR execution log reproduced here — do NOT summarize or truncate>

---

### Checkpoint <N>

<concise summary of what was produced, where it lives (path / Datadog URL), and any caveats>

Before I continue to Phase N+1 (<next title>):
- <2–3 phase-specific review prompts the user can answer>

Type "continue" to proceed, or give me adjustments.
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

**Trace pool preview (MANDATORY).** Before invoking the sub-skill, build and surface a Datadog Traces UI link that opens the **exact set of root spans Phase 4 will sample from**. This lets the user eyeball the pool, spot outliers, or adjust `--timeframe` before any classification work runs. The link must match the filter that `llm-obs-eval-bootstrap --emit-dataset` uses in Phase 4 (see `dd-llmo/llm-obs-eval-bootstrap/SKILL.md` → Phase 3D → Sampling): `@ml_app:"<ml_app>" @status:ok`, root spans only.

URL construction rules:

1. **Host**: prepend `app.` to `DD_SITE`. If `DD_SITE` is unset, default to `app.datadoghq.com`.
   - `datadoghq.com` → `app.datadoghq.com`
   - `datadoghq.eu` → `app.datadoghq.eu`
   - `us3.datadoghq.com` → `app.us3.datadoghq.com`
   - `us5.datadoghq.com` → `app.us5.datadoghq.com`
   - `ap1.datadoghq.com` → `app.ap1.datadoghq.com`
   - `ap2.datadoghq.com` → `app.ap2.datadoghq.com`
   - `datad0g.com` → `app.datad0g.com` (staging)
2. **Path**: `/llm/traces` (the LLM Observability Traces explorer).
3. **Query string**: URL-encode `@ml_app:"<ml_app>" @status:ok @parent_id:undefined` and bind to `query=`.
4. **Time window**: convert `--timeframe` to absolute epoch milliseconds (`now - <duration in ms>` for the start, `now` for the end) and bind to `start=` / `end=`.
5. **Optional**: append `&paused=true` so the UI does not live-stream the result on open.

Surface the link immediately before the Action block:

```
**Trace pool being analyzed** ({timeframe}, filter `@ml_app:"<ml_app>" @status:ok`, root spans only):

  <full URL>

Phase 1 will classify the first {min(20, trace-limit)} of these for orientation. Phase 4 will sample up to {trace-limit} from the same pool for the dataset. Click through if you want to see the pool before either runs, or adjust `--timeframe` and re-invoke if the window looks off.
```

**Action**: Follow the **`llm-obs-session-classify`** skill in **ml_app mode**, using:
- `ml_app` = the provided ml_app
- `timeframe` = the provided timeframe
- `sample_limit` = `min(20, trace-limit)` — keep this fast; Phase 4 will do the bigger sample

Run the complete ml_app mode workflow as defined in that skill (Steps M1 through M3). **Output the full classification output** (all compact per-unit blocks plus the final `# Session Classification Summary`) — do not summarize or truncate. Downstream Phase 2's RCA depends on the full text being in context.

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

Type "continue" to proceed, or give me adjustments.
```

Wait for explicit user confirmation. If the user excludes specific traces, mark them as "excluded by user" — drop them from Phase 2's failure bucket and from Phase 4's sampling. Do NOT re-classify.

---

## Phase 2: Root cause analysis

**Entity**: `failure mode`, `root cause`.

**Pedagogy banner**:

> **What a failure mode is**: a recurring pattern in *how* your app fails — e.g. "the model hallucinates citation URLs", "the agent forgets state across tool calls", "the retrieval returns irrelevant chunks". Each failure mode has one or more *root causes* (system prompt deficiency, tool gap, retrieval miss, etc.).
>
> **Why this phase matters**: an evaluator that scores generic "is this response good?" misses the specific things going wrong in your app. RCA lets Phase 3 propose evaluators that target your *actual* failure modes — sharper signal, fewer false alarms.

**Action**: Follow the **`llm-obs-trace-rca`** skill.

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

Type "continue" to proceed, or give me adjustments.
```

Wait for explicit user confirmation. If the user adjusts the taxonomy, incorporate the changes before continuing.

---

## Phase 3: Bootstrap evaluators

**Entity**: `evaluator`, `LLM judge`.

**Pedagogy banner**:

> **What an evaluator is**: a function that grades one record's output. Returns `bool` / `float` / a structured `EvaluatorResult`. Two flavors: **code evaluators** (deterministic checks — JSON validity, regex match, length, custom Python) and **LLM-as-judge evaluators** (a model graded against a rubric — e.g. "is this response grounded in the retrieved documents?").
>
> **What "online" vs "offline" means**: an offline evaluator runs inside an experiment against a dataset. An online evaluator runs on production spans as they're emitted. Pass `--publish` to bootstrap online LLM-judge evaluators; default is offline `.py` code suitable for experiments.
>
> **Why this phase matters**: evaluators are the contract between "this output looks fine" and "this output meets our quality bar." The bootstrapped suite is grounded in the failure taxonomy from Phase 2 — sharper than generic evaluators you'd otherwise hand-write.

**Action**: Follow the **`llm-obs-eval-bootstrap`** skill.

The RCA report from Phase 2 is in context. The skill detects the `## Failure Taxonomy` heading automatically and enters its "from RCA" path in Phase 0.

Pass through any flags:
- `--data-only` → emit a JSON spec instead of Python SDK code
- `--publish` → publish online LLM-judge evaluators directly to Datadog
- `--evaluator-style` → pass through unchanged

**The llm-obs-eval-bootstrap skill has its own mandatory proposal checkpoint** (the evaluator suite proposal before code generation). Honor it — do not skip or auto-confirm it.

### Checkpoint 3

```
## Phase 3 complete — evaluator suite ready

- Output: `<path to .py / .json / "published as drafts to Datadog">`
- Evaluators emitted: <list of names>
- Coverage: <one-liner: which failure-mode categories are now covered>

Next up — Phase 4 will sample production traces into a dataset you can run experiments against (using these evaluators or the placeholders the experiment template ships with).

If you only wanted evaluators (the classic eval-pipeline flow), this is the natural stopping point: re-invoke with `--stop-after eval-bootstrap` to formalize that as the exit.

Before I continue:
- Do the generated evaluators look right?
- Any to drop or rename before they're referenced in the experiment?

Type "continue" to proceed, or give me adjustments.
```

Wait for explicit user confirmation. If `--stop-after eval-bootstrap` is set, this is where the pipeline ends — emit the Stop summary instead of the Checkpoint and exit.

---

## Phase 4: Create dataset from prod traces

**Entity**: `dataset`, `dataset record`.

**Pedagogy banner**:

> **What a dataset is**: a named collection of records that an experiment runs against. Each record has `input_data` (what the task receives) and optionally `expected_output` (what you expect back). Datasets live in Datadog under your project and have a version — every time you push changes, a new version is created.
>
> **What a record is**: a single `(input_data, expected_output)` pair, optionally with `metadata` and `tags`. One record = one experiment row.
>
> **Why this phase matters**: experiments need a stable input set. Sampling production traces gives you a realistic starting dataset — the inputs your app actually sees — without making you write fixtures by hand.

**Action**: Follow the **`llm-obs-eval-bootstrap`** skill in **`--emit-dataset` mode**:

```
/eval-bootstrap <ml_app> --timeframe <timeframe> --trace-limit <trace-limit> --emit-dataset <output-dir>/dataset_<ml_app>_<YYYYMMDD>.json
```

This mode samples root spans, extracts `(input_data, expected_output)` pairs from each, applies a PII scrub, and writes a `DatasetRecordRaw[]` JSON file. **It does not propose or generate evaluators in this mode** — the dataset is the sole artifact. See `dd-llmo/llm-obs-eval-bootstrap/SKILL.md` → Phase 3D for the full spec.

If the user excluded specific traces in Checkpoint 1, pass that exclusion list along (sub-skill drops them during sampling — do NOT re-classify).

Reproduce the sub-skill's `## Generated Dataset` summary verbatim.

### Checkpoint 4

```
## Phase 4 complete — local dataset file ready

- File: `<path>`
- Records: <N> (skipped: <M> with no usable output)
- PII redactions: <P>
- Tag normalizations: <T>
- Caveat: `expected_output` is the **current production behavior baseline**, not ground truth. Spot-check a few records before publishing.

Next up — Phase 5 will publish this dataset to Datadog under project `<project_name>` so an experiment can pull it by name.

Before I continue:
- Want to open the file and edit any records first? (recommended — spot-check at least 3–5)
- Happy with the proposed dataset name `<ml_app>_seed_<YYYYMMDD>`, or pick another?
- Any records you want me to drop?

Type "continue" to proceed, or give me adjustments.
```

Wait for confirmation. The proposed `dataset_name` defaults to `<ml_app>_seed_<YYYYMMDD>` but the user can override — carry the final chosen name into Phase 5.

---

## Phase 5: Publish dataset

**Entity**: published dataset (Datadog-side), `dataset_name`, version.

**Pedagogy banner**:

> **What "publishing" means**: pushing the local records to Datadog so the dataset becomes addressable by name across all subsequent experiments. After publish, anyone in your org (with access) can pull it with `LLMObs.pull_dataset(dataset_name="…")` — including the experiment code we generate in Phase 6.
>
> **What a dataset version is**: every push that changes records produces a new version. You can pin an experiment to a specific version (`pull_dataset(version=N)`) so a refactor of the dataset doesn't silently change which inputs your experiment runs on.
>
> **Why this phase matters**: a published, named dataset is the contract between your dataset curation work (which lives in code/JSON) and the experiments that consume it. Without this phase, the experiment in Phase 6 has no input.

**This is one of the two phases in this pipeline where executable code is run.** The user is in "publish" mode and that's the clear, expected signal.

**Action**: Invoke the pre-shipped publish helper at `<this-skill-dir>/scripts/publish_dataset.py` via Bash. **Do not** inline the script content into this SKILL.md or re-write it from scratch — the helper is the source of truth for the publish flow (credential discovery, tag normalization, project creation, error handling). It accepts CLI args, so no placeholder substitution is needed.

Invocation:

```bash
python <skill-dir>/scripts/publish_dataset.py \
  --records <absolute path to dataset JSON from Phase 4> \
  --dataset-name <chosen dataset_name from Checkpoint 4> \
  --project-name <resolved project_name from Precheck> \
  [--env-file <path>]   # repeatable; takes precedence over auto-discovery
```

`<skill-dir>` resolves to wherever the skill is installed (e.g., `~/.claude/skills/llm-obs-eval-pipeline/`). The script bundles two sibling modules — `scripts/load_env.py` (the env-file discovery walker, identical to the one the generated experiment file ships with) and inline tag normalization — and prints either:

- `Loaded credentials from: <file paths>` (if any `.env` files contributed values), then
- `OK dataset_name=<name> record_count=<N> url=<url>` (on success), or
- `ERROR: <message>` on stderr with a non-zero exit (auth, missing keys, ddtrace import failure, etc.).

**Notes for the orchestrator:**
- Before invoking, do an import-availability precheck: `python -c "import ddtrace.llmobs"` via Bash. If it fails, stop and tell the user:
  > "`ddtrace` is not installed in the active Python environment. Run `pip install 'ddtrace>=4.7'` and re-invoke this skill (re-run from the top — Phases 1–4 outputs are idempotent)."
- The script's discovery walk mirrors the Precheck. If the Precheck already loaded credentials, the script's `_load_env_files()` will just be a confirmation no-op (the keys are already in `os.environ`).
- `LLMObs.enable(project_name=...)` inside the script is where the `--project-name` from the Precheck actually materializes — the Datadog project is created lazily on first call.
- If the script prints a `WARNING:` line about tag normalization, surface it in Checkpoint 5 so the user knows their upstream dataset had malformed tags.
- If the script prints `Loaded credentials from: ...`, include that file path in Checkpoint 5.
- If the script exits non-zero with an auth error (401/403), surface the stderr and stop — do not retry. Tell the user the most likely cause is a stale `.env` value, and that `export DD_API_KEY=... DD_APPLICATION_KEY=...` in their shell takes precedence and can be used to override.
- On success, capture the printed `dataset_name` and `url` and carry them into Phase 6.

### Checkpoint 5

```
## Phase 5 complete — dataset published

- Dataset name: `<dataset_name>`
- Project: `<project_name>` <(created if it did not exist)>
- Records published: <N>
- Datadog UI: <url or "open LLM Observability → Datasets to confirm">

Next up — Phase 6 will generate a Python experiment script that pulls this dataset and runs your task code (auto-discovered) against it.

Before I continue:
- Confirm you can see the dataset in the Datadog UI (LLM Observability → Datasets → search `<dataset_name>`)?
- Any second thoughts on the dataset records (we can re-emit before generating the experiment)?

Type "continue" to proceed, or give me adjustments.
```

Wait for confirmation.

---

## Phase 6: Generate experiment code

**Entity**: `experiment`, `task` function, `evaluator`.

**Pedagogy banner**:

> **What an experiment is**: a programmatic harness that, for each record in a dataset, calls a `task` function (your code under test — typically an LLM call), then runs one or more `evaluators` against the task's output. Datadog collects all those results into a single experiment view you can compare across runs.
>
> **What a task function is**: a Python callable that receives one record's `input_data` and a `config` dict, and returns whatever your app would have returned for that input. **The sub-skill introspects your project to find this function automatically** — no `# TODO(user)` placeholder unless nothing was found.
>
> **What an evaluator is**: covered in Phase 3 above. The generated experiment ships placeholder evaluators by default; if you ran Phase 3 with `--evaluator-style remote`, you can wire those names in here.
>
> **Why this phase matters**: this is the artifact you'll actually run. Everything before this phase was prep work.

**Action**: Follow the **`llm-obs-experiment-py-bootstrap`** skill:

```
/llm-obs-experiment-py-bootstrap \
  --dataset-name <dataset_name> \
  --project-name <project_name> \
  --format <format> \
  --evaluator-style <evaluator-style> \
  --app-root <app-root> \
  --env-file <env-file if set> \
  --output <output-dir>/experiment_<ml_app>_<YYYYMMDD>.<py|ipynb>
```

Reproduce the sub-skill's full output (including the generated SDK calls summary, the "Task function source" block, the credential discovery section, and the Next steps block) verbatim. Do not summarize. The "Task function source" block tells the user which `module:function` was auto-wired — that's load-bearing for Checkpoint 6.

### Checkpoint 6

```
## Phase 6 complete — experiment file ready to run

- File: `<path>`
- Format: <py | ipynb>
- Wired to dataset: `<dataset_name>` (pulled at runtime via LLMObs.pull_dataset)
- Task function source: <line lifted from the sub-skill — names the discovered module:function, or notes the placeholder fallback>
- Evaluators: <list the 2–3 evaluator names from the sub-skill output>

Next up — Phase 7 will run this file end-to-end against the published dataset.

**Before continuing — open the file and look at three things**:
1. The wired `task_fn` (section 4 of the generated file). The sub-skill introspected your app and imported the most likely entry point — **confirm it picked the right function**. If it picked a sibling helper or a deprecated path, edit the import in section 4 to point at the function you actually want to evaluate. If the sub-skill fell back to a placeholder (no LLM call site found in `<app-root>`), this is the phase where you replace it with a real call.
2. The placeholder evaluators — these are starting points. You can leave them for the first run, or refine. If Phase 3 produced online evaluators via `--publish`, swap one of the placeholders for a `RemoteEvaluator(eval_name="...")` referencing your judge.
3. The `experiment.run(jobs=<N>)` parallelism — defaults to 10; lower it if you're worried about rate limits.

Then confirm:
- Have you set `DD_API_KEY`, `DD_APPLICATION_KEY`, `DD_SITE` (if non-default), and the provider key your task needs (the generated file's section 1 asserts only the right one — check what it expects)?
- Ready to run?

Type "continue" to proceed, or give me adjustments.
```

Wait for confirmation.

---

## Phase 7: Run experiment

**Entity**: experiment run, `experiment.url`, metric stream.

**Pedagogy banner**:

> **What "running the experiment" means**: iterating over every record in the dataset, calling your `task` function, then calling each evaluator on the result, and streaming the scores to Datadog. The SDK creates the experiment in Datadog the first time it runs and updates the same experiment record on re-runs (if you keep the same `name=`).
>
> **What `experiment.url` is**: the deep link to the run in the Datadog Experiments UI. Phase 8 uses this to analyze results.
>
> **Why this phase matters**: until you actually run the experiment, the dataset and the code are just plumbing. The run is what produces the first measurements.

**This is the second of the two phases in this pipeline where executable code is run.** Clearly signal it.

**Action**: Execute the generated experiment file.

- For `--format py`: `python <generated_path>` via Bash. Stream output to the user.
- For `--format ipynb`: tell the user the generated file is a notebook and ask whether to (a) execute it via `jupyter nbconvert --to notebook --execute --inplace <path>` (requires `jupyter` installed), or (b) hand off — the user opens it in JupyterLab and runs cells manually. Default to (a) if `jupyter` is on PATH; otherwise (b).
- Capture the printed `experiment.url` from the run's stdout — the generated file always ends with `print(experiment.url)`. If you can't find it, parse stdout for the substring `https://app.datadoghq.com/llm/experiments/` (account for non-default `DD_SITE` hosts).
- If the run fails: do NOT retry automatically. Surface the full traceback, identify the failure category (auth, missing dep, dataset not found, task function raised, evaluator raised) in a one-line diagnosis, and ask the user whether to fix and re-run.

### Checkpoint 7

```
## Phase 7 complete — experiment run published

- Experiment URL: <experiment.url>
- Records processed: <N>
- Duration: <wall-clock seconds>
- Evaluator score summary (from stdout, if printed): <table or "open the UI">

Next up — Phase 8 will pull the experiment results back from Datadog and produce an analysis report (struggling metrics, qualitative examples, root-cause hypotheses).

Before I continue:
- Take a look at the experiment in the UI (link above). Do the per-record scores roughly match your expectations?
- Any specific question you want Phase 8 to focus on? (Optional — leaving it open runs an exploratory analysis.)

Type "continue" to proceed, or give me adjustments.
```

Wait for confirmation. If the user provides a focus question, carry it to Phase 8 as the analyzer's `question` argument.

---

## Phase 8: Analyze experiment

**Entity**: experiment `metric`, segment comparison, recommendation.

**Pedagogy banner**:

> **What an experiment metric is**: a per-record score produced by one of your evaluators, aggregated into a pass-rate / score-distribution across the dataset. The Datadog Experiments UI shows these as columns and lets you slice by `metadata` fields you attached to each record.
>
> **What a recommendation looks like**: based on which metrics underperformed and on patterns in the failing records, the analyzer surfaces hypotheses for what to change next (system prompt, retrieval, task code, the dataset itself, or the evaluator).
>
> **Why this phase matters**: this closes the loop. You started by looking at production behavior; you now have an evidence-backed read on where the experiment exposes gaps and what to try next.

**Action**: Follow the **`llm-obs-experiment-analyzer`** skill in **single-exploratory** (or **single-Q&A** if the user supplied a focus question in Checkpoint 7):

```
/llm-obs-experiment-analyzer <experiment_id_from_url> [<focus question if any>] --output agent
```

Extract the `<experiment_id>` from the URL captured in Phase 7 (the trailing UUID after `/llm/experiments/`).

Reproduce the analyzer's full report verbatim.

### Final Summary

After the analyzer report, emit the closing summary — this replaces the per-phase checkpoint:

```markdown
# LLM Obs Eval Pipeline complete

**ml_app**: `<ml_app>` | **Project**: `<project_name>` | **Timeframe**: <timeframe>

| Phase | Output |
|---|---|
| 1. Classify ml_app | <N> traces classified (<F> failures) |
| 2. Root cause analysis | <K> failure modes, <M> root causes |
| 3. Bootstrap evaluators | <J> evaluators → `<path>` (or "<N> drafts published to Datadog") |
| 4. Create dataset | <K> records → `<dataset_path>` |
| 5. Publish dataset | `<dataset_name>` (v1) in project `<project_name>` |
| 6. Generate experiment code | `<experiment_file_path>` |
| 7. Run experiment | <experiment.url> |
| 8. Analyze experiment | <2–3 bullet headline findings from the analyzer> |

## What you learned

- The five core entities you touched: **ml_app**, **failure mode**, **evaluator**, **dataset**, **experiment**. Each has a dedicated docs page — see Datadog Documentation below.
- The loop you can now repeat: **edit dataset → re-run experiment → compare in the UI**. Pull-by-name + auto-versioning makes the loop cheap.
- The reusable artifacts you produced: an evaluator suite (Phase 3), a published dataset (Phase 5), and an experiment script (Phase 6). All three survive beyond this pipeline run.

## Recommended next steps

1. Open the experiment in the Datadog UI: <experiment.url>
2. Replace the placeholder evaluators in `<experiment_file_path>` with the ones bootstrapped in Phase 3 (swap the function refs / `RemoteEvaluator` names).
3. Re-run the experiment after every meaningful change to your task code. Datadog will keep the run history under the same project.
4. If you published draft evaluators via `--publish`, review and enable them in the UI (LLM Observability → Evaluations).

## Datadog Documentation

- LLM Observability overview: <https://docs.datadoghq.com/llm_observability/>
- Datasets: <https://docs.datadoghq.com/llm_observability/experiments/datasets_and_experiments/>
- Experiments: <https://docs.datadoghq.com/llm_observability/experiments/>
- Evaluations: <https://docs.datadoghq.com/llm_observability/evaluations/>
- Python SDK reference: <https://docs.datadoghq.com/llm_observability/instrumentation/sdk/>
```

---

## Stop-after handling

`--stop-after <phase>` lets the user exit cleanly before the full eight-phase pipeline completes. Valid values map to the phase numbers:

| Value | Stop after | Use case |
|---|---|---|
| `classify` | Phase 1 | "I just want to see what's going on in my ml_app." |
| `rca` | Phase 2 | "I want to understand failure modes — I'll write evaluators myself." |
| `eval-bootstrap` | Phase 3 | **Matches the classic `llm-obs-eval-pipeline` behavior.** Use for "I want evaluators, not experiments." |
| `dataset` | Phase 4 | "I want a dataset JSON to inspect / edit before publishing." |
| `publish` | Phase 5 | "I want the dataset live in Datadog but I'll wire up the experiment myself." |
| `experiment` | Phase 6 | "Generate the experiment file but don't run it yet." |
| `run` | Phase 7 | "Run the experiment; I'll do the analysis manually." |
| `analyze` | Phase 8 (default) | Full pipeline. |

When the current phase matches the stop value, **replace the Checkpoint at the bottom of that phase with a Stop summary**:

```
## Pipeline stopped — `--stop-after <phase>`

Completed phases: <list 1..stop>
Skipped: <list stop+1..8 with one-line descriptions>

Artifacts produced: <list with paths / URLs>

Re-invoke with a later `--stop-after` (or no flag for the full run) when you're ready to continue. State from completed phases is idempotent — re-running them will just re-derive the same outputs.
```

This makes `--stop-after eval-bootstrap` a drop-in replacement for the old `llm-obs-eval-pipeline` behavior without losing the orchestrator's pedagogy banners.

---

## Orchestration Rules

- **Always run the precheck, even on re-invocations.** It's cheap and it catches a stale `ml_app` argument, an expired auth token, or a typo in `--project-name` before you waste a sub-skill call.
- **Always emit the precheck block.** Even though it isn't a "phase", users have learned to look for it as the first output.
- **Never auto-advance between phases.** Every checkpoint waits for explicit user input. If the user says something other than "continue" (e.g. "skip ahead", "redo phase 2"), interpret and act — but never silently move on.
- **Never truncate sub-skill output.** The user is here to learn what the sub-skills do; if you summarize their output, you defeat the pedagogical purpose. Reproduce verbatim. Downstream phases also depend on the full text being in context (Phase 2 detects Phase 1's classification summary; Phase 3 detects Phase 2's failure taxonomy).
- **The phase envelope is invariant.** The banner ("You are here. Phase N of 8…"), the entity block, the action label, and the checkpoint header must appear identically across every phase. The *content inside* may differ; the envelope must not. This is the determinism the skill promises.
- **Execute only at Phases 5 and 7.** No other phase runs code on the user's machine. If a sub-skill output suggests the user should run something themselves, hand it off — don't quietly execute it.
- **One backend for the whole run.** Detected at startup, propagated to all sub-skill calls. Do not re-detect mid-run.
- **`--project-name` is sticky.** Whatever the user picked at Precheck flows unchanged into Phases 5 and 6 and into the final summary. If the user changes their mind at Checkpoint 5, re-run Phase 5 (and only Phase 5) with the new name — do NOT silently rewrite earlier outputs.
- **Phase re-entry**: if the user types something like "redo phase 4 with --trace-limit 30", re-run that phase only (and clearly say so — "Re-running Phase 4 with the new trace limit. Phases 1–3 outputs are unchanged."). After it completes, fall through to Phase 5 just like a fresh run would.

---

## What this skill does NOT do

This list exists so reviewers can spot scope creep:

- **Does not instrument your app.** Audience assumption: the user already has `ml_app` traces flowing into Datadog. If the precheck finds zero traces, the skill stops and points the user at the instrumentation docs — it does not attempt to bootstrap instrumentation.
- **Does not push code or commit anything.** All generated files land in `<output-dir>`; the user owns version control.
- **Does not run any phase's code without an explicit checkpoint confirmation.** Phases 5 and 7 ask before executing.
- **Does not deeply modify your app.** Phase 6's experiment file *imports* your task function; it does not refactor it. If you want prompt / model variants without editing your app, inline the call inside `task_fn` in the generated file.
- **Does not auto-create `.env` files.** Credential files are discovered, not generated — secrets-on-disk decisions belong to the user.

---

## Tool Reference

This skill itself does almost no direct tool calls — the only direct calls are:

1. The **precheck** `search_llmobs_spans` (to confirm the ml_app has traces).
2. `Bash` for **Phase 5** (running the publish script) and **Phase 7** (running the generated experiment).
3. **No** Write for Phase 5 — the publish helper ships at `scripts/publish_dataset.py` alongside this SKILL.md; the orchestrator invokes it by path with CLI args. The skill does not generate the script on the fly.

Everything else routes through sub-skills, which carry their own MCP-to-pup mappings:

| Sub-skill | When invoked | Where its tool reference lives |
|---|---|---|
| `llm-obs-session-classify` | Phase 1 | `dd-llmo/llm-obs-session-classify/SKILL.md` (Tool Reference appendix) |
| `llm-obs-trace-rca` | Phase 2 | `dd-llmo/llm-obs-trace-rca/SKILL.md` (Tool Reference appendix) |
| `llm-obs-eval-bootstrap` (sdk_code / data_only / publish) | Phase 3 | `dd-llmo/llm-obs-eval-bootstrap/SKILL.md` (Tool Reference appendix) |
| `llm-obs-eval-bootstrap` (`--emit-dataset` mode) | Phase 4 | `dd-llmo/llm-obs-eval-bootstrap/SKILL.md` (Phase 3D + Tool Reference appendix) |
| `llm-obs-experiment-py-bootstrap` | Phase 6 | `dd-llmo/llm-obs-experiment-py-bootstrap/SKILL.md` |
| `llm-obs-experiment-analyzer` | Phase 8 | `dd-llmo/llm-obs-experiment-analyzer/SKILL.md` (Tool Reference appendix) |

### Precheck `search_llmobs_spans` ↔ pup

| MCP Tool | pup Command |
|---|---|
| `search_llmobs_spans(query="@ml_app:\"<ml_app>\"", root_spans_only=true, limit=1, from="<timeframe>")` | `pup llm-obs spans search --query "@ml_app:\"<ml_app>\"" --root-spans-only --limit 1 --from <stripped-timeframe> --summary` (strip the `now-` prefix from timeframe per the pup invocation rules above). |

- **MCP result parsing safety**: Before writing any script (Python, jq, etc.) that iterates over or accesses fields in an MCP tool result, inspect the raw structure first — check `type(result)`, top-level keys, and whether the payload is nested inside a content block (e.g. `[{'type': 'text', 'text': '<json>'}]`). Extract and `json.loads()` the inner payload if needed. Never assume MCP results are bare dicts or lists.
