---
name: llm-obs-onboarding-datasets-experiments
description: Guided onboarding for Datadog LLM Observability datasets and experiments. Walks an already-instrumented ml_app owner through six well-defined states — analyze ml_app, create dataset from prod traces, publish dataset, generate experiment code, run experiment, analyze experiment — each with a standardized banner that names the entity being produced and explains its purpose. Use when the user says "onboard me to datasets and experiments", "walk me through experiments", "I have an ml_app, now what", "LLM Obs onboarding", "guided experiment setup", or wants a deterministic, narrated tour of the dataset → experiment loop. Pure orchestration over existing dd-llmo skills (`llm-obs-session-classify`, `llm-obs-eval-bootstrap`, `llm-obs-experiment-py-bootstrap`, `llm-obs-experiment-analyzer`) — no new analytical logic.
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

`--backend pup` is accepted anywhere in the invocation arguments. Strip it from args before passing to sub-skills, but carry the pup-mode decision forward — every sub-skill must also operate in pup mode for the entire onboarding run.

**Sub-skill backend propagation**: The backend detected at onboarding startup applies to all sub-skills invoked across the six states. Do not re-detect per state. Announce once at startup:
- MCP mode: "(Running in MCP mode — all features available.)"
- pup mode: "(Running in pup mode — pup commands used throughout. All features available.)"

**Invocation ID:** At the very start of each invocation, before any MCP tool call, generate an 8-character hex invocation ID (e.g., `3a9f1c2b`). Keep it constant for the entire invocation.

**Intent tagging:** On every MCP tool call, prefix `telemetry.intent` with `skill:llm-obs-onboarding-datasets-experiments[<inv_id>] — ` followed by a description of why the tool is being called. On the **first MCP tool call only**, use `skill:llm-obs-onboarding-datasets-experiments:start[<inv_id>] — ` instead (note the `:start` suffix). Example first call: `skill:llm-obs-onboarding-datasets-experiments:start[3a9f1c2b] — Precheck: verify ml_app has traces in the last 7 days`

---

# LLM Obs Onboarding — Datasets & Experiments

A deterministic, six-state guided tour for an already-instrumented `ml_app` owner who wants to learn the Datadog LLM Obs **datasets + experiments** loop. Each state has the same envelope — a banner that names the entity being produced, an explanation of its purpose, the action (a sub-skill call or a small executable step), and a checkpoint. **You always know where you are.**

```
[Precheck] verify ml_app & backend
   ↓
[State 1: Analyze ml_app]                  entity: ml_app, trace, span
   ↓
[State 2: Create dataset from prod traces] entity: dataset record
   ↓
[State 3: Publish dataset]                 entity: published dataset, dataset_name
   ↓
[State 4: Generate experiment code]        entity: experiment, task, evaluator
   ↓
[State 5: Run experiment]                  entity: experiment run, experiment.url
   ↓
[State 6: Analyze experiment]              entity: metric, comparison, recommendation
```

This skill is **pure orchestration plus pedagogy**. The analytical work happens inside the sub-skills (`llm-obs-session-classify`, `llm-obs-eval-bootstrap` in `--emit-dataset` mode, `llm-obs-experiment-py-bootstrap`, `llm-obs-experiment-analyzer`). What this skill adds is the deterministic envelope: every state has the same shape, the same checkpoint contract, and the same entity-explanation banner — so the user gets a consistent, narrated experience regardless of how they phrased the original request.

## Usage

```
/llm-obs-onboarding-datasets-experiments <ml_app> [--timeframe <window>] [--trace-limit <N>] [--project-name <name>] [--format py|ipynb] [--output-dir <dir>]
```

Arguments: $ARGUMENTS

### Inputs

| Input | Required | Default | Description |
|-------|----------|---------|-------------|
| `ml_app` | Yes | — | The instrumented LLM app to onboard around. The precheck verifies it has recent traces. |
| `--timeframe` | No | `now-7d` | Lookback window for the State 1 analysis and the State 2 dataset sampling. |
| `--trace-limit` | No | `15` | How many traces to sample for the dataset in State 2. Reasonable default for an onboarding pass; the user can raise it on re-run. |
| `--project-name` | No | derived from cwd (see `llm-obs-experiment-py-bootstrap` for resolution order) | Datadog project name that the generated experiment lives under. |
| `--format` | No | `py` | Passed through to `llm-obs-experiment-py-bootstrap`: `py` (script) or `ipynb` (notebook). |
| `--output-dir` | No | `./experiments` | Where the dataset JSON and the generated experiment file are written. |

If `ml_app` is not provided, ask the user before proceeding.

---

## Precheck

Before State 1, do a single short verification pass — do **not** announce a "state" banner for this; it's plumbing.

1. **Backend** — already detected at the top of this skill. Note the chosen backend in the precheck output so the user can confirm.
2. **ml_app has recent traces** — call `search_llmobs_spans(query="@ml_app:\"<ml_app>\"", root_spans_only=true, limit=1, from="<timeframe>")` (MCP) or the pup equivalent. If the result is empty, stop and tell the user the precheck failed — there is nothing to onboard against — and suggest widening `--timeframe` or confirming the ml_app name.
3. **Resolve `project_name`** — if `--project-name` was not supplied, derive it using the resolution order documented in `dd-llmo/llm-obs-experiment-py-bootstrap/SKILL.md` (Workflow step 1). Keep this as a string in working memory; it gets used in State 4.
4. **Ensure `--output-dir` exists** — `mkdir -p <output-dir>` via Bash. Cheap.
5. **Resolve credentials.** Walk the discovery order below to find Datadog credentials before State 3 needs them — failing late at the publish step is bad UX. Read-only at this stage: do NOT write any new files. Do NOT print secret values to the user; only report which file was loaded and which keys were resolved.

   **Discovery order** (first hit per variable wins; shell env vars always override files):
   1. **Current shell environment** (`os.environ`) — already-exported `DD_API_KEY` / `DD_APPLICATION_KEY` / `DD_APP_KEY` / `DD_SITE` take precedence. If all required keys are already present, skip file loading entirely.
   2. **`<output-dir>/.env`** — if the user previously ran the skill and dropped a `.env` next to past artifacts, prefer that.
   3. **`<app-root>/.env`** — where `<app-root>` is the resolved `pyproject.toml` / `setup.cfg` / `setup.py` / `package.json` directory (or cwd if none).
   4. **`<app-root>/.env.local`** — git-ignored local override convention.
   5. **`<cwd>/.env`** — fallback if cwd differs from app-root.
   6. **Parent walk**: from cwd, walk up directory by directory looking for `.env` until reaching `/` or the user's home directory. Stop at the first hit.
   7. **`~/.datadog/credentials`** — Datadog's well-known per-user credentials file, if present.

   For each file checked, parse line-by-line: skip blanks / comment lines (`#`) / malformed lines (no `=`). Strip a leading `export ` if present (so `.envrc`-style files work). Split on the first `=`. Strip surrounding quotes on the value. Only set a variable that is not already in `os.environ` — never overwrite the shell.

   **Required keys**: `DD_API_KEY` AND (`DD_APPLICATION_KEY` OR `DD_APP_KEY`). `DD_SITE` is optional (defaults to `datadoghq.com`). Provider keys (OPENAI_API_KEY, ANTHROPIC_API_KEY, etc.) are validated later in States 4/5 against the introspected task — not here.

   **If all required keys resolved** — record which file(s) were loaded and emit a single-line summary in the Precheck block (see template below). Continue.

   **If required keys NOT found after walking every location** — stop and prompt the user with a clear, actionable message:

   > "Datadog credentials were not found in your shell env or any discovered `.env` file. Two options before continuing:
   > - **Export in your shell**: `export DD_API_KEY=…` and `export DD_APPLICATION_KEY=…` (also `DD_SITE=…` if non-default), then re-invoke this skill.
   > - **Drop a `.env`** at `<app-root>/.env` with `DD_API_KEY=…` and `DD_APPLICATION_KEY=…` on separate lines, then re-invoke.
   >
   > Make sure `.env` is in your `.gitignore` before committing."

   Do **not** offer to create the `.env` file for the user — secrets-on-disk decisions belong to the user, not the skill.

Output a one-block precheck summary, then move directly to State 1:

```
## Precheck

- Backend: <MCP | pup>
- ml_app `<ml_app>` has traces in <timeframe>: yes (<sample_count> root spans found)
- Project name: `<project_name>`
- Output dir: `<output-dir>` (created)
- Credentials: <one of:
    "loaded from shell env (DD_API_KEY, DD_APPLICATION_KEY, DD_SITE)"
  | "loaded from <relative path to .env file> (DD_API_KEY, DD_APPLICATION_KEY[, DD_SITE])"
  | "shell env + <relative path>: keys resolved from both (shell overrode file for <list>)"
  >

Starting State 1 of 6.
```

The exact list of keys in the parenthetical reflects what was actually discovered (so the user can verify nothing surprising was loaded). Never print the values.

---

## State Template

Every state below uses this exact template. Do not deviate — the deterministic envelope is what makes the onboarding experience consistent across invocations.

```
## State N of 6: <Title>

**You are here.** State N of 6 — <one-line position summary>.

**What this state produces**: <Entity name>
**What a <entity> is**: <2-3 sentence definition tailored to this state>
**Why it matters**: <1 sentence on why the user needs this>

→ Action: <invoke <sub-skill> | execute <script>>

<full sub-skill output OR execution log reproduced here — do NOT summarize or truncate>

---

### Checkpoint N

<concise summary of what was produced, where it lives (path / Datadog URL), and any caveats>

Before I continue to State N+1 (<next title>):
- <2–3 state-specific review prompts the user can answer>

Type "continue" to proceed, or give me adjustments.
```

**Never auto-advance.** Always pause at the checkpoint and wait for explicit user input. The whole point of this skill is determinism — that includes determinism over *when* the user moves on.

---

## State 1: Analyze ml_app

**Entity**: `ml_app`, `trace`, `span`.

**Pedagogy banner** (use verbatim, adapted only to the actual ml_app name):

> **What an ml_app is**: a logical LLM application — a name you tag spans with when instrumenting (`ml_app=<name>`). It groups all production traces and evaluator runs that belong to the same product surface. Every dataset and experiment you create later targets this scope.
>
> **What a trace is**: one end-to-end execution of your ml_app — typically the agent loop for a single user request, made up of one or more spans (LLM calls, tool calls, retrievals).
>
> **Why this state matters**: before you curate a dataset, you want a quick read on what your app actually does in production and where its current failure modes are. That signal informs which traces are worth turning into experiment records.

**Action**: Invoke the **`llm-obs-session-classify`** skill in **ml_app mode**, using:
- `ml_app` = the provided ml_app
- `timeframe` = the provided timeframe
- `sample_limit` = `min(20, trace-limit)` — keep this fast; State 2 will do the bigger sample

Run the complete ml_app mode workflow as defined in that skill. **Output the full classification output** (all compact per-unit blocks plus the final `# Session Classification Summary`) — do not summarize. The user is here to learn what the data looks like, not just hear a one-liner.

### Checkpoint 1

After the `# Session Classification Summary` is output, present:

```
## State 1 complete — you've seen what `<ml_app>` does in production

[verdict distribution table from session-classify]
[failure mode frequency table from session-classify]

Next up — State 2 will sample traces from this ml_app and turn them into dataset records you can run experiments against.

Before I continue:
- Do these failure patterns look right?
- Are there specific traces you'd like to exclude from the dataset sample in State 2?
- Any quality dimension you already know you want to measure later?

Type "continue" to proceed, or give me adjustments.
```

Wait for explicit user confirmation. If the user excludes specific traces, carry that exclusion forward to State 2.

---

## State 2: Create dataset from prod traces

**Entity**: `dataset`, `dataset record`.

**Pedagogy banner**:

> **What a dataset is**: a named collection of records that an experiment runs against. Each record has `input_data` (what the task receives) and optionally `expected_output` (what you expect back). Datasets live in Datadog under your project and have a version — every time you push changes, a new version is created.
>
> **What a record is**: a single `(input_data, expected_output)` pair, optionally with `metadata` and `tags`. One record = one experiment row.
>
> **Why this state matters**: experiments need a stable input set. Sampling production traces gives you a realistic starting dataset — the inputs your app actually sees — without making you write fixtures by hand.

**Action**: Invoke the **`llm-obs-eval-bootstrap`** skill in **`--emit-dataset` mode**:

```
/eval-bootstrap <ml_app> --timeframe <timeframe> --trace-limit <trace-limit> --emit-dataset <output-dir>/dataset_<ml_app>_<YYYYMMDD>.json
```

This mode samples root spans, extracts `(input_data, expected_output)` pairs from each, applies a PII scrub, and writes a `DatasetRecordRaw[]` JSON file. **It does not propose or generate evaluators in this mode** — the dataset is the sole artifact. See `dd-llmo/llm-obs-eval-bootstrap/SKILL.md` → Phase 3D for the full spec.

If the user excluded specific traces in State 1's checkpoint, pass that exclusion list along (sub-skill drops them during sampling — do NOT re-classify).

Reproduce the sub-skill's `## Generated Dataset` summary verbatim.

### Checkpoint 2

After the dataset JSON is written, present:

```
## State 2 complete — local dataset file ready

- File: `<path>`
- Records: <N> (skipped: <M> with no usable output)
- PII redactions: <P>
- Caveat: `expected_output` is the **current production behavior baseline**, not ground truth. Spot-check a few records before publishing.

Next up — State 3 will publish this dataset to Datadog under project `<project_name>` so an experiment can pull it by name.

Before I continue:
- Want to open the file and edit any records first? (recommended — spot-check at least 3–5)
- Happy with the proposed dataset name `<ml_app>_seed_<YYYYMMDD>`, or pick another?
- Any records you want me to drop?

Type "continue" to proceed, or give me adjustments.
```

Wait for confirmation. The proposed `dataset_name` defaults to `<ml_app>_seed_<YYYYMMDD>` but the user can override — carry the final chosen name into State 3.

---

## State 3: Publish dataset

**Entity**: published dataset (Datadog-side), `dataset_name`, version.

**Pedagogy banner**:

> **What "publishing" means**: pushing the local records to Datadog so the dataset becomes addressable by name across all subsequent experiments. After publish, anyone in your org (with access) can pull it with `LLMObs.pull_dataset(dataset_name="…")` — including the experiment code we generate in State 4.
>
> **What a dataset version is**: every push that changes records produces a new version. You can pin an experiment to a specific version (`pull_dataset(version=N)`) so a refactor of the dataset doesn't silently change which inputs your experiment runs on.
>
> **Why this state matters**: a published, named dataset is the contract between your dataset curation work (which lives in code/JSON) and the experiments that consume it. Without this step, the experiment in State 4 has no input.

**This is one of the two states in this skill where executable code is run.** The user is in "publish" mode and that's the clear, expected signal.

**Action**: Write a small one-shot publish script to `<output-dir>/_publish_dataset.py`, then execute it. The script MUST defensively normalize per-record `tags` before calling `LLMObs.create_dataset(...)` — the SDK's `Dataset.append()` raises `ValueError: Tag '<tag>' is malformed. Tags must be in 'key:value' format.` for any tag string lacking a `:` separator, and the upstream `eval-bootstrap --emit-dataset` output is not guaranteed to be 100% conformant. The publish script is the last line of defense before the SDK; treat normalization as non-optional.

Script content:

```python
"""One-shot dataset publisher for llm-obs-onboarding-datasets-experiments.

Auto-generated by the onboarding orchestrator — safe to delete after running.
"""
import json
import os
import pathlib


def _load_env_files() -> list[str]:
    """Walk standard locations for .env-style files. Shell env always wins.

    Mirrors the discovery order in the onboarding skill's Precheck step 5
    (see dd-llmo/llm-obs-onboarding-datasets-experiments/SKILL.md). Order:
    sibling dir of this script, app root, cwd, then a parent walk, then
    ~/.datadog/credentials. Returns the absolute paths of files loaded.
    """
    here = pathlib.Path(__file__).resolve().parent
    cwd = pathlib.Path.cwd().resolve()
    candidates: list[pathlib.Path] = [
        here / ".env",
        here / ".env.local",
        cwd / ".env",
        cwd / ".env.local",
    ]
    # Walk up from cwd looking for any .env file in a parent dir.
    p = cwd
    while p != p.parent and p != pathlib.Path.home().parent:
        candidates.append(p / ".env")
        p = p.parent
    candidates.append(pathlib.Path.home() / ".datadog" / "credentials")

    loaded: list[str] = []
    seen: set[pathlib.Path] = set()
    for path in candidates:
        try:
            path = path.resolve()
        except Exception:
            continue
        if path in seen or not path.is_file():
            continue
        seen.add(path)
        try:
            text = path.read_text()
        except Exception:
            continue
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            if line.startswith("export "):
                line = line[len("export "):]
            k, _, v = line.partition("=")
            k = k.strip()
            v = v.strip().strip('"').strip("'")
            if k and k not in os.environ:  # shell wins over file
                os.environ[k] = v
        loaded.append(str(path))
    return loaded


_env_files = _load_env_files()
if _env_files:
    print(f"Loaded credentials from: {', '.join(_env_files)}")

from ddtrace.llmobs import LLMObs

RECORDS_PATH = "<absolute path to dataset JSON from State 2>"
DATASET_NAME = "<chosen dataset_name from Checkpoint 2>"
PROJECT_NAME = "<resolved project_name from Precheck>"

api_key = os.getenv("DD_API_KEY")
app_key = os.getenv("DD_APPLICATION_KEY") or os.getenv("DD_APP_KEY")
assert api_key, "DD_API_KEY must be set"
assert app_key, "DD_APPLICATION_KEY (or DD_APP_KEY) must be set"

LLMObs.enable(
    api_key=api_key,
    app_key=app_key,
    site=os.getenv("DD_SITE", "datadoghq.com"),
    project_name=PROJECT_NAME,
    agentless_enabled=True,
)

with open(RECORDS_PATH) as f:
    records = json.load(f)


def _normalize_tags(raw):
    """Make every tag a 'key:value' string so Dataset.append() does not reject the record.

    The SDK's validate_tags_list requires ':' in every tag. Bare strings, empties, and
    None are common upstream mistakes — wrap bare strings as 'tag:<value>', drop empties.
    Returns (normalized_list, fix_count).
    """
    fixed = []
    fix_count = 0
    for t in raw or []:
        if not isinstance(t, str):
            fix_count += 1
            continue  # drop non-strings
        t = t.strip()
        if not t:
            fix_count += 1
            continue  # drop empties
        if ":" in t:
            k, _, v = t.partition(":")
            if k and v:
                fixed.append(t)
                continue
            # malformed (leading/trailing ':') — wrap so original is preserved
            fixed.append(f"tag:{t}")
            fix_count += 1
            continue
        # bare string — namespace under generic 'tag:' key
        fixed.append(f"tag:{t}")
        fix_count += 1
    return fixed, fix_count


total_fixes = 0
for r in records:
    if "tags" in r:
        r["tags"], n = _normalize_tags(r.get("tags"))
        total_fixes += n
if total_fixes:
    print(f"WARNING: normalized {total_fixes} malformed tag(s) before publish "
          f"(bare strings wrapped as 'tag:<value>'; empties dropped).")

dataset = LLMObs.create_dataset(
    dataset_name=DATASET_NAME,
    description=f"Seed dataset for {DATASET_NAME} (onboarding flow, sampled from production traces).",
    records=records,
)
url = dataset.url if hasattr(dataset, "url") else "<inspect in UI>"
print(f"OK dataset_name={DATASET_NAME} record_count={len(records)} url={url}")
```

**Notes for the orchestrator:**
- Credential discovery is delegated entirely to the publish script's `_load_env_files()` helper. The script walks the same locations the Precheck already inspected (script dir, app root, cwd, parent dirs, `~/.datadog/credentials`), and surfaces the loaded file path(s) in its stdout. Shell env always wins over file-loaded values — so the user can override anything by `export DD_API_KEY=...` before re-running.
- If the Precheck already established that credentials resolve, the script's `_load_env_files()` will just be a confirmation no-op (vars are already in `os.environ`).
- If `_normalize_tags` printed any `WARNING:` line, surface that prominently in Checkpoint 3 — the user should know their input dataset had malformed tags so they can chase the upstream cause on re-runs.
- If the script prints `Loaded credentials from: ...`, include that file path in Checkpoint 3 so the user knows which secrets file the publish actually used.

Execution rules:
- Use `python <path>` via Bash. Pipe stdout+stderr; surface the result to the user.
- **Before executing**, do a precheck: run `python -c "import ddtrace.llmobs"` via Bash. If it fails, stop and tell the user:
  > "`ddtrace` is not installed in the active Python environment. Run `pip install 'ddtrace>=4.7'` and re-invoke this skill from State 3 (just re-run from the top — State 1 and 2 outputs are idempotent). The publish script has its own `.env` loader, so `python-dotenv` is no longer required."
- If the script run fails with an auth error (401/403), surface the message and stop — do not retry. Tell the user the most likely cause is that a discovered `.env` file has a stale / wrong key, and that they can override via `export DD_API_KEY=... DD_APPLICATION_KEY=...` in their shell (which takes precedence) and re-run.
- If it succeeds, capture the printed `dataset_name` and `url`.

### Checkpoint 3

```
## State 3 complete — dataset published

- Dataset name: `<dataset_name>`
- Project: `<project_name>`
- Records published: <N>
- Datadog UI: <url or "open LLM Observability → Datasets to confirm">

Next up — State 4 will generate a Python experiment script that pulls this dataset and runs a placeholder task + evaluators against it.

Before I continue:
- Confirm you can see the dataset in the Datadog UI (LLM Observability → Datasets → search `<dataset_name>`)?
- Any second thoughts on the dataset records (we can re-emit before generating the experiment)?

Type "continue" to proceed, or give me adjustments.
```

Wait for confirmation.

---

## State 4: Generate experiment code

**Entity**: `experiment`, `task` function, `evaluator`.

**Pedagogy banner**:

> **What an experiment is**: a programmatic harness that, for each record in a dataset, calls a `task` function (your code under test — typically an LLM call), then runs one or more `evaluators` against the task's output. Datadog collects all those results into a single experiment view you can compare across runs.
>
> **What a task function is**: a Python callable that receives one record's `input_data` and a `config` dict, and returns whatever your app would have returned for that input.
>
> **What an evaluator is**: a function that judges one record's output. Returns `bool` / `float` / a structured `EvaluatorResult`. We'll start with three placeholder evaluators in the generated code — replace them with the ones that match your real quality bar.
>
> **Why this state matters**: this is the artifact you'll actually run. Everything before this state was prep work.

**Action**: Invoke the **`llm-obs-experiment-py-bootstrap`** skill with the dataset from State 3:

```
/llm-obs-experiment-py-bootstrap \
  --dataset-name <dataset_name> \
  --project-name <project_name> \
  --format <format> \
  --output <output-dir>/experiment_<ml_app>_<YYYYMMDD>.<py|ipynb>
```

Reproduce the sub-skill's full output (including the generated SDK calls summary and the Next steps block) verbatim. Do not summarize.

### Checkpoint 4

```
## State 4 complete — experiment file ready to run

- File: `<path>`
- Format: <py | ipynb>
- Wired to dataset: `<dataset_name>` (pulled at runtime via LLMObs.pull_dataset)
- Placeholder evaluators: <list the 2–3 evaluator names from the sub-skill output>
- Task function source: <line lifted from the sub-skill's "Task function source" output — names the discovered module:function, or notes the placeholder fallback>

Next up — State 5 will run this file end-to-end against the published dataset.

**Before continuing — open the file and look at three things**:
1. The wired `task_fn` (section 4 of the generated file). The sub-skill introspected your app and imported the most likely entry point — **confirm it picked the right function**. If it picked a sibling helper or a deprecated path, edit the import in section 4 to point at the function you actually want to evaluate. If the sub-skill fell back to a placeholder (no LLM call site found in `<app-root>`), this is the state where you replace it with a real call.
2. The placeholder evaluators — these are starting points. You can leave them for the first run, or refine.
3. The `experiment.run(jobs=<N>)` parallelism — defaults to 10; lower it if you're worried about rate limits.

Then confirm:
- Have you set `DD_API_KEY`, `DD_APPLICATION_KEY`, `DD_SITE` (if non-default), and `OPENAI_API_KEY` (if the placeholder task is unchanged)?
- Ready to run?

Type "continue" to proceed, or give me adjustments.
```

Wait for confirmation.

---

## State 5: Run experiment

**Entity**: experiment run, `experiment.url`, metric stream.

**Pedagogy banner**:

> **What "running the experiment" means**: iterating over every record in the dataset, calling your `task` function, then calling each evaluator on the result, and streaming the scores to Datadog. The SDK creates the experiment in Datadog the first time it runs and updates the same experiment record on re-runs (if you keep the same `name=`).
>
> **What `experiment.url` is**: the deep link to the run in the Datadog Experiments UI. State 6 uses this to analyze results.
>
> **Why this state matters**: until you actually run the experiment, the dataset and the code are just plumbing. The run is what produces the first measurements.

**This is the second of the two states in this skill where executable code is run.** Clearly signal it.

**Action**: Execute the generated experiment file.

- For `--format py`: `python <generated_path>` via Bash. Stream output to the user.
- For `--format ipynb`: tell the user the generated file is a notebook and ask whether to (a) execute it via `jupyter nbconvert --to notebook --execute --inplace <path>` (requires `jupyter` installed), or (b) hand off — the user opens it in JupyterLab and runs cells manually. Default to (a) if `jupyter` is on PATH; otherwise (b).
- Capture the printed `experiment.url` from the run's stdout — the generated file always ends with `print(experiment.url)`. If you can't find it, parse stdout for the substring `https://app.datadoghq.com/llm/experiments/`.
- If the run fails: do NOT retry automatically. Surface the full traceback, identify the failure category (auth, missing dep, dataset not found, task function raised, evaluator raised) in a one-line diagnosis, and ask the user whether to fix and re-run.

### Checkpoint 5

```
## State 5 complete — experiment run published

- Experiment URL: <experiment.url>
- Records processed: <N>
- Duration: <wall-clock seconds>
- Evaluator score summary (from stdout, if printed): <table or "open the UI">

Next up — State 6 will pull the experiment results back from Datadog and produce an analysis report (struggling metrics, qualitative examples, root-cause hypotheses).

Before I continue:
- Take a look at the experiment in the UI (link above). Do the per-record scores roughly match your expectations?
- Any specific question you want State 6 to focus on? (Optional — leaving it open runs an exploratory analysis.)

Type "continue" to proceed, or give me adjustments.
```

Wait for confirmation. If the user provides a focus question, carry it to State 6 as the analyzer's `question` argument.

---

## State 6: Analyze experiment

**Entity**: experiment `metric`, segment comparison, recommendation.

**Pedagogy banner**:

> **What an experiment metric is**: a per-record score produced by one of your evaluators, aggregated into a pass-rate / score-distribution across the dataset. The Datadog Experiments UI shows these as columns and lets you slice by `metadata` fields you attached to each record.
>
> **What a recommendation looks like**: based on which metrics underperformed and on patterns in the failing records, the analyzer surfaces hypotheses for what to change next (system prompt, retrieval, task code, the dataset itself, or the evaluator).
>
> **Why this state matters**: this closes the loop. You started by looking at production behavior; you now have an evidence-backed read on where the experiment exposes gaps and what to try next.

**Action**: Invoke the **`llm-obs-experiment-analyzer`** skill in **single-exploratory** (or **single-Q&A** if the user supplied a focus question in Checkpoint 5):

```
/llm-obs-experiment-analyzer <experiment_id_from_url> [<focus question if any>] --output agent
```

Extract the `<experiment_id>` from the URL captured in State 5 (the trailing UUID after `/llm/experiments/`).

Reproduce the analyzer's full report verbatim.

### Final Summary

After the analyzer report, emit the closing summary — this replaces the per-state checkpoint:

```markdown
# Onboarding complete — datasets & experiments

**ml_app**: `<ml_app>` | **Project**: `<project_name>` | **Timeframe**: <timeframe>

| State | Output |
|---|---|
| 1. Analyze ml_app | <N> traces classified (<F> failures) |
| 2. Create dataset | <K> records → `<dataset_path>` |
| 3. Publish dataset | `<dataset_name>` (v1) in project `<project_name>` |
| 4. Generate experiment code | `<experiment_file_path>` |
| 5. Run experiment | <experiment.url> |
| 6. Analyze experiment | <2–3 bullet headline findings from the analyzer> |

## What you learned

- The four core entities you touched: **ml_app**, **dataset**, **experiment**, **evaluator**. Each has a dedicated docs page — see Datadog Documentation below.
- The loop you can now repeat: **edit dataset → re-run experiment → compare in the UI**. Pull-by-name + auto-versioning makes the loop cheap.

## Recommended next steps

1. Open the experiment in the Datadog UI: <experiment.url>
2. Replace the placeholder `task_fn` in `<experiment_file_path>` with your actual app's call shape.
3. Tighten the evaluators — `llm-obs-eval-bootstrap` (without `--emit-dataset`) can propose a domain-aware evaluator suite based on the same ml_app's traces. Run `/llm-obs-eval-bootstrap <ml_app>` when you're ready.
4. Re-run the experiment after every meaningful change. Datadog will keep the run history.

## Datadog Documentation

- LLM Observability overview: <https://docs.datadoghq.com/llm_observability/>
- Datasets: <https://docs.datadoghq.com/llm_observability/experiments/datasets_and_experiments/>
- Experiments: <https://docs.datadoghq.com/llm_observability/experiments/>
- Evaluations: <https://docs.datadoghq.com/llm_observability/evaluations/>
- Python SDK reference: <https://docs.datadoghq.com/llm_observability/instrumentation/sdk/>
```

---

## Orchestration Rules

- **Always run the precheck, even on re-invocations.** It's cheap and it catches a stale `ml_app` argument or an expired auth token before you waste a sub-skill call.
- **Always emit the precheck block.** Even though it isn't a "state", users have learned to look for it as the first output.
- **Never auto-advance between states.** Every checkpoint waits for explicit user input. If the user says something other than "continue" (e.g. "skip ahead", "redo state 2"), interpret and act — but never silently move on.
- **Never truncate sub-skill output.** The user is here to learn what the sub-skills do; if you summarize their output, you defeat the pedagogical purpose. Reproduce verbatim.
- **The state envelope is invariant.** The banner ("You are here. State N of 6…"), the entity block, the action label, and the checkpoint header must appear identically across every state. The *content inside* may differ; the envelope must not. This is the determinism the skill promises.
- **Execute only at States 3 and 5.** No other state runs code. If a sub-skill output suggests the user should run something themselves, hand it off — don't quietly execute it.
- **One backend for the whole run.** Detected at startup, propagated to all sub-skill calls. Do not re-detect mid-run.
- **State re-entry**: if the user types something like "redo state 2 with --trace-limit 30", re-run that state only (and clearly say so — "Re-running State 2 with the new trace limit. States 1's output is unchanged."). After it completes, fall through to State 3 just like a fresh run would.

---

## What this skill does NOT do

This list exists so reviewers can spot scope creep:

- **Does not propose evaluators.** Evaluator generation is `llm-obs-eval-bootstrap` (default mode) or `llm-obs-eval-pipeline`. Recommended in the final summary, not run.
- **Does not do RCA.** That's `llm-obs-trace-rca`. State 6's analyzer surfaces hypotheses but is not a full RCA.
- **Does not instrument your app.** Audience assumption: the user already has `ml_app` traces flowing into Datadog. If the precheck finds zero traces, the skill stops and points the user at the instrumentation docs — it does not attempt to bootstrap instrumentation.
- **Does not push code or commit anything.** All generated files land in `<output-dir>`; the user owns version control.
- **Does not run any state's code without an explicit checkpoint confirmation.** States 3 and 5 ask before executing.

---

## Tool Reference

This skill itself does almost no direct tool calls — the only direct calls are:

1. The **precheck** `search_llmobs_spans` (to confirm the ml_app has traces).
2. `Bash` for **State 3** (running the publish script) and **State 5** (running the generated experiment).
3. `Write` for the State 3 publish script (`_publish_dataset.py`).

Everything else routes through sub-skills, which carry their own MCP-to-pup mappings in their respective Tool Reference sections:

| Sub-skill | When invoked | Where its tool reference lives |
|---|---|---|
| `llm-obs-session-classify` | State 1 | `dd-llmo/llm-obs-session-classify/SKILL.md` (Tool Reference appendix) |
| `llm-obs-eval-bootstrap` (in `--emit-dataset` mode) | State 2 | `dd-llmo/llm-obs-eval-bootstrap/SKILL.md` (Phase 3D + Tool Reference appendix) |
| `llm-obs-experiment-py-bootstrap` | State 4 | `dd-llmo/llm-obs-experiment-py-bootstrap/SKILL.md` |
| `llm-obs-experiment-analyzer` | State 6 | `dd-llmo/llm-obs-experiment-analyzer/SKILL.md` (Tool Reference appendix) |

### Precheck `search_llmobs_spans` ↔ pup

| MCP Tool | pup Command |
|---|---|
| `search_llmobs_spans(query="@ml_app:\"<ml_app>\"", root_spans_only=true, limit=1, from="<timeframe>")` | `pup llm-obs spans search --query "@ml_app:\"<ml_app>\"" --root-spans-only --limit 1 --from <stripped-timeframe> --summary` (strip the `now-` prefix from timeframe per the pup invocation rules above). |

- **MCP result parsing safety**: Before writing any script (Python, jq, etc.) that iterates over or accesses fields in an MCP tool result, inspect the raw structure first — check `type(result)`, top-level keys, and whether the payload is nested inside a content block (e.g. `[{'type': 'text', 'text': '<json>'}]`). Extract and `json.loads()` the inner payload if needed. Never assume MCP results are bare dicts or lists.
