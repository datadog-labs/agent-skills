---
name: unblock-pr
description: Load when investigating a failing PR CI pipeline or checking PR health. Attributes each CI failure as flaky, infra, or regression, proposes a targeted action, and reports code coverage and quality/security status.
metadata:
  version: "1.0.0"
  author: datadog-labs
  repository: https://github.com/datadog-labs/agent-skills
  tags: datadog,ci,cicd,flaky,flaky-tests,pipeline
  alwaysApply: "false"
---

# Unblock PR

One-line summary: Investigate a failing PR CI pipeline — attribute each failure as flaky, infra, or regression and propose a targeted action.

Requires: `dd-pup` skill (pup CLI installed and authenticated), `triage-flaky-test` skill (for flaky failure deep investigation).

---

## Backend

**Detection** — At the start of every invocation, before taking any action, determine which backend to use:

1. If the user passed `--backend pup` anywhere → use **pup mode** immediately. Skip steps 2–4.
2. Check whether `search_datadog_ci_pipeline_events` appears in your available tools.
3. If present → use **MCP mode** throughout. Call tools exactly as named in this skill's workflow sections.
4. If absent → check whether `pup` is executable: run `pup --version` via Bash. A JSON response containing `"version"` confirms pup is available.
5. If pup responds → use **pup mode** throughout. Translate every tool call using the Tool Reference appendix at the bottom of this file.
6. If neither is available → stop and tell the user:
   > "Neither the Datadog MCP server nor the pup CLI is available. Connect the MCP server or install pup (`brew install datadog-labs/pack/pup`)."

**pup invocation rules:**
- Invoke via Bash. pup always outputs JSON — parse directly.
- Repository IDs passed to pup must be fully lowercase (the API rejects mixed-case): `github.com/datadog/my-repo`, not `github.com/DataDog/my-repo`.
- If pup returns a 401/403, tell the user to run `pup auth refresh` or `pup auth login`.

---

## Input

| Parameter | Description |
|---|---|
| PR branch | The branch under investigation (e.g. `my-feature-branch`) |
| Repository | Lowercase, no-schema URL (e.g. `github.com/org/repo`). Derive from `git remote get-url origin` if not provided. |

---

## Workflow

### STEP 0 — Parse Input

Derive repository ID and default branch from git if not provided:

```bash
# Repository ID: fully lowercase, no-schema URL (the API rejects mixed-case)
git remote get-url origin
# Strip protocol and trailing .git, then lowercase the result
# e.g. https://github.com/DataDog/my-repo.git → github.com/datadog/my-repo

# Default branch
git symbolic-ref refs/remotes/origin/HEAD
# Strip refs/remotes/origin/ prefix — fall back to main if unset
```

### STEP 1 — Get PR CI Summary (run in parallel)

**Pipeline failures:**
```
Tool: search_datadog_ci_pipeline_events
query: @ci.status:error @git.branch:<branch> @git.repository.id_v2:"<repo>"
ci_level: job
from: now-24h
```

**Test failures** (only if pipeline results include test-runner jobs):
```
Tool: search_datadog_test_events
query: @test.status:fail @git.branch:<branch> @git.repository.id_v2:"<repo>"
from: now-24h
test_level: test
```

Run both in parallel. Collect all distinct `@test.service` values from test event results. If more than one distinct service is found, note each separately in the triage brief — do not collapse them into a single service filter. If pipeline results contain only infrastructure job types (build, lint, deploy) with no test-runner output, discard test results and skip to STEP 3.

### STEP 1.5 — Fetch PR Health (run in parallel with STEP 1)

This step runs unconditionally — PR health context is valuable whether CI is red or green.

**Code coverage** (both modes):
```
Tool: get_datadog_code_coverage_branch_summary
repository_id: <repo>
branch: <branch>
```

**PR number resolution** (MCP mode only — skip if PR number already provided as input):
```
Tool: get_prs_by_head_branch
repo_url: https://<repo>
head_branch: <branch>
```
Use the first open PR returned. If no open PR is found, skip the quality/security fetch and report "No data available" for Quality and Security.

**Code quality and security** (MCP mode only — only if PR number is available):
```
Tool: search_pr_insights
repo_url: https://<repo>
pr_number: <pr_number>
```
Extract only `code_quality` and `code_security` from `products_status`. Ignore `failed_tests`, `flaky_tests`, and `failed_jobs` — CI data comes from STEP 1–3.

> **pup mode note:** PR number resolution and `search_pr_insights` are not available in pup. Quality and Security always show "No data available" in pup mode.

### STEP 2 — Blame Guard per Failing Job

First check whether `@error_classification.domain` / `@error_classification.type` are present on job events from STEP 1 — if populated, use them as primary classification signals.

For each failing job where classification is still needed, run both checks in parallel:

**Default branch check** — was this job already failing before this PR?
```
Tool: aggregate_datadog_ci_pipeline_events
query: @ci.status:error @ci.job.name:"<job>" @git.branch:<default-branch> @git.repository.id_v2:"<repo>"
ci_level: job
aggregation: count
from: now-24h
```

**Blast radius check** — is this job failing on other branches too?
```
Tool: aggregate_datadog_ci_pipeline_events
query: @ci.status:error @ci.job.name:"<job>" @git.repository.id_v2:"<repo>"
ci_level: job
aggregation: count
group_by: ["@git.branch"]
from: now-24h
```

Performance fallback: if the blast radius query is slow or times out, skip it and rely on the default branch check alone.

### STEP 3 — Classify Each Failure

**Priority order:**
1. If `@error_classification.domain` / `@error_classification.type` present → use as primary signal
2. If test failure AND test in `get_datadog_flaky_tests` with `flaky_test_state:active` → **flaky**
3. Use blame guard results:

| Failing on default branch? | Failing on ≥3 other branches? | Classification |
|---|---|---|
| Yes | Yes | **infra** (pre-existing, widespread) |
| Yes | No | **infra** (pre-existing on default branch) |
| No | No | **regression** (introduced by this PR) |
| No | Yes | **flaky** (intermittent, cross-branch) |
| Insufficient data | — | **unknown** |

### STEP 4 — Produce Triage Brief

One entry per failing job:

```
PR CI Triage Brief
==================
Branch:   <branch>
Repo:     <repo>

Job: <job-name>
  Classification:  <flaky | infra | regression | unknown>
  Evidence:        <1 key data point — error message, pipeline count, or test result>
  Confidence:      <high | medium | low>
  Recommended:     <action>

[repeat for each failing job]

Overall: <N> failures — <e.g. "1 regression, 1 flaky, 1 infra">

PR Health
=========
Coverage:   <X>% on <branch> | No data available
Quality:    <N violations (X high, Y medium)> | No violations | No data available
Security:   <N violations> | No violations | No data available
```

All three lines always appear. Use "No data available" when a tool returned no data or is unavailable (pup mode for Quality/Security).

### STEP 5 — Propose Actions

**regression** → Prompt user to investigate their code changes. No write action available.

**flaky** → Load `triage-flaky-test` skill for deep investigation. That skill will:
- Attempt an agent-native fix using `flaky_category` + stack trace
- Propose quarantine via `update_datadog_flaky_test_states` if a quick fix isn't possible

**infra** → Before proposing a retry, assess whether the failure is transient:
- Check `@error_classification.type` and error message for signals like `timeout`, `runner unavailable`, `network error`, `quota exceeded` — transient failures where a retry is likely to help
- If the error is deterministic (build misconfiguration, missing secret, explicit test assertion failure), a retry is unlikely to help — suggest investigating the root cause
- If the failure is pre-existing on the default branch, inform the user — a retry will likely fail again; await the upstream fix instead

If transient:

**MCP mode — GitHub Actions:** use `retry_datadog_ci_job`. From the failing job event, collect:
- `@ci.provider.name` → ci_provider ("github")
- `@git.repository.id_v2` → repository_id
- `@ci.job.id` → job_id
- event `id` field → event_uuid (optional)

For `pipeline_id`: use `@ci.pipeline.id` if it contains a dash (e.g. `26027867390-1`). If bare, combine with `@github.run_attempt`: `"{@ci.pipeline.id}-{@github.run_attempt}"`. Fallback: parse `@ci.pipeline.url` for `runs/{run_id}/attempts/{attempt}`.

After retry returns, confirm via `search_datadog_ci_pipeline_events` (`query: @ci.job.name:"<job>" @git.branch:<branch>`, from: now-5m) that a new run appears.

**Fallback / pup mode — GitHub Actions:** extract the run ID from `@ci.pipeline.url`:
```bash
gh run rerun <run_id> --failed
```

**GitLab / other providers** (both modes): share `@ci.pipeline.url` and direct to the provider UI.

**unknown** → Suggest checking raw job logs via the CI provider UI or `@ci.pipeline.url` from the pipeline event.

---

## Tool Reference

This appendix applies only in **pup mode**. In MCP mode, use the tool names in the workflow sections directly.

| MCP Tool | pup Command |
|---|---|
| `search_datadog_ci_pipeline_events` (ci_level: job) | `pup cicd events search --query "..." --level job --from 24h --limit 50` |
| `aggregate_datadog_ci_pipeline_events` (count, group_by branch) | `pup cicd events aggregate --query "..." --compute count --group-by "@git.branch" --from 24h` |
| `aggregate_datadog_ci_pipeline_events` (count, no group_by) | `pup cicd events aggregate --query "..." --compute count --from 24h` |
| `search_datadog_test_events` | `pup cicd tests search --query "..." --from 24h --limit 50` |
| `get_datadog_flaky_tests` | `pup cicd flaky-tests search --query "flaky_test_state:active ..."` |
| `update_datadog_flaky_test_states` | Write body to `/tmp/flaky-update.json`, then `pup test-optimization flaky-tests update --file /tmp/flaky-update.json` |
| `get_datadog_code_coverage_branch_summary` | `repo_lower=$(echo "<repo>" \| tr '[:upper:]' '[:lower:]') && pup code-coverage branch-summary --repo "$repo_lower" --branch "<branch>"` |
| `get_prs_by_head_branch` | Not available in pup — skip; report "No data available" for Quality/Security |
| `search_pr_insights` | Not available in pup — skip; report "No data available" for Quality/Security |
| `retry_datadog_ci_job` | Not available in pup — use `gh run rerun <run_id> --failed` instead |
