# Profile: Regression Gate

**Profile ID:** `regression_gate`

## Select this profile when

The result must protect known behavior in a release, pull request, deployment, or CI pipeline.

## CaseSet

Use a pinned canonical CaseSet with stable IDs and a declared minimum case count. Record its dataset/fixture identity and version. Do not silently sample a different set for convenience.

## Evaluation contract

Prefer deterministic checks for critical contracts, including schema, required fields, exact behavior, safety, and error conditions. Add semantic or operational evaluators only when they are part of the declared decision.

Declare:

- required evaluator names and versions;
- required metrics;
- behavior for task/evaluator errors and incomplete rows; and
- thresholds for every gate metric.

## Acceptance and handoff

`acceptance_policy.enabled` must be `true`. A gate should include a machine-readable decision and CI-friendly exit status. A typical policy is:

```yaml
acceptance_policy:
  enabled: true
  min_cases: 1
  required:
    - no_task_errors
    - no_critical_case_failure
  thresholds:
    error_rate: 0.0
    contract_pass_rate: 1.0
```

A regression evaluator measures behavior; the acceptance policy decides whether the change passes.

## Anti-patterns

- using an unpinned or silently changing dataset;
- treating missing rows as passing;
- allowing evaluator errors to become false or zero scores; or
- defining a threshold without stating what happens on timeout or task failure.
