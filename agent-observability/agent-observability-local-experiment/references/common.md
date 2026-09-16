# Shared Local Experiment Contract

Load this file together with exactly one profile reference.

## Experiment plan

Before building an artifact, record:

```yaml
experiment_type: <profile id>
goal: <decision this run supports>
hypothesis: <testable expectation>

candidate:
  source: working_tree | git_ref | command | module
  revision: <commit or working-tree digest>

baseline:
  source: none | git_ref | prior_run | command
  revision: <commit or run ID>

case_source:
  kind: dataset | local_fixture | trace_sample | annotations | prior_run | synthetic
  identity: <path, name/id, or trace selection>
  version: <pinned version when applicable>

task:
  adapter: python | node | existing_http
  source: <verified function, command, endpoint, or explicit placeholder>

metrics:
  primary: [<metrics>]
  secondary: [<metrics>]
  operational: [error_rate, duration, token_usage, estimated_cost]

acceptance_policy:
  enabled: true | false
  min_cases: <number>
  thresholds: <explicit policy>

permissions:
  local_write: approved | ask
  datadog_publish: approved | ask
  production_data: approved | ask
```

Do not claim comparability when candidate and baseline use different cases, evaluator inputs, or configuration without explaining the difference.

## CaseSet and records

Every case should have:

- a stable ID;
- input data;
- optional `expected_output` only when desired behavior is known;
- metadata for trace IDs, annotations, sampling context, and configuration;
- low-cardinality `key:value` tags; and
- source and version provenance.

Keep secrets, full prompts, and large outputs out of tags. Scrub obvious PII and credential-like values before embedding or publishing local cases.

## Tasks

The task is a thin, reproducible adapter. It should accept case input and configuration, invoke the candidate, return structured output and diagnostic metadata, avoid evaluator logic, use bounded timeouts, and make external side effects explicit.

Supported task shapes include importable functions, shell/JSONL commands, existing local HTTP endpoints, CLIs, and approved trace replay. A missing task source is a blocker or an explicit placeholder; it is never an invented import.

## Evaluators and gates

Evaluators measure behavior. Gates decide whether the result is acceptable. Keep them separate.

Every evaluator needs a stable name, input contract, metric interpretation, and explicit behavior for missing data and errors. Evaluator errors must not become passing values.

Typical evaluator families:

- deterministic output/schema and contract checks;
- tool-call correctness;
- retrieval or grounding;
- safety/policy compliance;
- semantic quality;
- error taxonomy;
- latency, token, cost, reliability, and throughput metrics; and
- paired deltas or aggregate distributions.

Use a gate only when the profile and user decision require one. Exploration can produce useful findings without a pass/fail gate.

## Validation and execution

Before a full run:

- validate profile, goal, hypothesis, candidate, and baseline;
- validate stable and comparable cases;
- validate evaluator labels and thresholds;
- validate task source and adapter support;
- validate timeout, retry, and concurrency bounds;
- validate secret safety and non-destructive output paths; and
- run one representative smoke case.

A full run must preserve deterministic ordering or a sampling seed, per-case task/evaluator errors, incomplete rows, timing, and artifacts. Never mark a partially completed run successful without showing the partial status.

## Bootstrap handoff

When the plan is ready, call `agent-observability-experiment-bootstrap` for Python or Node construction. Provide the plan fields and ask it to emit the selected artifact. Consume its syntax/SDK validation result and run command; do not recreate the SDK implementation in this skill.

For local-only validation, keep publication disabled. For Datadog publication, state the project/dataset, record count, data leaving the environment, expected cost, credentials, and baseline/comparison behavior before asking for confirmation.

## Provenance and handoff

Preserve:

- profile, goal, and hypothesis;
- candidate/baseline revisions;
- CaseSet identity/version and sampling rule;
- task source and adapter;
- evaluator labels/versions and gate policy;
- model/configuration/environment details;
- local artifact paths; and
- Datadog IDs/URLs and failed row IDs when published.

The final handoff must state the profile, decision, results, failures, artifacts, publication status, and the next recommended action.
