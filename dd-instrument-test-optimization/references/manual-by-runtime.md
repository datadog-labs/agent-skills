# Manual Instrumentation by Runtime

Open this in step 3 when the CI provider does not support auto-instrumentation (Buildkite, Jenkins, Bitbucket, Azure Pipelines, Travis CI), when tests run inside a prebuilt Docker image, when the framework requires manual setup (Cypress, BenchmarkDotNet, Swift), or when the user asks for manual instrumentation.

## Contents

- Shared environment variables and CI provider rules
- Node.js (Jest, Vitest, Mocha, Playwright, Cucumber, Cypress)
- Python (pytest, unittest, pytest-benchmark)
- Java / JVM (Maven, Gradle, SBT)
- Go (`orchestrion`)
- Ruby (RSpec, Minitest, Cucumber)
- .NET (xUnit, NUnit, MSTest v2, BenchmarkDotNet)
- Swift (XCTest, Swift Testing)

## Shared environment variables and CI provider rules

Every manual test run needs these environment variables on the test step or job:

```yaml
DD_CIVISIBILITY_ENABLED: true
DD_CIVISIBILITY_AGENTLESS_ENABLED: true
DD_SITE: <SITE>
```

Plus `DD_API_KEY` from the CI provider's secret store:
- **GitHub Actions:** `DD_API_KEY: ${{ secrets.DD_API_KEY }}` and `JOB_CHECK_RUN_ID: ${{ job.check_run_id }}` (the tracers read it to link test runs to the exact job)
- **GitLab CI / Bitbucket / Travis CI:** `DD_API_KEY: $DD_API_KEY`
- **Buildkite:** `DD_API_KEY: $DD_API_KEY` (write single `$`, never `$$DD_API_KEY`)
- **CircleCI:** `echo "export DD_API_KEY=${DD_API_KEY}" >> $BASH_ENV`
- **Azure Pipelines:** `DD_API_KEY: $(DD_API_KEY)`

## Node.js (Jest, Vitest, Mocha, Playwright, Cucumber, Cypress)

1. Install `dd-trace` as a dev dependency before tests run (with the project's package manager: `npm install --save-dev dd-trace`, `yarn add --dev dd-trace`, `pnpm add --save-dev dd-trace`, or `bun add --dev dd-trace`).
2. Set `NODE_OPTIONS` for the test command:
   - **Jest, Mocha, Playwright, Cucumber:** `NODE_OPTIONS: "-r dd-trace/ci/init"`
   - **Vitest (>=1.6.0):** `NODE_OPTIONS: "--import dd-trace/register.js -r dd-trace/ci/init"`
   - Apply it only to commands that run after `dd-trace` is installed. Every Node process preloads `NODE_OPTIONS`, including `npm`, `yarn`, and `pnpm` themselves, so a preload set before the install fails with `Cannot find module 'dd-trace/ci/init'`. In GitHub Actions, put it in the test step's `env:` (installs run in earlier steps). When one step installs and tests (Buildkite, GitLab, Bitbucket, Travis CI), `export` it on a line after the install command, or prefix only the test command.
   - If `package.json` already sets `NODE_OPTIONS` inside the test script, preserve `${NODE_OPTIONS:-}` so the CI variable is not overwritten.
3. **Cypress (>=6.7.0):** auto-instrumentation and `NODE_OPTIONS` alone do not instrument Cypress browser specs. Install `dd-trace`, require `'dd-trace/ci/cypress/plugin'` in `cypress.config.{js,ts}` inside `setupNodeEvents(on, config)`, and import `'dd-trace/ci/cypress/support'` in `cypress/support/e2e.{js,ts}` (see the [JavaScript Test Optimization setup docs](https://docs.datadoghq.com/tests/setup/javascript/)).

Example (Buildkite `buildkite/pipeline.yml` for Jest):

```yaml
steps:
  - label: "Run tests"
    command:
      - npm install --save-dev dd-trace
      - export NODE_OPTIONS="-r dd-trace/ci/init"
      - npm test
    env:
      DD_CIVISIBILITY_ENABLED: true
      DD_CIVISIBILITY_AGENTLESS_ENABLED: true
      DD_SITE: datadoghq.com
      DD_API_KEY: $DD_API_KEY
```

## Python (pytest, unittest, pytest-benchmark)

1. Install `ddtrace` in the CI job before tests run: `pip install -U ddtrace`.
2. Set `DD_CIVISIBILITY_ENABLED: true`, `DD_CIVISIBILITY_AGENTLESS_ENABLED: true`, `DD_SITE`, and `DD_API_KEY`.
3. Update the test command:
   - **pytest / pytest-benchmark:** append `--ddtrace` (for example `pytest --ddtrace`).
   - **unittest:** prefix with `ddtrace-run` (for example `ddtrace-run python -m unittest`).

## Java / JVM (Maven, Gradle, SBT)

1. Download the Datadog Java tracer JAR before tests run:
   ```sh
   mkdir -p "$HOME/.datadog"
   wget -O "$HOME/.datadog/dd-java-agent.jar" https://dtdg.co/latest-java-tracer
   ```
2. Set environment variables on the test step:
   ```yaml
   DD_CIVISIBILITY_ENABLED: true
   DD_CIVISIBILITY_AGENTLESS_ENABLED: true
   DD_AGENTLESS_LOG_SUBMISSION_ENABLED: true
   DD_SITE: <SITE>
   DD_API_KEY: <secret reference>
   ```
3. Set the `-javaagent` flag from a shell command, not from a YAML `env:` value: most CI providers do not expand `$HOME` in YAML values, and the JVM then cannot open the JAR. Use the build tool's variable (`MAVEN_OPTS` for Maven, `GRADLE_OPTS` for Gradle, `SBT_OPTS` for SBT, `JAVA_TOOL_OPTIONS` for other JVMs), shown here for Maven:
   - **GitHub Actions:** in the download step, `echo "MAVEN_OPTS=-javaagent:$HOME/.datadog/dd-java-agent.jar" >> "$GITHUB_ENV"`
   - **CircleCI:** `echo "export MAVEN_OPTS=\"-javaagent:$HOME/.datadog/dd-java-agent.jar\"" >> "$BASH_ENV"`
   - **Other providers:** `export MAVEN_OPTS="-javaagent:$HOME/.datadog/dd-java-agent.jar ${MAVEN_OPTS:-}"` on a line before the test command

## Go (`orchestrion`)

1. Install `orchestrion` before running tests:
   ```sh
   go install github.com/DataDog/orchestrion@latest
   ```
2. Set `DD_CIVISIBILITY_ENABLED: true`, `DD_CIVISIBILITY_AGENTLESS_ENABLED: true`, `DD_SITE`, and `DD_API_KEY`.
3. Prefix the `go test` command with `orchestrion`:
   ```sh
   orchestrion go test ./...
   ```

## Ruby (RSpec, Minitest, Cucumber)

1. Add `datadog-ci` to the test group (in the `Gemfile` / `Dockerfile` or right before tests run):
   ```sh
   bundle add datadog-ci --group "test"
   ```
2. Set environment variables on the test step (or forward them into `docker run`):
   ```yaml
   DD_CIVISIBILITY_ENABLED: true
   DD_CIVISIBILITY_AGENTLESS_ENABLED: true
   DD_SITE: <SITE>
   DD_API_KEY: <secret reference>
   RUBYOPT: "-rbundler/setup -rdatadog/ci/auto_instrument"
   ```
   `RUBYOPT` auto-instruments RSpec, Minitest, and Cucumber without modifying the test command. Apply it only to commands that run after `datadog-ci` is installed: every Ruby process preloads `RUBYOPT`, Bundler included, so `bundle install` or `bundle add` run with it fails with `cannot load such file -- datadog/ci/auto_instrument`. When one step installs and tests, `export` it on a line after the install command.

## .NET (xUnit, NUnit, MSTest v2, BenchmarkDotNet)

1. Install the global `dd-trace` .NET tool before tests run:
   ```sh
   dotnet tool update --global dd-trace
   ```
2. Set `DD_CIVISIBILITY_ENABLED: true`, `DD_CIVISIBILITY_AGENTLESS_ENABLED: true`, `DD_SITE`, and `DD_API_KEY`.
3. Prefix the test command with `dd-trace ci run --`:
   ```sh
   dd-trace ci run -- dotnet test
   ```
   For BenchmarkDotNet, also install the `Datadog.Trace.BenchmarkDotNet` NuGet package.

## Swift (XCTest, Swift Testing)

Follow the [Swift Test Optimization setup documentation](https://docs.datadoghq.com/tests/setup/swift/): add the `dd-sdk-swift-testing` package dependency and pass `TEST_RUNNER_DD_CIVISIBILITY_ENABLED=true`, `TEST_RUNNER_DD_CIVISIBILITY_AGENTLESS_ENABLED=true`, `TEST_RUNNER_DD_SITE=<SITE>`, and `TEST_RUNNER_DD_API_KEY` to `xcodebuild test` or `swift test`.
