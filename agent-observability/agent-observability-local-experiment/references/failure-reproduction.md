# Profile: Failure Reproduction

**Profile ID:** `failure_reproduction`

## Select this profile when

The user wants to reproduce a known production failure, validate a fix, or turn an incident into a permanent regression case.

## CaseSet

Preserve source trace, span, annotation, incident, and sampling provenance. Tag the failure mode, severity, source, and reproduction status without placing large prompts or secrets in tags.

Keep the observed output separate from the expected contract. An observed production result is evidence of what happened, not proof of what should happen.

## Evaluation contract

Use evaluators for:

- reproduction of the original failure signature;
- the independently defined expected behavior/contract;
- structured or safety constraints;
- before/after differences; and
- task, evaluator, and operational errors.

Report trace/span IDs and source annotations as metadata where permitted, not as fabricated canonical identifiers.

## Acceptance and handoff

When validating a fix, define baseline and candidate explicitly. The gate should state whether the original failure must disappear, what expected contract must pass, and which unrelated regressions block acceptance.

The handoff should include:

- reproduced/not reproduced status;
- source provenance;
- failure mode and severity;
- baseline/candidate evidence; and
- whether the case should be promoted into a regression CaseSet.

## Anti-patterns

- copying the observed output into `expected_output` without validation;
- dropping trace/annotation provenance during case conversion; or
- declaring a fix successful because the original error disappeared while the expected contract still fails.
