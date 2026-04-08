---
name: eval-bootstrap
description: Bootstrap SDK-based evaluators from production traces. Use when user says "bootstrap evaluators", "generate evaluators", "create evals from traces", "eval bootstrap", "write evaluators", "build eval suite", or wants to generate BaseEvaluator/LLMJudge code from production LLM trace data. Works with ml_app and optional RCA report or failure hypothesis.
---

# Eval Bootstrap — Generate Evaluator Code from Production Traces

Given a sample of production LLM traces, analyze input/output patterns and quality dimensions, then generate ready-to-use evaluator code using the Datadog Evals SDK. The output is a `.py` file containing `BaseEvaluator` subclasses and/or `LLMJudge` instances that the user can run in LLM Experiments.

## Usage

```
Bootstrap evaluators for <ml_app> [over the last <timeframe>]
```

### Inputs

| Input | Required | Default | Description |
|-------|----------|---------|-------------|
| `ml_app` | Yes | — | ML application to scope traces |
| `timeframe` | No | `now-7d` | How far back to look |
| `rca_report` | No | — | Failure taxonomy from `eval-trace-rca` skill, or a free-text failure hypothesis |

If `ml_app` is missing, ask the user before proceeding.

## Available Tools

| Tool | Purpose |
|------|---------|
| `search_llmobs_spans` | Find spans by eval presence, tags, span kind, query syntax. Paginate with cursor. |
| `get_llmobs_span_details` | Metadata, evaluations (scores, labels, reasoning), and `content_info` map showing available fields + sizes. |
| `get_llmobs_span_content` | Actual content for a span field. Supports JSONPath via `path` param for targeted extraction. |
| `get_llmobs_trace` | Full trace hierarchy as span tree with span counts by kind. |
| `get_llmobs_agent_loop` | Chronological agent execution timeline (LLM calls, tool invocations, decisions). |
| `list_llmobs_evals` | List all evaluators (OOTB + custom) configured for `ml_app`, with `enabled` status. Call once in Phase 0 to map existing coverage before proposing new evaluators. |
| `get_llmobs_eval_config` | Full configuration (prompt, model, structured output) for a custom/BYOP evaluator. Use in Phase 0 to understand what a custom eval measures. Not supported for `source=ootb` — skip those. |

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

If you have access to dd-trace-py locally, verify the API surface by reading:

- `ddtrace/llmobs/_evaluators/llm_judge.py` — LLMJudge class, structured output types
- `ddtrace/llmobs/_experiment.py` — BaseEvaluator, EvaluatorContext, EvaluatorResult
- `ddtrace/llmobs/_evaluators/format.py` — JSONEvaluator, LengthEvaluator
- `ddtrace/llmobs/_evaluators/string_matching.py` — StringCheckEvaluator, RegexMatchEvaluator

---

## Workflow

### Phase 0: Resolve Inputs & Entry Mode

**Entry mode detection:**

| Mode | Signal | Behavior |
|------|--------|----------|
| **Cold Start** | Only `ml_app` provided (no RCA, no hypothesis) | Full open discovery — understand what the app does, identify quality dimensions worth measuring, propose evals for coverage |
| **From RCA** | Conversation contains an RCA report or user provides a failure hypothesis | Skip open discovery — use existing failure taxonomy as eval targets |

**Resolution steps:**

1. If `ml_app` not provided → ask the user.
2. Auto-detect entry mode:
   - If the conversation contains an RCA report (look for "Failure Taxonomy" heading, structured failure modes, or severity ratings) → `from_rca`. Extract the taxonomy.
   - If the user provides a free-text failure hypothesis (e.g., "the system prompt lacks grounding") → `from_rca`. Use the hypothesis as the starting eval target.
   - Otherwise → `cold_start`.
3. If `timeframe` not provided → default to `now-7d`.
4. **Map existing eval coverage**: Call `list_llmobs_evals(ml_app=<ml_app>)`. Then, for each eval with `source=custom`, call `get_llmobs_eval_config` to inspect its prompt and infer which quality dimension it covers. Issue all config calls in a **single message** (parallelize). Skip `source=ootb` evals — their names are self-describing.

   By the end of this step you have a complete coverage map: `{eval_name → source, enabled, dimension}`. Carry this into Phase 2 for deduplication.

---

### Phase 1: Explore Traces & Identify Eval Targets

**Goal**: Sample production traces, understand what the app does, and identify quality dimensions worth measuring.

#### Cold Start Path

1. **Sample the app**: `search_llmobs_spans(ml_app=<ml_app>, root_spans_only=true, limit=50, from=<timeframe>, query="@status:ok")`. Filter by `@status:ok` — error spans have no output to evaluate.

2. **Profile the app and identify evaluation target spans**: Call `get_llmobs_span_details` for span_ids grouped by trace_id. Inspect `content_info` to classify:

   | Signal | App Profile |
   |--------|------------|
   | `content_info` has `messages` | LLM/chat app |
   | `content_info` has `documents` | RAG app |
   | Spans include `agent` kind | Agent app |
   | `content_info` has `metadata` | Has custom metadata |

   For agent/multi-step apps, also call `get_llmobs_trace` on 2-3 traces to see the full span hierarchy. Compare `content_info` between the root span and its sub-spans (especially LLM sub-spans). The root span typically has a summary view (user query → final answer), while LLM sub-spans have the full picture (system prompt, tool call results, reasoning chain). Note which span level has the richest signal for each quality dimension — this determines the **evaluation target span** for each evaluator.

3. **Extract content and identify targets**: Call `get_llmobs_span_content` for representative spans. Fetch fields based on app profile:

   | App Profile | Fields to Fetch |
   |------------|----------------|
   | LLM/chat | `messages` (`path=$.messages[0]` for system prompt), `output` |
   | RAG | `documents`, `input`, `output` |
   | Agent | `get_llmobs_agent_loop` for the agent span, then `messages` for detail |
   | Any with metadata | `metadata` |

   Issue all calls in a single message. As you read, note quality patterns: what does "success" look like? What variance exists across outputs? Each observed quality dimension becomes an eval target, with the traces you've just read as evidence. Also look for safety signals — scope violations, sensitive data in outputs, out-of-character responses — and propose a safety evaluator if you find them.

#### From RCA Path

1. Extract the failure taxonomy from the RCA report. Each failure mode with High or Medium severity becomes an eval target.
2. For each target: if the RCA includes trace IDs, use them directly; otherwise search for matching traces. Fetch 2-3 traces per target with `get_llmobs_span_content` to understand the concrete pattern.

---

### Phase 2: Propose Evaluator Suite

**Goal**: Present a concrete evaluator proposal for user confirmation.

Each evaluator judges **one data point** — it receives `input_data` and `output_data` for a single record, not a full trace or batch. Design evaluators accordingly.

Generated evaluators target **offline experiments** — template variables use `EvaluatorContext` fields (`{{input_data}}`, `{{output_data}}`). The actual data shape depends on the user's dataset and task function (see EvaluatorContext note in SDK Reference).

Order proposals from broadest signal to most granular:

1. **Outcome evaluators** — Did this span produce a good result?
   - Examples: `task_completion`, `answer_correctness`, `response_groundedness`
2. **Format evaluators** — Does the output meet structural requirements?
   - Examples: `valid_json_output`, `response_length`, `citation_format`
3. **Safety evaluators** — Does the output stay within appropriate boundaries?
   - Examples: `no_pii_leakage`, `scope_adherence`, `no_hallucination`

#### Deduplication Against Existing Coverage

Before building the proposal, apply the coverage map from Phase 0:

1. **Enabled eval (OOTB or custom)**: Do NOT propose a new evaluator for the same quality dimension. That dimension is already covered — skip it.

2. **Disabled OOTB eval**: Do NOT propose a new custom evaluator for that dimension. Instead, surface it in a short note within the proposal and suggest enabling it via the Datadog UI rather than creating a duplicate. Example:

   > `hallucination` (ootb, disabled) — consider enabling in Datadog UI (Evaluations → Configure) instead of creating a custom eval.

3. **Gap identification**: Open the proposal with a coverage summary line: "Existing coverage: N evaluator(s) already configured ({names}). Proposing evaluators for uncovered dimensions only."

4. **All dimensions covered**: If the coverage map accounts for all identified quality dimensions, surface this explicitly and ask the user what they want: (a) review/improve existing eval prompts, (b) add coverage for additional dimensions, or (c) proceed anyway.

For each proposed evaluator:

- **Name**: Must match `^[a-zA-Z0-9_-]+$` (alphanumeric, underscore, hyphen only)
- **Type**: `LLMJudge` (Boolean/Score/Categorical/custom JSON schema), built-in (`JSONEvaluator`, `RegexMatchEvaluator`, etc.), or `BaseEvaluator` subclass
- **What it measures**: 1-2 sentence plain-language description
- **Target span**: Which span's data the evaluator was designed for (e.g., "root agent span", "LLM sub-span `anthropic.request`", "all `llm` spans"). If the root span's I/O is too lossy for the quality dimension (e.g., tool call results aren't visible), note this and specify which sub-span has the signal.
- **Pass/fail criteria**: `pass_when=True`, `min_threshold=7`, `pass_values=["correct"]`, or "no automatic assessment" for custom JSON schema
- **Template variables**: Which of `input_data`, `output_data`, `expected_output`, `metadata.*` it uses
- **Evidence**: At least one trace where it would have caught a failure (or confirmed correct behavior)

#### MANDATORY CHECKPOINT

**You MUST output the proposal and wait for user confirmation before proceeding.**

```
## Proposed Evaluator Suite

**App profile**: {LLM | RAG | Agent | Multi-agent}
**Entry mode**: {cold_start | from_rca}

| # | Name | Type | Measures | Pass Criteria |
|---|------|------|----------|---------------|
| 1 | task_completion | LLMJudge (Boolean) | Whether the task was completed | pass_when=True |
| 2 | ... | ... | ... | ... |

For each evaluator:
- **{name}**: {what it measures}
  - Target span: {which span's data it was designed for}
  - Rationale: {which quality dimension it covers and why}
  - Evidence: [Trace {id_short}](https://app.datadoghq.com/llm/traces?query=trace_id:{full_id})
```

**Which evaluators should I generate?** (Accept all, remove some, rename, add custom, or change provider/model.)

Do NOT proceed to code generation until the user confirms.

---

### Phase 3: Generate & Write Evaluator Code

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

---

## Output Format

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

---

## Operating Rules

- **Coverage over precision**: Propose 4-6 evaluators covering major quality dimensions. Users can always remove; they cannot easily add what was not proposed.
- **Don't overfit**: Write criteria that generalize beyond the specific sampled traces. Use examples as grounding, not as the sole criteria.
- **Show your work**: Every proposed evaluator cites at least one trace as evidence with a clickable link: `[Trace {first_8}...](https://app.datadoghq.com/llm/traces?query=trace_id:{full_32_char_id})`.
- **New file only**: Never modify existing evaluator code or experiment configurations.
- **Honest about uncertainty**: If fewer than 5 traces support a proposed evaluator, flag it as tentative.
