# Profile: Exploration / Discovery

**Profile ID:** `exploration`

## Select this profile when

The user wants to understand behavior, discover failure modes, identify clusters or opportunities, or generate hypotheses. The user is not asking for a predetermined release gate.

## CaseSet

Prefer a small, inspectable, diverse sample from traces, annotations, representative fixtures, or deliberately sampled cases. Expected output is optional. Record the sampling rule, source/version, stable IDs, and privacy handling.

Do not use a single narrow happy-path case when the purpose is discovery.

## Evaluation contract

Use measurements that explain behavior:

- semantic quality or structured-output checks when signals exist;
- operational errors, duration, tokens, and cost;
- error taxonomy and failure-mode labels;
- novelty, diversity, clustering, or representative-case signals; and
- summary evaluators that prioritize findings.

Do not collapse the result into one pass/fail score unless the user explicitly requests a secondary score.

## Acceptance and handoff

`acceptance_policy.enabled` is normally `false`. The result should return:

- representative successful and failed cases;
- clusters or failure modes;
- uncertainty and missing-data notes;
- hypotheses supported by the observations; and
- proposed next experiments, such as a paired comparison or regression fixture.

Observed production output is evidence, not automatically the desired answer.

## Anti-patterns

- inventing expected outputs from observed traces;
- silently converting exploratory findings into a release gate; or
- reporting only an aggregate score without examples or hypotheses.
