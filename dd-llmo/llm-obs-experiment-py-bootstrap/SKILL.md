---
name: llm-obs-experiment-py-bootstrap
description: Generates a self-contained Python experiment client that uses the ddtrace.llmobs SDK. Emits either a runnable .py script or a Jupyter .ipynb notebook matching the canonical DataDog reference notebook style. Use when the user says "generate Python experiment", "write an SDK experiment", "create a ddtrace experiment", "Python notebook experiment", "use the LLM Obs SDK", or has `ddtrace` installed and wants idiomatic SDK code.
---

# LLM Obs Experiment (Python) Bootstrap — Generate a Python Experiment Using `ddtrace.llmobs`

Produce a single self-contained Python experiment that uses the official **`ddtrace.llmobs` SDK**. Output is either a `.py` script or an `.ipynb` notebook. The generated code mirrors the patterns shown in DataDog's reference notebooks at <https://github.com/DataDog/llm-observability/tree/main/experiments/notebooks>.

The SDK handles lazy project/experiment creation, dataset push diffing, the 5 MB / 1000-record bulk threshold, eval metric streaming, and the status state machine on the user's behalf. This skill must therefore **never re-implement those primitives** — it just imports `LLMObs` and trusts it.

## Usage

```
/llm-obs-experiment-py-bootstrap [--format py|ipynb] [--dataset <path>] [--dataset-name <name>] [--dataset-version <int>] [--project-name <name>] [--evaluator-style function|class|remote] [--jobs <n>] [--output <path>]
```

Arguments: $ARGUMENTS

### Inputs

All inputs are optional. If the user omits a flag, fall back to the default — never block on prompting for `--jobs`, `--format`, etc.

| Input | Default | Description |
|---|---|---|
| `--format` | `py` | `py` (single `.py` file) or `ipynb` (Jupyter notebook with one cell per section). |
| `--dataset` | none — emit a sample 3-record `records=[...]` inline so the file is runnable as-is | Path to a local `DatasetRecordRaw[]` JSON or CSV. JSON → `create_dataset(records=...)`; CSV → `create_dataset_from_csv(...)`. Mutually exclusive with `--dataset-name`. |
| `--dataset-name` | none | Name of an existing Datadog dataset to fetch at runtime via `LLMObs.pull_dataset(...)`. Use this when the dataset already lives in Datadog (e.g. created in the UI or by a prior run) — no local file required. Mutually exclusive with `--dataset`. |
| `--dataset-version` | none (latest) | Pin to a specific dataset version when using `--dataset-name`. Passed through as `pull_dataset(version=N)`. Ignored if `--dataset-name` is not set. |
| `--project-name` | `experiment-<service-name>` — derived from the codebase (see Workflow step 1); falls back to `experiment-sdk-default` only if nothing resolves | Datadog project name (visible in the LLM Experiments UI). The SDK's `ml_app` tag falls back to this automatically — no separate flag needed. |
| `--evaluator-style` | `function` | `function` (plain functions — notebook default), `class` (`BaseEvaluator` subclasses), or `remote` (`RemoteEvaluator` instances). |
| `--jobs` | `10` | Passed to `experiment.run(jobs=N)`. |
| `--output` | `./experiments/experiment.<ext>` | File extension derives from `--format`: `.py` or `.ipynb`. |

---

## SDK Surface (Cited)

These are the public symbols the generated code uses. All come from `ddtrace.llmobs` (the public package — never from `ddtrace.llmobs._experiment` or other underscore-prefixed modules).

| Import | Source | What it gives you |
|---|---|---|
| `LLMObs` | `ddtrace/llmobs/__init__.py` re-exports `_llmobs.py` | `.enable()`, `.create_dataset()`, `.create_dataset_from_csv()`, `.pull_dataset(dataset_name, project_name, version)`, `.experiment()`, `.async_experiment()` |
| `RemoteEvaluator`, `EvaluatorContext` | `ddtrace/llmobs/__init__.py` | LLM-as-Judge that runs server-side; preferred over inline `LLMJudge` |
| `BaseEvaluator`, `EvaluatorResult` | `ddtrace/llmobs/__init__.py` | Class-based evaluator path (advanced) |
| `LLMJudge` | `ddtrace/llmobs/_evaluators/llm_judge.py` (re-exported) | Inline LLM-as-Judge with prompt template support |

**Canonical call signatures** (must match the generated code exactly):

```python
LLMObs.enable(
    api_key=os.getenv("DD_API_KEY"),
    app_key=os.getenv("DD_APPLICATION_KEY"),
    site=os.getenv("DD_SITE", "datadoghq.com"),  # required for non-prod sites (e.g. datad0g.com, datadoghq.eu)
    project_name="<project>",
    agentless_enabled=True,  # required when not running behind the dd-agent
)
# Note: ml_app is not a separate input. The SDK derives it from project_name
# when not supplied. If a user really wants to override it later, they can
# add `ml_app="..."` to enable() themselves.

dataset = LLMObs.create_dataset(
    dataset_name="<name>",
    description="<optional>",
    records=[
        # Per-record `tags` MUST be a list of "key:value" strings (e.g. "env:smoke"),
        # never bare strings — the SDK rejects malformed tags with a ValueError on append.
        {"input_data": {"<k>": "<v>"}, "expected_output": "<v>", "metadata": {}, "tags": ["env:<env>"]},
        # ...
    ],
)
# OR
dataset = LLMObs.create_dataset_from_csv(
    csv_path="<path>",
    dataset_name="<name>",
    input_data_columns=["<col1>", "<col2>"],
    expected_output_columns=["<col>"],
)
# OR pull an existing Datadog dataset by name (no local file needed)
dataset = LLMObs.pull_dataset(
    dataset_name="<name>",
    project_name="<project>",   # optional — defaults to the project on enable()
    version=2,                  # optional — pin a version; omit for the latest
)

def task_fn(input_data: dict, config: dict):
    # TODO(user): replace with your actual LLM call
    ...

# Plain function evaluator (default style)
def exact_match(input_data, output_data, expected_output) -> bool:
    return output_data == expected_output

experiment = LLMObs.experiment(
    name="<experiment_name>",
    dataset=dataset,
    task=task_fn,
    evaluators=[exact_match],
    config={
        "model": "gpt-4o-mini",
        "temperature": 0.0,
        # Provenance also lives in `config` so it renders in the
        # experiment's Configuration view alongside model/temperature.
        # `tags=` below only reaches metadata.tags, which the current UI
        # does not surface as chips — config is what users actually see.
        "generated_by": "claude-code",
        "skill": "llm-obs-experiment-py-bootstrap",
    },
    description="<optional>",
    tags={
        # Same provenance, sent to experiment metadata.tags for any future
        # tag-filter UI / API consumers. Always emitted alongside the
        # config copy — never one without the other.
        "generated_by": "claude-code",
        "skill": "llm-obs-experiment-py-bootstrap",
    },
)

experiment.run(jobs=10)
print(experiment.url)
```

---

## Evaluator Styles

Generated code uses **one** of three evaluator surfaces, picked by `--evaluator-style`. Whichever style is chosen, **prefer returning `EvaluatorResult` over a bare `bool`/`float`** whenever the evaluator has any signal beyond the raw value — see "Return EvaluatorResult, not bare values" below.

### Return `EvaluatorResult`, not bare values

Plain functions are allowed to return `bool` / `float` / `dict`, and `BaseEvaluator.evaluate()` is allowed to return raw `JSONType`. The SDK accepts both — but `EvaluatorResult` carries fields the Datadog UI surfaces in ways the raw value cannot:

| Field | Type | Used by Datadog UI for |
|---|---|---|
| `value` | `bool` / `float` / `str` / `dict` (JSONType) | The score itself — shown on the experiment metric. **Required.** |
| `reasoning` | `str` | Per-record explanation shown in the compare UI; lets reviewers see *why* an evaluator passed/failed without re-running the LLM. |
| `assessment` | `str` (e.g. `"pass"` / `"fail"` / `"partial"`) | Determines whether a metric trend going up vs. down is an improvement; the UI uses this to color baseline-vs-candidate comparisons. |
| `metadata` | `dict[str, JSONType]` | Free-form per-record context (e.g. `{"confidence": 0.95}`); shown in record drill-down. |
| `tags` | `dict[str, JSONType]` | Used to slice experiment results in the UI (e.g. `{"category": "accuracy"}`). |

The generated code should default to `EvaluatorResult` for any evaluator richer than a one-line equality check. The trivial `exact_match` and `length_under_500` shown below are the only cases where a bare `bool` is acceptable.

### `function` (default — what the notebooks use)

Plain Python functions with the signature `(input_data, output_data, expected_output)`. Always emit at least three: a trivial boolean (returns `bool`), a richer rule-based one (returns `EvaluatorResult`), and an LLM-as-Judge surrogate (a `RemoteEvaluator` reference or a placeholder).

```python
from ddtrace.llmobs import EvaluatorResult

# Trivial check — bare bool is fine here, the result has no extra signal.
def exact_match(input_data, output_data, expected_output) -> bool:
    return output_data == expected_output

# Richer check — use EvaluatorResult so reasoning/assessment surface in the UI.
def response_well_formed(input_data, output_data, expected_output) -> EvaluatorResult:
    if not isinstance(output_data, str):
        return EvaluatorResult(
            value=False,
            reasoning=f"output_data was {type(output_data).__name__}, expected str",
            assessment="fail",
        )
    if len(output_data) > 500:
        return EvaluatorResult(
            value=False,
            reasoning=f"output exceeded 500 chars (was {len(output_data)})",
            assessment="fail",
            metadata={"length": len(output_data)},
        )
    return EvaluatorResult(value=True, assessment="pass")
```

### `class` (advanced — for evaluators that need state or async I/O)

Always return `EvaluatorResult` from `evaluate()` — never a bare value. State-bearing evaluators usually have richer reasoning to surface anyway.

```python
from ddtrace.llmobs import BaseEvaluator, EvaluatorContext, EvaluatorResult

class FaithfulnessJudge(BaseEvaluator):
    def __init__(self):
        super().__init__(name="faithfulness")
        # TODO(user): initialize any client or state here

    def evaluate(self, context: EvaluatorContext) -> EvaluatorResult:
        # context exposes: input_data, output_data, expected_output, metadata
        # TODO(user): replace placeholder logic with your faithfulness check
        passed = context.output_data is not None
        return EvaluatorResult(
            value=1.0 if passed else 0.0,
            reasoning="placeholder — replace with your faithfulness rubric",
            assessment="pass" if passed else "fail",
            metadata={"evaluator_version": "v1"},
        )
```

### `remote` (LLM-as-Judge running server-side)

```python
from ddtrace.llmobs import RemoteEvaluator

# Create the judge in Datadog UI first: LLM Observability → Evaluations → New Evaluator
quality_judge = RemoteEvaluator(eval_name="<name-from-datadog-ui>")

# Optional: customize the payload the judge receives
custom_judge = RemoteEvaluator(
    eval_name="<name>",
    transform_fn=lambda ctx: {
        "question": ctx.input_data.get("question"),
        "answer": ctx.output_data,
        "reference": ctx.expected_output,
    },
)
```

---

## Generated File Structure

The same section sequence in both formats. In `.py` these become comment banners; in `.ipynb` each becomes one markdown cell + one code cell.

```
1. Env setup           — load_dotenv(), os.getenv reads, hard assert keys present
2. LLMObs.enable()     — explicit api_key/app_key/project_name/agentless_enabled
3. Dataset             — inline records OR create_dataset_from_csv
4. Task function       — placeholder OpenAI call with # TODO(user) marker
5. Evaluators          — 2-3 in the requested style
6. Experiment          — LLMObs.experiment(config={..., "generated_by": "claude-code", ...}, tags={"generated_by": "claude-code", ...})
7. Run                 — experiment.run(jobs=N); print(experiment.url)
8. Results inspection  — experiment.as_dataframe() if pandas, else print
```

---

## Workflow

1. **Parse arguments**. Default `--format py`. Resolve `--output` extension from `--format`.

   If `--project-name` is not provided, resolve a default of the form `experiment-<service-name>` by walking these sources in order, taking the first match:
   1. `pyproject.toml` → `[project] name` (PEP 621) or `[tool.poetry] name`.
   2. `setup.cfg` → `[metadata] name`.
   3. `setup.py` → first `name="..."` argument to `setup(...)`.
   4. `package.json` → `"name"` (useful when the LLM app lives in a TS/JS monorepo Python package).
   5. The basename of the current working directory, lowercased and slugified (`/^[a-z0-9-]+$/` — replace non-matching chars with `-`).

   The final project name is `experiment-<service-name>`. Strip a leading `experiment-` from `<service-name>` if it already starts with one (so a package literally named `experiment-foo` yields `experiment-foo`, not `experiment-experiment-foo`). If none of the five sources resolve to a non-empty string, fall back to `experiment-sdk-default` and emit a warning in the next-steps output that the user should set `--project-name` explicitly.

   Embed the resolved name as a string literal in the generated `PROJECT_NAME = "..."` line — don't emit runtime `os.getcwd()` lookups, since the user may run the file from a different directory than where the skill resolved it.

2. **Resolve the dataset source.** Error out if both `--dataset` and `--dataset-name` are passed — they're mutually exclusive.

   - **`--dataset <path>` (local file → inline records or CSV loader)**:
     - Read the file. If JSON, validate top-level array of `DatasetRecordRaw` shape (`input_data`, optional `expected_output`, `metadata`, `tags`). If CSV, parse header and auto-detect columns using the `dataset-bootstrap` heuristics: `prompt|input|query|question` → input, `expected|gold|truth|answer` → expected.
     - Run a PII scrub (email/phone/SSN/API-key regexes) on all string values; replace matches with `<REDACTED:pii-type>` and surface a warning listing affected indices.
     - **For JSON datasets**, embed the records inline in the generated file (`records=[...]`) so the user has a single self-contained artifact. **For CSV datasets**, emit `LLMObs.create_dataset_from_csv(csv_path="<absolute path>", ...)` and tell the user the CSV needs to be present at runtime.

   - **`--dataset-name <name>` (existing Datadog dataset → runtime pull)**:
     - Emit `LLMObs.pull_dataset(dataset_name="<name>", project_name="<project>"[, version=<n>])` in place of any `create_dataset*` call. The fetch happens when the generated experiment runs — the skill itself does not call Datadog.
     - Pass `version=<n>` through only if `--dataset-version` was set; otherwise omit it so the SDK resolves the latest.
     - Add a one-line comment above the call documenting what's being pulled, e.g. `# Pulled from Datadog: dataset_name="qa_v3", version=latest`.
     - Skip the PII scrub and the inline-records emission — there are no local records to scrub.

   - **Neither flag given**:
     - Fall back to the inline 3-record sample described under `--dataset`'s default, so the generated file remains runnable as-is.

   **Note on dataset IDs.** The public SDK's `LLMObs.pull_dataset(...)` takes a name, not an ID — so there's no `--dataset-id` flag. If a user only has a dataset ID from a Datadog UI URL (`/llm/datasets/<id>`), the workflow is: open that URL in the UI, copy the dataset name, and pass it as `--dataset-name`. The skill must not import `ddtrace.llmobs._experiment` or any other underscore module to work around this.

3. **Pick evaluator template** based on `--evaluator-style`:
   - `function`: 3 plain functions — one trivial boolean (`exact_match`-style, bare `bool` OK), one richer rule-based check returning `EvaluatorResult` with `reasoning` + `assessment`, and one LLM-as-Judge surrogate. If `--dataset` had structured `expected_output`, add a JSON-shape check (also returning `EvaluatorResult`).
   - `class`: 2 `BaseEvaluator` subclasses with `evaluate(self, context: EvaluatorContext) -> EvaluatorResult`. Always return `EvaluatorResult` (never a bare value) — state-bearing evaluators have richer signal to surface.
   - `remote`: 1-2 `RemoteEvaluator(eval_name=...)` instances with a comment instructing the user to create the judge in the Datadog UI first.

   **In all styles**: any evaluator with non-trivial logic must return `EvaluatorResult` populating at minimum `value` + `reasoning` + `assessment` (see the "Return `EvaluatorResult`, not bare values" section). The compare UI uses `reasoning` for per-record drill-downs and `assessment` to determine whether a metric trend is an improvement.

4. **Emit the file**.

   **For `.py`** — single file, one blank line between sections, banner comments like:
   ```python
   # ─── 3. Dataset ───────────────────────────────────────────────────────────────
   ```
   Use `from __future__ import annotations` and `from typing import Any, Dict` at the top. Type-hint task and evaluator function signatures.

   **For `.ipynb`** — valid Jupyter notebook JSON. Schema:
   ```json
   {
     "cells": [
       {"cell_type": "markdown", "metadata": {}, "source": ["## 1. Env setup\n", "..."]},
       {"cell_type": "code", "execution_count": null, "metadata": {}, "outputs": [], "source": ["..."]},
       ...
     ],
     "metadata": {
       "kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"},
       "language_info": {"name": "python", "version": "3.10"}
     },
     "nbformat": 4,
     "nbformat_minor": 5
   }
   ```
   One markdown cell + one code cell per section. Keep each code cell self-contained enough that re-running it in isolation makes sense.

5. **Best-effort syntax check** via Bash. Don't fail the skill if the toolchain is missing — just report.
   - `.py`: `python -m py_compile <path>`
   - `.ipynb`: `python -c "import json; nb = json.load(open('<path>')); assert nb.get('cells'); print(f'cells={len(nb[\"cells\"])}')"`

6. **Print next-steps** (see Output section).

---

## What the Generated Code MUST NOT Do

A reviewer should be able to run these `grep` checks against the generated file and get zero matches:

| `grep` pattern | Why it's wrong |
|---|---|
| `uuid4`, `uuid.uuid4` | `record_id` is minted by the SDK on `dataset.append()`; never client-generate. |
| `PATCH `, `batch_update`, `records/upload` | Status state machine and dataset diff are SDK responsibilities. |
| `from ddtrace.llmobs._` | Private import paths. Always use `from ddtrace.llmobs import ...`. |
| `"record_id"`, `"canonical_id"` (as dict keys in records) | The SDK owns them. |
| `DD_API_KEY = "<actual key>"` | Always read from `os.environ`. |
| `requests.post`, `httpx.post` | The skill produces SDK-only code. Direct HTTP calls bypass the SDK's lazy creation, push-diff, and bulk-threshold handling. |

If any of those slip into the output, the skill is wrong — re-emit.

---

## Output

After writing, print:

```
Generated SDK experiment: <format>
Path: <path>
Lines: <count>   (or Cells: <count> for .ipynb)

SDK calls used:
  ✓ LLMObs.enable(...)                       (line/cell ~<N>)
  ✓ LLMObs.<create_dataset|create_dataset_from_csv|pull_dataset>(...)  (line/cell ~<N>)
  ✓ task_fn(input_data, config)              (line/cell ~<N>)
  ✓ <N> evaluators (style: <function|class|remote>)
  ✓ LLMObs.experiment(...).run(jobs=<N>)     (line/cell ~<N>)
  ✓ Provenance (in config + tags): generated_by=claude-code, skill=llm-obs-experiment-py-bootstrap

Syntax check: <pass | skipped: toolchain missing | fail with details>

Install:
  pip install "ddtrace>=4.7" python-dotenv openai

Environment variables (required at runtime):
  export DD_API_KEY=...
  export DD_APPLICATION_KEY=...
  export DD_SITE=datadoghq.com
  export OPENAI_API_KEY=...   # only if you keep the placeholder task

Run:
  python <path>                  # for --format py
  jupyter notebook <path>        # for --format ipynb

Next steps:
1. Replace the placeholder task_fn with your actual LLM call.
2. Adjust the evaluators (or wire up RemoteEvaluator names you created in the Datadog UI).
3. Run it. The script prints experiment.url at the end.
4. Watch the experiment: https://app.datadoghq.com/llm/experiments
```

---

## Reference Notebook Patterns (use as templates)

The canonical set lives at <https://github.com/DataDog/llm-observability/tree/main/experiments/notebooks> and serves as the style reference — the generated code should feel like it could have come from this set.

| Notebook | Pattern demonstrated |
|---|---|
| `00-basic-datasets.ipynb` | Dataset create/append/push lifecycle |
| `01-basic-experiments.ipynb` | Minimum viable experiment — inline records, OpenAI task, 2 boolean evaluators |
| `02-extra-data.ipynb` | CSV-loaded dataset, multi-value task output, confidence-based evaluators |
| `04-multi-span-experiments.ipynb` | Two-step LLM pipelines inside a single `task_fn` |
| `07-remote-evaluators.ipynb` | `RemoteEvaluator` with custom `transform_fn` |

When `--evaluator-style remote`, lean toward the `07` style. When `--dataset` is a CSV, lean toward `02`. Default (no `--dataset`, `--evaluator-style function`) is the `01` style.

---

## Operating Rules

- **SDK only.** No `requests.post`, no manual JSON:API envelope construction, no manual ID generation. If a feature seems to require those, you're solving the wrong problem — the SDK already covers it.
- **Public imports only.** `from ddtrace.llmobs import ...`. Never `_experiment`, `_llmobs`, or any underscore-prefixed module.
- **Env vars, not literals.** Credentials always read from `os.environ`. The generated `main()` (or the env-setup cell) must `assert` they're set with a clear message.
- **Always pass `site=` to `LLMObs.enable()`.** Read it from `os.getenv("DD_SITE", "datadoghq.com")`. Omitting `site=` silently defaults to US1 prod, which breaks every non-prod org (e.g. staging `datad0g.com`, `datadoghq.eu`). The canonical signature already includes it — never drop it.
- **Per-record `tags` are `"key:value"` strings.** When inlining records (whether from `--dataset` JSON, CSV, or the default sample), each entry in a record's `"tags"` list must be a `"key:value"` string like `"env:prod"`, `"source:traces"`, `"category:geography"`. Bare strings (`"smoke"`, `"baseline"`) trigger `ValueError: Tag '<name>' is malformed.` at `Dataset.append()` time. If the source data has bare-string tags, namespace them — e.g. wrap `"smoke"` as `"tag:smoke"` rather than dropping it.
- **`# TODO(user)` markers** on the placeholder task and on at least one evaluator so reviewers can't ship the placeholder by accident.
- **Match notebook conventions.** Plain function evaluators by default; class-based only when the user opts in. Print `experiment.url` at the end of every generated file.
- **Tag every experiment with provenance — in both `config` and `tags`.** Every `LLMObs.experiment(...)` call **must** carry `"generated_by": "claude-code"` and `"skill": "llm-obs-experiment-py-bootstrap"` as keys in **both** the `config={...}` dict (so they render in the experiment's Configuration view, which is where users actually look) **and** the `tags={...}` dict (which the SDK serializes into `metadata.tags` for future tag-filter consumers). The `tags=` path alone is not enough: the current LLM Experiments UI does not surface `metadata.tags` as filterable chips, so users won't see the provenance unless it's also in `config`. If a user later edits the generated file to add their own keys, they extend both dicts — never replace the provenance keys silently.
- **PII scrub at the door.** If `--dataset` is given, scrub before inlining into the generated file. Never embed a record that contains an unmasked email/phone/SSN/API-key pattern.
- **Don't generate `requirements.txt` or `pyproject.toml`.** Print the `pip install` command in the next-steps message instead — most users already have a venv.
- **No silent fallbacks.** If `--format` is unsupported, error out with the valid choices.
- **Python only.** If a user passes `--language typescript` (or any non-Python language flag), error out — this skill produces Python `ddtrace.llmobs` SDK code only.
