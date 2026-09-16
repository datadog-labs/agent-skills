---
name: agent-observability-local-experiment
description: Map a user's LLM Observability goal to one reproducible experiment profile, select its cases and evaluation contract, then delegate actual Python or Node experiment construction to agent-observability-experiment-bootstrap.
---

# LLM Observability Local Experiment

This skill is the **goal-to-experiment-profile selector and orchestrator**. It answers:

> What question is the user trying to answer, and which experiment shape can answer it?

It does not contain SDK construction recipes. After the profile, cases, task, evaluators, and permissions are clear,
it must call `agent-observability-experiment-bootstrap` to build the actual Python or Node experiment artifact.

## Responsibilities

This skill should:

1. Understand the user's decision, goal, and hypothesis.
2. Select exactly one primary experiment profile.
3. Load the shared contract and only the selected profile reference.
4. Define the CaseSet, task contract, evaluators, metrics, provenance, and gate policy at a planning level.
5. Delegate adapter-specific artifact construction to `agent-observability-experiment-bootstrap`.
6. Coordinate validation, a single-case smoke test, bounded local execution, and the final handoff.

Do not generate SDK code directly in this skill. Do not load every profile reference “for completeness.”

## Profile selection

Select the profile from the user's purpose, not from the available implementation technology:

| Profile ID | Select when the user wants to… | Primary result |
| --- | --- | --- |
| `exploration` | understand behavior, discover failure modes, or find opportunities | findings, clusters, hypotheses, and representative cases |
| `paired_comparison` | compare a baseline and candidate on identical cases | per-case and aggregate deltas plus tradeoff interpretation |
| `regression_gate` | protect known behavior in a release or CI pipeline | machine-readable pass/fail decision against thresholds |
| `failure_reproduction` | reproduce an incident, validate a fix, or promote a failure to a case | reproduction status and before/after contract evidence |
| `optimization` | prepare a benchmark for iterative prompt/model/retrieval/agent improvement | validated baseline and scoring contract for iteration |
| `cost_performance` | measure latency, tokens, cost, throughput, variance, or reliability | operational comparison with quality guardrails |

### Selection decision rules

Use these rules in order:

1. If the user wants to **learn what is happening**, select `exploration`.
2. If the user names a **baseline and candidate** and wants to compare them on the same cases, select `paired_comparison`.
3. If the result must **block or allow a release**, select `regression_gate`.
4. If the starting point is a **known production failure or incident**, select `failure_reproduction`.
5. If the user wants to **iterate toward improvement**, select `optimization`; require a fixed benchmark before any loop.
6. If **operational efficiency** is the primary decision, select `cost_performance`.
7. If multiple goals are present, choose one primary profile and represent the others as secondary metrics or follow-up experiments. Do not silently combine incompatible gate policies.

Ask a focused question only when the goal, comparison, case source, or acceptance decision cannot be inferred. Do not invent a pass/fail gate for exploration.

## Context loading

Load context lazily:

1. Read `references/common.md` for shared planning, execution, provenance, and safety rules.
2. Read exactly one profile reference:
   - `references/exploration.md`
   - `references/paired-comparison.md`
   - `references/regression-gate.md`
   - `references/failure-reproduction.md`
   - `references/optimization.md`
   - `references/cost-performance.md`
3. When building the artifact, invoke `agent-observability-experiment-bootstrap`. Let that skill load the selected Python or Node SDK reference; do not duplicate or pre-load SDK API details here.

The profile reference is the source of truth for profile-specific cases, metrics, evaluator guidance, gates, and handoff criteria.

## General planning rules

Before construction, resolve these shared concepts:

- **Goal and hypothesis:** what decision the run supports and what change is being tested.
- **Candidate and baseline:** working tree, Git revision, prior run, or explicitly absent.
- **CaseSet:** stable case IDs, input, optional expected output, metadata, tags, source, and version.
- **Task:** a reproducible adapter from case input to the application under test.
- **Evaluators:** named measurements with clear inputs and missing-data/error behavior.
- **Gate policy:** separate acceptance thresholds from evaluator measurements.
- **Operational metrics:** errors, duration, tokens, cost, throughput, or variance when available.
- **Provenance:** Git/configuration/model/evaluator/CaseSet identity and permission decisions.

`expected_output` is optional. Never copy an observed production output into `expected_output` without validation that it represents the desired behavior.

## Build delegation

Call `agent-observability-experiment-bootstrap` **when the actual experiment is being built**, after the selected profile reference has been applied. Do not manually write Python or Node SDK code in this skill.

Pass the bootstrap skill:

- the resolved purpose and hypothesis;
- the selected Python or Node adapter and artifact format;
- project name and provenance;
- CaseSet path/records and pinned version when applicable;
- real task source or an explicit placeholder/blocker;
- evaluator labels, rubrics, metrics, and gate policy;
- requested local artifact path; and
- the permission decision that local execution is allowed and publication is not yet approved.

Conceptually, the handoff is:

```text
/agent-observability-experiment-bootstrap
  --adapter <python|node>
  --purpose "<resolved goal>"
  --task-source <verified task source>
  --output <local artifact path>
```

The exact SDK syntax, dataset APIs, evaluator APIs, environment setup, and validation commands belong to the bootstrap skill and its selected adapter reference.

If the task is an existing local HTTP endpoint, pass that runner contract only when the user supplied or approved it. Do not ask the bootstrap skill to invent an HTTP client or unverified API.

## Orchestration after construction

After bootstrap returns an artifact:

1. Check the artifact and manifest against `references/common.md` and the selected profile reference.
2. Run one representative case as a smoke test.
3. Ask for confirmation before a costly, production-connected, or Datadog-publishing full run.
4. Run the bounded local suite with per-case errors and timeouts preserved.
5. Publish only after explicit confirmation, using the bootstrap artifact and pinned provenance.
6. Return local paths, run IDs/URLs when available, the gate decision, findings, failures, and next actions.

For `optimization`, do not autonomously change code or start hill-climbing. Hand off to `agent-observability-auto-experiment` only after a validated baseline and scoring contract exist.

## Safety boundaries

- Do not modify application source code unless explicitly asked.
- Do not overwrite the current worktree or silently checkout a baseline over it.
- Do not write credentials into plans, manifests, generated artifacts, or reports.
- Do not publish prompts, outputs, traces, datasets, or annotations without confirmation.
- Preserve task errors, evaluator errors, incomplete rows, and partial publication separately.
- Treat production traces and observed outputs as evidence or references, not automatic ground truth.
- Stop and report a blocker when the task source, CaseSet, adapter capability, or provenance is not reproducible.

## Composition

Use adjacent skills only when their output is needed:

- `agent-observability-experiment-bootstrap`: build the Python or Node artifact.
- `agent-observability-trace-rca`: diagnose a failure before selecting `failure_reproduction`.
- `agent-observability-eval-bootstrap`: generate evaluator candidates from traces or a failure hypothesis.
- `agent-observability-experiment-analyzer`: analyze published results or paired deltas.
- `agent-observability-auto-experiment`: iterate after the `optimization` baseline is validated.
- `agent-observability-eval-pipeline`: orchestrate broad production-trace-to-experiment workflows.

## Completion handoff

Return:

```text
Profile:
Goal:
Hypothesis:
Candidate:
Baseline:
CaseSet:
Adapter:
Evaluators:
Primary metrics:
Gate decision:
Local artifacts:
Datadog IDs/URL:

Next actions:
1. ...
2. ...
3. ...
```

A local experiment is ready to hand off only when the profile is explicit, the selected profile reference was applied, the bootstrap artifact passed validation, the smoke test ran or its blocker is recorded, and per-case results/provenance are preserved.
