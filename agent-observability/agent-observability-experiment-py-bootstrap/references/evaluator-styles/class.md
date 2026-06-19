# `--evaluator-style class` (advanced — for evaluators that need state or async I/O)

`BaseEvaluator` subclasses with `evaluate(self, context: EvaluatorContext) -> EvaluatorResult`. Always return `EvaluatorResult` — never a bare value. State-bearing evaluators usually have richer reasoning to surface anyway.

## Code to emit

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

## Rules

- Call `super().__init__(name=...)` in `__init__`. The `name` is the column header in the Datadog Experiments UI.
- `evaluate()` runs in the experiment's worker pool. Do NOT mutate `self` from `evaluate()` (thread safety) — state set in `__init__` should be read-only thereafter.
- For async work (e.g., calling an LLM judge over the network), prefer wrapping with `asyncio.run(...)` inside `evaluate()` rather than making `evaluate` itself async. Keeps the experiment runner sync.

## When NOT to use this style

If the evaluator is a one-line check (`exact_match`, `length_under_500`), use `function` style — the class boilerplate adds noise. See `references/evaluator-styles/function.md`.
