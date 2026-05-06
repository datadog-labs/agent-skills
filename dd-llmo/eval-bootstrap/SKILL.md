---
name: eval-bootstrap
description: Bootstrap evaluators from production traces — offline experiment SDK code, framework-agnostic JSON specs, or in-product Datadog evaluator configs (span-scoped or trace-scoped, auto-decided per evaluator). Use when user says "bootstrap evaluators", "generate evaluators", "create evals from traces", "eval bootstrap", "write evaluators", "build eval suite", "create trace-level evaluations", "create span-level evaluations", or wants to generate evaluator artifacts from production LLM trace data. Works with ml_app and optional RCA report or failure hypothesis.
---

# Eval Bootstrap — Generate Evaluators from Production Traces

Given a sample of production LLM traces, analyze input/output patterns and quality dimensions, then emit evaluator artifacts in one of three output modes:

- **`sdk_code`** (default) — Python `.py` file with `BaseEvaluator` subclasses and/or `LLMJudge` instances for offline `LLMObs.experiment()` runs.
- **`data_only`** (`--data-only`) — framework-agnostic JSON eval spec, no SDK dependency.
- **`in_product`** (`--in-product`) — Datadog **in-product evaluator configs** that run online against production traffic. The skill **auto-decides per evaluator** whether each one is **span-scoped** (one judgment per matching span, with `{{span_input}}` / `{{span_output}}` templating) or **trace-scoped** (one judgment per trace, with `{{spans...}}` templating). Output is a single JSON config file with mixed scopes plus a step-by-step UI walkthrough.

In-product mode is the right choice when you want continuous evaluation of production traffic. Within it, span scope is the default; the skill promotes an evaluator to trace scope only when its judgment cannot be answered from one span alone (agent goal completion, tool-use correctness, RAG faithfulness).

## Usage

```
/eval-bootstrap <ml_app> [--timeframe <window>] [--data-only | --in-product]
```

Arguments: $ARGUMENTS

### Inputs

| Input | Required | Default | Description |
|-------|----------|---------|-------------|
| `ml_app` | Yes | — | ML application to scope traces |
| `timeframe` | No | `now-7d` | How far back to look |
| `rca_report` | No | — | Failure taxonomy from `eval-trace-rca` skill, or a free-text failure hypothesis |
| `--data-only` | No | off | Emit a self-contained JSON spec file instead of Python SDK code |
| `--in-product` | No | off | Emit Datadog in-product evaluator configs (one JSON file + UI walkthrough) instead of offline experiment artifacts. The skill auto-classifies each evaluator as span-scoped or trace-scoped — the user does not pick the scope. |

`--data-only` and `--in-product` are mutually exclusive — they target different surfaces (offline experiment vs. online production traffic). If both are passed, ask the user which one to use.

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
| Single-span semantic judgment in production (per-span tone, helpfulness, format compliance) | **Span-scoped in-product evaluator** — `in_product` output mode (see Phase 3C) |
| Quality requires reasoning across an entire trace (agent goal completion, tool-use chain, RAG faithfulness) | **Trace-scoped in-product evaluator** — `in_product` output mode (see Phase 3C) |

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

**Parse arguments**: Extract `ml_app` (first non-flag argument), `--timeframe` (default `now-7d`), and `--data-only` flag. Set `output_mode = data_only` if the flag is present; otherwise `output_mode = sdk_code`.

**Resolution steps:**

1. If `ml_app` not provided → ask the user.
2. Auto-detect entry mode:
   - If the conversation contains an RCA report (look for "Failure Taxonomy" heading, structured failure modes, or severity ratings) → `from_rca`. Extract the taxonomy.
   - If the user provides a free-text failure hypothesis (e.g., "the system prompt lacks grounding") → `from_rca`. Use the hypothesis as the starting eval target.
   - Otherwise → `cold_start`.
3. If `timeframe` not provided → default to `now-7d`.
4. **Map existing eval coverage** — **skip if `output_mode = data_only`** (there is no Datadog eval project to check coverage against): Call `list_llmobs_evals(ml_app=<ml_app>)`. Then, for each eval with `source=custom`, call `get_llmobs_eval_config` to inspect its prompt and infer which quality dimension it covers. Issue all config calls in a **single message** (parallelize). Skip `source=ootb` evals — their names are self-describing.

   By the end of this step you have a complete coverage map: `{eval_name → source, enabled, dimension}`. Carry this into Phase 2 for deduplication.

5. **Notebook context detection**: Scan the current conversation for a Datadog notebook URL that was produced by `/eval-trace-rca` (pattern: `https://app.datadoghq.com/notebook/{numeric-id}`). If found, store it as `rca_notebook_url` and extract the numeric ID as `rca_notebook_id`. This is used after Phase 3 to offer appending the evaluator suite to that notebook instead of creating a new one.

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

**In `data_only` mode**: skip this section entirely (coverage map was not built in Phase 0). Proceed directly to the proposal table.

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
- **Scope (in_product mode only)**: Auto-classify each evaluator as `span` or `trace` per the rule below — do NOT ask the user to pick. Show your classification in the proposal so the user can override.

#### Span vs. Trace Scope Classification (`in_product` mode)

For each proposed evaluator, decide its scope using a two-question test:

1. **Can the judgment be answered from one span's `meta.input.value` and `meta.output.value` alone?** If yes → **span scope**.
2. **Does the judgment require correlating data across two or more spans** (e.g., "was the answer grounded in the documents this `retrieval` span returned", "did the agent call the right tools in the right order", "is the final response consistent with what the `tool` spans actually computed")? If yes → **trace scope**.

If both conditions partly apply, **default to span scope on the LLM sub-span that has the richest signal**. Trace scope is more expensive (one judgment per completed trace, larger payloads) — only promote when span scope can't answer the question.

Apply per-evaluator, then summarize the split in the proposal header so the user sees the recommendation at a glance:

> Recommended scope split: 4 span-scoped, 1 trace-scoped (use `--in-product` to emit both).

A short, explicit rationale in the per-evaluator block is what makes the recommendation auditable — write *why* this evaluator is span vs. trace, not just which:

> `tool_use_correctness` — **trace scope**. Judgment requires comparing the agent's final response against the inputs/outputs of every `tool` span in the same trace; no single span has both. Span scope on the agent root would only see input → output without the tool chain.

#### MANDATORY CHECKPOINT

**You MUST output the proposal and wait for user confirmation before proceeding.**

```
## Proposed Evaluator Suite

**App profile**: {LLM | RAG | Agent | Multi-agent}
**Entry mode**: {cold_start | from_rca}
**Output mode**: {sdk_code | data_only | in_product}
{Only when in_product:} **Recommended scope split**: {N} span-scoped, {M} trace-scoped

| # | Name | Type | Scope | Measures | Pass Criteria |
|---|------|------|-------|----------|---------------|
| 1 | task_completion | LLMJudge (Boolean) | span | Whether the task was completed | pass_when=True |
| 2 | tool_use_correctness | LLMJudge (Categorical) | trace | Right tool, right args, in order | in [correct] |
| 3 | ... | ... | ... | ... | ... |

(Drop the **Scope** column when not in `in_product` mode.)

For each evaluator:
- **{name}**: {what it measures}
  - Target span: {which span's data it was designed for}
  - Rationale: {which quality dimension it covers and why}
  - {Only when in_product:} Scope: {span | trace} — {one-sentence rationale: why this scope is the right one}
  - Evidence: [Trace {id_short}](https://app.datadoghq.com/llm/traces?query=trace_id:{full_id})
```

**Which evaluators should I generate?** (Accept all, remove some, or rename. In `sdk_code` mode you may also add custom evaluators or change provider/model. In `in_product` mode you may also override any scope classification you disagree with.)

Do NOT proceed to code generation until the user confirms.

---

### Phase 3: Generate Output

Branch on `output_mode`:
- `sdk_code` → **Phase 3A** below
- `data_only` → skip to **Phase 3B**
- `in_product` → skip to **Phase 3C**

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

After displaying the summary, offer notebook export:

- **If `rca_notebook_url` was detected in Phase 0**:
  > An RCA notebook was created earlier in this session: `{rca_notebook_url}`
  > Would you like to (a) append the evaluator suite summary to that notebook, or (b) create a new standalone notebook?

  If **append**: call `mcp__datadog-mcp-core__edit_datadog_notebook` with `id={rca_notebook_id}`, `append_only=true`, and the evaluator suite summary cell (see Notebook cell content below).

  If **new**: call `mcp__datadog-mcp-core__create_datadog_notebook` (see below).

- **If no `rca_notebook_url`**:
  > Would you like to export this evaluator suite summary to a Datadog notebook?

  If yes: call `mcp__datadog-mcp-core__create_datadog_notebook` with:
  - **`name`**: `Eval Bootstrap: {ml_app} — YYYY-MM-DD`
  - **`type`**: `report`
  - **`cells`**: single markdown cell with the evaluator suite summary
  - **`time`**: `{ "live_span": "1h" }`

After the notebook is created or updated, output the URL:
`Evaluator suite exported to notebook: <url>`

**Notebook cell content** — the markdown cell should contain:

```markdown
## Eval Bootstrap: {ml_app}

**Generated**: YYYY-MM-DD | **App profile**: {LLM | RAG | Agent | Multi-agent} | **Entry mode**: {cold_start | from_rca}
**Generated code**: `{output_path}`

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

Same logic as Phase 3A — offer to append to the RCA notebook if `rca_notebook_url` was detected, or create a new standalone notebook. Use the same notebook cell format as Phase 3A, substituting `output_path` with the JSON spec file path.

---

### Phase 3C: Generate & Write In-Product Evaluator Configs

**Goal**: Emit one Datadog in-product evaluator config per confirmed evaluator, at the scope assigned in Phase 2 (span or trace), ready to register through the Datadog UI or PUT to the custom-evaluator API. In-product evaluators run online against production traffic — they are not Python SDK code.

**Output path**: `./evals/{ml_app}_in_product_evals.json`

#### Templating per scope

In-product evaluators do **not** use `EvaluatorContext` / `{{input_data}}` / `{{output_data}}`. The User message is templated against the matched span (span scope) or the whole trace (trace scope) at evaluation time. The System Prompt is **static** in both scopes — it does NOT resolve `{{ ... }}` placeholders. Put the rubric there; put data injections in the User message only.

**Span scope** — one judgment per matching span. Use direct paths or aliases against the span's own JSON:

| Pattern | What it resolves to |
|---|---|
| `{{span_input}}` | `meta.input.messages[*].content` for LLM spans, `meta.input.value` otherwise |
| `{{span_output}}` | `meta.output.messages[*].content` for LLM spans, `meta.output.value` otherwise |
| `{{name}}` | The span's name |
| `{{meta.input.value}}` / `{{meta.output.value}}` | Direct field paths |
| `{{meta.input.messages[0].content}}` | First message |
| `{{meta.input.messages[*].content}}` | All messages, joined with newlines |
| `{{meta.input.messages[0,2].content}}` | Inclusive range |
| `{{*}}` | Entire span payload as JSON |

**Trace scope** — one judgment per trace, with every span available under the `spans` array. The `{{span_input}}` / `{{span_output}}` aliases are **not** available in trace scope:

| Pattern | What it resolves to |
|---|---|
| `{{spans}}` | JSON of every span in the trace |
| `{{spans[N].meta.input.value}}` | Single span by index |
| `{{spans[*].meta.output.value}}` | All spans' outputs, joined with newlines |
| `{{spans[name:span_name].meta.input.value}}` | Filter by span name |
| `{{spans[meta.span.kind:llm].meta.output.value}}` | Filter by span kind (`agent`, `llm`, `tool`, `retrieval`, `workflow`) |
| `{{spans[meta.span.kind:tool]}}` | Whole tool spans as JSON, paired in/out |
| `{{*}}` | Entire trace payload as JSON |

The same `EvaluatorContext`-vs-span-data pitfall does not apply: there is no offline dataset, the data is the production span/trace itself.

#### Output JSON Schema

```json
{
  "schema_version": "1",
  "generated_at": "<ISO 8601 UTC>",
  "generated_by": "eval-bootstrap",
  "ml_app": "<string>",
  "evaluators": [
    {
      "name": "snake_case_name",
      "category": "outcome | format | safety",
      "evaluation_scope": "span | trace",
      "scope_rationale": "<one-sentence why this scope>",
      "description": "<1-2 sentence plain-language description>",
      "filter_query": "<UI query string — see field notes per scope>",
      "sampling_rate": 0.05,
      "system_prompt": "<full text — static instructions, no placeholders>",
      "user_prompt": "<full text with templating per scope>",
      "structured_output": {
        "format": "categorical | boolean | score | json",
        "schema": { "<...raw JSON Schema, ready to paste into the UI...>": "" }
      },
      "assessment_criteria": {
        "pass_when": "<true | false | null>",
        "categories_pass": ["<only present when format=categorical>"],
        "min_threshold": "<number or null>",
        "max_threshold": "<number or null>"
      },
      "evidence": [
        {
          "trace_id": "<32-char hex>",
          "span_id": "<the span this evaluator would target>",
          "url": "https://app.datadoghq.com/llm/traces?query=trace_id:<trace_id>",
          "observation": "<why this trace illustrates the evaluator>"
        }
      ]
    }
  ]
}
```

#### Field Notes

- **`evaluation_scope`**: Comes straight from the Phase 2 classification. The rest of the fields are populated according to scope.
- **`scope_rationale`**: One-sentence justification carried over from Phase 2. The user already saw and confirmed it; copying it into the JSON keeps the artifact self-explanatory for whoever reviews the file later.
- **`filter_query`** — depends on scope:
  - **Span scope**: a query that selects the spans to evaluate, e.g. `@meta.span.kind:llm`, `@parent_id:undefined`, `@name:my_tool_call`. Match the narrowest set that still answers the question. Avoid org/env filters — the evaluator is already scoped.
  - **Trace scope**: always include `@parent_id:undefined` so the evaluator only triggers on completed root spans. Add `@meta.span.kind:agent` (or whatever root kind the app emits) and application-specific narrowing observed in Phase 1.
- **`sampling_rate`**: Default `0.05` (5%) for the first scale run. The walkthrough should remind the user to raise after a manual review of ~30 results validates judge accuracy.
- **`system_prompt`**: Static instructions. If the rubric needs concrete examples, embed them as literal text — they will not be templated.
- **`user_prompt`** — depends on scope:
  - **Span scope**: prefer aliases (`{{span_input}}`, `{{span_output}}`) over deep paths so the prompt adapts when the same evaluator runs across LLM and non-LLM spans.
  - **Trace scope**: bound the payload by tool count, span name, or kind. Avoid `{{spans}}` (whole trace) as the default — the per-field 250 KB limit can truncate large traces. Reach for it only as a fallback alongside narrower extracts.
- **`structured_output.schema`**: Strict JSON Schema with `additionalProperties: false`, `required` listing every property, and `strict: true` at the schema level. For categorical outputs, list the `reasoning` field **before** the categorical field in `required` and `properties` so the model writes its justification before committing to a label (improves accuracy on borderline cases for OpenAI / Anthropic strict-mode judges).
- **`assessment_criteria`**: Maps the structured-output value to Pass/Fail. For categorical, name only the categories that should Pass; everything else is Fail. For score, set `min_threshold` (or `max_threshold`). For boolean, set `pass_when` to `true` or `false`.
- **`evidence`**: Pull from traces actually inspected in Phase 1. For span-scoped evaluators, the `span_id` should be the specific span the evaluator would target. Each entry must link to a real trace — fabricated IDs are a non-starter.

#### Writing instructions

1. Iterate the confirmed evaluator suite. Each evaluator was already classified in Phase 2 — honor that classification (or the user's override) and do not re-classify here.
2. For each evaluator, generate `system_prompt` and `user_prompt` using the scope's templating syntax:
   - Anchor the User prompt with explicit named extracts (e.g., user request, final response, tool calls) **before** any wide-payload fallback. The judge does better with anchors than with raw blobs.
   - Strip PII from any literal text included in either prompt.
   - Match the structured output format to the judgment shape (boolean → "did X happen", categorical → "which of these labels", score → "1-10", json → multi-dimensional).
3. Build the JSON object following the schema above and write to `./evals/{ml_app}_in_product_evals.json` with 2-space indentation.
4. Display a completion summary plus a UI walkthrough that branches per scope:

```
## Generated In-Product Evaluators

Wrote `./evals/{ml_app}_in_product_evals.json`:

| # | Name | Scope | Format | Pass Criteria | Sample Rate |
|---|------|-------|--------|---------------|-------------|
| 1 | task_completion | span | boolean | pass_when=true | 5% |
| 2 | tool_use_correctness | trace | categorical | in [correct] | 5% |

### Configure each evaluator in Datadog

For each entry above:

1. Open **LLM Observability → Evaluations → Create Evaluation → Create your own**.
2. Enter the `name`, pick an LLM judge **Account** and **Model**.
3. **Evaluation Scope**:
   - **Application**: `{ml_app}`
   - **Evaluate On**: **Span** for span-scoped evaluators, **Trace** for trace-scoped evaluators (per the **Scope** column).
   - **Filter**: paste `filter_query`.
   - **Sampling Rate**: `sampling_rate * 100`%.
4. **System Prompt**: paste `system_prompt` (no placeholders here).
5. **User**: paste `user_prompt`. Span-scoped prompts use `{{span_input}}` / direct paths; trace-scoped prompts use `{{spans...}}`.
6. **Structured Output**: choose `structured_output.format`, paste `structured_output.schema` into the JSON Schema editor.
7. **Assessment Criteria**: configure per `assessment_criteria`.
8. Pick 3–5 samples from the right-hand pane (matching spans for span scope, sample traces for trace scope) and click **Test Evaluation** before **Save and Publish**.

### Next Steps

1. **Review** the generated prompts, scopes, and schemas in the JSON file
2. **Test on 3–5 samples per evaluator** in the UI before publishing — verify the judge's reasoning matches your expectation on a synthetic test, a typical happy path, and at least one known failure
3. **Raise sampling rate** from the seed value after a manual review of ~30 results validates judge accuracy
```

#### Notebook export (after summary)

Same logic as Phase 3A and 3B — offer to append to the RCA notebook if `rca_notebook_url` was detected, or create a new standalone notebook. Use the same notebook cell format, substituting `output_path` with the in-product evals JSON file path and noting that the artifacts are in-product configs (not SDK code). Include the scope split (`{N} span-scoped, {M} trace-scoped`) in the summary.

---

## Operating Rules

- **Coverage over precision**: Propose 4-6 evaluators covering major quality dimensions. Users can always remove; they cannot easily add what was not proposed.
- **Don't overfit**: Write criteria that generalize beyond the specific sampled traces. Use examples as grounding, not as the sole criteria.
- **Show your work**: Every proposed evaluator cites at least one trace as evidence with a clickable link: `[Trace {first_8}...](https://app.datadoghq.com/llm/traces?query=trace_id:{full_32_char_id})`.
- **New file only**: Never modify existing evaluator code or experiment configurations.
- **Honest about uncertainty**: If fewer than 5 traces support a proposed evaluator, flag it as tentative.
