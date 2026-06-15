# `--evaluator-style function` (default — what the notebooks use)

Plain Python functions with the signature `(input_data, output_data, expected_output)`. Always emit at least three: a trivial boolean (returns `bool`), a richer rule-based one (returns `EvaluatorResult`), and an LLM-as-Judge surrogate (a `RemoteEvaluator` reference or a placeholder).

## Code to emit

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

## When to extend

- If the user passed `--dataset` with a structured `expected_output`, add a JSON-shape check (also returning `EvaluatorResult`).
- For LLM-as-Judge surrogates, prefer `RemoteEvaluator` references (server-side, scalable) over inline `LLMJudge` calls.

## When NOT to use this style

If the evaluator needs persistent state (a model client, a cached lookup, an async I/O resource), use `class` style instead — `BaseEvaluator.__init__` is where you set up state safely. See `references/evaluator-styles/class.md`.
