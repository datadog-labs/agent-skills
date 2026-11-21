---
name: dd-instrument-code-coverage
description: Sets up Datadog Code Coverage by adding one coverage-report upload step to a repository's GitHub Actions, GitLab CI or Azure Pipelines pipeline, or by reusing an existing Test Optimization setup, for LCOV, Cobertura, JaCoCo, Go, Clover, OpenCover and SimpleCov reports. Use when asked to add, set up, enable or verify Code Coverage or coverage uploads to Datadog. Not for setting up Test Optimization, which dd-instrument-test-optimization does, or for reading a pull request's coverage.
metadata:
  version: "0.1.0"
  author: datadog-labs
  repository: https://github.com/datadog-labs/agent-skills
  tags: datadog,code-coverage,ci,github-actions,gitlab,azure-pipelines,datadog-ci,lcov,cobertura,jacoco
  alwaysApply: "false"
---

# Datadog Code Coverage

Code Coverage shows file- and line-level coverage for every commit, branch and pull request. CI writes a coverage report, and an upload step or the Test Optimization library sends it to Datadog, which merges all reports for the same commit.

## How it works

- **The report comes first.** This skill adds only the upload. It does not run the tests, and it changes test commands, dependencies or coverage configuration only when the user agrees.
- **There are three upload methods.** `DataDog/coverage-upload-github-action@v1` on GitHub Actions, `datadog-ci coverage upload` on GitLab CI and Azure Pipelines, or a Test Optimization library that uploads the report by itself. Without one of them, the Code Coverage page shows nothing for the repository.
- **Test Optimization is not Code Coverage.** `DD_CIVISIBILITY_*` variables, `datadog/test-visibility-github-action`, `ddtrace-run` or `datadog-ci junit upload` show Test Optimization, not a coverage upload.
- **Git data comes from a source code integration** for GitHub, GitLab, Azure DevOps or Bitbucket, so the repository must be connected there.
- **Credentials.** CI reads `DD_API_KEY` from its secret store. Never write a literal key into a pipeline file; a local `.env` key is not visible to CI.

## Set up

Copy these steps into your reply and tick them off as you go.

1. **Inspect.** In a repository with several independent projects, ask which one to set up. Record:
   - **CI provider and test job:** `.github/workflows/*.yml` or `*.yaml` is `github`, `.gitlab-ci.yml` is `gitlab`, and `azure-pipelines.yml` (or a file with `trigger:`, `pool:` and `steps:`) is `azure-pipelines`. Anything else is another provider.
   - **Coverage report:** the tool and CI command that write it, its format and path, and any CI-only condition, using [references/coverage-formats.md](references/coverage-formats.md).
   - **Existing setup:** a `DataDog/coverage-upload-github-action` step or a `datadog-ci coverage upload` command, and whether the job that writes the report also runs Test Optimization, with which library and version.
   - **Site (`<SITE>`):** `DD_SITE` from the environment or from `grep -E '^DD_SITE=' .env` (read only that line, because env files hold secrets), and `datadoghq.com` when it is unset. The valid sites are `datadoghq.com`, `datadoghq.eu`, `us3.datadoghq.com`, `us5.datadoghq.com`, `ap1.datadoghq.com`, `ap2.datadoghq.com`, `uk1.datadoghq.com` and `ddog-gov.com`. Stop and ask on any other value.
   - **Repository (`<REPO>`):** lowercase `host/owner/repo` without a scheme or `.git`, from `git remote get-url origin` or the project manifest. It is used for verification.

2. **Check the stop conditions.**
   - **Coverage already reaches Datadog:** an upload exists that matches no row in Gotchas, or a supported Test Optimization library runs, as described in [references/test-optimization.md](references/test-optimization.md), and the Code Coverage setting is confirmed on. Change nothing and go to Verify and view. An upload with `--skip-git-metadata-upload=1` is broken: replace only that flag with the bare flag and report it. For other Gotchas matches, report and ask.
   - **No pipeline, or another CI provider:** stop before editing. Report the provider and that this skill supports GitHub Actions, GitLab CI and Azure Pipelines only, and point to the [Code Coverage setup docs](https://docs.datadoghq.com/code_coverage/setup/).
   - **CI writes no supported report:** the test command has no coverage option, the report is written only outside CI, or the format is unsupported, such as `.xcresult`. Stop before editing, name the one change that would write a report, and ask. Make it only after the user agrees. When you cannot ask, end without changes.

3. **Choose the method.**
   - **`test_optimization`:** a supported library already runs in the job that writes the report. Follow [references/test-optimization.md](references/test-optimization.md). No pipeline file changes.
   - **`github_action`:** the pipeline is GitHub Actions. Follow [references/upload-github-actions.md](references/upload-github-actions.md).
   - **`datadog_ci_upload`:** the pipeline is GitLab CI or Azure Pipelines, or GitHub Actions when the user asks for the CLI. Follow [references/upload-datadog-ci.md](references/upload-datadog-ci.md).

   When the Datadog Onboarding MCP server is connected, also call `Datadog:code_coverage_onboarding` with `language`, `ci_provider`, `coverage_format` (the tool names in [references/coverage-formats.md](references/coverage-formats.md)) and `instrumentation_method`, and apply its steps with the rules of the reference file. Where the tool's text differs from the reference file or Gotchas, such as `--skip-git-metadata-upload=1`, follow the reference file. Without the server, the reference file has every step.

4. **Add one upload step.**
   - Put it in the job that writes the report, directly after the step that writes it, and point it at the report file or its directory. Follow the file's indentation and action-pinning style, and leave every other job, step and comment as it is.
   - When several jobs or test shards each write part of the coverage, add the step to each of them, because Datadog merges them. When a matrix runs the same tests on several versions, run the step on one entry only.
   - Reference the secret as `${{ secrets.DD_API_KEY }}` in GitHub Actions, `$DD_API_KEY` in GitLab CI, or `$(DD_API_KEY)` in the step's `env:` in Azure Pipelines, and set the site to `<SITE>`. Keep the key on the upload step only, never in job-level `env:`.
   - Guard the step, as the method's reference file shows, so that it is skipped where CI gives no secrets: Dependabot runs and fork pull requests on GitHub, and fork builds on GitLab and Azure. Unguarded, it exits 1 there.

5. **Check the edit statically.** Parse each changed YAML file, for example with `ruby -ryaml -e 'YAML.parse_file(ARGV[0])' <file>`, which accepts anchors and `!reference` tags. Confirm that each upload step follows the step that writes the report in the same job, that the key comes from the secret store, that the site is `<SITE>`, and that any `--format` uses a `datadog-ci` name with explicit files. Do not run the test suite or install the project's dependencies.

## Verify and view

Record the highest level you actually reached:

1. **Configuration changed:** step 5 passed, or the `test_optimization` checks passed.
2. **Report detected:** check this only when a report from an earlier run already exists locally. In the git checkout, `DD_API_KEY=dry-run npx --yes @datadog/datadog-ci@5 coverage upload --dry-run --skip-git-metadata-upload <report path>` lists the file. A dry run sends nothing.
3. **CI uploaded the report:** after a push, the upload step's log shows `Uploaded <n> files` with `n` above 0 and no `Upload failed for` line. The count alone proves nothing, because `datadog-ci` exits 0 on most HTTP errors.
4. **Datadog shows coverage:** `Datadog:get_datadog_code_coverage_branch_summary` with `repository_url` `https://<REPO>` and the pushed branch returns `total_coverage`. Without the MCP server, when `DD_API_KEY` and `DD_APP_KEY` are set, use the [branch summary](https://docs.datadoghq.com/api/latest/code-coverage/) endpoint. A 404 means no coverage has arrived for that branch yet.

   ```sh
   SITE=<site from step 1> REPO=<host/owner/repo> BRANCH=<pushed branch>
   BODY=$(printf '{"data":{"type":"ci_app_coverage_branch_summary_request","attributes":{"repository_url":"https://%s","branch":"%s"}}}' "$REPO" "$BRANCH")
   { echo "DD-API-KEY: $DD_API_KEY"; echo "DD-APPLICATION-KEY: $DD_APP_KEY"; } |
     curl -sS -w ' HTTP %{http_code}' -H @- -H 'Content-Type: application/json' -X POST -d "$BODY" "https://api.$SITE/api/v2/code-coverage/branch/summary"
   ```

A pipeline edit does not run CI. Before the first run, the user creates the `DD_API_KEY` secret as the method's reference file describes, connects a [source code integration](https://docs.datadoghq.com/integrations/guide/source-code-integration/) when the repository is not connected yet, turns on the Code Coverage setting for `test_optimization`, and pushes the change.

The app host is `app.datadoghq.com` for `datadoghq.com`, `app.datadoghq.eu` for `datadoghq.eu`, `app.ddog-gov.com` for `ddog-gov.com`, and the site itself for the others, for example `us5.datadoghq.com`. End with this summary:

```text
Code Coverage: configured (<github_action | datadog_ci_upload | test_optimization>, <format> on <ci_provider>) | already configured | stopped (<reason>) | waiting for your choice
Changed: <files, or none>
Before the first CI run: <DD_API_KEY secret in <CI secret store> (create a key at https://APP_HOST/organization-settings/api-keys); source code integration for <REPO>; Code Coverage setting (test_optimization only); commit and push>
Evidence: <highest level reached>; not checked: <levels and the step left>
View: https://APP_HOST/ci/code-coverage
```

## Undo

Remove the upload steps and `datadog-ci` install lines that this run added, and revert any coverage change that the user agreed to.

## Gotchas

| Mistake | Symptom | Fix |
|---|---|---|
| Writing `--skip-git-metadata-upload=1` | `datadog-ci` v5 exits 1 with `Invalid option name` | Write the bare flag `--skip-git-metadata-upload` |
| Using `datadog/datadog-ci-action` | The workflow fails because that action does not exist | Use `DataDog/coverage-upload-github-action@v1` |
| Passing a tool name such as `cobertura_xml` to `--format`, or `--format` with a directory | `Unsupported format`, or every other file fails validation | Use a `datadog-ci` format name, with explicit files only |
| Pointing the upload at the wrong path, placing it before the report step, or relying on detection for a name such as `results.xml` | `Uploaded 0 files`, and the job still passes | Point it at the report file, after the step that writes it |
| An upload step with no guard for runs without secrets | Dependabot runs and fork pull requests fail with `API key is missing` (or a 403 on Azure) | Add the guard from the method's reference file |
| An Azure `script:` step on a Windows agent without `call` before `npm` and `datadog-ci` | The step stays green, and the upload never runs | Use `call`, or a `bash:` step |
