---
name: llm-obs-experiment-py-bootstrap
description: Generates a self-contained Python experiment client that uses the ddtrace.llmobs SDK. Emits either a runnable .py script or a Jupyter .ipynb notebook matching the canonical DataDog reference notebook style. Use when the user says "generate Python experiment", "write an SDK experiment", "create a ddtrace experiment", "Python notebook experiment", "use the LLM Obs SDK", or has `ddtrace` installed and wants idiomatic SDK code.
---

# LLM Obs Experiment (Python) Bootstrap — Generate a Python Experiment Using `ddtrace.llmobs`

Produce a single self-contained Python experiment that uses the official **`ddtrace.llmobs` SDK**. Output is either a `.py` script or an `.ipynb` notebook. The generated code mirrors the patterns shown in DataDog's reference notebooks at <https://github.com/DataDog/llm-observability/tree/main/experiments/notebooks>.

The SDK handles lazy project/experiment creation, dataset push diffing, the 5 MB / 1000-record bulk threshold, eval metric streaming, and the status state machine on the user's behalf. This skill must therefore **never re-implement those primitives** — it just imports `LLMObs` and trusts it.

## Usage

```
/llm-obs-experiment-py-bootstrap [--purpose <free text>] [--format py|ipynb] [--dataset <path>] [--dataset-name <name>] [--dataset-version <int>] [--project-name <name>] [--evaluator-style function|class|remote] [--jobs <n>] [--output <path>] [--task-source <module:function>] [--placeholder-task] [--app-root <path>] [--env-file <path>]
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
| `--task-source` | auto — discovered by application introspection (see Workflow step 2.5) | Explicit override: `<dotted.module.path>:<function_name>` for the function to wrap as `task_fn`. Use when you already know the entry point and want to skip the introspection scan. |
| `--placeholder-task` | off | Opt out of application introspection and emit the generic `# TODO(user)` placeholder task. Use when scaffolding without a real app, in tests, or when the user explicitly wants to fill in the task themselves. |
| `--app-root` | resolved from `pyproject.toml` / `setup.cfg` / `setup.py` / cwd | Root directory the introspection scan is restricted to. Skipped if `--placeholder-task` or `--task-source` is set. |
| `--env-file` | none — generated file auto-discovers `.env` files at runtime (see Workflow step 4, section 1) | Explicit absolute path to a `.env`-style file. Generated code preloads this path **first** before the auto-discovery walk. Use when your credentials live in a non-standard location (e.g. `~/.config/dd/staging.env`). |
| `--purpose` | auto — prompted via `AskUserQuestion` in Workflow step 2.0 if not set or inferable from the invocation message | Free-form string describing what the experiment is meant to validate (e.g. `"test that the agent picks the right tool for ambiguous user requests"`, `"verify SQL output always parses"`, `"regression-test prompt v3 against prod baseline"`). Used as reasoning context — biases candidate ranking in Workflow step 2.5, shapes the wrapper return type in 2.5d, seeds evaluator selection in step 3, and is embedded in the generated file's header comment. NOT a fixed taxonomy — Claude reads the string and decides effects dynamically per invocation. |

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
    # In real output, this is wired to the user's discovered LLM call site via
    # Workflow step 2.5. Only emits as a generic placeholder (with # TODO(user))
    # when --placeholder-task is set or introspection found nothing.
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
        "purpose": "<one-line purpose from step 2.4>",
    },
    description="<one-line purpose from step 2.4>",
    tags={
        # Same provenance, sent to experiment metadata.tags for any future
        # tag-filter UI / API consumers. Always emitted alongside the
        # config copy — never one without the other.
        "generated_by": "claude-code",
        "skill": "llm-obs-experiment-py-bootstrap",
        "purpose": "<one-line purpose from step 2.4>",
    },
)

experiment.run(jobs=10)
print(experiment.url)
```

---

## Evaluator Styles

Generated code uses **one** of three evaluator surfaces, picked by `--evaluator-style`. The detail for each lives in `references/evaluator-styles/<style>.md` and is loaded on demand — only the chosen style is consulted at generation time. The single piece of guidance that applies to all three is the `EvaluatorResult` rule below.

### Return `EvaluatorResult`, not bare values

Plain functions are allowed to return `bool` / `float` / `dict`, and `BaseEvaluator.evaluate()` is allowed to return raw `JSONType`. The SDK accepts both — but `EvaluatorResult` carries fields the Datadog UI surfaces in ways the raw value cannot:

| Field | Type | Used by Datadog UI for |
|---|---|---|
| `value` | `bool` / `float` / `str` / `dict` (JSONType) | The score itself — shown on the experiment metric. **Required.** |
| `reasoning` | `str` | Per-record explanation shown in the compare UI; lets reviewers see *why* an evaluator passed/failed without re-running the LLM. |
| `assessment` | `str` (e.g. `"pass"` / `"fail"` / `"partial"`) | Determines whether a metric trend going up vs. down is an improvement; the UI uses this to color baseline-vs-candidate comparisons. |
| `metadata` | `dict[str, JSONType]` | Free-form per-record context (e.g. `{"confidence": 0.95}`); shown in record drill-down. |
| `tags` | `dict[str, JSONType]` | Used to slice experiment results in the UI (e.g. `{"category": "accuracy"}`). |

Default to `EvaluatorResult` for any evaluator richer than a one-line equality check. Trivial checks like `exact_match` and `length_under_500` are the only cases where a bare `bool` is acceptable.

### Style router

When `--evaluator-style` is resolved (default `function`), read the matching reference file from `<this-skill-dir>/references/evaluator-styles/` and emit section 5 of the generated file using the code template it contains:

| `--evaluator-style` value | Read | Best for |
|---|---|---|
| `function` *(default)* | `references/evaluator-styles/function.md` | Most cases — matches the canonical notebook style |
| `class` | `references/evaluator-styles/class.md` | Evaluators that need persistent state or an async client |
| `remote` | `references/evaluator-styles/remote.md` | Server-side LLM-as-Judge configured in the Datadog UI, reused across experiments |

Do **not** load all three. Each reference file is self-contained — code template + when-to-use + when-not-to-use guidance.

---

## Generated File Structure

The same section sequence in both formats. In `.py` these become comment banners; in `.ipynb` each becomes one markdown cell + one code cell.

```
0. File header docstring — name, generated_at, purpose (from step 2.4), provider, wired task source.
1. Env setup           — auto-discover .env files (cwd, app root, parent walk,
                         ~/.datadog/credentials), then os.getenv reads + hard-assert
                         required keys. NO python-dotenv dependency. Shell env wins
                         over file-loaded values. Only the provider keys actually
                         needed by the wired task_fn are asserted (see step 2.5).
2. LLMObs.enable()     — explicit api_key/app_key/project_name/agentless_enabled
3. Dataset             — inline records OR create_dataset_from_csv
4. Task function       — REAL task function imported from the user's app (via Workflow step 2.5
                         introspection) and adapted to the SDK signature. Only falls back to a
                         placeholder OpenAI call with # TODO(user) if --placeholder-task is set or
                         no LLM call site was found in the project.
5. Evaluators          — 2-3 in the requested style, semantics seeded by the purpose (step 3)
6. Experiment          — LLMObs.experiment(config={..., "generated_by": "claude-code", "purpose": "...", ...}, tags={...})
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

2.4. **Determine the experiment's purpose.** Capture a one-sentence statement of what the experiment is meant to validate. This becomes a *reasoning input* that biases every downstream step (introspection ranking in 2.5, wrapper shape in 2.5d, evaluator selection in step 3, file header in step 4). It is **not** a fixed taxonomy — the user types whatever describes their goal, and Claude reads it and applies judgment per invocation.

   **Resolution order**:

   1. If `--purpose "<text>"` was passed → use it verbatim. Skip to 2.5.
   2. If the user's original invocation message contains a clear purpose statement (e.g. *"set up an experiment to test my tool selection logic"*, *"validate that SQL output is always parseable"*, *"regression-test prompt v3"*), extract it and present it back for confirmation via `AskUserQuestion` with the extracted text as a single option labeled "Use the purpose I described" plus "Pick a different purpose" and "Other". If confirmed, use the extracted text. Skip to 2.5.
   3. Otherwise, prompt with `AskUserQuestion`. The options are **seed prompts**, not a constrained taxonomy — they exist to give the user something to react to. Each "label" maps to a starter sentence that the user can refine; selecting an option uses its starter sentence as `purpose` unless the user provides notes/free text instead.

   **`AskUserQuestion` payload** (use these 5 options verbatim — they cover the common cases without locking the user into them):

   ```
   question: "What is this experiment meant to validate?"
   header:   "Purpose"
   options:
     - label: "Output accuracy / answer quality"
       description: "Verify the model produces correct answers compared to expected outputs. Most common starting point."
     - label: "Tool call correctness"
       description: "For agent apps — validate the agent picks the right tool with the right arguments. Useful when tool routing is the failure surface."
     - label: "Structured output / schema validity"
       description: "Verify the output always conforms to a required shape (JSON, SQL, citation format, etc.)."
     - label: "Retrieval / RAG faithfulness"
       description: "Verify the answer is grounded in retrieved documents — no hallucinations beyond the retrieved context."
     - label: "Refactor / regression test"
       description: "Check whether a prompt or code change preserves observed behavior. Uses the dataset's expected_output as the ground-truth baseline."
   ```

   The user picks an option (which becomes the starter sentence), or picks "Other" and writes their own. Either way, the resulting string is the `purpose` carried forward.

   **What to do with the purpose downstream** — Claude reads it and reasons about effects, no static mapping:

   - **In step 2.5 (introspection ranking)**: read the purpose; if it mentions tools / agents → boost candidates with `@LLMObs.agent`, `@workflow`, LangChain `ReActAgent`, or tool-using shapes. If it mentions retrieval / RAG / grounding → boost candidates that import vector stores or call `retrieve`/`query_engine`. If it mentions schema / JSON / SQL → boost candidates that use `response_format`, pydantic, or structured-output APIs. Apply judgment — there's no hardcoded mapping.
   - **In step 2.5d (wrapper generation)**: if the purpose needs richer signal than just the final output (tool calls, retrieved docs, intermediate state), emit a wrapper that captures it *if the user's function exposes it*. Otherwise emit a `# Note:` comment explaining what the wrapper would ideally return and why a richer return shape would help — do not refactor the user's actual function.
   - **In step 3 (evaluator template)**: read the purpose and seed the evaluator list to match. Accuracy → standard `exact_match` + LLMJudge. Tool calls → an LLMJudge with a tool-correctness rubric, or a `RemoteEvaluator` if the user has one configured. Schema → `JSONEvaluator(required_keys=[...])` or `RegexMatchEvaluator`. Retrieval → an LLMJudge for groundedness. Regression → `exact_match` + a near-match check. The `--evaluator-style` flag (function / class / remote) still picks the SURFACE; the purpose picks the SEMANTICS.
   - **In step 4 (file emission)**: include the purpose as a `# Purpose:` comment in the file's docstring header so the user (and reviewers) can see what the experiment was scaffolded for.
   - **In Output (next-steps)**: surface the resolved purpose in the next-steps block so it's part of the run-summary record.

   **If the user is silent / picks the default option without notes**, use the starter sentence as-is. Never block on this step — there should always be a `purpose` string moving forward, even if it's a generic "Output accuracy / answer quality".

---

2.5. **Discover the task function from the user's application code.** This step replaces the old "placeholder `task_fn`" behavior — an onboarding-grade experiment needs to actually exercise the user's real LLM logic. The skill must do the work, not push it onto the user.

   **Skip this entire step if** `--placeholder-task` is set, OR if `--task-source <module>:<function>` is set (in which case use the provided source directly — jump to substep 2.5d). Otherwise:

   **2.5a — Resolve the app root.** If `--app-root <path>` was supplied, use it. Otherwise use the directory containing whichever of these resolved during step 1 (in order): `pyproject.toml`, `setup.cfg`, `setup.py`, `package.json`. If none of those resolved, use the current working directory. **Hard cap** the scan to that directory tree; do **not** traverse `node_modules`, `.venv`, `venv`, `__pycache__`, `.git`, `dist`, `build`, `target`, `vendor`, `third_party`, or any directory matched by `.gitignore` if present. Refuse to scan if the resolved root is `/` or `~` — that means resolution failed; treat as no candidate found.

   **2.5b — Candidate discovery.** Use Grep / Bash to find Python files inside the app root and identify call sites of these LLM SDKs (the union of common LLM clients):

   | Module / call pattern | Signal |
   |---|---|
   | `openai.ChatCompletion.create`, `openai.chat.completions.create`, `client.chat.completions.create` | OpenAI chat |
   | `openai.completions.create`, `client.completions.create` | OpenAI text |
   | `anthropic.messages.create`, `client.messages.create`, `Anthropic(...).messages.create` | Anthropic |
   | `litellm.completion`, `litellm.acompletion` | LiteLLM (router) |
   | `langchain.*.invoke`, `ChatOpenAI(...)`, `ChatAnthropic(...)`, `LLMChain(...)` | LangChain |
   | `from llama_index`, `as_query_engine`, `as_chat_engine` | LlamaIndex |
   | `google.generativeai.GenerativeModel(...).generate_content` | Vertex/Gemini |
   | `boto3.client("bedrock-runtime").invoke_model` | AWS Bedrock |
   | `@LLMObs.llm`, `@LLMObs.agent`, `@LLMObs.workflow`, `@LLMObs.task`, `@workflow`, `@agent` | Already instrumented |

   For each match, walk **up** to the enclosing function (`def` / `async def`) — call it the *call-site function*. Record `{file, line, function_name, is_async, signature, enclosing_class}`.

   **Skip** files matching `test_*.py`, `*_test.py`, `tests/**`, `conftest.py`, `*_fixtures.py`. Skip private helpers (`def _foo` where the function is the *only* one in the file and looks like a utility — judge by whether it has a typed string input).

   **2.5c — Score and rank candidates.** Score each candidate; higher is more likely to be the "core" entry point:

   | Signal | Score delta |
   |---|---|
   | Function name in {`generate`, `chat`, `complete`, `respond`, `answer`, `handle_request`, `process_query`, `process_message`, `run`, `predict`, `infer`, `call_llm`, `query`, `agent_loop`, `main`} | **+5** |
   | Function takes exactly one or two parameters, at least one being a `str` or `dict` | **+3** |
   | Function is decorated with any `@LLMObs.*` / `@workflow` / `@agent` decorator | **+5** (this is the *intended* instrumentation point) |
   | Function is at the top of its module (first non-import block) | **+2** |
   | Module path matches `**/{main,app,api,handlers,server,routes,agent,bot,chat}.py` | **+3** |
   | Function is a class method (`self` first arg) but the class name matches `*Agent`, `*Bot`, `*Handler`, `*Service` | **+2** |
   | Function name starts with `_` and module has other non-underscore candidates | **−3** (likely a helper) |
   | Function file is under `examples/`, `scripts/`, `notebooks/` | **−2** |
   | Function uses LLM SDK at multiple lines (looks like a multi-step orchestration, not just a single call) | **+1** |

   **Apply the experiment purpose from step 2.4 as an additional soft bias on top of these scores.** Read the purpose string and bump candidates whose shape matches what the user is trying to test — agent / tool-using functions for tool-correctness purposes; retrieval / vector-store users for RAG / grounding purposes; structured-output users for schema purposes; etc. Use judgment, not a hardcoded mapping. A purpose-aligned candidate should typically beat a generically-better-named one (+3 to +5 effective bias is reasonable). See step 2.4 "What to do with the purpose downstream" for examples.

   Pick the **top 3** candidates and present them to the user as a single short prompt (no `AskUserQuestion` — just a checkpoint prompt in chat):

   ```
   ## Task function discovery

   Scanned `<app_root>` and found <N> LLM-calling functions. Top candidates:

   1. `<module.path>:<function_name>`   score <S>   (<file>:<line>, args=<sig>)
   2. ...
   3. ...

   I'll wire candidate **1** as the experiment's task function unless you say otherwise.
   Reply with the number to pick a different candidate, or "placeholder" to emit a
   generic placeholder task instead.
   ```

   Wait for confirmation. If the user is silent / says "go" / says "1", use #1. If they pick another number, use that. If they say "placeholder", set `--placeholder-task` semantics and skip to step 3. If the user supplies a different `module:function`, validate it exists, then use it.

   **If zero candidates were found**, do not block the user — print a one-line note ("No LLM call sites detected under `<app_root>`. Emitting a generic placeholder task — replace it before running.") and fall through with placeholder semantics.

   **2.5d — Generate the task function wrapper.** Once a target `<module>:<function>` is locked in, emit a real `task_fn` that imports and calls the user's function. This is **section 4** of the generated file ("Task function") and must NOT include `# TODO(user)` markers if introspection succeeded — the whole point is to remove that burden.

   Wrapper construction rules:

   - **Import**: emit `from <module> import <function_name>` at the top of section 4 (NOT at the top of the file — keeping it local makes the section self-contained and easy to swap). If the function is a class method, also import the class and instantiate it lazily inside `task_fn` (default `__init__()`, with a `# TODO: pass constructor args if needed` comment only if the constructor takes required arguments).
   - **Signature adaptation**: the SDK's task function signature is `task_fn(input_data: dict, config: dict) -> Any`. The user's function probably has a different signature. Build a small adapter:

     - If the user's function takes one parameter and the dataset's `input_data` looks like `{"<key>": <value>}` with one key, call `<function_name>(input_data["<key>"])`.
     - If the user's function takes multiple parameters, map them by name from `input_data` keys. If a name doesn't match, fall back to positional via `input_data.values()` order but emit a one-line comment flagging the assumption.
     - If the user's function takes `**kwargs` or a single dict, pass `input_data` through unmodified.
     - If the user's function is `async`, wrap with `asyncio.run(...)` inside a sync `task_fn` (simplest path; lets `LLMObs.experiment(...).run()` stay sync). Alternative: emit an `async def task_fn` and use `LLMObs.async_experiment(...)` — only do this if the rest of the experiment is already async.
   - **Config passthrough**: never silently drop the `config` argument. If the user's function takes a `config` / `model` / `temperature` parameter, wire `config.get("...")` into it.
   - **Comment header**: emit a comment block above `task_fn` that names the source function, file, line, the experiment purpose (from step 2.4), and any adaptation choices, e.g.:

     ```python
     # Task function wired to: <module.path>:<function_name>
     #   source:  <file>:<line>
     #   purpose: <one-line purpose from step 2.4>
     #   adapter: input_data["<key>"] -> <function_name>(<key>=...)
     #
     # To experiment with prompt / model variants without editing your app, inline
     # the call here instead of importing.
     ```

   - **Richer-return shape when the purpose demands it**: read the purpose from step 2.4. If validating just the final output string is too lossy for what the user is testing (e.g. they're checking tool-call selection — the final string says "I'll book that for you" but the *interesting* signal is which tool was called with which args), emit a wrapper that captures the richer signal **only if the user's function exposes it**. Two cases:

     - **The function already returns richer data** (e.g. `{"output": str, "tool_calls": [...]}`, or a LangChain `AgentExecutor.invoke()` result with `intermediate_steps`): wire `task_fn` to return that shape directly. Evaluators in step 3 will receive it as `output_data`.
     - **The function returns just a string** but the purpose needs more: emit a `# Note:` comment above `task_fn` explaining what richer shape would help, and how the user could expose it (e.g. *"To capture tool calls for evaluation, refactor `respond(query)` to also return the `intermediate_steps` from your AgentExecutor."*). **Do not refactor the user's function** — emit a note, ship the simple wrapper, let the user choose.

   Never invent a richer return shape that the user's function doesn't actually provide. The wrapper is plumbing, not surgery.

   - **Side-effect warning**: scan the chosen function (and its immediate calls within the same module) for these patterns. If any are present, print a `WARNING:` line in the next-steps output, but do NOT alter the import:

     | Pattern | Warning |
     |---|---|
     | `os.environ[...]` reads beyond LLM API keys | "Task reads env vars beyond LLM credentials — make sure they're set when running the experiment." |
     | `requests.`, `httpx.`, `aiohttp.` calls to non-LLM-provider URLs | "Task makes external HTTP calls — running the experiment will hit those endpoints." |
     | DB drivers (`psycopg2`, `sqlalchemy`, `pymongo`, `redis`, `boto3` ≠ bedrock) | "Task hits a database — point at a non-prod instance before running." |
     | File I/O writes (`open(..., 'w')`, `Path.write_*`) | "Task writes to disk — make sure the path is safe in your experiment env." |

   - **Fallback to placeholder**: if `--placeholder-task` is set OR the introspection picked nothing, emit the original placeholder block (generic OpenAI call with `# TODO(user)`). This is the only path where `TODO(user)` is allowed in the task section.

2.6. **Determine required credentials and emit the env-setup section.** The generated experiment must work without the user pre-exporting anything in their shell, *as long as* a discoverable `.env` lives in a standard location. Don't push setup work onto the user that the file can do itself.

   **Required Datadog keys** (always): `DD_API_KEY` AND (`DD_APPLICATION_KEY` OR `DD_APP_KEY`). `DD_SITE` is optional (defaults to `datadoghq.com`).

   **Required provider keys** — depend on what step 2.5 picked. Inspect the imported call site to identify the provider, then **read the matching reference file** for the exact assert lines, adapter notes, and gotchas. Only load the one that applies; do not load all of them.

   | Detected SDK in task | Read |
   |---|---|
   | OpenAI (`openai.*`, `client.chat.completions.create`) — also the placeholder fallback | `references/providers/openai.md` |
   | Azure OpenAI (`AzureOpenAI(...)`, `azure_endpoint=`) | `references/providers/openai.md` (Azure section) |
   | Anthropic (`anthropic.*`, `Anthropic().messages.create`) | `references/providers/anthropic.md` |
   | LiteLLM (`litellm.completion`, `litellm.acompletion`) | `references/providers/litellm.md` |
   | LangChain (`ChatOpenAI` / `ChatAnthropic` / etc.) | `references/providers/langchain.md` (walks one level deeper to the underlying provider) |
   | LlamaIndex | `references/providers/llamaindex.md` |
   | Google Gemini (`google.generativeai`) / Vertex AI | `references/providers/gemini.md` |
   | AWS Bedrock (`boto3.client("bedrock-runtime")`) | `references/providers/bedrock.md` |
   | Custom / not-recognized SDK | No reference file — emit a `# TODO(user): set the API key(s) your task_fn needs in your .env or shell.` comment instead of an assert. Do NOT fabricate provider keys. |

   **Emit section 1 by reading the shipped template at `<this-skill-dir>/scripts/env_setup_template.py` and substituting two placeholders.** The template ships alongside this SKILL.md (`cp -r` install handles it) and is the canonical source for the loader + assert shape — do **not** re-derive it inline. Read the file, perform the substitutions below, and emit the result verbatim as section 1 of the generated experiment file.

   | Placeholder | Replacement | Example |
   |---|---|---|
   | `{{ENV_FILE_OVERRIDE_LIST}}` | A Python list literal of absolute paths from `--env-file` (repeatable; empty list if not passed) | `[]` or `["/Users/me/.config/dd/staging.env"]` |
   | `{{PROVIDER_ASSERTS}}` | Zero or more `assert os.getenv(...)` lines derived from what step 2.5 wired — per the SDK-to-key table above. One line per required provider key. Empty string if the task uses LiteLLM (which auto-routes) or a custom/unrecognized SDK. | `assert os.getenv("ANTHROPIC_API_KEY"), "ANTHROPIC_API_KEY is required for the wired task_fn."` |

   The exact assert line(s) to substitute into `{{PROVIDER_ASSERTS}}` live in each provider's reference file under the `## {{PROVIDER_ASSERTS}} substitution` heading. Read only the file that matches the detected SDK from the table above.

   The template's discovery walk (env file → script dir → cwd → parent dirs → `~/.datadog/credentials`) and shell-overrides-file contract are stable — they live in the template, not in this skill body. If you need to change discovery semantics, edit `scripts/env_setup_template.py` directly; do not re-derive them in-prose here.

3. **Pick evaluator template** based on `--evaluator-style` AND the purpose from step 2.4.

   `--evaluator-style` decides the **surface** (function / class / remote — see `references/evaluator-styles/<style>.md` for the syntax shape). The purpose decides the **semantics** — what each evaluator actually measures. Both inputs apply together.

   Default shape (purpose: accuracy / unspecified):
   - `function`: 3 plain functions — one trivial boolean (`exact_match`-style, bare `bool` OK), one richer rule-based check returning `EvaluatorResult` with `reasoning` + `assessment`, and one LLM-as-Judge surrogate. If `--dataset` had structured `expected_output`, add a JSON-shape check (also returning `EvaluatorResult`).
   - `class`: 2 `BaseEvaluator` subclasses with `evaluate(self, context: EvaluatorContext) -> EvaluatorResult`. Always return `EvaluatorResult` (never a bare value) — state-bearing evaluators have richer signal to surface.
   - `remote`: 1-2 `RemoteEvaluator(eval_name=...)` instances with a comment instructing the user to create the judge in the Datadog UI first.

   **Purpose-driven amendments** (read the purpose string from step 2.4 and adjust the default seed accordingly — use natural-language judgment, not a hardcoded lookup):

   - If the purpose mentions **tool calls / agent routing / tool selection**: replace the LLM-as-Judge surrogate with a `tool_calls_correct` LLMJudge whose rubric checks the right-tool-with-right-args question. The rubric should reference `output_data["tool_calls"]` if the wrapper from 2.5d captured them; otherwise check the textual output for the tool name.
   - If the purpose mentions **groundedness / faithfulness / RAG**: add a `groundedness` LLMJudge that takes `input_data` (the question + retrieved docs) and `output_data` (the answer) and checks that every claim in the answer traces to the docs. Make the rubric explicit about hallucinations being a fail.
   - If the purpose mentions **structured output / JSON / SQL / schema**: lean into `JSONEvaluator(required_keys=[...])` or `RegexMatchEvaluator(pattern=r"...")` from the built-in set. Add a richer `EvaluatorResult` check for semantic schema-fit if the user's structured output has constraints beyond shape (e.g. enum values, numeric ranges).
   - If the purpose mentions **regression / refactor / preserve behavior**: prefer `exact_match` as the primary signal, plus a near-match check (`difflib.SequenceMatcher.ratio()` ≥ 0.95 returning `EvaluatorResult` with assessment="partial" between 0.7 and 0.95). Drop the LLMJudge surrogate — regression tests want determinism, not model judgment.
   - For purposes the SKILL.md doesn't recognize verbatim: read the user's purpose string, reason about what evaluator shape would best measure it, and write the evaluator from scratch. Cite the purpose in a comment above the evaluator so the user can see the link. Never invent a `RemoteEvaluator(eval_name=...)` for a name that may not exist in their Datadog org — when in doubt emit an LLMJudge with an inline rubric instead.

   Whatever you emit, the **count of evaluators stays 2–3** to keep the generated file lean. The user can add more after the first run.

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

Purpose:
  "<resolved purpose string from step 2.4>"
  (sourced via: --purpose flag | extracted from invocation message | AskUserQuestion default | AskUserQuestion + free text)

SDK calls used:
  ✓ LLMObs.enable(...)                       (line/cell ~<N>)
  ✓ LLMObs.<create_dataset|create_dataset_from_csv|pull_dataset>(...)  (line/cell ~<N>)
  ✓ task_fn(input_data, config)              (line/cell ~<N>)
  ✓ <N> evaluators (style: <function|class|remote>, semantics seeded by purpose)
  ✓ LLMObs.experiment(...).run(jobs=<N>)     (line/cell ~<N>)
  ✓ Provenance (in config + tags): generated_by=claude-code, skill=llm-obs-experiment-py-bootstrap, purpose=...

Task function source:
  ✓ Wired to: <module.path>:<function_name>   (source: <file>:<line>)
  ✓ Adapter: <one line describing the input_data → call shape mapping>
  ✓ Return shape: <plain string | {output, tool_calls} | {answer, retrieved_docs} | etc.>
  ✓ Sync/async: <sync | async (wrapped with asyncio.run)>
  [WARNING lines from the side-effect scan in Workflow step 2.5d, if any]
  [Note line if the purpose requested richer return shape but the function only exposes a string]

(If --placeholder-task was used or introspection found nothing:)
Task function source:
  ⚠ Placeholder task emitted (no real LLM call site found / opted out).
    Replace `task_fn` with your actual LLM call before running.

Syntax check: <pass | skipped: toolchain missing | fail with details>

Install:
  pip install "ddtrace>=4.7" <provider-sdk-if-needed>
  # python-dotenv is NOT required — the generated file ships its own .env loader.

Credentials:
  The generated file auto-discovers .env files at runtime. Discovery order
  (first non-empty value wins per key; shell env always overrides files):
    1. --env-file path baked in as ENV_FILE_OVERRIDE (if --env-file was passed)
    2. <output-file's-directory>/.env
    3. <output-file's-directory>/.env.local
    4. <cwd>/.env  and  <cwd>/.env.local
    5. parent-walk from cwd up to /
    6. ~/.datadog/credentials

  Drop a .env at any of those locations with at minimum:
    DD_API_KEY=...
    DD_APPLICATION_KEY=...
    DD_SITE=datadoghq.com           # only if not the US1 prod site
    <PROVIDER>_API_KEY=...           # the provider key the wired task needs
  Or override on a per-run basis by exporting them in your shell — the loader
  never overwrites a value that is already in os.environ.

Run:
  python <path>                  # for --format py
  jupyter notebook <path>        # for --format ipynb

Next steps:
1. Confirm the wired task_fn matches the entry point you want to evaluate (see "Task function source" above).
   Edit the import in section 4 of the generated file if you'd rather inline the call or pick a different function.
2. Confirm the purpose ("<purpose string>") matches what you actually want to measure — section 0 of the
   file documents it, section 5's evaluators were seeded against it, and section 6's experiment carries
   it as a config tag. Re-run this skill with `--purpose "<new text>"` to regenerate against a different
   target without changing your code.
3. Adjust the evaluators if needed (or wire up RemoteEvaluator names you created in the Datadog UI).
4. Run it. The script prints experiment.url at the end.
5. Watch the experiment: https://app.datadoghq.com/llm/experiments
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

## Datadog Documentation

These are the canonical reference pages on <https://docs.datadoghq.com/>. Use them to ground answers about LLM Observability features and to look up details that aren't covered in this skill.

| Topic | URL | Use when |
|---|---|---|
| LLM Observability overview | <https://docs.datadoghq.com/llm_observability/> | Establishing what the product covers, terminology |
| Setup | <https://docs.datadoghq.com/llm_observability/setup/> | API/app key creation, project + ml_app setup, region/site selection |
| Instrumentation overview | <https://docs.datadoghq.com/llm_observability/instrumentation/> | Auto-instrumentation, manual SDK usage, span model |
| Python SDK reference | <https://docs.datadoghq.com/llm_observability/instrumentation/sdk/> | Public symbol list, decorator semantics, span kinds, annotate/enable signatures |
| Experiments | <https://docs.datadoghq.com/llm_observability/experiments/> | `LLMObs.experiment(...)`, dataset lifecycle, eval streaming, status states |
| Evaluations | <https://docs.datadoghq.com/llm_observability/evaluations/> | Evaluator concepts, managed vs custom evaluators |
| Custom LLM-as-a-judge evaluations | <https://docs.datadoghq.com/llm_observability/evaluations/custom_llm_as_a_judge_evaluations/> | `RemoteEvaluator` payload shape and rubric design |
| Managed evaluations | <https://docs.datadoghq.com/llm_observability/evaluations/managed_evaluations/> | Pre-built judges (faithfulness, toxicity, etc.) |
| Monitoring | <https://docs.datadoghq.com/llm_observability/monitoring/> | Alerts, dashboards, span-level monitors |
| Terms / glossary | <https://docs.datadoghq.com/llm_observability/terms/> | Span kinds, sessions, traces, ml_app |
| Evaluation developer guide | <https://docs.datadoghq.com/llm_observability/guide/evaluation_developer_guide/> | Writing offline evaluators, validation strategy |
| Claude Code skills guide | <https://docs.datadoghq.com/llm_observability/guide/claude_code_skills/> | How this skill fits alongside the rest of the `dd-llmo` set |
| MCP server | <https://docs.datadoghq.com/llm_observability/mcp_server/> | Connecting MCP-compatible clients to LLM Obs data |
| Reference notebooks (GitHub) | <https://github.com/DataDog/llm-observability/tree/main/experiments/notebooks> | Style-of-life examples for the generated `.py` / `.ipynb` |

### Researching features the skill does not cover

If the user asks about an LLM Observability feature the skill's body doesn't address (e.g., specific span kinds, dataset versioning semantics, an evaluator type not covered above), fetch the relevant page from `docs.datadoghq.com` rather than guessing:

1. **Pick the most specific URL** from the table above. Most LLM Obs questions resolve under `/llm_observability/{experiments,evaluations,instrumentation,monitoring}/`.
2. **Use `WebFetch`** on that URL with a focused query (e.g., `"How does Dataset.push() handle the 5 MB threshold?"`). Prefer `WebFetch` over generic web search — the canonical page is almost always under `docs.datadoghq.com/llm_observability/`.
3. **Fall back to `WebSearch`** with `site:docs.datadoghq.com/llm_observability` if you don't know which subpage owns the topic.
4. **Cite the page** in the answer with its URL so the user can verify and bookmark.

Never invent symbols or behaviors not present in this skill body or the docs above. If the docs don't cover the question either, say so explicitly and suggest filing an issue on `DataDog/llm-observability` rather than fabricating a workaround.

---

## Operating Rules

- **SDK only.** No `requests.post`, no manual JSON:API envelope construction, no manual ID generation. If a feature seems to require those, you're solving the wrong problem — the SDK already covers it.
- **Public imports only.** `from ddtrace.llmobs import ...`. Never `_experiment`, `_llmobs`, or any underscore-prefixed module.
- **Env vars, not literals.** Credentials always read from `os.environ`. The generated `main()` (or the env-setup cell) must `assert` they're set with a clear message.
- **Auto-discover, don't push setup work onto the user.** Section 1 always emits the `_load_env_files` helper (no `python-dotenv` dependency). It walks the discovery order documented in Workflow step 2.6 and prints which file(s) it loaded. Never substitute `load_dotenv()` from the third-party `python-dotenv` package — the inline helper has zero dependencies and is identical in behavior. Shell env vars always win over file-loaded values so the user can override any auto-discovered value by `export <KEY>=...` before re-running.
- **Provider-key asserts must match the wired task.** Generate the assert for `OPENAI_API_KEY` only if the task imports / calls OpenAI; same for Anthropic / Gemini / Bedrock / etc. Per the Workflow step 2.6 table. Never emit asserts for provider keys the task does not actually need — they're confusing and cause spurious "missing key" failures.
- **Always pass `site=` to `LLMObs.enable()`.** Read it from `os.getenv("DD_SITE", "datadoghq.com")`. Omitting `site=` silently defaults to US1 prod, which breaks every non-prod org (e.g. staging `datad0g.com`, `datadoghq.eu`). The canonical signature already includes it — never drop it.
- **Per-record `tags` are `"key:value"` strings.** When inlining records (whether from `--dataset` JSON, CSV, or the default sample), each entry in a record's `"tags"` list must be a `"key:value"` string like `"env:prod"`, `"source:traces"`, `"category:geography"`. Bare strings (`"smoke"`, `"baseline"`) trigger `ValueError: Tag '<name>' is malformed.` at `Dataset.append()` time. If the source data has bare-string tags, namespace them — e.g. wrap `"smoke"` as `"tag:smoke"` rather than dropping it.
- **Always resolve a purpose before generating.** Step 2.4 must produce a non-empty `purpose` string before steps 2.5 / 3 / 4 run. If the user provides one via `--purpose` or in their invocation, use it. Otherwise prompt via `AskUserQuestion` with the 5 seed options + Other. Never skip the prompt; never proceed with a blank purpose. A weak purpose ("test it") is still better than no purpose — generic accuracy semantics will at least seed reasonable defaults.
- **Treat the purpose as reasoning input, not a switch statement.** There is no hardcoded mapping from purpose strings to evaluator code or wrapper shape. Read the purpose, reason about what's being measured, and emit appropriately. The same purpose string may produce different output for two different apps (a tool-call purpose in a LangChain app generates different wrapper code than in a raw OpenAI app) — that's expected.
- **Introspect first, placeholder last.** The default behavior is Workflow step 2.5 — scan the user's app, find the LLM entry point, wire `task_fn` to it. A `# TODO(user)` marker in the task section is only acceptable when introspection genuinely found nothing or the user passed `--placeholder-task`. Never emit a placeholder task when a real candidate exists in the project — that's the failure mode this skill exists to fix.
- **`# TODO(user)` markers on at least one evaluator** so reviewers can't ship un-customized evaluators by accident. (Evaluators stay user-owned even when the task is auto-wired.)
- **Introspection is bounded.** The scan in Workflow step 2.5 must respect `--app-root` (or its default-resolved value), `.gitignore` if present, and the directory blocklist (`node_modules`, `.venv`, `__pycache__`, etc.). Refuse to scan `/` or `~`. If a scan would touch more than ~10k Python files, narrow the root or ask the user to point at the relevant subdirectory.
- **Match notebook conventions.** Plain function evaluators by default; class-based only when the user opts in. Print `experiment.url` at the end of every generated file.
- **Tag every experiment with provenance + purpose — in both `config` and `tags`.** Every `LLMObs.experiment(...)` call **must** carry `"generated_by": "claude-code"`, `"skill": "llm-obs-experiment-py-bootstrap"`, AND `"purpose": "<step 2.4 string>"` as keys in **both** the `config={...}` dict (so they render in the experiment's Configuration view, which is where users actually look) **and** the `tags={...}` dict (which the SDK serializes into `metadata.tags` for future tag-filter consumers). The `tags=` path alone is not enough: the current LLM Experiments UI does not surface `metadata.tags` as filterable chips, so users won't see the values unless they're also in `config`. The `purpose` field is what makes future runs of the same experiment discoverable by intent — without it, users see ten experiments with cryptic names and no idea what each was testing. Also set `description="<purpose>"` on the experiment so the UI list view shows it.
- **PII scrub at the door.** If `--dataset` is given, scrub before inlining into the generated file. Never embed a record that contains an unmasked email/phone/SSN/API-key pattern.
- **Don't generate `requirements.txt` or `pyproject.toml`.** Print the `pip install` command in the next-steps message instead — most users already have a venv.
- **No silent fallbacks.** If `--format` is unsupported, error out with the valid choices.
- **Python only.** If a user passes `--language typescript` (or any non-Python language flag), error out — this skill produces Python `ddtrace.llmobs` SDK code only.
- **Research, don't invent.** If the user asks about an LLM Observability feature, span kind, evaluator type, or SDK symbol that is not documented in this skill body, `WebFetch` the relevant `docs.datadoghq.com/llm_observability/*` page (see the Datadog Documentation table above for the canonical URLs) before answering. Cite the page URL in the response. If the docs don't cover the topic, say so explicitly — never fabricate symbols, flags, or behaviors.
