# `--evaluator-style remote` (LLM-as-Judge running server-side)

`RemoteEvaluator` instances that point at a judge configured in the Datadog UI. The judge LLM call runs on Datadog's side, not in the user's experiment process — useful when the judge is shared across experiments or has its own quota / model selection.

## Code to emit

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

## Setup the user has to do first

The judge must exist in the Datadog UI before the experiment runs. Emit a comment in the generated file telling the user:

```
# Before running this experiment:
#   1. Open Datadog → LLM Observability → Evaluations → Custom evaluators
#   2. Create an LLM-as-a-Judge evaluator. Note the eval_name you give it.
#   3. Paste that name into RemoteEvaluator(eval_name="...") below.
```

## When to prefer remote over inline LLMJudge

- The same judge is reused across multiple experiments (single source of truth in the UI).
- The judge needs its own model/provider config that the experiment process doesn't have access to.
- The user wants to swap judges without changing experiment code.

For one-off rubrics tied to a single experiment, inline `LLMJudge` (under the `function` style) is simpler. See `references/evaluator-styles/function.md`.

## When NOT to use this style

If the user doesn't have a judge configured in Datadog yet and won't set one up, fall back to `function` style with an `LLMJudge` placeholder — at least the experiment runs end-to-end.
