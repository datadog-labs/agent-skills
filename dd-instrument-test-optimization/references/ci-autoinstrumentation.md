# CI Auto-Instrumentation & Container Forwarding

Open this in step 3 when the project uses GitHub Actions, GitLab CI, or CircleCI with a supported runtime (`js`, `python`, `java`, `ruby`, `dotnet`, `go`) and the tests do not run inside a prebuilt Docker image.

## Contents

- Language token mapping
- GitHub Actions (`datadog/test-visibility-github-action@v3`)
- GitLab CI (`test-optimization-gitlab-script`)
- CircleCI (`datadog/test-optimization-circleci-orb`)
- Running tests inside Docker containers (`docker run` / `docker compose`)

## Language token mapping

Use these exact tokens for the `languages` input:

| Runtime | `languages` value | Notes |
|---|---|---|
| Node.js (JS/TS: Jest, Vitest, Mocha, Playwright, Cucumber) | `js` | Cypress is not supported by auto-instrumentation; use `manual-by-runtime.md` |
| Python (pytest, unittest, pytest-benchmark) | `python` | |
| Java / JVM (JUnit, TestNG, Spock, Scalatest, MUnit, Weaver, Karate) | `java` | |
| Ruby (RSpec, Minitest, Cucumber) | `ruby` | |
| .NET (xUnit, NUnit, MSTest v2) | `dotnet` | BenchmarkDotNet requires manual instrumentation |
| Go (`go test`) | `go` | The action and script install Orchestrion; set `go-module-dir` (GitHub) or `GO_MODULE_DIR` (GitLab) when `go.mod` is not at the repository root |

## GitHub Actions (`datadog/test-visibility-github-action@v3`)

1. Place the `Configure Datadog Test Optimization` step **immediately before** the step that runs tests (if placed before `actions/checkout` or dependency installation, those steps can wipe the injected tracer). Use `@v3`, or an exact release tag or full commit SHA when the repository pins its actions.
2. The action exports `DD_API_KEY`, `DD_SITE`, and `JOB_CHECK_RUN_ID` to later steps, so the test step needs no other Datadog variables, apart from `NODE_OPTIONS` for JavaScript.
3. **For `languages: js`:** GitHub Actions blocks actions from modifying `NODE_OPTIONS` for subsequent steps, so the action exports `DD_TRACE_PACKAGE` (and `DD_TRACE_ESM_IMPORT` for Vitest). You must add `NODE_OPTIONS` on the test step itself:
   - **Jest, Mocha, Playwright, Cucumber:** `NODE_OPTIONS: -r ${{ env.DD_TRACE_PACKAGE }}`
   - **Vitest:** `NODE_OPTIONS: -r ${{ env.DD_TRACE_PACKAGE }} --import ${{ env.DD_TRACE_ESM_IMPORT }}`

Example (`.github/workflows/test.yml`):

```yaml
      - name: Configure Datadog Test Optimization
        uses: datadog/test-visibility-github-action@v3
        with:
          languages: js
          api_key: ${{ secrets.DD_API_KEY }}
          site: datadoghq.com
      - name: Run tests
        run: npm test
        env:
          NODE_OPTIONS: -r ${{ env.DD_TRACE_PACKAGE }}
```

For Vitest, set `NODE_OPTIONS: -r ${{ env.DD_TRACE_PACKAGE }} --import ${{ env.DD_TRACE_ESM_IMPORT }}`.

## GitLab CI (`test-optimization-gitlab-script`)

In `.gitlab-ci.yml`, add the auto-instrumentation script right before the test command in the test job's `script:` block. Keep the release URL and its checksum together; when you change the version, change the checksum with it:

```yaml
test-job:
  stage: test
  script:
    - |
      LANGUAGES="<language_token>" SITE="<SITE>" API_KEY="$DD_API_KEY" \
      SRC=$(curl -fsSL https://github.com/DataDog/test-optimization-gitlab-script/releases/download/v1.2.10/script.sh); \
      echo "$SRC" | sha256sum | grep -q '^c82030bdbc7cbbbb139751885ca0392a7191c858f5a080ae364e0ec16218b783' && \
        source <(echo "$SRC") || \
        echo "ERROR: SHA256 mismatch. Datadog Test Optimization auto-instrumentation not enabled." >&2
    # For Vitest only, also add:
    # - export NODE_OPTIONS="$NODE_OPTIONS --import $DD_TRACE_ESM_IMPORT"
    - <existing test command>
```

## CircleCI (`datadog/test-optimization-circleci-orb`)

In `.circleci/config.yml` (with `version: 2.1` at the top), declare the orb at the top level and invoke `autoinstrument` right before the test step:

```yaml
version: 2.1
orbs:
  test-visibility-circleci-orb: datadog/test-optimization-circleci-orb@2

jobs:
  test:
    steps:
      - checkout
      - test-visibility-circleci-orb/autoinstrument:
          languages: <language_token>
          site: <SITE>
      # For Vitest only, also add:
      # - run: echo "export NODE_OPTIONS=\"$NODE_OPTIONS --import $DD_TRACE_ESM_IMPORT\"" >> $BASH_ENV
      - run: <existing test command>
```

The user must set `DD_API_KEY` in CircleCI Project Settings > Environment Variables.

## Running tests inside Docker containers (`docker run` / `docker compose`)

Apply this section **only** when the CI test step dispatches the test process from the runner into a separate container via `docker run` or `docker compose` (not when the CI job itself runs inside a standard container executor, or when Docker only runs a sidecar database).

### 1. Prebuilt image vs. image built in the job

- **If the test container uses a prebuilt image** (or a `Dockerfile` that only copies selective files and does not copy the runner's auto-instrumented tracer directory): host-level auto-instrumentation (`datadog/test-visibility-github-action@v3`) cannot inject the tracer into the container. Do **not** use the auto-instrumentation action; instead, follow the manual instrumentation reference linked in `SKILL.md` step 3 to install the tracer inside the container or image and pass the environment variables below into `docker run`.
- **If the image is built in the job after auto-instrumentation or mounts the runner workspace:** forward the environment variables below into `docker run` or `docker-compose.yml`.

### 2. Environment variables to forward into `docker run` / `docker compose`

Pass these variables with `-e VAR` (or under `environment:` in `docker-compose.yml`):

- **Datadog core variables (always):**
  `DD_CIVISIBILITY_ENABLED`, `DD_CIVISIBILITY_AGENTLESS_ENABLED`, `DD_SITE`, `DD_API_KEY`
- **Runtime injection variables (by runtime):**
  - `nodejs`: `NODE_OPTIONS`, `DD_TRACE_PACKAGE`, `DD_TRACE_ESM_IMPORT`
  - `ruby`: `RUBYOPT`
  - `java`: `DD_AGENTLESS_LOG_SUBMISSION_ENABLED`, plus `MAVEN_OPTS` (Maven), `GRADLE_OPTS` (Gradle), `SBT_OPTS` (SBT), or `JAVA_TOOL_OPTIONS`
- **CI provider metadata variables:**
  - **GitHub Actions:** `GITHUB_ACTION`, `GITHUB_SERVER_URL`, `GITHUB_RUN_ID`, `GITHUB_RUN_NUMBER`, `GITHUB_RUN_ATTEMPT`, `GITHUB_WORKFLOW`, `GITHUB_WORKSPACE`, `GITHUB_REPOSITORY`, `GITHUB_SHA`, `GITHUB_HEAD_REF`, `GITHUB_REF`, `GITHUB_JOB`, `JOB_CHECK_RUN_ID`
  - **GitLab CI:** `GITLAB_CI`, `CI_PIPELINE_ID`, `CI_PIPELINE_URL`, `CI_PIPELINE_IID`, `CI_PROJECT_PATH`, `CI_PROJECT_DIR`, `CI_PROJECT_URL`, `CI_JOB_STAGE`, `CI_JOB_NAME`, `CI_JOB_URL`, `CI_JOB_ID`, `CI_RUNNER_ID`, `CI_REPOSITORY_URL`, `CI_COMMIT_SHA`, `CI_COMMIT_REF_NAME`, `CI_COMMIT_BRANCH`, `CI_COMMIT_TAG`, `CI_COMMIT_AUTHOR`, `CI_COMMIT_MESSAGE`, `CI_COMMIT_TIMESTAMP`
  - **CircleCI:** `CIRCLECI`, `CIRCLE_WORKFLOW_ID`, `CIRCLE_BUILD_NUM`, `CIRCLE_BUILD_URL`, `CIRCLE_JOB`, `CIRCLE_PROJECT_REPONAME`, `CIRCLE_REPOSITORY_URL`, `CIRCLE_SHA1`, `CIRCLE_BRANCH`, `CIRCLE_TAG`, `CIRCLE_WORKING_DIRECTORY`
  - **Jenkins:** `JENKINS_URL`, `BUILD_TAG`, `BUILD_NUMBER`, `BUILD_URL`, `BUILD_ID`, `JOB_NAME`, `JOB_URL`, `WORKSPACE`, `NODE_NAME`, `GIT_COMMIT`, `GIT_URL`, `GIT_BRANCH`
  - **Buildkite:** `BUILDKITE`, `BUILDKITE_BUILD_ID`, `BUILDKITE_BUILD_NUMBER`, `BUILDKITE_BUILD_URL`, `BUILDKITE_JOB_ID`, `BUILDKITE_PIPELINE_SLUG`, `BUILDKITE_REPO`, `BUILDKITE_COMMIT`, `BUILDKITE_BRANCH`, `BUILDKITE_TAG`, `BUILDKITE_BUILD_CHECKOUT_PATH`
  - **Bitbucket:** `BITBUCKET_PIPELINE_UUID`, `BITBUCKET_BUILD_NUMBER`, `BITBUCKET_REPO_FULL_NAME`, `BITBUCKET_REPO_SLUG`, `BITBUCKET_COMMIT`, `BITBUCKET_BRANCH`, `BITBUCKET_TAG`, `BITBUCKET_CLONE_DIR`
  - **Azure Pipelines:** `TF_BUILD`, `BUILD_BUILDID`, `BUILD_BUILDNUMBER`, `SYSTEM_TEAMFOUNDATIONSERVERURI`, `SYSTEM_TEAMPROJECT`, `SYSTEM_JOBID`, `SYSTEM_TASKINSTANCEID`, `BUILD_REPOSITORY_URI`, `BUILD_SOURCEVERSION`, `BUILD_SOURCEBRANCH`, `BUILD_SOURCESDIRECTORY`

Example (`docker run` on GitHub Actions for Ruby RSpec):

```yaml
      - name: Run tests in Docker
        env:
          JOB_CHECK_RUN_ID: ${{ job.check_run_id }}
          DD_API_KEY: ${{ secrets.DD_API_KEY }}
        run: |
          docker run --rm \
            -e DD_CIVISIBILITY_ENABLED=true \
            -e DD_CIVISIBILITY_AGENTLESS_ENABLED=true \
            -e DD_SITE=datadoghq.com \
            -e DD_API_KEY \
            -e RUBYOPT="-rbundler/setup -rdatadog/ci/auto_instrument" \
            -e GITHUB_ACTION -e GITHUB_SERVER_URL -e GITHUB_RUN_ID \
            -e GITHUB_RUN_NUMBER -e GITHUB_RUN_ATTEMPT -e GITHUB_WORKFLOW \
            -e GITHUB_WORKSPACE -e GITHUB_REPOSITORY -e GITHUB_SHA \
            -e GITHUB_HEAD_REF -e GITHUB_REF -e GITHUB_JOB -e JOB_CHECK_RUN_ID \
            my-test-image:latest bundle exec rspec
```
