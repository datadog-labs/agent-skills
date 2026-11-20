---
name: dd-instrument-test-optimization
description: Sets up Datadog Test Optimization (CI test visibility, per-test traces, flaky test detection) in a repo's CI pipeline for Node.js, Python, Java/JVM, Go, Ruby, .NET, Swift, or JUnit XML. Configures CI auto-instrumentation or manual test tracers, forwards container variables, and checks that test events arrive. Use when asked to add, set up, enable or verify Test Optimization or CI test visibility. Not for triaging flaky tests or unblocking a failing PR; use triage-flaky-test or unblock-pr.
metadata:
  version: "0.1.0"
  author: datadog-labs
  repository: https://github.com/datadog-labs/agent-skills
  tags: datadog,test-optimization,ci-visibility,ci,testing,flaky-tests,instrumentation
  alwaysApply: "false"
---

# Datadog Test Optimization

Test Optimization collects per-test traces, failure reasons, and git metadata from CI test runs so Datadog can detect flaky tests and run Test Impact Analysis. No application resource has to be created in Datadog beforehand: the test service appears automatically when a test run sends spans with a valid API key and site.

## How it works

- **What turns Test Optimization on:** the CI test job must either run the Datadog Test Optimization CI step (`datadog/test-visibility-github-action@v3`, the GitLab script, or the CircleCI orb), run the language tracer in CI Visibility mode (`DD_CIVISIBILITY_ENABLED=true` and `DD_CIVISIBILITY_AGENTLESS_ENABLED=true` with the test runner hook), or upload JUnit XML reports (`datadog-ci junit upload`). Having `dd-trace` or `ddtrace` installed for APM or Code Coverage does not turn Test Optimization on.
- **GitHub Actions cannot mutate `NODE_OPTIONS` across steps:** for JavaScript and TypeScript on GitHub Actions, `datadog/test-visibility-github-action@v3` exports `DD_TRACE_PACKAGE` (and `DD_TRACE_ESM_IMPORT` for Vitest), which the test step itself must wire into `NODE_OPTIONS`. Without that env line on the test step, no test spans are collected.
- **Containers cross an execution boundary:** when a CI step runs tests through `docker run` or `docker compose`, host-level environment variables do not enter the container automatically. The Datadog variables, the runtime injection variable, and the CI provider's git/pipeline variables must be passed into the container, and prebuilt test images must use manual instrumentation inside the container.
- **Credentials:** Test Optimization uses `DD_API_KEY` in agentless mode. Reference it from the CI provider's secret store (`${{ secrets.DD_API_KEY }}`, `$DD_API_KEY`, or `$(DD_API_KEY)`); never write a literal key into a workflow or config file.

## Set up

Copy these steps into your reply and tick them off as you go.

1. **Inspect.** In a collection of independent projects, ask which one to instrument. Read dependency manifests, test configs, and CI workflow files (`.github/workflows/*.yml`, `.gitlab-ci.yml`, `.circleci/config.yml`, `Jenkinsfile`, `.buildkite/pipeline.yml`, `buildkite/pipeline.yml`, `bitbucket-pipelines.yml`, `.travis.yml`, `azure-pipelines.yml`) to record:
   - **Runtime and language:** `nodejs` (JS/TS), `python`, `java` (Java/Kotlin/Scala + `maven`/`gradle`/`sbt`), `go`, `ruby`, `dotnet`, `swift`, or `other`.
   - **Test framework and version:** Jest (>=24.8.0 with `jest-circus`), Vitest (>=1.6.0), Mocha (>=5.2.0), Playwright (>=1.18.0), Cypress (>=6.7.0), Cucumber, pytest (>=3.0.0), unittest, JUnit 4 (>=4.10) / JUnit 5 (>=5.3), TestNG, Spock, Scalatest, RSpec (>=3.0.0), Minitest (>=5.0.0), `go test`, xUnit (>=2.2), NUnit (>=3.0), MSTest v2 (>=14), BenchmarkDotNet, or XCTest.
   - **CI provider and test jobs:** which workflow/job runs the tests, and whether the test command crosses into a container (`docker run` or `docker compose`).
   - **Repository name (`<REPO>`):** normalized lowercase `host/owner/repo` without scheme or `.git` (from `git remote get-url origin`, `package.json`, `go.mod`, `pyproject.toml`, or `pom.xml`).
   - **Datadog site (`<SITE>`):** from `DD_SITE` or existing Datadog config (default `datadoghq.com` when unset; if an invalid site is supplied, stop and ask).
   - **Existing Test Optimization setup:** whether the test job already runs auto-instrumentation, manual CI Visibility (`DD_CIVISIBILITY_ENABLED` / `ddtrace-run` / `--ddtrace` / `orchestrion go test` / `dd-trace ci run`), or `datadog-ci junit upload`.

2. **Check prerequisites and stop conditions.**
   - **Already instrumented with auto-instrumentation:** make no changes and proceed to Verify and view.
   - **No test runner or no CI workflow found:** stop before editing, report what is missing, and ask which test command or CI provider to configure.
   - **Unsupported test runner (such as Node's built-in `node --test` or an older framework version):** stop before editing unless the user explicitly requested JUnit XML upload. Explain the two options—migrating to a supported runner (such as Vitest for Node tests) for full tracing and flaky-test management, or uploading JUnit XML reports via [references/junit-xml.md](references/junit-xml.md)—and ask which path to take.

3. **Choose the instrumentation mode.**
   - When the Datadog Onboarding MCP server is connected, call `Datadog:test_optimization_onboarding` with the substantiated `runtime`, `language`, `test_framework`, `build_tool` (for Java), `ci_provider`, `repo_name`, `instrumentation` (`auto` by default, `manual` or `junitxml` when required), and `tests_run_in_containers`. When the MCP server is not connected or the tool fails, follow the bundled reference files below for the exact setup steps.
   - Whether or not the MCP tool is available, apply the exact rules in the matching reference:
     - **Auto-instrumentation** (recommended for GitHub Actions, GitLab CI, and CircleCI when tests run on the runner or in a container built during the job, except Cypress and BenchmarkDotNet): open [references/ci-autoinstrumentation.md](references/ci-autoinstrumentation.md).
     - **Manual instrumentation** (required for Buildkite, Bitbucket, Jenkins in automated runs, Azure Pipelines, Travis CI, Cypress, BenchmarkDotNet, Swift, or when tests run inside a **prebuilt** Docker image where host auto-instrumentation cannot install the tracer): open [references/manual-by-runtime.md](references/manual-by-runtime.md).
     - **JUnit XML upload** (when requested or chosen for an unsupported runner): open [references/junit-xml.md](references/junit-xml.md).

4. **Wire the CI workflow and test configuration.**
   - Modify only the existing test job(s) in the CI workflow; preserve unrelated jobs, steps, comments, and YAML formatting.
   - If upgrading a job from manual or JUnit XML instrumentation to auto-instrumentation, remove the old manual/JUnit steps after wiring auto-instrumentation (if `datadog-ci junit upload` used `--xpath-tag`, warn that custom XPath tags are not carried over).
   - When `tests_run_in_containers` is true (`docker run` or `docker compose`), forward the required Datadog, runtime, and CI provider environment variables into the container as shown in [references/ci-autoinstrumentation.md](references/ci-autoinstrumentation.md).
   - Reference `DD_API_KEY` from the CI secret store (for example `${{ secrets.DD_API_KEY }}` in GitHub Actions or `$DD_API_KEY` in GitLab/Buildkite/CircleCI). In Buildkite, write `$DD_API_KEY`, never `$$DD_API_KEY` (which escapes substitution).

5. **Run local validation.** Run the project's test command or configuration check locally (without exposing real keys in command arguments), fix any errors your edits caused, and run it again until it passes.

## Verify and view

Record the highest level you actually reached:

1. **Configuration changed:** the CI workflow and any dependency/config files are updated and valid YAML.
2. **Tests ran:** the test suite ran locally or in CI and exited 0.
3. **Export accepted:** in a local run with `DD_CIVISIBILITY_ENABLED=true` and `DD_CIVISIBILITY_AGENTLESS_ENABLED=true`, or in a CI run with `DD_API_KEY` configured, the tracer or `datadog-ci junit upload` accepted the test payload without authentication errors.
4. **Datadog shows test events:** `Datadog:search_datadog_test_events`, or the [Search test events](https://docs.datadoghq.com/api/latest/ci-visibility-tests/#search-test-events) endpoint (`POST /api/v2/ci/tests/events/search`) when `DD_API_KEY` and `DD_APP_KEY` are set, finds `@git.repository.id_v2:"<REPO>"` in the last 15 minutes:

   ```sh
   SITE=<site from step 1> REPO=<lowercase host/owner/repo>
   case "$SITE" in
     datadoghq.com) API_HOST=api.datadoghq.com ;;
     datadoghq.eu) API_HOST=api.datadoghq.eu ;;
     us3.datadoghq.com) API_HOST=api.us3.datadoghq.com ;;
     us5.datadoghq.com) API_HOST=api.us5.datadoghq.com ;;
     ap1.datadoghq.com) API_HOST=api.ap1.datadoghq.com ;;
     ap2.datadoghq.com) API_HOST=api.ap2.datadoghq.com ;;
     uk1.datadoghq.com) API_HOST=api.uk1.datadoghq.com ;;
     ddog-gov.com) API_HOST=api.ddog-gov.com ;;
     *) API_HOST= ;;
   esac
   if [ -n "$API_HOST" ] && [ -n "$DD_API_KEY" ] && [ -n "$DD_APP_KEY" ]; then
     keys() { echo "DD-API-KEY: $DD_API_KEY"; echo "DD-APPLICATION-KEY: $DD_APP_KEY"; }
     BODY=$(printf '{"filter":{"query":"@git.repository.id_v2:\\"%s\\"","from":"now-15m","to":"now"},"page":{"limit":5}}' "$REPO")
     keys | curl -sS -w ' HTTP %{http_code}' -H @- -H 'Content-Type: application/json' -X POST -d "$BODY" "https://$API_HOST/api/v2/ci/tests/events/search"
   fi
   ```

A local config edit does not trigger CI by itself. Tell the user to add `DD_API_KEY` to their CI secrets (if not already set) and push a commit to run the workflow.

The app host is `app.datadoghq.com` for `datadoghq.com`, `app.datadoghq.eu` for `datadoghq.eu`, `app.ddog-gov.com` for `ddog-gov.com`, and the site itself for the others, for example `us5.datadoghq.com`. URL-encode `@git.repository.id_v2:"<REPO>"` in the query string. End with this summary:

```text
Test Optimization: configured (<auto | manual | junitxml> for <framework> on <ci_provider>) | already configured | waiting for your choice
Changed: <files, or none>
Secret needed: DD_API_KEY in <CI provider secret store> (create key at https://APP_HOST/organization-settings/api-keys)
Evidence: <highest level reached>; not checked: <levels and the step left>
View: https://APP_HOST/ci/test/runs?query=%40git.repository.id_v2%3A%22<URL_ENCODED_REPO>%22
```

## Undo

Revert the CI workflow edits and any tracer devDependency added during this run.

## Gotchas

| Mistake | Symptom | Fix |
|---|---|---|
| Using `datadog/test-optimization-action` in GitHub Actions | Workflow fails because that action does not exist | Use `datadog/test-visibility-github-action@v3` with `api_key` |
| Omitting `NODE_OPTIONS` on the GitHub Actions test step for JS/TS | Action runs, but 0 test spans are collected | Add `NODE_OPTIONS: -r ${{ env.DD_TRACE_PACKAGE }}` (plus `--import ${{ env.DD_TRACE_ESM_IMPORT }}` for Vitest) |
| Setting `NODE_OPTIONS` (`-r dd-trace/ci/init`) or `RUBYOPT` (`-rdatadog/ci/auto_instrument`) in the `env:` of a step that also installs dependencies | `npm` or `bundle` fails on a clean checkout, because the preloaded library is not installed yet | Export the variable on a line after the install command, or prefix only the test command |
| Running tests in `docker run` without `-e` flags | Container runs tests without Datadog or CI git metadata | Forward Datadog, runtime, and CI provider env vars into the container; use manual setup for prebuilt images |
| Writing `$$DD_API_KEY` in Buildkite `env:` | Buildkite escapes `$$` to a literal string, causing 403 auth failure | Write `$DD_API_KEY` |
