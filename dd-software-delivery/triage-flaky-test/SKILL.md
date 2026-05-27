---
name: triage-flaky-test
description: Load when investigating a specific flaky test. Gets history, failure pattern, and category, then recommends fix, quarantine, or escalate.
metadata:
  version: "1.0.0"
  author: datadog-labs
  repository: https://github.com/datadog-labs/agent-skills
  tags: datadog,ci,cicd,flaky,flaky-tests,test-optimization
  alwaysApply: "false"
---

# Triage Flaky Test

One-line summary: Investigate a specific flaky test — get history, failure pattern, and category, then recommend fix, quarantine, or escalate.

Requires: `dd-pup` skill (pup CLI installed and authenticated).

---

## Backend

**Detection** — At the start of every invocation, before taking any action, determine which backend to use:

1. If the user passed `--backend pup` anywhere → use **pup mode** immediately. Skip steps 2–4.
2. Check whether `get_datadog_flaky_tests` appears in your available tools.
3. If present → use **MCP mode** throughout. Call tools exactly as named in this skill's workflow sections.
4. If absent → check whether `pup` is executable: run `pup --version` via Bash. A JSON response containing `"version"` confirms pup is available.
5. If pup responds → use **pup mode** throughout. Translate every tool call using the Tool Reference appendix at the bottom of this file.
6. If neither is available → stop and tell the user:
   > "Neither the Datadog MCP server nor the pup CLI is available. Connect the MCP server or install pup (`brew install datadog-labs/pack/pup`)."

**pup invocation rules:**
- Invoke via Bash. pup always outputs JSON — parse directly.
- Repository IDs passed to pup must be fully lowercase (the API rejects mixed-case).
- Sort values starting with `-` require `=` syntax: `--sort="-last_flaked"` (not `--sort "-last_flaked"`).
- If pup returns a 401/403, tell the user to run `pup auth refresh` or `pup auth login`.

---

## Input

| Parameter | Description |
|---|---|
| Test name | Fully qualified test name (e.g. `TestMyFunc` or `com.example.MyTest`) |
| Repository | Lowercase, no-schema URL (e.g. `github.com/org/repo`). Derive from `git remote get-url origin` if not provided. |

---

## Workflow

### STEP 0 — Parse Input

Derive repository ID from git if not provided:
```bash
git remote get-url origin
# Strip protocol and trailing .git, then lowercase the result
# e.g. https://github.com/DataDog/my-repo.git → github.com/datadog/my-repo
```

**Validation fallback:** If STEP 1 returns no results, confirm the correct repository by searching without a repo filter:
```
Tool: search_datadog_test_events
query: @test.name:"<test-name>"
from: now-30d
test_level: test
```
Extract `@git.repository.id_v2` from results and retry STEP 1 with the confirmed value.

### STEP 1 — Get Flaky Test Details

**Preferred — use `fingerprint_fqn` if known** (`fingerprint_fqn` is a valid CI Visibility search facet):
```
Tool: get_datadog_flaky_tests
query: fingerprint_fqn:<fqn>
sort_field: last_flaked
sort_order: desc
```

**Fallback — use name + suite + repo:**
```
Tool: get_datadog_flaky_tests
query: @test.name:"<test-name>" @test.suite:"<suite>" @git.repository.id_v2:"<repo>"
sort_field: last_flaked
sort_order: desc
```
Omit `@test.suite` if unknown. Do not filter by `flaky_test_state` — return the test regardless of state.

Note: the query filter facet is `flaky_test_state`; the returned response attribute is `flaky_state` — do not use `flaky_state:active` as a query filter.

Extract from results:
- `fingerprint_fqn` — unique test identifier; used as the `id` in STEP 5 write call. **If absent, do not proceed to quarantine — see STEP 5.**
- `flaky_state` — current state (active / quarantined / disabled / fixed)
- `test_stats.failure_rate_pct` — percentage of runs that fail
- `flaky_category` — root cause category
- `codeowners` — owning team
- `pipeline_stats.total_lost_time_ms` — total CI time lost

### STEP 2 — Get Recent Failure History

```
Tool: search_datadog_test_events
query: @test.name:"<test-name>" @test.suite:"<suite>" @test.status:fail @git.repository.id_v2:"<repo>"
from: now-7d
test_level: test
```

Extract:
- Error messages and stack traces (`@error.message`, `@error.stack`)
- Failing branches (`@git.branch`) — branch-specific vs. widespread
- Frequency pattern — random timing or specific conditions
- Unique `@ci.pipeline.id` values for blast radius (STEP 3)

### STEP 3 — Check Blast Radius

Count distinct pipelines impacted using pipeline IDs from STEP 2:

```
Tool: aggregate_datadog_ci_pipeline_events
query: @ci.status:error @ci.pipeline.id:(<id1> OR <id2> OR ...) @git.repository.id_v2:"<repo>"
ci_level: pipeline
aggregation: count
group_by: ["@ci.pipeline.name"]
from: now-7d
```

Use the first 10 pipeline IDs from STEP 2 (cap at 10; if more are available, run a second batch and merge results by summing counts per `@ci.pipeline.name` across batches). Report blast radius as: total number of unique pipelines impacted and whether failures are branch-specific or widespread.

Note: a pipeline failure is not necessarily caused solely by this flaky test — treat blast radius as a signal, not a definitive count.

### STEP 4 — Recommend Fix or Quarantine

Use `flaky_category` from STEP 1 and error messages from STEP 2.

**Root cause first:**
- Read the full error trace from bottom to top — chained errors hide the real cause; the innermost error is the root cause, not the first line.
- Identify the exact source of nondeterminism (race, ordering, stale state, timing).
- If the root cause is a CI infrastructure problem (runner unavailable, Docker daemon failure, network outage) → do NOT propose a code fix; classify as `infra` and recommend retry instead.
- If root cause is uncertain and cannot be confirmed from the stack trace → skip fix, go to quarantine.

**Fix at the correct layer:**
- Test issue → fix in test or test helper only.
- Production bug exposed by the test → fix in production code.
- Shared helper used by multiple tests → fix the helper AND update all call sites.

**Forbidden — do not propose these:**
- Timing hacks: increasing timeouts, adding sleeps, widening time windows, adding retries.
- Masking: relaxing assertions (e.g., exact match → at least 1), dropping validations.
- Partial fixes: touching one call site when multiple share the root cause.

**Fix patterns by category:**

| Category | Approach |
|---|---|
| `timeout` | Identify the slow operation and make it synchronous or deterministic — do NOT simply raise the timeout constant |
| `concurrency` | Add deterministic synchronization (barriers, channels, locks); remove shared mutable state between tests |
| `network` | Mock or stub network calls at the boundary; if the test requires a real connection, isolate it with a test server |
| `time` | Inject a controllable clock; replace wall-clock assertions with relative or event-driven checks |
| `order_dependency` | Isolate test state with setup/teardown; eliminate dependencies on execution order or global state |
| `environment_dependency` | Mock env variables and external config; use test-local fixtures, not shared directories or singletons |
| `resource_leak` | Ensure every resource opened in a test is closed in teardown; use cleanup hooks that run even on failure |
| `randomness` | Fix the random seed for the test run; use deterministic inputs instead of random generation |
| `asynchronous_wait` | Replace fixed sleeps with condition polling or event/signal-driven waits with a hard timeout |
| `io` | Use temp files/dirs cleaned up in teardown; mock or stub filesystem interactions |
| `unknown` | Skip fix attempt → go to quarantine |

**Before proposing code changes, verify all of the following — if any fails, skip fix and recommend quarantine:**
- The root cause is the innermost error in the trace, not a surface-level symptom.
- The failure is a code problem, not a CI infrastructure problem.
- The fix eliminates the root cause (not just reduces flake probability).
- The fix is at the correct layer (test vs. production vs. shared helper).
- All call sites of any shared code are updated.
- No timing hacks or relaxed assertions introduced.

**Decision:**
- If category is `unknown` OR verification above fails → skip fix, recommend quarantine
- If category is known AND root cause is confirmed AND fix is valid → propose specific code change

### STEP 5 — Produce Triage Brief and Act

```
Flaky Test Triage Brief
=======================
Test:           <fully qualified test name>
Service:        <@test.service>
Category:       <flaky_category>
Failure Rate:   <test_stats.failure_rate_pct>%
Duration Lost:  <pipeline_stats.total_lost_time_ms>ms
Codeowners:     <codeowners>
Blast Radius:   <N> pipelines (<branch-specific | widespread>) [approximate — other failures in the same pipeline runs may not be related]

Evidence:
  <1-2 key error message lines from STEP 2>

Recommendation: <fix | quarantine | escalate>
Confidence:     <high | medium | low>
Action:         <specific next step>
```

**Decision thresholds:**
- `failure_rate_pct > 10` OR blast radius > 5 pipelines → **quarantine**
- `failure_rate_pct ≤ 10` AND known category AND clear fix → **fix**
- `failure_rate_pct ≤ 10` AND category `unknown` → **escalate** to codeowners with triage brief

**If recommending quarantine**, present and require explicit user approval before writing:

```
Proposed action: quarantine "<test-name>"
  id (fingerprint_fqn): <fingerprint_fqn from STEP 1>
  Effect: test still runs but failures are suppressed (CI will not be blocked)
  Reversible: yes — set new_state: active to restore

Approve? (yes/no)
```

**If `fingerprint_fqn` was not returned in STEP 1** (test not yet in FTM or query returned no results): do not attempt the write. Surface an error and ask the user to open the Flaky Test Management UI directly to quarantine manually.

Only after explicit approval and a confirmed `fingerprint_fqn`:

**MCP mode:**
```
Tool: update_datadog_flaky_test_states
test_ids: ["<fingerprint_fqn>"]
new_state: quarantined
```

**pup mode:**
```bash
cat > /tmp/flaky-update.json <<'EOF'
{
  "data": {
    "type": "UpdateFlakyTestsRequest",
    "attributes": {
      "tests": [{"id": "<fingerprint_fqn>", "new_state": "quarantined"}]
    }
  }
}
EOF
pup test-optimization flaky-tests update --file /tmp/flaky-update.json
```

To undo: repeat with `new_state: active` / `"new_state": "active"`.

---

## Tool Reference

This appendix applies only in **pup mode**. In MCP mode, use the tool names in the workflow sections directly.

| MCP Tool | pup Command |
|---|---|
| `get_datadog_flaky_tests` (by fingerprint_fqn) | `pup cicd flaky-tests search --query "fingerprint_fqn:<fqn>" --sort="-last_flaked" --limit 5` |
| `get_datadog_flaky_tests` (by name + suite + repo) | `pup cicd flaky-tests search --query "@test.name:\"...\" @test.suite:\"...\" @git.repository.id_v2:\"...\"" --sort="-last_flaked" --limit 10` |
| `search_datadog_test_events` (validation fallback) | `pup cicd tests search --query "@test.name:\"<test-name>\"" --from 30d --limit 5` |
| `search_datadog_test_events` (failure history) | `pup cicd tests search --query "@test.name:\"...\" @test.suite:\"...\" @test.status:fail @git.repository.id_v2:\"...\"" --from 7d --limit 20` |
| `aggregate_datadog_ci_pipeline_events` (blast radius) | `pup cicd events aggregate --query "@ci.status:error @ci.pipeline.id:(...) @git.repository.id_v2:\"...\"" --compute count --group-by "@ci.pipeline.name" --from 7d` |
| `update_datadog_flaky_test_states` | Write body to `/tmp/flaky-update.json`, then `pup test-optimization flaky-tests update --file /tmp/flaky-update.json` |
