# Upload with datadog-ci

Open this in step 3 for GitLab CI and Azure Pipelines, or for GitHub Actions when the user asks for the CLI.

## Contents

- Install and command
- GitLab CI
- Azure Pipelines
- GitHub Actions with the CLI

## Install and command

Install `datadog-ci` v5 in the job that writes the coverage report, after the tests. Pin the major version, so that a future major release cannot change the command under the pipeline:

- When the job's image or agent has Node.js: `npm install -g @datadog/datadog-ci@5`.
- Otherwise, download the standalone binary of a v5 release for the runner, for example on Linux x64: `curl -L --fail "https://github.com/DataDog/datadog-ci/releases/download/v5.28.0/datadog-ci_linux-x64" --output "/usr/local/bin/datadog-ci" && chmod +x /usr/local/bin/datadog-ci`. The other binaries are `datadog-ci_linux-arm64`, `datadog-ci_alpine-x64`, `datadog-ci_alpine-arm64`, `datadog-ci_darwin-x64`, `datadog-ci_darwin-arm64` and `datadog-ci_win-x64`. When `/usr/local/bin` is not writable, write the binary into the workspace and run `./datadog-ci`.

Then run:

```sh
datadog-ci coverage upload --skip-git-metadata-upload <coverage report or directory>
```

`--skip-git-metadata-upload` is a bare flag. The source code integration already provides git data, and the flag avoids failed git syncs on shallow clones. Add `--base-path <dir>` when the paths in the coverage report are relative to a subdirectory, and `--format <name>` only with explicit files. The command reads `DD_API_KEY` and `DD_SITE` from the environment, and it exits 1 when `DD_API_KEY` is empty.

## GitLab CI

In `.gitlab-ci.yml`, add the upload to the `script:` of the job that writes the coverage report, after the test command, and add `DD_SITE` to that job's `variables:`:

```yaml
test:
  script:
    - <existing test command>
    - |
      if [ -n "$DD_API_KEY" ]; then
        npm install -g @datadog/datadog-ci@5
        datadog-ci coverage upload --skip-git-metadata-upload coverage/lcov.info
      else
        echo "DD_API_KEY is not available in this pipeline; skipping the Datadog coverage upload."
      fi
  variables:
    DD_SITE: <SITE>
```

GitLab passes project CI/CD variables to every job, so the script reads `$DD_API_KEY` without a `variables:` entry. The `if` skips the upload where the variable is missing: merge requests from forks, which run in the fork's project, and unprotected branches when the variable is protected. Keep existing `variables:` entries and merge `DD_SITE` into them. When the job's `image:` has no Node.js, such as `python:3.12` or `golang:1.23`, use the standalone binary. On a Windows runner with PowerShell, write the guard as `if ($env:DD_API_KEY) { ... }`.

Tell the user to create the variable: in the project, open **Settings > CI/CD > Variables**, click **Add variable**, and add `DD_API_KEY` with a Datadog API key and **Masked** selected. A **Protected** variable reaches only protected branches and tags, so other branches skip the upload.

## Azure Pipelines

In the pipeline file, add a step to the job that writes the coverage report, after the test step. Azure does not pass secret variables to scripts, so map the key into the step's `env:`:

```yaml
  - script: |
      npm install -g @datadog/datadog-ci@5
      datadog-ci coverage upload --skip-git-metadata-upload coverage/lcov.info
    displayName: Upload coverage to Datadog
    condition: and(succeeded(), ne(variables['System.PullRequest.IsFork'], 'True'))
    env:
      DD_API_KEY: $(DD_API_KEY)
      DD_SITE: <SITE>
```

Azure gives no secrets to pull request builds from forks by default. There `$(DD_API_KEY)` stays the literal text `$(DD_API_KEY)`, the upload gets a 403 and exits 1, and the `condition:` skips the step.

On a Windows agent, such as `vmImage: windows-latest`, a `script:` step runs in `cmd.exe`, and `npm` and `datadog-ci` are batch files. A batch file called without `call` never returns, so the upload line would never run and the step would stay green. Write `call npm install -g @datadog/datadog-ci@5` and `call datadog-ci coverage upload ...`, or use a `- bash:` step instead of `- script:`.

Microsoft-hosted agents have Node.js. On a self-hosted agent without it, use the standalone binary. Keep the step's indentation the same as the steps around it, and put it in the same job, for example under the same `- job:` or `steps:` list.

Tell the user to create the variable: open the pipeline, click **Edit > Variables > New variable**, add `DD_API_KEY` with a Datadog API key, and select **Keep this value secret**. A variable group linked to the pipeline also works.

## GitHub Actions with the CLI

```yaml
      - name: Upload coverage to Datadog
        if: ${{ github.actor != 'dependabot[bot]' && (github.event_name != 'pull_request' || github.event.pull_request.head.repo.full_name == github.repository) }}
        run: |
          npm install -g @datadog/datadog-ci@5
          datadog-ci coverage upload --skip-git-metadata-upload coverage/lcov.info
        env:
          DD_API_KEY: ${{ secrets.DD_API_KEY }}
          DD_SITE: <SITE>
```

The guard and the secret are described in [upload-github-actions.md](upload-github-actions.md).
