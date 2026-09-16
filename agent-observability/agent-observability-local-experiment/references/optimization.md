# Profile: Optimization Loop Preparation

**Profile ID:** `optimization`

## Select this profile when

The user wants to improve a prompt, model, retrieval setting, tool policy, or agent implementation iteratively.

This profile prepares a reliable benchmark and scoring contract. It does not authorize autonomous code changes or an unbounded hill-climb.

## CaseSet

Use a fixed benchmark with stable IDs, pinned version, and representative coverage. Keep the CaseSet and evaluator inputs unchanged across iterations unless the user explicitly approves a new benchmark version.

## Evaluation contract

Define:

- a primary quality metric;
- deterministic critical-contract checks;
- secondary quality metrics;
- cost, latency, token, and error guardrails; and
- missing-data, timeout, and evaluator-error behavior.

Run and preserve a baseline before the first candidate change.

## Acceptance and handoff

Record one focused change per iteration, the Git revision or working-tree digest, the measured delta, and the keep/revert decision. A gate should state acceptable quality, cost, latency, and reliability tradeoffs.

After the benchmark, baseline, and scoring contract are validated, hand off the iterative loop to `agent-observability-auto-experiment`. The local experiment skill should not silently start that loop.

## Anti-patterns

- optimizing against a moving CaseSet;
- making multiple untracked changes before scoring;
- starting the loop without a baseline; or
- keeping a candidate without recording the decision and revision.
