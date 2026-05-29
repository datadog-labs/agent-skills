---
name: llm-obs-eval-bootstrap
description: Bootstrap evaluators from production traces — emit SDK code, a framework-agnostic JSON spec, or publish online LLM-judge evaluators directly to Datadog. Use when user says "bootstrap evaluators", "generate evaluators", "create evals from traces", "eval bootstrap", "write evaluators", "build eval suite", "publish evaluators", or wants to generate BaseEvaluator/LLMJudge code or online judge configs from production LLM trace data. Works with ml_app and optional RCA report or failure hypothesis.
---

## Backend

**Detection** — At the start of every invocation, before taking any action, determine which backend to use:

1. If the user passed `--backend pup` anywhere in their invocation → use **pup mode** immediately, regardless of whether MCP tools are present. Skip steps 2–4.
2. Check whether MCP tools are present in your active tool list. The canonical signal is whether `mcp__datadog-llmo-mcp__list_llmobs_evals` appears in your available tools.
3. If MCP tools are present → use **MCP mode** throughout. Call MCP tools exactly as named in this skill's workflow sections.
4. If MCP tools are absent → check whether `pup` is executable: run `pup --version` via Bash. A JSON response containing `"version"` confirms pup is available.
5. If pup responds → use **pup mode** throughout. Translate every MCP tool call to its pup equivalent using the Tool Reference appendix at the bottom of this file.
6. If neither is available → stop and tell the user:
   > "Neither the Datadog MCP server nor the pup CLI is available. Connect the MCP server (`claude mcp add --scope user --transport http datadog-llmo-mcp 'https://mcp.datadoghq.com/api/unstable/mcp-server/mcp?toolsets=llmobs'`) or install pup."

`--backend pup` is accepted anywhere in the invocation arguments and is stripped before passing remaining args to the skill logic.

**pup invocation rules:**
- Invoke via Bash: `pup llm-obs <subcommand> [flags]`
- pup always outputs JSON. Parse directly — no content-block unwrapping (unlike MCP results, which may wrap JSON in `[{"type": "text", "text": "<json>"}]`).
- If pup returns an auth error, tell the user to run `pup auth login` and stop.
- Parallelization: issue multiple Bash tool calls in a single message (one pup command per call).
- Time flags: pup accepts bare duration strings (`1h`, `7d`, `30m`) and RFC3339 timestamps. Do **not** use `now-`-prefixed strings — strip the prefix when converting from a skill `--timeframe` argument: `now-7d` → `7d`, `now-24h` → `24h`, `now-30d` → `30d`.
- `--summary` on `pup llm-obs spans search` strips payload fields to essential metadata only. Use it in bulk/search phases where content is not needed.

**Invocation ID:** At the very start of each invocation, before any MCP tool call, generate an 8-character hex invocation ID (e.g., `3a9f1c2b`). Keep it constant for the entire invocation.

**Intent tagging:** On every MCP tool call, prefix `telemetry.intent` with `skill:llm-obs-eval-bootstrap[<inv_id>] — ` followed by a description of why the tool is being called. On the **first MCP tool call only**, use `skill:llm-obs-eval-bootstrap:start[<inv_id>] — ` instead (note the `:start` suffix). Example first call: `skill:llm-obs-eval-bootstrap:start[3a9f1c2b] — Phase 0: map existing eval coverage for task-cruncher`

# Eval Bootstrap — Generate Evaluators from Production Traces

Given a sample of production LLM traces, analyze input/output patterns and quality dimensions, then emit a ready-to-use evaluator suite. Four output modes:

- **`sdk_code`** *(default)* — Python `.py` file using the Datadog Evals SDK (`BaseEvaluator` / `LLMJudge`) for **offline** experiments.
- **`data_only`** — self-contained JSON spec, framework-agnostic.
- **`publish`** — write **online** LLM-judge evaluators directly to Datadog via `create_or_update_llmobs_evaluator`. These run automatically on matching production spans or traces (no dataset, no task function). The skill **auto-classifies** each proposed evaluator as **span-scoped** or **trace-scoped** based on what the judgment requires (a per-LLM-call tone check vs. an agent goal completion that needs the whole trace) — the user accepts or overrides the classification at the proposal checkpoint.
- **`emit_dataset`** — sample production traces and write a `DatasetRecordRaw[]` JSON file shaped for `LLMObs.create_dataset(records=...)`. **Skips evaluator proposal and generation entirely** — this mode produces a dataset, not evaluators. Used by `llm-obs-onboarding-datasets-experiments` to seed an experiment dataset from production behavior.

## Usage

```
/eval-bootstrap <ml_app> [--timeframe <window>] [--data-only | --publish | --emit-dataset <path>] [--trace-limit <N>]
```

Arguments: $ARGUMENTS

### Inputs

| Input | Required | Default | Description |
|-------|----------|---------|-------------|
| `ml_app` | Yes | — | ML application to scope traces |
| `timeframe` | No | `now-7d` | How far back to look |
| `rca_report` | No | — | Failure taxonomy from `eval-trace-rca` skill, or a free-text failure hypothesis |
| `--data-only` | No | off | Emit a self-contained JSON spec file instead of Python SDK code |
| `--publish` | No | off | Publish online LLM-judge evaluators to Datadog (mutually exclusive with `--data-only`) |
| `--emit-dataset <path>` | No | off | **Dataset-only mode.** Sample production traces and write a `DatasetRecordRaw[]` JSON to `<path>`. Skips evaluator workflow. Mutually exclusive with `--data-only` and `--publish`. |
| `--trace-limit` | No | `20` (cap `50`) | Max traces to sample in `emit_dataset` mode |

If `ml_app` is missing, ask the user before proceeding. If more than one of `--data-only`, `--publish`, `--emit-dataset` are supplied, error out and ask which mode the user wants.

## Available Tools

| Tool | Purpose |
|------|---------|
| `search_llmobs_spans` | Find spans by eval presence, tags, span kind, query syntax. Paginate with cursor. |
| `get_llmobs_span_details` | Metadata, evaluations (scores, labels, reasoning), and `content_info` map showing available fields + sizes. |
| `get_llmobs_span_content` | Actual content for a span field. Supports JSONPath via `path` param for targeted extraction. |
| `get_llmobs_trace` | Full trace hierarchy as span tree with span counts by kind. |
| `get_llmobs_agent_loop` | Chronological agent execution timeline (LLM calls, tool invocations, decisions). |
| `list_llmobs_evals` | List every evaluator configured for the caller's org across all ml_apps, with `enabled` status and `ml_app` per result. Call once in Phase 0 to map existing coverage before proposing new evaluators — filter the result by `ml_app` client-side. |
| `get_llmobs_evaluator` | Fetch the **full** persisted evaluator config by name (target ml_app + sampling + filter, provider, prompt template, parsing type, output schema, assessment criteria). Use in Phase 0 to understand what each existing custom eval measures, and (in publish mode) **before any update** — `create_or_update_llmobs_evaluator` is full-replace, so you must round-trip the full config to avoid clobbering fields. Not all evaluators have a stored config (notably `source=ootb`); a not-found error there is expected — skip those. |
| `create_or_update_llmobs_evaluator` | *(publish mode)* Write an LLM-judge evaluator config to Datadog. Full-replace semantics: any omitted optional field resets to its default. See "Publishing Conventions" for required fields and structured output → JSON schema mapping. |
| `delete_llmobs_evaluator` | *(publish mode)* Only used if the user explicitly asks to remove an evaluator. Never invoke speculatively. |

### Key `get_llmobs_span_content` Patterns

Use the `path` parameter to extract targeted data without fetching full payloads:

| Field | Path | What you get |
|-------|------|-------------|
| `messages` | `$.messages[0]` | System prompt (first message, usually `system` role) |
| `messages` | `$.messages[-1]` | Last assistant response |
| `messages` | *(no path)* | Full conversation including tool calls |
| `input` / `output` | — | Span I/O |
| `documents` | — | Retrieved documents (RAG apps) |
| `metadata` | — | Custom metadata (prompt versions, feature flags, user segments) |

### How to Use `search_llmobs_spans`

Additional filters combine with space (AND): `@status:error @ml_app:my-app`. Dedicated params (`span_kind`, `root_spans_only`, `ml_app`) work alongside `query`, but `query` takes precedence over `tags`.

To find spans with a specific eval: `@evaluations.custom.<eval_name>:*` — you can only query for eval *presence*, not specific results.

### Parallelization Rules

1. **`get_llmobs_span_details`**: Group span_ids by trace_id. One call per trace_id with ALL its span_ids. Issue ALL calls for a page in a **single message**.
2. **`get_llmobs_span_content`**: Each call is independent — always issue ALL in a single message.
3. **`get_llmobs_trace` / `get_llmobs_agent_loop`**: Parallelize across different traces in a single message.
4. **Pipeline parallelism**: Start `get_llmobs_span_details` for page 1 results immediately — don't wait to collect all pages.

---

## Evaluator SDK Reference

> **Applies to `sdk_code` mode only.** In `data_only` mode, use this section as domain context when writing rubric prompts — no SDK classes are emitted.

### Imports

```python
# Core classes
from ddtrace.llmobs._experiment import BaseEvaluator, EvaluatorContext, EvaluatorResult

# LLM-as-judge
from ddtrace.llmobs._evaluators.llm_judge import (
    LLMJudge,
    BooleanStructuredOutput,
    ScoreStructuredOutput,
    CategoricalStructuredOutput,
)

# Built-in evaluators (use only if needed)
from ddtrace.llmobs._evaluators.format import JSONEvaluator, LengthEvaluator
from ddtrace.llmobs._evaluators.string_matching import StringCheckEvaluator, RegexMatchEvaluator
```

Only import what the generated file actually uses.

### EvaluatorContext (what `evaluate()` receives)

```python
@dataclass(frozen=True)
class EvaluatorContext:
    input_data: dict[str, Any]          # Task inputs (from dataset record, NOT from span)
    output_data: Any                     # Task output (from task function return, NOT from span)
    expected_output: Optional[JSONType] = None  # Ground truth (if available)
    metadata: dict[str, Any] = {}        # Additional metadata
    span_id: Optional[str] = None        # LLMObs span ID
    trace_id: Optional[str] = None       # LLMObs trace ID
```

**Important — span data vs evaluator data**: When exploring production traces, you see span I/O (e.g., `input.value`, `output.messages`). But evaluators run in offline experiments where `input_data` and `output_data` come from the user's **dataset records and task function**, not from spans. The dataset schema is user-defined and may not match span structure. Write evaluator prompts with generic `{{input_data}}` / `{{output_data}}` placeholders and add comments describing what data the evaluator was designed for, so the user can adapt to their dataset shape.

### EvaluatorResult (what `evaluate()` returns)

```python
EvaluatorResult(
    value=...,                    # Required. JSONType (str, int, float, bool, None, list, dict)
    reasoning="...",              # Optional. Explanation string
    assessment="pass" or "fail",  # Optional. Pass/fail assessment
    metadata={...},              # Optional. Evaluation metadata dict
    tags={...},                  # Optional. Tags dict
)
```

### LLMJudge — LLM-as-Judge Evaluator

```python
judge = LLMJudge(
    user_prompt="...",              # Required. Supports {{template_vars}}
    system_prompt="...",            # Optional. Does NOT support template vars
    structured_output=...,          # Optional. Boolean/Score/Categorical output, or a dict for custom JSON schema
    provider="openai",              # "openai" | "anthropic" | "azure_openai" | "vertexai" | "bedrock"
    model="gpt-4o",                # Model identifier
    model_params={"temperature": 0.0},  # Optional. Passed to LLM API
    name="eval_name",              # Optional. Must match ^[a-zA-Z0-9_-]+$
)
```

**Template variables** in `user_prompt`: `{{input_data}}`, `{{output_data}}`, `{{expected_output}}`, `{{metadata.key}}` — resolved from `EvaluatorContext` fields via dot-path into nested dicts.

### Structured Output Types

**Boolean** — true/false with optional pass/fail:

```python
BooleanStructuredOutput(
    description="Whether the response is factually accurate",
    reasoning=True,                    # Include reasoning field in LLM response
    reasoning_description=None,        # Optional custom description for reasoning field
    pass_when=True,                    # True → pass when true, False → pass when false, None → no assessment
)
```

**Score** — numeric within a range with optional thresholds:

```python
ScoreStructuredOutput(
    description="Helpfulness score",
    min_score=1,                       # Minimum possible score
    max_score=10,                      # Maximum possible score
    reasoning=True,
    reasoning_description=None,
    min_threshold=7,                   # Scores >= 7 pass (optional)
    max_threshold=None,                # Scores <= N pass (optional)
)
```

**Categorical** — select from predefined categories:

```python
CategoricalStructuredOutput(
    categories={
        "correct": "The response correctly answers the question",
        "partially_correct": "The response is partially correct but missing key information",
        "incorrect": "The response is factually wrong or irrelevant",
    },
    reasoning=True,
    reasoning_description=None,
    pass_values=["correct"],           # Which categories count as passing (optional)
)
```

**Custom JSON schema** — arbitrary structured responses for multi-dimensional evals:

```python
# Pass a raw dict as structured_output — used as the JSON schema directly
structured_output={
    "type": "object",
    "properties": {
        "relevance": {"type": "boolean", "description": "Whether the response addresses the question"},
        "confidence": {"type": "number", "description": "Confidence score (0.0 to 1.0)"},
        "reasoning": {"type": "string", "description": "Explanation for the evaluation"},
    },
    "required": ["relevance", "confidence", "reasoning"],
    "additionalProperties": False,
}
```

Always write standard JSON schema — the SDK adapts it per provider automatically (e.g., Anthropic doesn't support `minimum`/`maximum` on number fields, so the SDK moves range constraints into the `description`; Vertex AI converts `const`/`anyOf` to `enum`). The full parsed JSON dict becomes the eval `value`; a `"reasoning"` key (if present) is automatically extracted. No automatic pass/fail assessment.

### LLMJudge Prompt Guidelines

The `structured_output` parameter enforces the response format via JSON schema. **Do not** prescribe the format in the prompt (no "Answer YES/NO", "Rate 1-10", etc.). Instead, describe the **evaluation criteria** and let the structured output handle the format.

- **system_prompt**: Set the judge's role and the app's domain context. Does NOT support template vars.
- **user_prompt**: Present the data via `{{input_data}}` / `{{output_data}}`, then describe what good vs. bad looks like for this dimension.

### BaseEvaluator — Custom Code-Based Evaluator

For deterministic checks that do not need LLM judgment:

```python
class MyEvaluator(BaseEvaluator):
    def __init__(self, name=None, ...custom_params...):
        super().__init__(name=name)
        self._param = ...  # Store config as private attrs

    def evaluate(self, context: EvaluatorContext) -> EvaluatorResult:
        # Access: context.input_data, context.output_data, context.expected_output, context.metadata
        # Must NOT modify self attributes (thread safety)
        passed = ...  # Your logic here
        return EvaluatorResult(
            value=passed,
            reasoning="...",
            assessment="pass" if passed else "fail",
        )
```

### Built-in Evaluators

```python
# Validate JSON syntax + optional required keys
JSONEvaluator(required_keys=["name", "age"], output_extractor=None, name=None)

# Validate length (characters, words, or lines)
LengthEvaluator(count_by="words", min_length=10, max_length=500, output_extractor=None, name=None)
# count_by: "characters" | "words" | "lines"

# String matching
StringCheckEvaluator(operation="contains", expected="success", case_sensitive=False, name=None)
# operation: "eq" | "ne" | "contains" | "icontains"

# Regex matching
RegexMatchEvaluator(pattern=r"\d{4}-\d{2}-\d{2}", match_mode="search", name=None)
# match_mode: "search" | "match" | "fullmatch"
```

### Evaluator Type Decision Matrix

| Signal | Evaluator Type |
|--------|---------------|
| Output must be valid JSON | `JSONEvaluator` |
| Output must match a regex pattern | `RegexMatchEvaluator` |
| Output has length constraints | `LengthEvaluator` |
| Output must contain/not contain specific strings | `StringCheckEvaluator` |
| Semantic quality judgment (tone, accuracy, completeness) | `LLMJudge` + `BooleanStructuredOutput` |
| Graded quality on a scale | `LLMJudge` + `ScoreStructuredOutput` |
| Classification into categories | `LLMJudge` + `CategoricalStructuredOutput` |
| Multi-dimensional judgment (evaluate several aspects at once) | `LLMJudge` + custom JSON schema `dict` |
| Complex domain logic combining multiple checks | `BaseEvaluator` subclass |

### Source Verification

If you have access to dd-trace-py locally, verify the API surface by reading the corresponding modules:

- `ddtrace.llmobs._evaluators.llm_judge` — `LLMJudge`, `BooleanStructuredOutput`, `ScoreStructuredOutput`, `CategoricalStructuredOutput`
- `ddtrace.llmobs._experiment` — `BaseEvaluator`, `EvaluatorContext`, `EvaluatorResult`
- `ddtrace.llmobs._evaluators.format` — `JSONEvaluator`, `LengthEvaluator`
- `ddtrace.llmobs._evaluators.string_matching` — `StringCheckEvaluator`, `RegexMatchEvaluator`

---

## Workflow

### Phase 0: Resolve Inputs & Entry Mode

**Entry mode detection:**

| Mode | Signal | Behavior |
|------|--------|----------|
| **Cold Start** | Only `ml_app` provided (no RCA, no hypothesis) | Full open discovery — understand what the app does, identify quality dimensions worth measuring, propose evals for coverage |
| **From RCA** | Conversation contains an RCA report or user provides a failure hypothesis | Skip open discovery — use existing failure taxonomy as eval targets |

**Parse arguments**: Extract `ml_app` (first non-flag argument), `--timeframe` (default `now-7d`), `--trace-limit` (default `20`), `--data-only`, `--publish`, and `--emit-dataset <path>` flags. Set `output_mode` as follows (exactly one must be true; error if more than one is set):

- `--emit-dataset <path>` set → `output_mode = emit_dataset`. Skip the rest of the workflow entry-mode logic and jump directly to **Phase 3D** below.
- `--publish` set → `output_mode = publish`.
- `--data-only` set → `output_mode = data_only`.
- otherwise → `output_mode = sdk_code`.

**Resolution steps:**

1. If `ml_app` not provided → ask the user.
2. Auto-detect entry mode:
   - If the conversation contains an RCA report (look for "Failure Taxonomy" heading, structured failure modes, or severity ratings) → `from_rca`. Extract the taxonomy.
   - If the user provides a free-text failure hypothesis (e.g., "the system prompt lacks grounding") → `from_rca`. Use the hypothesis as the starting eval target.
   - Otherwise → `cold_start`.
3. If `timeframe` not provided → default to `now-7d`.
4. **Map existing eval coverage** — **skip if `output_mode = data_only`** (there is no Datadog eval project to check coverage against): Call `list_llmobs_evals` (org-wide; filter the result client-side to entries where `ml_app == <ml_app>`). Then, for each eval with `source=custom`, call `get_llmobs_evaluator(eval_name=...)` to inspect its prompt template, target, sampling, and filter, and infer which quality dimension it covers. Issue all evaluator calls in a **single message** (parallelize). Skip `source=ootb` evals — their names are self-describing and they may not have a fetchable config.

   By the end of this step you have a complete coverage map: `{eval_name → source, enabled, dimension}`. Carry this into Phase 2 for deduplication.

   **In `publish` mode, also note any template-variable convention** the existing custom evaluators already use (so a new suite reads consistently). Online evaluator templates resolve against the **full span JSON**, not against `EvaluatorContext`. See the "Online Template Variables" section under "Publishing Conventions" for the supported syntax (`{{span_input}}`, `{{span_output}}`, dot-paths, array selectors, filter accessors).

5. **Notebook context detection**: Scan the current conversation for a Datadog notebook URL that was produced by `/eval-trace-rca` (pattern: `https://app.datadoghq.com/notebook/{numeric-id}`). If found, store it as `rca_notebook_url` and extract the numeric ID as `rca_notebook_id`. This is used after Phase 3 to offer appending the evaluator suite to that notebook instead of creating a new one.

---

### Phase 1: Explore Traces & Identify Eval Targets

**Goal**: Sample production traces, understand what the app does, and identify quality dimensions worth measuring.

#### Cold Start Path

1. **Sample the app**: `search_llmobs_spans(query="@ml_app:\"<ml_app>\" @status:ok", root_spans_only=true, limit=50, from=<timeframe>)`. Filter by `@status:ok` — error spans have no output to evaluate.

2. **Profile the app and identify evaluation target spans**: Call `get_llmobs_span_details` for span_ids grouped by trace_id. Inspect `content_info` to classify:

   | Signal | App Profile |
   |--------|------------|
   | `content_info` has `messages` | LLM/chat app |
   | `content_info` has `documents` | RAG app |
   | Spans include `agent` kind | Agent app |
   | `content_info` has `metadata` | Has custom metadata |
   | Multiple span kinds in one trace (`agent` + `tool` / `retrieval` + `llm` from `get_llmobs_trace`) | Multi-step app — at least one trace-scope evaluator likely belongs in the suite (`publish` mode) |

   For agent/multi-step apps, also call `get_llmobs_trace` on 2-3 traces to see the full span hierarchy. Compare `content_info` between the root span and its sub-spans. Then ask **two** questions for each candidate quality dimension, in this order:

   1. **Does the verdict depend on more than one span?** (e.g., faithfulness depends on a `retrieval` span's documents AND an `llm` span's answer; goal completion depends on the chain of `tool` calls AND the final response.) If yes → **trace scope** in `publish` mode. Don't try to compress this into a single span.
   2. **Only if the answer to (1) is no**: pick the single span with the richest signal for that dimension (root has the summary; LLM sub-spans have the full system prompt + tool call results + reasoning chain).

   Record the span-kind histogram (agent + tool + llm + retrieval) — multiple kinds under one root is a strong signal you'll have at least one trace-scope evaluator in the suite. See Phase 2's "Span vs. Trace Scope Classification" for the mandatory walk-through of canonical trace-scope use cases.

3. **Extract content and identify targets**: Call `get_llmobs_span_content` for representative spans. Fetch fields based on app profile:

   | App Profile | Fields to Fetch |
   |------------|----------------|
   | LLM/chat | `messages` (`path=$.messages[0]` for system prompt), `output` |
   | RAG | `documents`, `input`, `output` |
   | Agent | `get_llmobs_agent_loop` for the agent span, then `messages` for detail |
   | Any with metadata | `metadata` |

   Issue all calls in a single message. As you read, capture two streams of signal:

   **Generic quality signals** — what does "success" look like? What variance exists across outputs? Each observed quality dimension becomes a candidate evaluator, with the traces you've just read as evidence. Also look for safety signals (scope violations, sensitive data in outputs, out-of-character responses) and add a safety evaluator if you find them.

   **Domain signals** — these become the *domain-specific evaluator* category in Phase 2 (the highest-leverage category). For every 5–10 traces, write down:
   - **Recurring intents / question categories** — what classes of request does this app handle? (`applying for benefit X`, `comparing flight options`, `summarizing a policy`, `creating a widget`)
   - **Entities the app emits in outputs** — URLs, agency / company names, code identifiers, monetary amounts, dates, IDs, file paths, phone numbers. Note which ones the user *acts* on downstream (those are worth a correctness evaluator) versus which are passing references.
   - **Tool argument shapes** (for agent apps) — name each tool the agent calls and the rough schema of its inputs. Tools with non-trivial schemas (≥ 3 fields, structured types) are candidates for argument-correctness evaluators.
   - **Persona / voice rules** — does the app always cite a source, always refuse certain topics (medical, legal, financial advice), always speak in a particular tone? Extract the rules implicitly followed across observed outputs.
   - **Failure modes specific to the domain** — fabricated identifiers, outdated policy references, currency / locale mismatches, off-by-one errors in IDs, wrong units. One observed instance is enough to seed a candidate evaluator.

   Don't try to enumerate domain signals exhaustively before reading traces — let the patterns surface as you read. The goal is breadth in the eventual proposal, not completeness in this exploration step.

#### From RCA Path

1. Extract the failure taxonomy from the RCA report. Each failure mode with High or Medium severity becomes an eval target.

2. **Check root cause categories for infrastructure failures.** Before proposing evaluators, scan the Root Cause column of the taxonomy for any of: `Instrumentation Deficiency`, `Harness Deficiency`, `Runtime Error`, `Upstream Data Issue`, or any other root cause that points to infrastructure/environment rather than model behavior. If any are present, pause and ask:

   > "Some failure modes were diagnosed as infrastructure or instrumentation issues rather than model behavior (e.g., `{list the infra root causes}`). Evaluators can be designed two ways:
   > - **Behavior-targeted** (recommended for ongoing quality): measure whether the model produces correct, specific output — useful once the infrastructure is fixed and you want to track real quality
   > - **Artifact-targeted** (useful as regression guard): detect the specific broken output observed (e.g., generic placeholder responses) — catches regressions if the infrastructure breaks again
   >
   > Which approach do you want, or both?"

   - If **behavior-targeted**: design evaluators for what correct output looks like, not what the broken output looked like. Use the RCA's `expected_output` / gold-standard examples as the quality bar.
   - If **artifact-targeted**: design evaluators that detect the specific failure symptom (e.g., `StringCheckEvaluator` for a known bad string, `LLMJudge` that checks for generic placeholders).
   - If **both**: propose each category separately, clearly labelled.

   If all root causes are behavioral (System Prompt Deficiency, Tool Gap, Tool Misuse, Retrieval Failure, etc.) → skip this step and proceed directly.

3. For each target: if the RCA includes trace IDs, use them directly; otherwise search for matching traces. Fetch 2-3 traces per target with `get_llmobs_span_content` to understand the concrete pattern.

---

### Phase 2: Propose Evaluator Suite

**Goal**: Present a concrete evaluator proposal for user confirmation.

Each evaluator judges **one data point** — it receives input and output for a single record/span, not a full trace or batch. Design evaluators accordingly.

**Targeting depends on `output_mode`:**

- `sdk_code` / `data_only` → **offline experiments**. Template variables use `EvaluatorContext` fields (`{{input_data}}`, `{{output_data}}`). The actual data shape depends on the user's dataset and task function (see EvaluatorContext note in SDK Reference).
- `publish` → **online evaluation on production spans**. Template variables resolve against the **full span JSON** via dot-paths (`{{meta.input.value}}`, `{{meta.output.messages[*].content}}`, …) or the built-in span-kind-aware aliases (`{{span_input}}`, `{{span_output}}`). See "Online Template Variables" under Publishing Conventions for the full syntax. Each evaluator also needs `eval_scope`, `sampling_percentage`, and (optionally) `filter` — surface these in the proposal table so the user can confirm before publishing.

Order proposals from broadest signal to most granular. **Propose broadly, let the user curate** — see "How many evaluators to propose" below.

1. **Domain-specific evaluators** — What does "good" mean *for this specific app*? These are the highest-leverage proposals because they capture quality bars generic evaluators miss. Derive them from the **domain signals** Phase 1 captured:
   - **Recurring intents / question categories** the app handles (e.g., "applying for a federal benefit", "comparing flight options", "explaining a policy"). Propose an `intent_classification` or `intent_handling_correctness` evaluator scoped to the dominant intents.
   - **Specific entities the app produces** (URLs, agency names, code identifiers, monetary amounts, dates, IDs). Propose a per-entity correctness evaluator for the ones with real downstream cost when wrong (e.g., `cited_url_is_real`, `agency_name_matches_request`, `monetary_amount_is_consistent_with_input`).
   - **Tool argument shapes** observed across `tool` spans. Propose a per-tool argument-correctness evaluator for the tools with non-trivial schemas (e.g., `search_flights_args_match_user_request`, `update_dashboard_widget_targets_correct_widget`).
   - **Persona / voice expectations** — does the app always cite sources, always refuse out-of-scope requests, always speak in a specific tone? Propose evaluators for the voice rules you can extract from observed outputs (`cites_a_source`, `refuses_medical_advice`, `tone_matches_brand`).
   - **Domain-specific failure modes** seen across traces (fabricated identifiers, outdated policy references, unit mismatches, currency / locale mismatches). One evaluator per recurring failure mode.

   Name each evaluator after the *user-facing concern*, not the technical check (`agency_url_is_real` over `regex_url_match`). Use the trace IDs you read in Phase 1 as evidence — at least one passing case and one failing case per evaluator if you saw both.

2. **Outcome evaluators** — Did this span / trace produce a good result for the request?
   - Examples: `task_completion`, `answer_correctness`, `response_groundedness`
3. **Format evaluators** — Does the output meet structural requirements?
   - Examples: `valid_json_output`, `response_length`, `citation_format`
4. **Safety evaluators** — Does the output stay within appropriate boundaries?
   - Examples: `no_pii_leakage`, `scope_adherence`, `no_hallucination`

##### How many evaluators to propose

The default `4-6` cap from the older skill version was too tight — it pushed the skill toward generic evaluators only and left domain signals on the table. Updated guidance:

- **Aim for 8–15 evaluators** in the proposal, distributed across all four categories (with domain-specific usually the largest bucket, outcome second, format and safety smaller). For very simple single-LLM-call apps, fewer is fine; for agent / RAG apps with rich domain signals, lean toward the upper end.
- **Quality > generic**: every domain-specific proposal should be backed by at least one observed pattern in the sampled traces. Don't invent generic domain evaluators ("`response_quality`") if you don't have evidence for them.
- **Let the user curate**: the MANDATORY CHECKPOINT below explicitly asks the user to **remove** what doesn't apply, not just to approve. Treat the proposal as a candidate set the user trims.

#### Deduplication Against Existing Coverage

**In `data_only` mode**: skip this section entirely (coverage map was not built in Phase 0). Proceed directly to the proposal table.

Before building the proposal, apply the coverage map from Phase 0. **Coverage is keyed on `(dimension, scope)` — not on dimension alone**: every OOTB evaluator runs at span scope, and an enabled OOTB eval does NOT preclude proposing a trace-scope evaluator for the same dimension. The two answer different questions.

1. **Enabled span-scope eval (OOTB or custom)** for dimension D:
   - Do NOT propose a new **span-scope** evaluator for D — that dimension is already covered at span scope.
   - DO propose a **trace-scope** evaluator for D when the trace shape calls for it (multi-step app, judgment depends on cross-span context). Note the relationship in the rationale: e.g., "OOTB `Goal Completeness` evaluates each LLM span in isolation; this trace-scope `goal_completion` checks whether the agent's full sequence of steps achieved the user's request — different question."

2. **Enabled trace-scope custom eval** for dimension D: do NOT propose another trace-scope evaluator for the same dimension; that's a real duplicate. Span-scope on the same dimension is still fair game if the data also fits a single span.

3. **Disabled OOTB eval**: Do NOT propose a new custom span-scope evaluator for that dimension. Instead, surface it in a short note within the proposal and suggest enabling it in the Datadog UI rather than creating a duplicate. Example:

   > `hallucination` (ootb, disabled) — consider enabling in Datadog UI (Evaluations → Configure) instead of creating a custom span-scope eval. (A trace-scope `rag_faithfulness` is still in scope and covers a different question.)

4. **Gap identification**: Open the proposal with a coverage summary line: "Existing coverage: N evaluator(s) already configured ({names}, all span-scope unless noted). Proposing evaluators for uncovered dimensions and uncovered scopes."

5. **All dimensions covered**: A dimension is "fully covered" only when **both** scopes are present (or the scope doesn't apply to the app shape). If the coverage map accounts for every identified quality dimension at the appropriate scope(s), surface this explicitly and ask the user what they want: (a) review/improve existing eval prompts, (b) add coverage for additional dimensions, or (c) proceed anyway.

For each proposed evaluator:

- **Name**: Must match `^[a-zA-Z0-9_-]+$` (alphanumeric, underscore, hyphen only)
- **Type**: `LLMJudge` (Boolean/Score/Categorical/custom JSON schema), built-in (`JSONEvaluator`, `RegexMatchEvaluator`, etc.), or `BaseEvaluator` subclass. *In `publish` mode, only LLM-judge evaluators are supported by the MCP tool — code-based checks must NOT be silently dropped. List them in the same proposal table with `Type` set to the code-based class, mark them under a "Not publishable in this mode" subsection of the proposal, and tell the user to run the skill again in default `sdk_code` mode (or `--data-only`) to capture them. Treat the code-based proposals as part of the suite for counting and coverage purposes.*
- **What it measures**: 1-2 sentence plain-language description
- **Target span**: Which span's data the evaluator was designed for (e.g., "root agent span", "LLM sub-span `anthropic.request`", "all `llm` spans"). If the root span's I/O is too lossy for the quality dimension (e.g., tool call results aren't visible), note this and specify which sub-span has the signal. *In `publish` mode this maps to a combination of `eval_scope` (`span`/`trace`/`session`), `root_spans_only`, and the EVP `filter` query (e.g. `@meta.span.kind:llm` or `service:web`).*
- **Pass/fail criteria**: `pass_when=True`, `min_threshold=7`, `pass_values=["correct"]`, or "no automatic assessment" for custom JSON schema
- **Template variables**: Which of `input_data`, `output_data`, `expected_output`, `metadata.*` it uses (offline) — or which span paths / aliases it pulls from (publish mode: `{{span_input}}`, `{{span_output}}`, `{{meta.input.messages[*].content}}`, `{{meta.metadata.<key>}}`, etc.)
- **Evidence**: At least one trace where it would have caught a failure (or confirmed correct behavior)
- **Publish-only fields** *(only in `publish` mode)*: `integration_provider` (default `openai`), `model_name` (default `gpt-5.4-mini`), `sampling_percentage` (default `10`), `eval_scope` (default `span`), and any `filter` query needed to scope to the right spans. Surface defaults in the proposal so the user can override before publishing.
- **`integration_account_id`** *(only in `publish` mode)*: the integration account the judge LLM is called through. Auto-detected from existing evaluators in the same ml_app (Phase 0 coverage map). Never asked from the user as a raw UUID. If no existing evaluator has one, the field is omitted and the user picks an account in the UI before activating. **All evaluators are published with `enabled: false` regardless** — see "Always publish as draft" in Phase 3C for the full activation workflow.

#### Span vs. Trace Scope Classification (`publish` mode)

**Don't ask the user; classify per evaluator and let them override at the checkpoint.**

##### Mandatory: walk the four canonical trace-scope use cases first

If Phase 1 found multi-step traces (≥ 2 span kinds, or any `tool` / `retrieval` / `workflow` span under an `agent` root), you **MUST** walk through the four canonical trace-scope use cases below before finalizing the suite. For each, decide explicitly: **applies** (include with `eval_scope: trace`) or **does not apply** (record a one-line reason in a "Skipped trace-scope candidates" subsection of the proposal). Skipping all four without per-item justification is a sign you've over-anchored on span scope — re-check.

| Canonical use case | Triggers when |
|---|---|
| `goal_completion` — did the agent finish the user's request? | Any agent / multi-step app. Almost always applies. |
| `tool_use_correctness` — right tool with right arguments? | Trace contains `tool` kind spans. |
| `rag_faithfulness` — answer grounded in retrieved documents? | Trace contains `retrieval` kind spans. |
| `conversation_quality` — coherence across multi-turn LLM calls? | Trace contains ≥ 2 `llm` spans, or app instruments multi-turn sessions. |

For other proposed evaluators (e.g. tone, format, safety), apply this two-question test:

1. Can the judgment be answered correctly from **one** span's `meta.input` + `meta.output`, where "correctly" means the verdict cannot change if you considered other spans in the trace? → **`eval_scope: span`**.
2. Otherwise → **`eval_scope: trace`**. In particular, default to trace when the evaluator name contains *grounding*, *faithfulness*, *hallucination*, *completeness*, *correctness across steps*, *consistency*, or *workflow* — these almost always need cross-span context.

##### Trade-offs (don't let these dominate the choice)

Trace scope costs more than span scope: one judgment per **completed** trace (vs. per matching span), larger prompt payloads, and a 3-minute trigger latency (Datadog waits 3 minutes of inactivity before considering a trace complete; later spans are excluded). These are **cost-control** levers — handle with `sampling_percentage` and `filter`, not by demoting scope. The *correctness* of the eval is what picks the scope.

##### Surface the classification

Add a **Scope** column to the proposal table and a one-sentence rationale per evaluator. If you skipped a canonical trace-scope use case, list it under a "Skipped trace-scope candidates" subsection with the reason — the user will see and can override.

> Example rationales:
> - `tone_check` — **span**. Judging "is this single response polite" needs only one LLM span's `meta.output.messages[*].content`; no other span in the trace can change that verdict.
> - `goal_completion` — **trace**. Whether the agent finished the user's request depends on the sequence of tool calls and the final LLM response together — `meta.output` of any single span only shows that step's output.
> - `tool_use_correctness` — **trace**. Comparing tool inputs against the request and the final response requires correlating ≥ 3 spans (root, tool, final LLM).
> - `rag_faithfulness` — **trace**. Grounding pairs the `retrieval` span's documents with the LLM span's answer.
>
> Example "Skipped trace-scope candidates" entry:
> - `conversation_quality` — skipped: traces contain a single LLM call (no multi-turn signal in this app's instrumentation).

#### MANDATORY CHECKPOINT

**You MUST output the proposal and wait for user confirmation before proceeding.**

```
## Proposed Evaluator Suite

**App profile**: {LLM | RAG | Agent | Multi-agent}
**Entry mode**: {cold_start | from_rca}

| # | Name | Type | Scope | Measures | Pass Criteria |
|---|------|------|-------|----------|---------------|
| 1 | task_completion | LLMJudge (Boolean) | span | Whether the task was completed on this span | pass_when=True |
| 2 | tool_use_correctness | LLMJudge (Categorical) | trace | Right tool with right arguments across the agent run | pass_values=["correct"] |
| 3 | ... | ... | ... | ... | ... |

(Drop the **Scope** column when not in `publish` mode.)

For each evaluator:
- **{name}**: {what it measures}
  - Target span: {which span's data it was designed for}
  - Rationale: {which quality dimension it covers and why}
  - {Only in publish mode:} Scope: {span | trace} — {one-sentence rationale}
  - Evidence: [Trace {id_short}](https://app.datadoghq.com/llm/traces?query=trace_id:{full_id})

{Only in publish mode, for multi-step apps. Required if any of the four canonical trace-scope use cases was not included above:}

**Skipped trace-scope candidates:**
- `{canonical_use_case}` — {one-line reason it does not apply to this app}

{Only in publish mode, when the suite contains code-based evaluators (JSONEvaluator, RegexMatchEvaluator, LengthEvaluator, StringCheckEvaluator, BaseEvaluator). Required when any code-based proposal exists.}

**Not publishable in this mode** (code-based evaluators — the publish API is LLM-judge only):
- `{name}` ({type}) — {what it would check}. Re-run `/eval-bootstrap {ml_app}` in default mode to emit as offline SDK code, or `/eval-bootstrap {ml_app} --data-only` for a framework-agnostic JSON spec.
```

**Which evaluators should I generate?** Treat the proposal as a candidate set — the suite below is intentionally broad so you can pick what matters for your team's quality bar. Reply with **which to keep, which to drop, and which to rename**; not every domain-specific proposal will fit your priorities. In `sdk_code` mode you may also add custom evaluators or change provider/model. In `publish` mode you may override `integration_provider`, `model_name`, `sampling_percentage`, `eval_scope`, `root_spans_only`, or `filter` per evaluator.

Do NOT proceed to code generation until the user confirms.

---

### Phase 3: Generate Output

Branch on `output_mode`:

- `sdk_code` → **Phase 3A** below
- `data_only` → skip to **Phase 3B**
- `publish` → skip to **Phase 3C**
- `emit_dataset` → skip to **Phase 3D** (Phases 0 step 4, 1, and 2 are bypassed — see Phase 3D for the dataset-mode workflow)

---

### Phase 3A: Generate & Write Evaluator Code

**Goal**: Generate the final `.py` file and write it to disk.

For each confirmed evaluator, generate production-quality Python code following the SDK Reference patterns above.

#### Code Generation Rules

1. **Ground prompts in traces**: LLMJudge system prompts and user prompts must reference patterns actually observed in production traces. Never write generic prompts like "evaluate whether the response is good" — ground them in the app's domain, observed failure patterns, and success criteria.

2. **Keep template variables generic, add comments for context**: Use `{{input_data}}` and `{{output_data}}` as top-level placeholders in prompts — do NOT reference nested span paths like `{{input_data.messages[-1].content}}`. The evaluator's data comes from the user's dataset and task function, not directly from spans. Instead, add a comment above each evaluator describing what data it was designed for and what the user should adapt:

   ```python
   # Designed for: input_data = user query, output_data = assistant response text
   # Observed from: root agent span (input.value → output.value)
   # If your dataset uses a different structure, adapt the prompt references below.
   ```

3. **Use the narrowest evaluator type**: If a check can be done with `JSONEvaluator`, `RegexMatchEvaluator`, `StringCheckEvaluator`, or `LengthEvaluator`, do NOT use an LLMJudge. Code-based evaluators are faster, cheaper, and deterministic.

4. **BaseEvaluator subclasses**:
   - Call `super().__init__(name=name)` in `__init__`
   - Return `EvaluatorResult` from `evaluate()`
   - Do NOT modify instance attributes in `evaluate()` (thread safety)

5. **Names**: Must match `^[a-zA-Z0-9_-]+$`. Use snake_case descriptive names.

6. **Imports**: Consolidate at the top of the file. Only import classes that are actually used.

7. **Evaluator list**: Collect all evaluators into an `evaluators` list at the bottom of the file.

8. **Anonymize PII**: Strip emails, names, and sensitive data from any trace content included in LLMJudge prompts or the header comment.

#### Output Format

The generated `.py` file should follow this structure:

```python
"""
Auto-generated evaluators for {ml_app}
Generated: {YYYY-MM-DD} by eval-bootstrap

App profile: {LLM | RAG | Agent | Multi-agent}

Quality dimensions covered:
  - {target_name}: {description}
    Evidence: https://app.datadoghq.com/llm/traces?query=trace_id:{full_id}
  ...

Usage:
    from ddtrace.llmobs import LLMObs

    experiment = LLMObs.experiment(
        name="my-experiment",
        task=my_task_fn,
        dataset=dataset,
        evaluators=evaluators,
    )
    experiment.run()
"""

{imports — only what is used}


# --- Outcome Evaluators ---

{evaluator code}


# --- Format Evaluators ---

{evaluator code}


# --- Safety Evaluators ---

{evaluator code}


# --- Evaluator Suite ---

evaluators = [
    {eval_1_variable_name},
    {eval_2_variable_name},
    ...
]
```

Only include section comments (Outcome/Format/Safety) for categories that have evaluators.

#### Write the file

Write the generated code to the output path (suggest `./evals/{ml_app}_evaluators.py` if not specified), then display a summary:

```
## Generated Evaluators

Wrote {N} evaluators to `{output_path}`:

| # | Name | Type | Covers |
|---|------|------|--------|
| 1 | ... | ... | ... |

### Next Steps

1. **Review**: Check the generated prompts and criteria match your expectations
2. **Test offline**: Use `LLMObs.experiment(evaluators=evaluators)` to batch-evaluate against a labeled dataset and verify scores
```

#### Notebook export (after summary)

After displaying the summary, offer notebook export.

- **If `rca_notebook_url` was detected in Phase 0**:
  > An RCA notebook was created earlier in this session: `{rca_notebook_url}`
  > Would you like to (a) append the evaluator suite summary to that notebook, or (b) create a new standalone notebook?

  If **append**: use the notebook creation fallback pattern (see below) with `mcp__datadog-mcp__edit_datadog_notebook` (`id={rca_notebook_id}`, `append_only=true`, evaluator suite summary cell).

  If **new**: use the notebook creation fallback pattern (see below) with `mcp__datadog-mcp__create_datadog_notebook`.

- **If no `rca_notebook_url`**:
  > Would you like to export this evaluator suite summary to a Datadog notebook?

  If yes: use the notebook creation fallback pattern (see below) with `mcp__datadog-mcp__create_datadog_notebook`:
  - **`name`**: `Eval Bootstrap: {ml_app} — YYYY-MM-DD`
  - **`type`**: `report`
  - **`cells`**: single markdown cell with the evaluator suite summary
  - **`time`**: `{ "live_span": "1h" }`

**Notebook creation fallback pattern** (apply to every `create_datadog_notebook` / `edit_datadog_notebook` call):

1. Try the MCP tool first.
2. **If the MCP call fails**, inspect the error:
   - **Auth / permission error (401, 403)** → stop and tell the user.
   - **Field validation error** (error names a specific field) → fix that field and retry the MCP call once.
   - **Any other error** (binding, serialization, unexpected response) → fall back to pup:
     - Write the payload to `/tmp/nb_bootstrap_{ml_app}.json` as a full API envelope: `{"data": {"attributes": {"name": "...", "time": {...}, "cells": [...]}, "type": "notebooks"}}`
     - Run `pup notebooks create --file /tmp/nb_bootstrap_{ml_app}.json`
     - If pup is not available either, render the notebook content as markdown in chat.
3. After successful creation by either method, output the URL:
   `Evaluator suite exported to notebook: <url>`

**Notebook cell content** — the markdown cell should contain:

```markdown
## Eval Bootstrap: {ml_app}

**Generated**: YYYY-MM-DD | **App profile**: {LLM | RAG | Agent | Multi-agent} | **Entry mode**: {cold_start | from_rca}
**Generated code**: `{output_path}`

{One sentence: what does this app do?}

**Coverage**: {N} new evaluators ({comma-separated dimension names}) | {N} existing (unchanged: {names}) | {gaps if any: dimensions identified but not covered, and why}

### Evaluator Suite

| # | Name | Type | Measures | Pass Criteria |
|---|------|------|----------|---------------|
| 1 | ... | ... | ... | ... |

### Evidence

{For each evaluator: name — 1-line description — [Trace link]}

### Next Steps

1. Review generated prompts in `{output_path}`
2. Run against a labeled dataset to validate scores
3. Deploy to Datadog LLM Experiments
```

---

### Phase 3B: Generate & Write Eval Spec JSON

**Goal**: Serialize the confirmed evaluator suite and representative trace samples to a single self-contained JSON file — zero SDK dependencies.

**Output path**: `./evals/{ml_app}_eval_spec.json`

#### JSON Schema

```json
{
  "schema_version": "1",
  "generated_at": "<ISO 8601 UTC>",
  "generated_by": "eval-bootstrap",
  "app": {
    "ml_app": "<string>",
    "app_type": "LLM | RAG | Agent | Multi-agent",
    "trace_window": "<timeframe param, e.g. now-7d>",
    "trace_count": "<integer>"
  },
  "evaluators": [
    {
      "name": "snake_case_name",
      "category": "outcome | format | safety",
      "type": "llm_judge | code_check",
      "description": "<1-2 sentence plain-language description>",
      "target_span": "<which span: root, llm sub-span, etc.>",
      "scoring": {
        "scale": "boolean | score_1_10 | categorical",
        "categories": ["<only present when scale=categorical>"],
        "pass_criteria": "<human-readable: true, >= 7, in [correct], etc.>"
      },
      "rubric": "<full prompt text for llm_judge; null for code_check>",
      "implementation_hints": {
        "type_if_code_check": "json_valid | regex | contains | length_words | null",
        "pattern_if_code_check": "<pattern string or null>",
        "notes": "<optional framework-agnostic implementation guidance>"
      },
      "evidence": [
        {
          "trace_id": "<32-char hex>",
          "span_id": "<16-char hex>",
          "url": "https://app.datadoghq.com/llm/traces?query=trace_id:<trace_id>",
          "observation": "<why this trace illustrates the evaluator>"
        }
      ]
    }
  ],
  "sample_records": [
    {
      "trace_id": "<string>",
      "span_id": "<string>",
      "input": {},
      "output": "<string>",
      "suggested_labels": {
        "<evaluator_name>": "pass | fail | <score>"
      }
    }
  ]
}
```

#### Field Notes

- **`evaluators[].type`**: `"llm_judge"` for semantic evaluators; `"code_check"` for deterministic checks (regex, length, JSON validity, etc.).
- **`evaluators[].rubric`**: For `llm_judge` — full prompt text grounded in observed trace patterns. Use `{{input}}` and `{{output}}` as generic placeholders (not `{{input_data}}` — that's ddeval-specific). For `code_check` — null.
- **`evaluators[].implementation_hints.notes`**: Optional framework-agnostic guidance, e.g. "For OpenAI Evals, use `rubric` as a model-graded criterion. For Braintrust, use as an LLM scorer. For Promptfoo, use as an `llm-rubric` assertion."
- **`sample_records`**: 10–20 representative traces from Phase 1. `suggested_labels` are Claude's best-read from trace inspection — not ground truth. The field name communicates this explicitly.
- **PII rule**: Strip emails, names, and sensitive data from all `input`, `output`, and `evidence[].observation` fields before writing (same as Phase 3A).

#### Writing Instructions

1. Assemble the JSON object in memory following the schema above.
2. Populate `sample_records` from traces already fetched in Phase 1. Fetch additional traces (up to 20 total) if fewer than 10 were read.
3. Anonymize PII in all `input`, `output`, and `evidence[].observation` fields.
4. Write the file with 2-space indentation using the Write tool.
5. Display a completion summary:

```
## Generated Eval Spec

Wrote `./evals/{ml_app}_eval_spec.json`:

- **{N} evaluators** ({outcome_count} outcome, {format_count} format, {safety_count} safety)
- **{M} sample records** with suggested labels

| # | Name | Category | Type | Pass Criteria |
|---|------|----------|------|---------------|
| 1 | ... | ... | ... | ... |

### Next Steps

1. **Review**: Open `./evals/{ml_app}_eval_spec.json` and verify the rubrics match your expectations
2. **Implement**: Use the `rubric` field to configure evaluators in your framework of choice:
   - OpenAI Evals: use `rubric` as a model-graded criterion
   - Braintrust: create an LLM scorer with the rubric text
   - Promptfoo: use as an `llm-rubric` assertion
   - Custom code: call your LLM API with the rubric and parse the structured output
3. **Label**: `suggested_labels` are Claude's best guesses from trace inspection — verify against ground truth before using as training data
```

#### Notebook export (after summary)

Same logic as Phase 3A — offer to append to the RCA notebook if `rca_notebook_url` was detected, or create a new standalone notebook. Use the same notebook cell format as Phase 3A, substituting `output_path` with the JSON spec file path. In pup mode, use `pup notebooks create` / `pup notebooks edit` as described in Phase 3A.

---

### Phase 3C: Publish Online Evaluators to Datadog

**Goal**: For each confirmed evaluator, write an LLM-judge configuration to Datadog via `create_or_update_llmobs_evaluator` so it runs automatically on matching production spans.

#### Pre-publish checks (single message — parallelize)

For every proposed `eval_name`, call `get_llmobs_evaluator(eval_name=...)`:

- **Not found** → safe to create.
- **Found** → existing evaluator with the same name. Surface a diff to the user (existing dimension/prompt vs. proposed) and ask:
  > Evaluator `{name}` already exists. Overwrite, rename, or skip?

  If **overwrite**: keep the fetched config as the base and **merge** your generated fields on top, then send the **complete** object back. The MCP tool is full-replace — any field you omit (e.g. `temperature`, `max_tokens`, `filter`, `sampling_percentage`) reverts to its default. Never re-publish without round-tripping the existing config.

  If **rename**: append a suffix (e.g. `_v2`) and treat as new.

  If **skip**: drop from the publish set.

#### Publishing Conventions

**Required parameters** for each `create_or_update_llmobs_evaluator` call: `eval_name`, `application_name` (= `ml_app`), `enabled`, `integration_provider`, `model_name`, `prompt_template`, `parsing_type`, `output_schema`, plus a `telemetry.intent` string.

**Defaults** to use unless the user overrides:

| Field | Default |
|-------|---------|
| `enabled` | `false` (always — see "Always publish as draft") |
| `integration_provider` | `openai` |
| `model_name` | `gpt-5.4-mini` |
| `temperature` | `0` |
| `parsing_type` | `structured_output` |
| `sampling_percentage` | `10` for span scope, `5` for trace scope |
| `eval_scope` | `span` (auto-promoted to `trace` per the classification rule in Phase 2) |

**Prompt template**: convert the LLMJudge prompt into the MCP shape — an ordered array of `{role, content}` messages. The system prompt becomes `{role: "system"}`, the user prompt becomes `{role: "user"}`. Use **span-data placeholders** (see below) — **not** the offline `{{input_data}}` / `{{output_data}}` form, which only exists in `EvaluatorContext`.

##### Online Template Variables

Online evaluator prompts run through the dd-source `template` library (`domains/ml-observability/shared/libs/template`). Missing paths → empty string. **The data shape templates resolve against depends on `eval_scope`:**

- **`eval_scope: span`** *(default)* — placeholders resolve against a **single span's JSON** (the `llmobs.Span` JSON-marshaled to a map). Use the span aliases / dot-paths below directly.
- **`eval_scope: trace`** — placeholders resolve against the **trace payload** `{ spans: [...] }`. Use `{{spans[N]...}}`, `{{spans[*]...}}`, or `{{spans[field.path:value]...}}` to select span(s) before applying field paths. The `{{span_input}}` / `{{span_output}}` aliases are **not available** in trace scope — reference span data through the `spans` array instead.
- **`eval_scope: session`** — not supported by this skill; classify as `span` and surface the limitation to the user.

###### Span-scope (`eval_scope: span`)

**Built-in span-kind-aware aliases** (preferred when the evaluator is generic across span kinds):

| Alias | LLM span (`meta.span.kind = "llm"`) | Other spans (agent, workflow, task, …) |
|-------|------------------------------------|----------------------------------------|
| `{{span_input}}`  | `meta.input.messages[*].content`  | `meta.input.value`  |
| `{{span_output}}` | `meta.output.messages[*].content` | `meta.output.value` |

**Common explicit dot-paths** (use when the evaluator is purpose-built for one span kind):

| Path | What you get |
|------|--------------|
| `{{meta.input.value}}` / `{{meta.output.value}}` | Plain string I/O on agent / workflow / task / tool spans |
| `{{meta.input.messages[*].content}}` | All input message contents on an LLM span (newline-joined) |
| `{{meta.input.messages[0].content}}` | First message (typically system prompt) |
| `{{meta.output.messages[*].content}}` | Assistant response(s) |
| `{{meta.input.documents}}` | Retrieved docs (RAG) — JSON-serialized |
| `{{meta.metadata.<key>}}` | Custom metadata fields |
| `{{meta.tool_definitions}}` | Available tools — JSON array |
| `{{*}}` | Entire span as compact JSON (debug / fall-back catch-all) |

###### Trace-scope (`eval_scope: trace`)

| Pattern | What you get |
|---------|--------------|
| `{{spans}}` | JSON of every span in the trace |
| `{{spans[N].meta.input.value}}` | Single span by index — `spans[0]` is the trace root |
| `{{spans[*].name}}` | All span names in order, newline-joined |
| `{{spans[*].meta.output.value}}` | All spans' outputs, newline-joined (handy for "final answer = last output") |
| `{{spans[name:my-span].meta.input.value}}` | Filter by span name |
| `{{spans[meta.span.kind:llm].meta.output.value}}` | All LLM-kind span outputs |
| `{{spans[meta.span.kind:tool]}}` | Whole tool spans as JSON, paired in/out — useful for tool-use correctness |
| `{{spans[meta.span.kind:retrieval].meta.output.documents[*].text}}` | Text of every retrieved document — useful for RAG faithfulness |
| `{{*}}` | Entire trace payload as JSON (debug fallback) |

###### Array selector syntax (applies to both scopes)

- `[N]` — index (0-based)
- `[START,END]` — inclusive range, `END` is clamped to slice length
- `[*]` — wildcard (fan-out over all elements)
- `[field.path:value]` — filter array elements by a nested field equality, e.g. `messages[role:user]` or `spans[meta.span.kind:tool]`

**Resolution rules to keep in mind when writing prompts:**
- Arrays of strings → newline-joined
- Arrays of objects / mixed values → compact JSON
- Single empty slice → empty string
- Implicit fan-out: `messages.content` behaves the same as `messages[*].content`
- Negative indices are **not** supported (parse error) — use `[N]` with a known index, or `[*]` for "last assistant turn" semantics

**When to pick which form:**
- **Generic span evaluator** (e.g. `tone_check`, `output_format`) → use `{{span_input}}` / `{{span_output}}` so it works across span kinds.
- **LLM-span-specific evaluator** (e.g. `system_prompt_adherence`) → reach for explicit `meta.input.messages[*].content` / `meta.output.messages[*].content` so you can split system vs. user vs. assistant turns.
- **Span-scope RAG evaluator** (single retrieval+generation span) → combine `{{meta.input.documents}}` with `{{span_output}}`.
- **Trace-scope evaluator** → see "Trace-scope evaluator examples" below for the four canonical patterns (goal completion, tool-use correctness, RAG faithfulness, conversation quality).
- **Metadata-aware evaluator** → reference `{{meta.metadata.<key>}}` directly.

If the user has existing custom evaluators in the same ml_app (Phase 0 coverage map), match their convention when there is no strong reason to deviate.

###### Trace-scope evaluator examples

Concrete user-prompt bodies for the four canonical trace-scope use cases, drawn from the public docs ([Trace-Level Evaluations](https://docs.datadoghq.com/llm_observability/evaluations/custom_llm_as_a_judge_evaluations/trace_level_evaluations/)). Each goes alongside a static System prompt that describes the rubric (no placeholders).

| Use case | `filter` | User prompt body |
|---|---|---|
| **Goal completion** — agent finished the user's request | `@parent_id:undefined @meta.span.kind:agent` | `User goal:\n{{spans[0].meta.input.value}}\n\nAgent steps:\n{{spans}}` |
| **Tool-use correctness** — right tool with right arguments | `@parent_id:undefined @meta.span.kind:agent` | `User question:\n{{spans[0].meta.input.value}}\n\nTool calls:\n{{spans[meta.span.kind:tool].meta.input.parameters}}\n\nFinal response:\n{{spans[*].meta.output.value}}` |
| **RAG faithfulness** — answer grounded in retrieved docs | `@parent_id:undefined` | `Retrieved context:\n{{spans[meta.span.kind:retrieval].meta.output.documents[*].text}}\n\nFinal answer:\n{{spans[meta.span.kind:llm].meta.output.value}}` |
| **Conversation quality** — coherence and consistency across turns | `@parent_id:undefined` | `Conversation:\n{{spans[meta.span.kind:llm].meta.input.messages[*].content}}\n\nAssistant responses:\n{{spans[meta.span.kind:llm].meta.output.messages[*].content}}` |

Use these as starting points. Adapt the `filter` and span paths to the actual span names / kinds the app emits (observed during Phase 1).

**`output_schema` wrapper format (required for all providers)**

The `output_schema` field is NOT a bare JSON Schema. It must use the OpenAI `json_schema` object shape. **`name` is a fixed type discriminator**, not the evaluator name — the UI validates it against a strict allowlist and rejects any other value:

| LLMJudge type | `name` value | property key inside `schema` |
|---------------|-------------|------------------------------|
| Boolean | `"boolean_eval"` | `boolean_eval` |
| Score | `"score_eval"` | `score_eval` |
| Categorical | `"categorical_eval"` | `categorical_eval` |

The property key inside `schema.properties` must match `name` exactly. The `required` array may only be `["<type_key>"]` or `["<type_key>", "reasoning"]` — any other value is rejected. Always include `"reasoning": {"type": "string"}` for UI display.

**Boolean** (`BooleanStructuredOutput(pass_when=True)`):

```json
{
  "output_schema": {
    "name": "boolean_eval",
    "strict": true,
    "schema": {
      "type": "object",
      "properties": {
        "boolean_eval": {"type": "boolean", "description": "Whether the criterion is met"},
        "reasoning": {"type": "string", "description": "Explanation for the evaluation"}
      },
      "required": ["boolean_eval", "reasoning"],
      "additionalProperties": false
    }
  },
  "assessment_criteria": {"pass_when": true}
}
```

**Score** (`ScoreStructuredOutput(min_score=1, max_score=10, min_threshold=7)`):

```json
{
  "output_schema": {
    "name": "score_eval",
    "strict": true,
    "schema": {
      "type": "object",
      "properties": {
        "score_eval": {"type": "number", "description": "Score from 1 to 10", "minimum": 1, "maximum": 10},
        "reasoning": {"type": "string", "description": "Explanation for the score"}
      },
      "required": ["score_eval", "reasoning"],
      "additionalProperties": false
    }
  },
  "assessment_criteria": {"min_threshold": 7}
}
```

Add `max_threshold` to `assessment_criteria` if set.

**Categorical** (`CategoricalStructuredOutput(categories={...}, pass_values=[...])`):

```json
{
  "output_schema": {
    "name": "categorical_eval",
    "strict": true,
    "schema": {
      "type": "object",
      "properties": {
        "categorical_eval": {
          "type": "string",
          "anyOf": [
            {"const": "correct", "description": "The response correctly answers the question"},
            {"const": "partially_correct", "description": "Partially correct but missing information"},
            {"const": "incorrect", "description": "The response is wrong or irrelevant"}
          ]
        },
        "reasoning": {"type": "string", "description": "Explanation for the category chosen"}
      },
      "required": ["categorical_eval", "reasoning"],
      "additionalProperties": false
    }
  },
  "assessment_criteria": {"pass_values": ["correct"]}
}
```

Note: categorical uses `"type": "string"` alongside `anyOf` (each `const` is a string value), unlike the offline SDK which uses bare `anyOf` at the property root.

**Custom / multi-dimensional**: not directly supported via the fixed-name schema. Implement as a score or categorical evaluator where possible, or split into multiple evaluators. The `name` must be one of the three fixed values above.

**Filter scoping**: when the proposal targets a specific span kind (e.g. an LLM sub-span), translate it into an EVP `filter` query — e.g. `@meta.span.kind:llm`, `service:checkout-agent`, or a more specific tag. Combine with `root_spans_only:true` only when the target is the trace root.

**For `eval_scope: trace`:**

- The evaluator triggers once per **completed** trace, after a **3-minute inactivity window**. Late-arriving spans (>3 min after the prior span on the same trace) are **excluded** from the evaluation. Surface this in the proposal so the user knows about both the latency and the potential miss for sparse-activity agents (long-running agents whose steps are sparser than 3 minutes apart).
- The `filter` query must match the trace's **root span only** — always include `@parent_id:undefined` (or `root_spans_only: true`) to avoid double-firing across descendants. Combine with `@meta.span.kind:agent` (or whatever kind the app uses for root spans, observed in Phase 1) for narrowing.
- Sampling at trace scope is heavier than at span scope (one trace = many spans on the judge's side). Default `sampling_percentage` to **`5`** for trace-scope evaluators (instead of the span default `10`); the user can raise it after a manual review pass.

#### Always publish as draft (`enabled: false`)

**Always create / update evaluators with `enabled: false`** — regardless of whether `integration_account_id` was auto-detected from existing evaluators. The UI is the source of truth for activation; the skill should never auto-enable evaluators on the user's behalf. The user reviews each draft in the UI, confirms the integration account is correct (the auto-detected ID may belong to a different judge LLM than the one they want for this app), and flips the toggle when they're satisfied.

This makes the workflow safe by default: a wrong `integration_account_id`, a mistuned prompt, or an over-broad filter never goes live without a human pass. Auto-detection of the account ID still helps because the draft renders with the right account pre-selected — review is faster.

#### integration_account_id resolution

The `integration_account_id` is an opaque UUID that the UI matches against the org's integration accounts list to populate the account section dropdown. Users typically don't know this value, so **never ask the user to supply a raw UUID**.

**Resolution order:**

1. **Inherit from existing evaluators** — in Phase 0 you called `get_llmobs_evaluator` for each existing custom evaluator. Check the `llm_provider.integration_account_id` field on those responses. If any of them have a value, use that same ID on the published drafts. If multiple different IDs appear across existing evaluators, pick the most common one and note which you chose so the user can correct it during the UI review pass.

2. **Omit if no existing evaluator has one** — if no custom evaluator in the ml_app has an `integration_account_id`, omit the field from the publish payload. The draft will render without an account pre-selected; the user picks one during the UI review pass before activating.

Either way, the evaluator is published with `enabled: false`. The user is the gate — see "Always publish as draft" above.

#### Publish (single message — parallelize)

Issue all `create_or_update_llmobs_evaluator` calls in a **single message** (one per evaluator). Set `telemetry.intent` to a short English description like `"skill:llm-obs-eval-bootstrap — Bootstrap evaluator suite for ml_app=<ml_app> from production trace analysis."`.

If any call fails, capture the error and continue with the remaining evaluators — never silently abort the batch. Report failures explicitly in the summary.

#### Summary

```
## Published Evaluators (drafts — pending UI review)

Wrote {N} online evaluators to ml_app `{ml_app}`. **All published as drafts (`enabled: false`)** — review and activate them in the UI before they start scoring spans.

| # | Name | Action | Provider/Model | Sampling | Scope | Account auto-detected | Status |
|---|------|--------|----------------|----------|-------|-----------------------|--------|
| 1 | task_completion | created (draft) | openai/gpt-5.4-mini | 10% | span | yes | ok |
| 2 | response_groundedness | overwrote (draft) | openai/gpt-5.4-mini | 10% | span | yes | ok |
| 3 | scope_adherence | renamed (`_v2`) (draft) | openai/gpt-5.4-mini | 10% | span | no — pick in UI | ok |
| 4 | citation_format | failed | openai/gpt-5.4-mini | 10% | span | — | error |

{If any failed:}
**Errors**:
- `{name}`: {error message}

{If any code-based proposals were dropped:}
**Not published** (code-based, not supported by online evaluator API):
- `{name}` ({type}) — consider running offline via `/eval-bootstrap {ml_app}` (SDK mode).

### Next Steps — review and activate in the UI

The drafts are intentionally not running yet. Walk through each one in the Datadog UI before flipping the enable toggle:

1. **Open the drafts**: Datadog → LLM Observability → Evaluations → filter by ml_app `{ml_app}` (the new drafts appear with status `Disabled`).
2. **For each draft**:
   - **Verify the integration account** in the Provider section. If the column above shows `auto-detected: yes`, confirm it's the correct account for the judge LLM you want this evaluator to call through. If `no`, pick an account from the dropdown.
   - **Skim the prompt template** and the structured-output schema — make sure the spans-vs-trace scope, filter, and sampling match what you actually want to measure.
   - **Click into a sample span/trace** and use the test pane to dry-run the prompt against real data. Confirm the result matches your expectation.
3. **Enable**: once each draft passes review, toggle it to enabled. Datadog starts scoring incoming spans immediately.
4. **Wait for first scores**: with `sampling_percentage=10` (span scope) or `5` (trace scope), expect first results within minutes for high-traffic apps.
5. **Tune sampling/filter**: if results are noisy or volume is too high, reduce `sampling_percentage` or tighten the `filter` from the UI. Re-running `/eval-bootstrap {ml_app} --publish` will round-trip the existing config before overwriting — your manual tweaks survive across reruns.
```

#### Notebook export (after summary)

Same logic as Phase 3A — offer to append to the RCA notebook if `rca_notebook_url` was detected, or create a new standalone notebook. The notebook cell should list the published evaluators with their UI links and the `ml_app` they target. In pup mode, use `pup notebooks create` / `pup notebooks edit` as described in Phase 3A.

---

### Phase 3D: Emit Dataset from Production Traces

**Goal**: Sample production traces for `ml_app`, extract input / output pairs, and write a `DatasetRecordRaw[]` JSON file shaped for `LLMObs.create_dataset(records=...)`. **This mode does not propose or generate evaluators** — Phases 1 and 2 are bypassed. The dataset is the sole artifact.

**When this mode runs**: triggered by `--emit-dataset <path>`. Skip Phase 0 step 4 (eval coverage map) — it's unrelated to dataset extraction.

#### Sampling

1. Call `search_llmobs_spans(query="@ml_app:\"<ml_app>\" @status:ok", root_spans_only=true, limit=<trace-limit>, from=<timeframe>)`. Filter to `@status:ok` so the resulting dataset records reflect intended app behavior (errored traces have no usable output baseline).
2. For each sampled root span, call `get_llmobs_span_details` (group by `trace_id`) and `get_llmobs_span_content` to read the root span's `input` and `output` fields. Issue all calls for a page in a single message (parallelize).

#### Record extraction

For each sampled root span, build one `DatasetRecordRaw` entry:

| Field | Source | Notes |
|---|---|---|
| `input_data` | Root span's `meta.input` | If `meta.input.value` is a plain string, wrap as `{"input": "<value>"}`. If `meta.input.messages` exists, use `{"messages": [...]}` (preserving the role/content shape). For RAG roots that also have `documents`, include `{"documents": [...]}`. |
| `expected_output` | Root span's `meta.output.value` (or `meta.output.messages[-1].content` for LLM-kind roots) | This is the **production behavior baseline**, not ground truth. Document this clearly in the summary so the user does not over-trust it. |
| `metadata` | `{"source": "production", "trace_id": "<id>", "span_id": "<id>", "ml_app": "<ml_app>", "extracted_at": "<ISO 8601 UTC>"}` | Trace/span IDs let the user click back to the source trace in Datadog. |
| `tags` | `["env:prod", "source:traces", "ml_app:<ml_app>"]` | Always `"key:value"` strings — bare strings will fail `Dataset.append()`. See the per-record tag rules in `llm-obs-experiment-py-bootstrap/SKILL.md` (search "Per-record tags"). |

**PII scrub.** Before writing, run the same scrub used elsewhere in the dd-llmo skill set: replace email / phone / SSN / API-key regex matches in all string values with `<REDACTED:pii-type>`. Surface a one-line warning listing affected record indices in the summary.

**Tag normalization (MANDATORY pre-write step).** The SDK's `Dataset.append()` calls `validate_tags_list` which rejects any tag without a `:` separator (`Tag '<tag>' is malformed. Tags must be in 'key:value' format.`). Before writing the JSON, run this normalization on every record's `tags` list:

1. **Drop** any non-string, `None`, or empty-string entries.
2. **Already `key:value`** (`":" in tag` and both halves non-empty) → keep as-is.
3. **Bare string** (no `:`) → wrap as `"tag:<value>"` (so e.g. `"interactive"` becomes `"tag:interactive"`). Never silently drop — the original observation is preserved under the generic `tag:` key.
4. **Trailing or leading `:`** (e.g. `"foo:"`, `":bar"`) → wrap as `"tag:<full-original-string>"` so the malformed value is preserved without crashing the SDK.

Track the count of normalizations and surface in the summary as a one-line note (`Tag normalizations applied: N (bare strings auto-prefixed with 'tag:')`). Do not write the dataset if any record's `tags` list still contains a non-`key:value` string after normalization — that's a bug in this step.

**Skip records** where:
- `meta.output` is empty or missing (nothing usable as a baseline).
- The root span's I/O is purely structural (e.g. only tool calls, no natural-language output). Record the count of skipped records and surface in the summary.

#### Write the file

1. Write the JSON array (2-space indented) to `<path>` using the Write tool. The path was supplied by the user via `--emit-dataset <path>` — use it verbatim.
2. **Do not** push to Datadog from this mode. Publishing to Datadog is the next step in the onboarding flow (`LLMObs.create_dataset(records=...)`) and is handled by the caller (the onboarding orchestrator or the user directly).

#### Summary

After writing, print:

```
## Generated Dataset

Wrote `<path>` — {N} records sampled from `<ml_app>` ({timeframe}).

| Stat | Value |
|---|---|
| Records emitted | {N} |
| Records skipped (no output) | {M} |
| PII redactions applied | {P} (indices: {list or "none"}) |
| Tag normalizations applied | {T} (bare strings auto-prefixed with `tag:`) |

**Important**: `expected_output` is the **current production behavior baseline**, not ground truth. Use this dataset for regression-style experiments (does my refactor change observed outputs?) before promoting it to a labelled gold set.

### Next Steps

1. **Review**: open `<path>` and spot-check a few records. Adjust `expected_output` where the production output was wrong.
2. **Publish to Datadog**:
   ```python
   from ddtrace.llmobs import LLMObs
   import json, os
   LLMObs.enable(api_key=os.getenv("DD_API_KEY"), app_key=os.getenv("DD_APPLICATION_KEY"),
                 site=os.getenv("DD_SITE", "datadoghq.com"),
                 project_name="<your-project>", agentless_enabled=True)
   records = json.load(open("<path>"))
   ds = LLMObs.create_dataset(dataset_name="<your-name>", records=records)
   print(ds)
   ```
3. **Run an experiment** against the published dataset via `llm-obs-experiment-py-bootstrap --dataset-name <your-name>`.
```

Skip the notebook export prompt in this mode — datasets aren't well-served by a markdown summary in a notebook, and the next step (experiment generation) will produce its own artifacts.

---

## Operating Rules

- **Breadth over precision; let the user curate**: Propose **8–15 evaluators** distributed across **domain-specific** (largest bucket — derived from Phase 1 domain signals), outcome, format, and safety. Users can always remove what doesn't fit their quality bar; they cannot easily add what was not proposed. Anchor every domain-specific proposal in at least one observed trace pattern — don't invent generic domain evaluators without evidence.
- **Don't overfit**: Write criteria that generalize beyond the specific sampled traces. Use examples as grounding, not as the sole criteria.
- **Show your work**: Every proposed evaluator cites at least one trace as evidence with a clickable link: `[Trace {first_8}...](https://app.datadoghq.com/llm/traces?query=trace_id:{full_32_char_id})`.
- **New file only**: Never modify existing evaluator code or experiment configurations.
- **Honest about uncertainty**: If fewer than 5 traces support a proposed evaluator, flag it as tentative.

---

## Tool Reference

This appendix applies only in **pup mode**. In MCP mode, use the tool names in the workflow sections directly.

### Spans and traces

| MCP Tool | pup Command |
|---|---|
| `search_llmobs_spans(query, ml_app, from, to, limit, cursor, root_spans_only, span_kind, summary)` | `pup llm-obs spans search --query "@ml_app:A [other_filters]" [--from F] [--to T] [--limit N] [--cursor C] [--root-spans-only] [--span-kind K] [--summary]` — **always use `--query "@ml_app:A"` to filter by ml_app**; the `--ml-app A` flag is unreliable and silently returns spans from other apps. |
| `get_llmobs_span_details(trace_id, span_ids, from, to)` | `pup llm-obs spans get-details --trace-id T --span-ids S1,S2,...` |
| `get_llmobs_span_content(trace_id, span_id, field, path)` | `pup llm-obs spans get-content --trace-id T --span-id S --field F [--path P]` |
| `get_llmobs_trace(trace_id, include_tree)` | `pup llm-obs spans get-trace --trace-id T [--include-tree]` |
| `get_llmobs_agent_loop(trace_id, span_id)` | `pup llm-obs spans get-agent-loop --trace-id T [--span-id S]` |
| `find_llmobs_error_spans(trace_id)` | `pup llm-obs spans find-errors --trace-id T` |
| `expand_llmobs_spans(trace_id, span_ids, max_depth, filter_kind)` | `pup llm-obs spans expand --trace-id T --span-ids S1,S2,... [--max-depth N] [--filter-kind K]` |

### Evaluators

| MCP Tool | pup Command |
|---|---|
| `list_llmobs_evals()` | `pup llm-obs evals list` (filter by `ml_app` client-side) |
| `list_llmobs_evals_by_ml_app(ml_app)` | `pup llm-obs evals list-by-ml-app --ml-app A` |
| `get_llmobs_evaluator(eval_name)` | `pup llm-obs evals get-evaluator EVAL_NAME` |
| `get_llmobs_eval_aggregate_stats(eval_name, ml_app, from, to)` | `pup llm-obs evals get-aggregate-stats EVAL_NAME [--ml-app A] [--from F] [--to T]` |
| `delete_llmobs_evaluator(eval_name)` | `pup llm-obs evals delete EVAL_NAME` |
| `create_or_update_llmobs_evaluator(...)` | `pup llm-obs evals create-or-update EVAL_NAME --file /tmp/eval_EVAL_NAME.json` — see flat schema note below |

#### `create_or_update_llmobs_evaluator` in pup mode

pup uses a **flat** JSON file (all fields top-level). `get-evaluator` returns a **nested** object. Transform as follows:

1. **Round-trip check**: Call `pup llm-obs evals get-evaluator EVAL_NAME` first. If it exists, start from its config.
2. **Flatten `llm_provider`**: hoist `integration_provider`, `model_name`, `integration_account_id`, `temperature` to top level, dropping the `llm_provider` key.
3. **Merge and set `enabled: false`**.
4. **Write to temp file and call**:
   ```bash
   pup llm-obs evals create-or-update EVAL_NAME --file /tmp/eval_EVAL_NAME.json
   ```
   Use unique temp file names when publishing multiple evaluators in parallel (e.g. `/tmp/eval_toxicity.json`).

| `get-evaluator` field | Flat JSON key |
|---|---|
| `llm_provider.integration_provider` | `integration_provider` |
| `llm_provider.model_name` | `model_name` |
| `llm_provider.integration_account_id` | `integration_account_id` |
| `llm_provider.temperature` | `temperature` |
| All other fields | Unchanged (already top-level) |

### Notebooks

| MCP Tool | pup Command |
|---|---|
| `create_datadog_notebook(name, cells, ...)` | `pup notebooks create --title "TITLE" --file /tmp/nb_cells.json` — confirm exact flags with `pup notebooks create --help` |
| `edit_datadog_notebook(id, cells, append_only=true)` | `pup notebooks edit NOTEBOOK_ID --file /tmp/nb_cells.json` (fetches current notebook, appends provided cells, writes back) |

The cells file is a JSON array of cell objects:
```json
[{"attributes": {"definition": {"type": "markdown", "text": "## Section\n\nContent."}}, "type": "notebook_cells"}]
```
- **MCP result parsing safety**: Before writing any script (Python, jq, etc.) that iterates over or accesses fields in an MCP tool result, inspect the raw structure first — check `type(result)`, top-level keys, and whether the payload is nested inside a content block (e.g. `[{'type': 'text', 'text': '<json>'}]`). Extract and `json.loads()` the inner payload if needed before parsing. Never assume MCP results are bare dicts or lists.
