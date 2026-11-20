# JUnit XML Report Upload

Open this in step 3 when the project uses an unsupported language or test runner (and the user chose JUnit XML reporting over migrating to a supported runner), or when the user explicitly asked for JUnit XML upload.

## How it works

1. The test runner must emit a valid JUnit XML file (`<testsuite>` / `<testcase>` elements with test names, durations, and `<failure>` / `<error>` child nodes on failed tests).
2. Install `@datadog/datadog-ci` in the CI job (`npm install -g @datadog/datadog-ci`, `yarn global add @datadog/datadog-ci`, or download the standalone `datadog-ci` binary from `https://github.com/DataDog/datadog-ci/releases/latest/` when Node.js is not present). Do **not** use a third-party or deprecated GitHub Action for JUnit uploads; invoke `datadog-ci junit upload` directly.
3. Run `datadog-ci junit upload` **even when the test step fails**, while preserving the test step's exit code so a failing test suite still fails the CI job:
   - **GitHub Actions:** add `if: ${{ !cancelled() }}` on the upload step.
   - **GitLab CI / Travis CI:** run the upload in `after_script:`.
   - **Bitbucket Pipelines:** run the upload in `after-script:` (with a hyphen).
   - **CircleCI:** add `when: always` on the upload step.
   - **Other providers:** capture `exit_code=$?` under `set +e`, upload, and `exit $exit_code`.

## Example (GitHub Actions)

```yaml
      - name: Run tests
        run: npm test
      - name: Upload test results to Datadog
        if: ${{ !cancelled() }}
        run: npx --yes @datadog/datadog-ci junit upload --service <service_name> --env ci ./junit.xml
        env:
          DD_SITE: <SITE>
          DD_API_KEY: ${{ secrets.DD_API_KEY }}
```

Keep each XML report under 250 MiB. Note in the final summary that JUnit XML upload provides test results and failure tracking in Datadog, but does not provide runtime span traces or automatic Test Impact Analysis test skipping.
