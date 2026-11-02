---
name: unblock-pr
description: Load when investigating a failing PR CI pipeline or checking PR health. Attributes each CI failure as flaky, infra, or regression, proposes a targeted action, and reports code coverage and quality/security status.
metadata:
  version: "1.1.0"
  author: datadog-labs
  repository: https://github.com/datadog-labs/agent-skills
  tags: datadog,ci,cicd,flaky,flaky-tests,pipeline
  alwaysApply: "false"
---

# Unblock PR

One-line summary: Investigate a failing PR CI pipeline — attribute each failure as flaky, infra, or regression and propose a targeted action.

Requires: Datadog MCP tools, or `dd-pup` with pup installed and authenticated; `triage-flaky-test` for deep investigation of flaky failures.

---

## Backend

**Detection** — At the start of every invocation, before taking any action, determine which backend to use:

1. If the user passed `--backend pup` anywhere → use **pup mode** immediately. Skip steps 2–4.
2. Check whether `search_datadog_ci_pipeline_events` appears in your available tools.
3. If present → use **MCP mode** throughout. Call tools exactly as named in this skill's workflow sections.
4. If absent → check whether `pup` is executable: run `pup --version` via Bash. If the command exits successfully (exit code 0), pup is available.
5. If pup responds → use **pup mode** throughout. Translate every tool call using the Tool Reference appendix at the bottom of this file.
6. If neither is available → stop and tell the user:
   > "Neither the Datadog MCP server nor the pup CLI is available. Connect the MCP server or install pup (`brew install datadog-labs/pack/pup`)."

**pup invocation rules:**
- Invoke via Bash with JSON output (`pup --output json ...`) and parse the response.
- Repository IDs passed to pup must be fully lowercase (the API rejects mixed-case): `github.com/datadog/my-repo`, not `github.com/DataDog/my-repo`.
- If pup returns a 401/403, tell the user to run `pup auth refresh` or `pup auth login`.

---

## Input

| Parameter | Description |
|---|---|
| PR branch | The branch under investigation (e.g. `my-feature-branch`) |
| PR number | Optional. If omitted, resolve it from the branch in MCP mode. |
| Repository | Lowercase, no-schema URL (e.g. `github.com/org/repo`). Derive from `git remote get-url origin` if not provided. |

---

## Workflow

### STEP 0 — Parse Input

Derive repository ID and default branch from git if not provided:

```bash
# Repository ID: fully lowercase, no-schema URL (the API rejects mixed-case)
git remote get-url origin
# Strip protocol and trailing .git, normalize SSH git@host:org/repo to host/org/repo, then lowercase
# e.g. https://github.com/DataDog/my-repo.git → github.com/datadog/my-repo

# Default branch
git symbolic-ref refs/remotes/origin/HEAD
# Strip refs/remotes/origin/ prefix — fall back to main if unset
```

### STEP 1 — Get PR CI Summary

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

Collect all distinct `@test.service` values from test event results. If more than one distinct service is found, note each separately in the triage brief — do not collapse them into a single service filter. Skip the test query for purely infrastructure jobs (build, lint, deploy with no test output), then continue the job-level blame guard and classification.

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

Load [references/failure-classification.md](references/failure-classification.md) for detailed heuristics and insufficient-data cases.

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

**flaky** → Load `triage-flaky-test` for deep investigation once per distinct failing test, passing its name, suite/module or `fingerprint_fqn` when known, and repository. Preserve the selected backend when invoking it. That skill will:
- Determine whether the flake is self-introduced, pre-existing, or undetermined, with failure history taking precedence over diff overlap or stated authorship.
- Surface an existing Bits Code fix, or attempt a guarded agent-native fix using `flaky_category` and the stack trace.
- Prefer fix or escalation to the author for self-introduced flakes; propose quarantine for inherited flakes when appropriate.

Optionally pass an **author-owned (self-introduced)** hint when loading `triage-flaky-test` — it re-runs the full attribution itself (its STEP 2.5, which owns the definition and failure-history precedence), so this is only a hint. Send the hint **only for a branch-local flake**: STEP 2's blast-radius check must show the test failing on the current branch and **not** on the default branch **and not across other branches**, AND either the PR's diff touches the failing test's file or the user stated they authored it.

Checking diff overlap needs the PR's changed paths, which STEP 1.5 does *not* fetch — obtain them by whatever means you have: if the normalized local remote matches this repo and HEAD is the change under investigation, run `git diff --name-only $(git merge-base origin/<default-branch> HEAD)...HEAD`; otherwise use the code host's changed-files source for this PR/MR (GitHub, GitLab, etc.). **If changed paths can't be obtained by any means, skip diff overlap — do not guess.** Stated authorship still works with no file access, so fall back to that; if neither signal is available, pass no hint and let `triage-flaky-test` classify it. For a cross-branch flake (failing on ≥3 branches — the case that made STEP 3 classify it `flaky`) the flake pre-dates this PR: send **no** hint even if the file overlaps.

**infra** → Before proposing a retry, assess whether the failure is transient:
- Check `@error_classification.type` and error message for signals like `timeout`, `runner unavailable`, `network error`, `quota exceeded`, `flaky` — transient failures where a retry is likely to help
- If the error is deterministic (build misconfiguration, missing secret, explicit test assertion failure with no flakiness history), a retry is unlikely to help — suggest investigating the root cause
- If the failure is pre-existing on the default branch, inform the user — a retry will likely fail again; await the upstream fix instead

If transient:

**MCP mode — GitHub Actions:** use `retry_datadog_ci_job`. GitHub retries **all currently-failed jobs** in the workflow run, not just the selected job; explain that scope before proceeding. From the failing job event, collect:
- `@ci.provider.name` → ci_provider ("github")
- `@git.repository.id_v2` → repository_id
- `@ci.job.id` → job_id
- event `id` field → event_uuid (optional)

For `pipeline_id`: use `@ci.pipeline.id` directly if it matches `^\d+-[1-9]\d*$` (e.g. `26027867390-1`). If it is a bare numeric run ID, combine it with `@github.run_attempt` from the same event: `"{@ci.pipeline.id}-{@github.run_attempt}"`. Fallback: parse `@ci.pipeline.url` — extract `{run_id}` and `{attempt}` from `runs/{run_id}/attempts/{attempt}`.

After retry returns, confirm via `search_datadog_ci_pipeline_events` (`query: @ci.job.name:"<job>" @git.branch:<branch> @git.repository.id_v2:"<repo>"`, `ci_level: job`, `from: now-5m`) that a new run appears. A queued retry is not a successful run; do not resubmit merely because ingestion is delayed.

**Fallback / pup mode — GitHub Actions** (if the retry tool is unavailable or returns a permission error): extract the run ID immediately after `/runs/` in `@ci.pipeline.url`, not the attempt number. This also retries **all currently-failed jobs**:
```bash
gh run rerun <run_id> --failed --repo <owner/repo>
```

**MCP mode — GitLab:** use `retry_datadog_ci_job` with `ci_provider: gitlab`, `repository_id` from `@git.repository.id_v2`, numeric `pipeline_id` from `@ci.pipeline.id`, `job_id` from `@ci.job.id`, and optional `event_uuid` from the event’s `id`. Confirm the async retry with the same repository-scoped job query above.

**GitLab fallback / pup mode:** if the retry tool is unavailable or returns a permission error, share `@ci.pipeline.url` and direct to GitLab’s CI/CD UI.

**Other providers** (both modes): share `@ci.pipeline.url` and direct to the provider UI.

**unknown** → Suggest checking raw job logs via the CI provider UI or `@ci.pipeline.url` from the pipeline event.

---

## Tool Reference

This appendix applies only in **pup mode**. In MCP mode, use the tool names in the workflow sections directly.

| MCP Tool | pup Command |
|---|---|
| `search_datadog_ci_pipeline_events` (initial failures, ci_level: job) | `pup cicd events search --query "..." --level job --from 24h --limit 50` |
| `search_datadog_ci_pipeline_events` (retry confirmation, ci_level: job) | `pup cicd events search --query "@ci.job.name:\"<job>\" @git.branch:<branch> @git.repository.id_v2:\"<repo>\"" --level job --from 5m --limit 10` |
| `aggregate_datadog_ci_pipeline_events` (count, group_by branch) | `pup cicd events aggregate --query "ci_level:job ..." --compute count --group-by "@git.branch" --from 24h` |
| `aggregate_datadog_ci_pipeline_events` (count, no group_by) | `pup cicd events aggregate --query "ci_level:job ..." --compute count --from 24h` |
| `search_datadog_test_events` | `pup cicd tests search --query "test_level:test ..." --from 24h --limit 50` |
| `get_datadog_flaky_tests` | `pup cicd flaky-tests search --query "flaky_test_state:active ..."` |
| `update_datadog_flaky_test_states` | Write body to `/tmp/flaky-update.json`, then `pup test-optimization flaky-tests update --file /tmp/flaky-update.json` |
| `get_datadog_code_coverage_branch_summary` | `repo_lower=$(echo "<repo>" \| tr '[:upper:]' '[:lower:]') && pup code-coverage branch-summary --repo "$repo_lower" --branch "<branch>"` |
| `get_prs_by_head_branch` | Not available in pup — skip; report "No data available" for Quality/Security |
| `search_pr_insights` | Not available in pup — skip; report "No data available" for Quality/Security |
| `retry_datadog_ci_job` | Not available in pup — GitHub: `gh run rerun <run_id> --failed --repo <owner/repo>`; GitLab/others: provider UI |

---

## References

- [Failure classification](references/failure-classification.md): classification fields, attribution hints, and insufficient-data cases.
