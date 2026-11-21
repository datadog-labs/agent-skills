# Upload with the GitHub Action

Open this in step 3 for GitHub Actions. `DataDog/coverage-upload-github-action@v1` installs the `datadog-ci` standalone binary and runs `datadog-ci coverage upload`, so the job needs no Node.js. It runs on Linux (x64 and arm64), macOS (x64 and arm64) and Windows (x64) runners.

## Step

Add this step to the job that writes the coverage report, directly after the step that writes it:

```yaml
      - name: Upload coverage to Datadog
        if: ${{ github.actor != 'dependabot[bot]' && (github.event_name != 'pull_request' || github.event.pull_request.head.repo.full_name == github.repository) }}
        uses: DataDog/coverage-upload-github-action@v1
        with:
          api_key: ${{ secrets.DD_API_KEY }}
          site: <SITE>
          files: <coverage report or directory, for example coverage/lcov.info>
```

Always set `site`, because the action defaults to `datadoghq.com`.

GitHub gives Actions secrets neither to runs that Dependabot triggers nor to pull requests from forks. There the key is empty, `datadog-ci` fails with `API key is missing`, and the test job turns red. The `if:` guard skips the step in those runs. When the workflow already has an `if:` on this step, join both conditions with `&&`. When the user adds `DD_API_KEY` as a Dependabot secret too (**Settings > Secrets and variables > Dependabot**), drop the `github.actor` part of the guard so that Dependabot runs upload as well.

## Inputs

| Input | Use |
|---|---|
| `api_key` (required) | `${{ secrets.DD_API_KEY }}` |
| `site` | `<SITE>` |
| `files` | Files, directories or glob patterns, separated by spaces. The default `.` searches the whole workspace. |
| `format` | A `datadog-ci` format name, only when `files` lists explicit files |
| `base-path` | The subdirectory that the paths in the coverage report are relative to |
| `ignored-paths` | Comma-separated paths to skip during the search |
| `flags` | Comma-separated labels to group uploads, for example `type:unit-tests` |
| `version` | The `datadog-ci` version to install. The default is the latest `v5`. |

## Pinned actions

When the workflow pins its actions to commit SHAs, pin this one the same way. Find the newest `v1.x.y` tag and its commit with `git ls-remote --tags https://github.com/DataDog/coverage-upload-github-action`, then write `uses: DataDog/coverage-upload-github-action@<sha> # v1.x.y`. Do not mix tag and SHA references in one workflow.

## Matrix jobs

When the matrix runs the same tests on several versions, guard the step so that it runs once, for example by adding `matrix.node-version == '22' &&` in front of the guard, using a value that exists in the matrix. When the matrix splits the tests into shards, keep the step on every entry.

## Secret

Tell the user to create the secret: in the repository, open **Settings > Secrets and variables > Actions**, click **New repository secret**, and add `DD_API_KEY` with a Datadog API key. An organization secret with the same name also works.
