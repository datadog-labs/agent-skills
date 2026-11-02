---
name: agent-observability-online-experiment
description: >-
  Load this skill when the user wants to run an online experiment (live-traffic A/B test) on an
  LLM application instrumented with Agent Observability: compare two versions of a prompt, model,
  or behavior using a Datadog feature flag, score metrics from evaluations, and cost and token
  metrics. Covers creating the flag, wiring the app, creating the experiment, attaching metrics,
  setting the traffic split, and starting it. Triggers: online experiment, A/B test an LLM app,
  test a prompt change on live traffic, experiment with feature flag and evaluation score.
---

# Set up an online experiment on an LLM application

An online experiment splits live traffic between a control and a treatment version of an LLM application. A Datadog feature flag assigns each **subject** to a variant, and an Agent Observability evaluation score (plus cost and token usage) measures the outcome. This skill walks through the whole setup, from the flag to starting the experiment.

Online experiments for Agent Observability may require the feature to be enabled for the user's organization. If any step reports the feature is unavailable, tell the user to contact their Datadog representative rather than retrying.

## When to use this skill

- The user wants to test a change (prompt, model, retrieval strategy, tool set) on real traffic rather than a fixed dataset.
- The user has a change on a branch and wants to know whether it is better, cheaper, or both in production.
- The user wants an experiment whose metrics come from Agent Observability evaluations, token counts, or cost.

Do **not** use it for offline experiments over a dataset (use the LLM Observability experiment tooling), for analyzing an experiment that already ran (`agent-observability-experiment-analyzer` or the Datadog experiments tooling), or for promoting an existing offline experiment.

## Ground rules

- **Use one setup approval checkpoint.** Before any write, finish the read-only discovery in Phase 0, make sensible recommendations, and present the complete proposed setup in one confirmation request. Include the flag, variants, metric definitions or reused metric IDs, desired-change directions, environment, subject, traffic split, code change, and every organization write you intend to make. Once the user approves that proposal, do not ask again before each flag, draft, link, metric, enablement, or allocation write that was clearly listed.
- **Make production risk explicit in that same checkpoint.** If the proposal enables a production flag or replaces production allocations, say that the change takes effect immediately. A clear approval of that production proposal is sufficient; do not add a second confirmation that repeats the same information. Ask again only if read-back reveals a materially different or destructive replacement that was not in the approved proposal.
- **Starting is separate unless already explicit.** Start only if the user explicitly asked to start the experiment. If the original request included starting, include it in the single setup proposal; otherwise finish with a draft and ask no start question until the user requests it.
- **Go one step at a time and read back.** After each write, read the object back and report what the server actually stored, not what you sent.
- **Never attach metrics in parallel or alongside other metric edits.** The attach operation sends the experiment's complete metric set to the server, so concurrent edits can overwrite each other.
- **Report failures plainly.** If a step is refused (permissions, production restrictions), say so and give the user the manual alternative. Do not look for a workaround.
- **Do not guess identifiers.** Environment IDs, flag IDs, metric IDs, and variant keys all come from the server.

## Phase 0 — Discover, recommend, and confirm once

Do not interrupt the investigation to ask for one input at a time. First perform all available read-only discovery:

- Inspect the active tool list and verify that the session has the `llmobs`, `experiments`, and `feature-flags` capabilities needed by this workflow: environment discovery; flag lookup, creation, enablement, and allocation management; experiment lookup, creation, linking, and start; and experiment-metric lookup, creation, and attachment. The skill's toolset configuration controls its visibility with OR semantics; it does not guarantee that all three toolsets are enabled. If capabilities are missing, report every missing group and the toolset to enable, then stop before any write instead of repeatedly retrying individual operations.
- Inspect the application and its deployment configuration. Confirm it reports to Agent Observability, find where it submits evaluations (if it does), find the code path the treatment changes, and infer its `ml_app`, `DD_ENV`, and stable subject identifier.
- List environments, look for an existing flag and experiment for the proposed change, and search existing experiment metrics before proposing new objects.

Then establish the complete proposal. Infer from context and use the defaults below wherever reasonable:

| Input | Notes |
|---|---|
| Application and its ML app name | The `ml_app` the application reports to Agent Observability. |
| Control and treatment | What differs between them. Usually the control is the current code path and the treatment is the change under test. |
| Hypothesis | Draft one or two sentences stating the expected mechanism and effect on quality, tokens, or cost. |
| Score metric(s) | Recommend the most relevant existing numeric evaluation score, including its desired-change direction. See Phase 4. |
| Environment | Infer from deployment configuration and the user's live-traffic goal. An experiment links to exactly one environment. |
| Traffic split | Default 50/50 control/treatment. |
| Flag name | Default to a short, descriptive, kebab-case name. |
| Subject | What counts as one subject. See Phase 2. |

When metric-name search is needed, try naming variants before concluding that no metric exists: spaces, hyphens, underscores, and a meaningful substring. Inspect duplicate definitions rather than choosing by name alone. Prefer a definition already used by active experiments only when its source, columns, filters, aggregation, and desired-change direction match the proposal. Do not infer ML-app scope from a metric's name. If the user's requested existing metric lacks an ML-app filter or otherwise differs from the recommended definition, disclose that in the proposal and recommend whether to reuse or replace it.

Present one compact confirmation request containing:

- application and `ml_app`;
- control, treatment, and hypothesis;
- proposed flag key and Boolean variants;
- subject identifier and code location where the flag will be evaluated;
- target environment and 50/50 or proposed split;
- primary evaluation metric, stored definition or proposed definition, and desired-change direction;
- token and cost secondary metrics;
- whether existing objects will be reused or new ones created;
- the complete list of writes, including production enablement/allocation when applicable;
- whether starting is included (only when the user already requested it).

End with one question: **Confirm this complete setup, or tell me what to change.** Use follow-up questions only for a genuinely blocking ambiguity that cannot be resolved from code, server state, or a safe default.

## Phase 1 — Create the feature flag

Create a **Boolean** flag with two variants: `control` = `false` and `treatment` = `true`. Make `control` the default variant.

- Do **not** add allocations, targeting rules, or a percentage rollout when creating the flag. Linking it to an experiment does not create an allocation by itself: create or update one experiment-linked `FEATURE_GATE` allocation in Phase 5 after linking. Extra rules interfere with the split.
- Check first whether a flag with that key already exists, and reuse it if the user says so.
- If the organization has no feature flags at all, the server may point to a guided onboarding flow for first-time setup. Direct creation is fine here when the user has asked for a specific flag; mention the onboarding flow only if the user seems new to Datadog Feature Flags.
- Record the flag's ID and the IDs of its two variants.

Listing environments gives the available ones (typically Production, Staging, Development). Note which are production: some actions on production are not available through the server and must be done in the Datadog UI.

## Phase 2 — Wire the application

The application must do three things for every subject: evaluate the flag, run the matching behavior, and report the score and spans under the same subject identifier.

**Choose the subject identifier.** It must be stable across the whole interaction being measured.
- Application with signed-in users: a stable user ID.
- No users, but sessions: the session ID.
- Autonomous workflows: generate one UUID at the start of a run and reuse it for the whole run.

Use the same value in three places: the feature flag evaluation context's `targetingKey`, the `subject_identifier` tag on the evaluation score (the join key the Datadog online experiments guide requires), and the `subject_identifier` tag on the root span of each trace, which is what the cost and token metrics read. If these differ, Datadog cannot join exposure to outcome and the experiment shows missing metric data.

**Evaluate the flag.** Follow the Feature Flags SDK for the application's language. For Python this means a recent `ddtrace` plus the OpenFeature SDK, registering the Datadog provider once, and evaluating a Boolean flag with `targeting_key` set to the subject identifier. The SDK needs the Datadog API key, site, and `DD_ENV` set. Default the evaluation to `false` (control) and catch failures so a flag outage degrades to control instead of breaking the app.

Feature-flag management guidance may point to a React integration resource regardless of the application's language. Treat that as generic tool guidance, not as an instruction to add React code. Follow the SDK for the application's actual language and prefer an existing working provider/evaluation pattern in the repository.

**Choose where the variant is applied.** If the behavior is baked into state that persists (for example a system prompt stored in a conversation object), evaluate the flag once when that state is created, not on every call, so a subject cannot change variants mid-interaction. Derive the treatment from the control in code where possible so the two cannot drift.

**Report the score.** Submit the score evaluation with the Agent Observability SDK and add `subject_identifier` to its tags. Only evaluations with a numeric `score` metric type can be experiment metrics; Boolean, categorical, and other types cannot.

**Tag the spans and the evaluations.** Add the `subject_identifier` tag (for example `subject_identifier:73b2efe1-03e3-40c1-ab6a-bd2a6cfbc865`) to the Agent Observability spans and to the evaluation scores in the application's own code. Datadog converts the tag into the event's `@usr.id` field during ingestion, and that field is what the experiment joins to the flag exposure. Set the tag, not `@usr.id`: the SDK does not set that field directly, and the conversion happens in Datadog's backend.

Cost and token metrics read each completed root trace's rollup, so the tag must be on the trace's root span: the outermost workflow or agent span. Annotate that span through the SDK's span annotation call. For a span-scoped managed evaluator, also add the same tag to every span that evaluator scores because its outcome derives the subject from the evaluated span's own tags. A trace-scoped managed evaluator can propagate the root span's subject to its outcomes. Verify the evaluator's scope before reusing its metric. Tagging evaluated child spans does not double count cost or tokens because those metrics still measure only root traces.

The code must be **deployed** for the experiment to collect anything. Tell the user plainly that the flag, allocation, and experiment can exist before the deploy but record nothing until the application evaluates the flag in the linked environment, and that the application's `DD_ENV` must match that environment.

## Phase 3 — Create the experiment and link the flag

1. Create a draft experiment with a clear name and the hypothesis. The primary metric can be attached afterwards, so it is fine to create the draft before the metrics exist.
2. Link the flag from Phase 1 to the experiment.
3. Read the experiment back. A draft may report an incomplete set-up state until it is started; that alone is not an error. The start step reports concrete blockers if there are any.
4. Resolve the draft's subject type and verify that its Product Analytics attribute is `@usr.id`, matching the field produced from Phase 2's `subject_identifier` tag. Starting requires a subject type, but do not accept an arbitrary server-selected default: subject types mapped to another field cannot join these Agent Observability outcomes. Explicitly select an `@usr.id`-compatible subject type before proceeding. If the available capabilities cannot inspect or configure the mapping, direct the user to the Datadog UI and stop until it is confirmed.

An alternative to creating the flag first is a single operation that creates the flag together with an experiment-linked allocation (the `create-experiment-feature-flag` tool). It needs the experiment and the environment to exist up front. The step-by-step path above keeps each write small and easy to read back, which is why this skill uses it.

Report the experiment's name and ID. Construct its direct Product Analytics URL as
`https://<app-host>/product-analytics/experiments/<experiment-id>`, using the ID returned by the server and the application host for the organization's Datadog site. For US1 use `app.datadoghq.com`; for EU use `app.datadoghq.eu`; regional sites such as `us5.datadoghq.com` use that host directly; and `datad0g.com` uses `dd.datad0g.com`. Include this link in the final handoff after completing the setup instructions, whether the experiment remains a draft or has been started.

## Phase 4 — Create and attach metrics

### Reuse before creating

List the existing experiment metrics first. If a metric already measures the user's score for this ML app, reuse it. If any existing metric is built on Agent Observability data, read its stored definition and copy its column names instead of guessing them. The same applies to cost and token metrics.

### Primary metric: the score evaluation

The primary metric is the score approved in Phase 0. If the user named none, list the evaluations that exist for the ML app and recommend the most relevant numeric score in the single setup proposal. Do not pause here for another choice unless read-back contradicts the approved definition.

- **Source:** Agent Observability evaluation metrics, filtered to the ML app and the evaluation label.
- **Aggregation:** average of the score value.
- **Desired change:** the direction that counts as an improvement, one of `METRIC_INCREASES` or `METRIC_DECREASES`. This field is required, and a wrong value inverts how every result reads, so include it explicitly in the Phase 0 proposal.

Example definition (the columns are the standard evaluation event fields: label, score value, and ML app):

```
data source:        DATADOG
source type/subtype: LLMOBS / LLMOBS_EVAL_METRICS
aggregation:        average of @score_value
source filter:      @ml_app = <ml_app> AND @label = <evaluation label>
desired change:     METRIC_INCREASES (for a score where higher is better)
```

### Secondary metrics: tokens and cost

Add two secondary metrics so the user can see what the change costs. Both measure completed LLM Observability root traces and are averaged per trace:

- **Average trace tokens:** average of `@trace.total_tokens`. Desired change: `METRIC_DECREASES`.
- **Average trace cost:** average of `@trace.estimated_total_cost`. Desired change: `METRIC_DECREASES`.

Use the Agent Observability span source (`LLMOBS` / `LLMOBS_SPANS`) with the `average` operation and column type `int`. Filter on `@ml_app = <ml_app>` through the ordinary event filters so traces from other applications used by the same subject cannot enter the result. Do not add filters for trace completion or root-span status: the platform applies those eligibility rules itself and a metric definition cannot override them. Do not sum over all spans, which counts child spans and unfinished traces. If the organization already has an LLM Observability span metric, copy its column names and type from its stored definition instead of relying on the names here, but reuse it only if its stored filter already scopes it to this `@ml_app`. The trace rollups are expected to be integers. If the metric shows no data, check the column type first, since the stored type must match what the platform exposes.

### Attach the metrics

Attach one at a time, never concurrently:
1. Attach the score metric as the **primary**.
2. Attach tokens as a secondary metric.
3. Attach cost as a secondary metric.

Read the experiment back and confirm the primary metric is set. Some experiment reads omit secondary metrics; in that case use the successful attachment response's complete metric set as the confirmation and say plainly that the general experiment read does not expose them.

## Phase 5 — Environments and traffic split

An experiment links to **exactly one** environment through one allocation.

1. Enable the flag in the environment(s) the user asked for. Enabling a flag may be refused for a production environment through the server, which directs the user to the UI. If that happens, give the user the flag's UI link and ask them to enable it there. Changing allocations (step 3) is a separate operation and can still be available for production.
2. List the flag's complete allocation set in the target environment and in any other environment already linked to this experiment. Setting allocations is a full replacement, so preserve every unrelated allocation and all of its settings in each replacement request.
3. Add or update exactly one feature-gate allocation linked to the experiment, while retaining unrelated allocations in the complete replacement list. Its variant weights are percentages that total exactly 100 (for example 50 control and 50 treatment). Include an exposure schedule in the same call, or the experiment will not start (see the next paragraph). Setting the allocation without one succeeds, and the start step later rejects it as not ready.
   The allocation needs an exposure schedule with all of these fields, or the start step reports it as not ready. This holds even though the generic allocation schema says to omit the schedule for feature gates: that advice is for gates that are not linked to an experiment.
   - a rollout strategy of uniform intervals, with a `selection_interval_ms` (required by that strategy);
   - a start of `none`, so the schedule does not begin by itself and the experiment's own start controls timing;
   - a single rollout step with `exposure_ratio` 1.0 (a 0-1 ratio, not 100), `is_pause_record` false, and `grouped_step_index` 0, so the percentages in the variant weights apply to all traffic;
   - the schedule's `control_variant_key`, naming the control variant (the allocation can be saved without it, but the start step then reports invalid allocation weights).

   Do not use a partial rollout ramp here. The 50/50 split is already expressed by the weights.
4. If the experiment is already linked to an allocation in another environment, read that environment's complete allocation set and remove only the allocation linked to this experiment, preserving every other allocation and its settings, before creating the new allocation. Do not replace an environment with an empty list unless the approved setup explicitly removes all of its allocations. If an allocation key already exists, identify the conflicting allocation: reuse it only when it belongs to the approved setup; otherwise choose a new unique key. Ask again if this recovery differs materially from the approved proposal.
5. Read the allocations back and confirm the environment, the weights, and the experiment link.

Production allocation changes take effect immediately. If the environment requires approval for flag changes, the change may wait for that approval instead of applying. The approved Phase 0 proposal is the environment and production confirmation; do not ask again unless the existing allocation set differs materially from what that proposal said would be replaced.

## Phase 6 — Start the experiment

Start only when the user explicitly asks. Immediately before starting, independently verify rather than relying only on the start operation's readiness check:
- The flag is enabled in the linked environment.
- The 50/50 (or chosen) allocation is in place and linked.
- The primary metric is attached.
- The application change is deployed or the user explicitly accepts starting the clock before deployment.

Do not invoke start while the linked environment is known to be disabled. The start readiness check may validate allocation structure without enforcing flag enablement or deployment, so a successful start does not prove traffic can flow.

Then start the experiment (the `start-experiment` tool). It runs its own readiness check and returns the structural blockers it can detect, each with an action. Apply all of the actions, then retry once. Expect to need more than one pass: it reports the blockers it can currently detect, and fixing one can reveal the next (for example a missing exposure schedule first, then an invalid control variant on that schedule). Repairing an allocation replaces the flag's allocation set, so read the allocations first; ask again only when the required replacement differs from the one approved in Phase 0. If it returns a permissions error, report that the account lacks permission to start experiments, stop, and offer the UI's preview-and-start flow. Do not loop on retries.

After it starts, read the experiment back and report its status.

## Phase 7 — Verify once traffic flows

Once the application is deployed and receiving traffic:
- Run the experiment diagnostics. They check exposure balance and flag zero-data metrics, unreliable metrics, and sample ratio mismatch.
- If a metric has no data, check in this order: the flag is being evaluated for the subject (the experiment's code-location lookup shows where the flag is used in source); the evaluation has a numeric score; the `subject_identifier` matches the `targetingKey` exactly; the root span carries the tag; the traces are complete; the metric's column names and types are right.
- Fix a wrong metric definition by updating the metric rather than creating a duplicate.

For reading the results once enough data has arrived, use `agent-observability-experiment-analyzer` or the Datadog experiments tooling available in the active MCP server.

## Pitfalls checklist

- Subject identifier mismatch between the flag, the evaluation, and the spans.
- Subject tag missing from the root span, so the cost and token metrics cannot join to exposures.
- Subject type mapped to an attribute other than `@usr.id`.
- Span-scoped managed evaluation missing the subject tag on the span it scores.
- Cost or token metrics defined as a sum over all spans, or on span attributes other than the trace rollups.
- Cost or token metric missing its `@ml_app` filter.
- Flag enabled in one environment but the application running in another.
- Experiment linked to the wrong environment's allocation (it can only link one).
- Allocation replacement that drops unrelated allocations or their exposure schedules.
- Allocation weights not totaling 100.
- Allocation set without an exposure schedule, or with a schedule that has no `control_variant_key`, which blocks the start step.
- Evaluation is Boolean or categorical, not a score.
- Metric `desired change` set the wrong way round.
- Attaching metrics in parallel.
- Starting before the user has asked, or before the flag is enabled where the app runs.
