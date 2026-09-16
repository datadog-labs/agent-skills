# RFC: LLM Obs Local Experiment Builder Skill

- **Status:** Draft
- **Authors:** Experiments Improvements workstream
- **Last updated:** 2026-08-19
- **Target:** Local coding-agent workflows for Datadog LLM Observability Experiments

## Summary

Create an opinionated coding-agent skill that turns a local LLM application or code change into a reproducible Datadog LLM Observability experiment. The skill should help an agent select the right experiment shape, construct the correct data and evaluation primitives, run a validated local experiment, optionally publish it to Datadog, and return durable artifacts and actionable follow-ups.

The skill is intentionally a workflow and design guide, not just a code generator. It should prevent experiments that run successfully but cannot answer a meaningful product or engineering question.

## Motivation

Current experiment workflows span local application code, SDK clients, HTTP APIs, datasets, evaluators, annotations, production traces, and the Experiments UI. A local coding agent needs a consistent bridge across those surfaces. Without an opinionated workflow, agents may:

- use observed production outputs as unverified ground truth;
- compare candidates on different cases or evaluator suites;
- conflate evaluators with CI pass/fail gates;
- omit cost, token, latency, and error measurements;
- lose Git, dataset, evaluator, and configuration provenance;
- publish data without a clear permission boundary; or
- generate one-off scripts that cannot be reproduced or promoted into regression tests.

## Goals

1. Provide a standardized local-to-Datadog experiment workflow.
2. Support multiple experiment goals through explicit profiles.
3. Treat datasets/case sets, tasks, evaluators, metrics, metadata, and artifacts as first-class primitives.
4. Support Python SDK, Node SDK, and HTTP API construction paths behind one conceptual model.
5. Make local execution possible before any Datadog publication.
6. Preserve provenance and produce machine-readable artifacts.
7. Make post-run findings actionable for a coding agent.
8. Compose with existing evaluator, analyzer, RCA, and auto-experiment skills.

## Non-goals

- Replacing the Datadog Experiments product or UI.
- Building a universal execution framework for every programming language in the first version.
- Automatically changing application source code without explicit user direction.
- Treating every exploratory run as a pass/fail gate.
- Copying production-specific replay infrastructure into a generic local skill.

## Terminology

### CaseSet / Dataset

A reusable, versioned collection of experiment scenarios. A Datadog Dataset remains a supported implementation, but the skill should reason about a logical CaseSet so that local fixtures, trace-derived cases, annotations, CSV/JSON, and future sources can be adapted consistently.

### Case / Dataset record

One scenario with a stable ID, input, optional expected output, metadata, tags, and provenance.

### Task

A thin adapter around the candidate application. It accepts case input and configuration, invokes the application, and returns structured output. Evaluation logic must remain outside the task.

### Evaluator

A row-level or summary-level measurement of quality, correctness, safety, cost, latency, or reliability.

### Gate policy

A separate policy that decides whether a run passes a release or CI requirement. Evaluators measure; gates decide.

## Experiment profiles

The first release should support the following opinionated profiles.

### 1. Exploration / discovery

Used to discover behavior, failure modes, clusters, opportunities, and follow-up hypotheses.

- Expected output is optional.
- Sources may include production traces, annotations, and diverse samples.
- Use operational metrics, semantic judges, error taxonomies, and novelty/diversity signals.
- Do not reduce the result to a single pass/fail score.
- Return representative cases, findings, hypotheses, and proposed next experiments.

### 2. Offline paired comparison

Used to compare a baseline and candidate on the same immutable cases. “Offline paired comparison” is preferred to “offline A/B” because this does not represent live traffic allocation.

- Use identical case IDs and evaluator suites for both variants.
- Record baseline and candidate revisions/configuration.
- Report per-case deltas and aggregate quality, cost, latency, and error differences.
- Require an explicit interpretation of acceptable tradeoffs.

### 3. Regression gate

Used to prevent a release or code change from degrading known behavior.

- Use a pinned canonical CaseSet version.
- Declare baseline selection, minimum cases, required metrics, thresholds, and error behavior.
- Prefer deterministic checks for critical contracts.
- Produce a CI-friendly exit status and machine-readable report.

### 4. Failure reproduction

Used to reproduce a production failure, validate a fix, or turn an incident into a permanent regression case.

- Preserve source trace/span and annotation provenance.
- Tag failure mode, severity, reproduction status, and source.
- Treat expected behavior as a contract; do not assume the observed output is correct.
- Compare before/after behavior when validating a fix.

### 5. Optimization loop

Used to improve prompts, models, retrieval settings, or agent implementations iteratively.

- Start with a fixed benchmark and baseline score.
- Make one focused change per iteration.
- Record a Git revision or working-tree digest for every candidate.
- Use a deterministic scoring contract and keep/revert decisions.
- Delegate iterative hill-climbing to the existing auto-experiment skill once the harness is validated.

### 6. Cost/performance benchmark

Used to measure token usage, estimated cost, latency, throughput, and reliability.

- Operational metrics are primary.
- Quality evaluators remain guardrails.
- Repeated runs may be required to estimate variance.
- Expected output is not mandatory.

Future profiles may cover robustness/adversarial tests, safety/policy checks, retrieval/grounding, tool-use correctness, and evaluator calibration.

## Primitive guidelines

### CaseSets and datasets

Every CaseSet must have a purpose, source, version, stable IDs, sampling policy, and provenance policy. A dataset must not become an unstructured dumping ground.

### Expected output

`expected_output` is one oracle type, not a required field. Use it only when the expected behavior is known. Distinguish missing values from an intentionally empty object. Production observations should be labeled as references or pseudo-labels unless independently verified.

### Tags and metadata

Use low-cardinality tags for queryable slices such as:

- `source:production`
- `failure_mode:tool_retry`
- `risk:high`
- `domain:billing`
- `variant:baseline`

Use metadata for trace IDs, annotation IDs, sampling context, case-specific configuration, and provenance. Do not put large prompts, secrets, or high-cardinality blobs in tags.

### Tasks

Tasks should expose a stable input/output contract, avoid evaluator logic, use bounded timeouts, and make side effects explicit. Supported local adapters should include importable functions, shell/JSONL commands, local HTTP endpoints, existing CLIs, and trace replay where available.

### Evaluators and gates

Use deterministic evaluators for exact contracts and semantic judges only where deterministic checks are insufficient. Always collect operational metrics: task errors, duration, token usage, and estimated cost where available. Summary evaluators are appropriate for aggregate metrics and distribution comparisons, but should not hide row-level failures.

Acceptance thresholds and CI pass/fail decisions belong in a separate gate policy.

### Provenance

Every run should record:

- experiment profile, goal, and hypothesis;
- candidate and baseline revisions;
- branch and working-tree state;
- CaseSet and dataset version;
- evaluator and rubric versions;
- model and application configuration;
- bootstrap skill version;
- local artifact paths; and
- Datadog project, dataset, experiment, and trace IDs when published.

## Proposed workflow

### Phase 0: Repository and environment discovery

Inspect project instructions, Git state, application entry points, existing tests, and available execution commands. Infer the candidate from the current worktree and select a safe baseline. Do not checkout over the user’s current branch; use a temporary worktree or an explicit baseline command.

### Phase 1: Plan and profile selection

Create an `ExperimentPlan` containing the goal, hypothesis, profile, comparison, CaseSet source, task adapter, evaluator suite, metrics, gate policy, permissions, and expected outputs. Ask the user when the goal or permission boundary is ambiguous.

### Phase 2: Case source selection

Support existing Datadog datasets, local fixtures, trace samples, annotation queues, prior experiment runs, and synthetic cases. Recommend the source based on the selected profile.

### Phase 3: Evaluation contract

Define primary and secondary metrics, row-level checks, summary metrics, expected-output policy, minimum sample size, timeout/error behavior, and acceptance policy. Reject plans with no meaningful evaluation signal.

### Phase 4: Suite generation

A durable suite may use:

```text
experiments/<slug>/
  experiment.yaml
  cases.py
  task.py
  evaluators.py
  run.py
  README.md
  artifacts/
```

Ephemeral generated experiments should default to `.ddog/experiments/<slug>/`. Adding a tracked suite to the repository requires user approval.

### Phase 5: Validation

Validate case IDs, input completeness, expected-output semantics, dataset comparability, evaluator coverage, metric names, acceptance metrics, timeouts, concurrency, secret safety, provenance, and source mutation boundaries.

### Phase 6: Single-case smoke test

Run one representative case before the full suite. Report task output, evaluator results, trace IDs, artifacts, and schema or execution problems.

### Phase 7: Local execution

Support `list`, `check`, `run-case`, `run`, and `compare` operations with bounded concurrency, per-case timeouts, cancellation, and structured output.

Local execution should not require Datadog writes.

### Phase 8: Optional publication

Publishing requires explicit confirmation. The skill must clearly state whether it will create/update a project or dataset, send prompts and outputs, access production traces, or incur evaluator/API cost. Published runs must use pinned dataset versions and carry the same manifest metadata as local runs.

### Phase 9: Handoff and next actions

Return local artifact paths, Datadog IDs and comparison URL, the strongest findings, and recommended next actions such as promoting cases to a regression dataset, creating a CI gate, rerunning failed cases, invoking experiment analysis, or starting an optimization loop.

## Adapter architecture

The generalized bootstrap skill should expose one conceptual experiment model with multiple construction adapters:

1. Python SDK adapter.
2. Node SDK adapter.
3. HTTP API adapter.

The adapter should be selected based on user preference, repository language, installed dependencies, and required API capability. The generated plan and manifest should be adapter-independent wherever possible.

The Python bootstrap skill must be refactored before implementing this new skill. The refactored skill must document the actual local Node SDK API from the built `dd-trace-js` library and the actual HTTP backend API from `dd-source`; it must not infer or invent signatures.

## Permission model

### Inspect

Read repository files and Git state. No network or mutations.

### Local execution

Write approved generated files and artifacts and execute local commands. No Datadog writes.

### Datadog publication

Create/update projects or datasets and publish runs. Requires explicit confirmation.

### Production/external data

Query production traces, annotation queues, external services, or sandboxes. Requires explicit confirmation and secret-safe handling.

Credentials must remain in environment/configuration mechanisms and never be written into artifacts.

## Lessons from Alex’s experiment framework

Adopt these patterns:

- immutable and validated cases;
- a central experiment specification;
- suite-owned task and evaluator behavior;
- thin runners;
- prepared, run-scoped execution resources;
- separate local and published execution paths;
- explicit persisted and acceptance metrics;
- Git, dataset, evaluator, and configuration provenance;
- atomic, secret-safe artifacts;
- preflight validation; and
- bounded execution and cleanup.

Do not copy production-specific replay, frozen-world, or sandbox infrastructure into the generic skill unless a future profile requires it.

## Composition with existing skills

The new skill should compose with, rather than duplicate:

- `llm-obs-experiment-py-bootstrap` / its generalized successor for SDK script generation;
- `llm-obs-eval-bootstrap` for evaluator generation;
- `llm-obs-experiment-analyzer` for post-run analysis;
- `agent-observability-auto-experiment` for hill-climbing;
- `llm-obs-trace-rca` for failure-driven experiments; and
- `llm-obs-eval-pipeline` for broad production-trace-to-experiment workflows.

The new skill’s unique responsibility is choosing the right experiment shape for a local coding goal and generating a validated, reproducible suite around that shape.

## Acceptance criteria

The implementation is ready for initial use when it can:

1. Select among exploration, paired comparison, regression, failure reproduction, optimization, and cost/performance profiles.
2. Generate a complete plan and manifest before execution.
3. Construct equivalent experiments through Python SDK, Node SDK, and HTTP API adapters.
4. Support at least one local execution path without Datadog publication.
5. Validate dataset/case, evaluator, metric, provenance, timeout, and secret-safety requirements.
6. Run a single-case smoke test and a bounded full suite.
7. Persist local artifacts and return a clear next-action handoff.
8. Publish only after explicit confirmation.

## Implementation progress

- [x] RFC captured the goal, profiles, primitive guidance, workflow, permissions, and acceptance criteria.
- [x] Refactored the legacy Python bootstrap skill into the generalized `agent-observability-experiment-bootstrap` contract with Python and Node SDK adapters.
- [x] Added the initial `agent-observability-local-experiment` workflow skill.
- [ ] Validate the generalized bootstrap against representative Python, Node, and HTTP generation requests.
- [ ] Add runtime helpers/templates only where repeated usage demonstrates a need.
- [ ] Resolve the cross-adapter manifest and artifact schema.
- [ ] Define the first end-to-end regression-gate example.

## Open questions

1. What is the canonical cross-adapter manifest schema?
2. Which HTTP API endpoints are stable enough for generated skill examples?
3. Which Node SDK capabilities are available in the current local build versus planned API surface?
4. Should the first generated runner be Python-first with Node/HTTP adapters, or should each adapter emit native code?
5. What is the canonical local artifact directory and ignore policy?
6. How should baseline runs be selected and cached for regression gates?
7. Which evaluator outputs and operational metrics are guaranteed to be available across all adapters?
8. How should local report links map back to Datadog comparison pages?

## Decision log

- Use explicit experiment profiles instead of one generic free-form workflow.
- Treat exploratory and CI workflows as different modes over shared primitives.
- Use a logical CaseSet abstraction while retaining Dataset compatibility.
- Keep `expected_output` optional and semantically explicit.
- Separate evaluator measurements from gate decisions.
- Make local execution the default and Datadog publication opt-in.
- Refactor the existing Python bootstrap skill into a generalized SDK/HTTP bootstrap skill before implementing the local experiment builder.
