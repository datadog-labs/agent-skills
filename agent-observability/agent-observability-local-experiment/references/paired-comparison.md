# Profile: Offline Paired Comparison

**Profile ID:** `paired_comparison`

## Select this profile when

The user wants to compare a baseline and candidate implementation, prompt, model, retrieval configuration, or agent setting on the same cases. This is offline comparison, not live-traffic A/B allocation.

## CaseSet

Use one immutable CaseSet snapshot for both variants. Preserve identical case IDs, inputs, expected-output policy, evaluator inputs, and sampling configuration. Record the dataset/fixture version and both candidate revisions.

Pause if the variants cannot run on comparable cases.

## Evaluation contract

Use the same stable evaluator names and versions for both variants. Report:

- per-case quality and error deltas;
- aggregate quality and error changes;
- latency, token, and estimated-cost differences; and
- missing, failed, or non-comparable rows.

The evaluator measures each variant; the gate interprets the tradeoff.

## Acceptance and handoff

Declare an explicit tradeoff rule, for example:

```yaml
acceptance_policy:
  enabled: true
  thresholds:
    quality_delta: ">= 0"
    error_rate_delta: "<= 0.01"
    estimated_cost_delta: "<= 0.10"
```

The handoff should identify the largest wins and regressions and state whether the candidate is acceptable under the user's priorities.

## Anti-patterns

- changing the CaseSet between baseline and candidate;
- calling the run an online A/B test; or
- selecting the winner from one aggregate score while hiding per-case regressions or operational cost.
