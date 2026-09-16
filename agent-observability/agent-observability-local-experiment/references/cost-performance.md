# Profile: Cost / Performance Benchmark

**Profile ID:** `cost_performance`

## Select this profile when

Operational behavior is the primary decision: latency, token use, estimated cost, throughput, variance, reliability, or resource efficiency.

## CaseSet

Use a representative CaseSet with enough volume or repeated runs to estimate variance. Record concurrency, repetitions, warm-up behavior, sampling, and environment configuration.

Expected output is optional unless quality is also a decision criterion.

## Evaluation contract

Primary metrics normally include:

- duration/latency distributions;
- token usage;
- estimated cost;
- throughput;
- task and evaluator error rate; and
- variance or confidence intervals when repeated runs are used.

Keep quality, structured-output, safety, or grounding evaluators as guardrails. Do not trade away a critical quality contract silently for a lower cost or shorter latency.

## Acceptance and handoff

A cost/performance run may have no single pass/fail gate, but it must state quality guardrails and operational comparison rules. Report distributions, repetitions, outliers, incomplete rows, and environment provenance rather than only averages.

The handoff should recommend whether to adopt the candidate, repeat the benchmark with more variance coverage, or investigate a quality/reliability regression.

## Anti-patterns

- comparing one cold run against one warm run;
- reporting averages without sample count or variance;
- omitting quality guardrails; or
- estimating cost without recording the model/configuration used.
