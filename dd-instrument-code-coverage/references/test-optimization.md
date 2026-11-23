# Coverage through Test Optimization

Open this in step 2 or 3 when the job that writes the coverage report already runs Datadog Test Optimization. With a supported library, the library uploads the coverage report by itself after the test command finishes, so no pipeline file changes.

## Supported libraries

| Language | Test Optimization library | First supported version | Coverage source |
|---|---|---|---|
| Ruby | `datadog-ci` gem | 1.27.0 | SimpleCov |
| JavaScript, TypeScript | `dd-trace` | 5.85.0 in 5.x, any 6.x | Jest, Vitest or NYC coverage |
| Python | `ddtrace` | 4.4.0 | the default pytest plugin with coverage.py |
| Java, Kotlin, Scala | `dd-java-agent` | 1.53.0 | JaCoCo |

Read the version from the lockfile, the Gemfile.lock, the requirements file or the agent download in CI. Treat the version as supported when CI installs the latest library, for example through `datadog/test-visibility-github-action`, the GitLab auto-instrumentation script, an unpinned install, or the `dtdg.co/latest-java-tracer` download.

Use an upload step instead (`github_action` or `datadog_ci_upload`) for .NET, Go, Swift and PHP, and for a library older than the version above. Upgrading the library is the user's choice, so mention it but do not make the change.

## Steps

1. Confirm that Test Optimization runs in the job that writes the coverage report: the Test Optimization action or script, or `DD_CIVISIBILITY_ENABLED=true` with the library's test hook.
2. Confirm that the CI test command writes coverage from the source in the table, for example `jest --coverage`, `vitest --coverage`, `pytest --cov`, or SimpleCov started in `spec_helper.rb`. When it does not, stop and ask as in step 2 of SKILL.md.
3. Change no pipeline file, test command or dependency.
4. Tell the user to turn on **Code Coverage** in the CI/CD settings at `https://APP_HOST/ci/settings/ci-cd/repositories?tab=organization`, at the organization, repository or test service level. Without this setting, the library uploads nothing. When the Datadog MCP server is connected, `Datadog:get_datadog_test_optimization_settings` with `repository_id` `<REPO>`, the test service name and the environment shows whether `code_coverage` is already on. Do not change the setting without the user's explicit approval.

Build `APP_HOST` as described in Verify and view in SKILL.md.
