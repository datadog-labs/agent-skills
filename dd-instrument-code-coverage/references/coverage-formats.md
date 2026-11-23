# Coverage report formats

Open this in step 1 to find the coverage report that CI writes, and in step 2 to name the change that would write one.

## Contents

- Reports by language
- Format names
- How `datadog-ci` finds reports
- CI-only conditions

## Reports by language

| Language | Tool and CI command that writes the report | Format | Usual path |
|---|---|---|---|
| JavaScript, TypeScript | Jest `--coverage` (the default reporters include `lcov` and `clover`) | LCOV | `coverage/lcov.info` |
| JavaScript, TypeScript | Vitest `--coverage` with `@vitest/coverage-v8` or `@vitest/coverage-istanbul` (the default reporters include `clover`; `lcov` must be added to `coverage.reporter`) | Clover, LCOV | `coverage/clover.xml`, `coverage/lcov.info` |
| JavaScript, TypeScript | NYC, for example `nyc --reporter=lcov mocha`, also used with Playwright and Cypress | LCOV | `coverage/lcov.info` |
| Python | pytest-cov `pytest --cov=<package> --cov-report=xml`, or coverage.py `coverage xml` | Cobertura | `coverage.xml` |
| Java, Kotlin, Scala | JaCoCo Maven plugin `report` goal | JaCoCo | `target/site/jacoco/jacoco.xml` |
| Java, Kotlin, Scala | Gradle `jacocoTestReport` with `xml.required = true` (Gradle writes no XML by default) | JaCoCo | `build/reports/jacoco/test/jacocoTestReport.xml` |
| Kotlin | Kover `koverXmlReport` | JaCoCo | `build/reports/kover/report.xml` |
| Go | `go test -coverprofile=coverage.out ./...` | Go coverprofile | `coverage.out` |
| .NET | Coverlet collector, `dotnet test --collect:"XPlat Code Coverage"` | Cobertura | `TestResults/<guid>/coverage.cobertura.xml` |
| .NET | coverlet.msbuild, `dotnet test /p:CollectCoverage=true /p:CoverletOutputFormat=opencover` | OpenCover | `coverage.opencover.xml` |
| Ruby | SimpleCov with its JSON formatter | SimpleCov JSON | `coverage/coverage.json` |
| Ruby | SimpleCov's own result file, written on every run | SimpleCov internal | `coverage/.resultset.json` |
| PHP | PHPUnit `--coverage-clover clover.xml` or `--coverage-cobertura cobertura.xml` (needs Xdebug or PCOV) | Clover, Cobertura | the path given |
| Swift | Xcode and SwiftPM write `.xcresult` bundles or llvm-cov JSON, which Datadog does not read. An LCOV export (`xcrun llvm-cov export -format=lcov`) or a Cobertura converter is needed. | LCOV, Cobertura | the path given |

An existing Codecov or Coveralls step, a GitLab `artifacts:reports:coverage_report` entry, or an Azure `PublishCodeCoverageResults` task already names the coverage report. Keep that step and add the Datadog upload after it.

When a tool writes the same coverage in two formats, such as Jest's `lcov.info` and `clover.xml`, upload only one of them.

## Format names

The onboarding tool and `datadog-ci` name the formats differently. Use the tool names only as `coverage_format` arguments, and the `datadog-ci` names only with `--format` or the action's `format` input.

| Format | Onboarding tool `coverage_format` | `datadog-ci --format` |
|---|---|---|
| LCOV | `lcov` | `lcov` |
| Cobertura XML | `cobertura_xml` | `cobertura` |
| JaCoCo XML | `jacoco_xml` | `jacoco` |
| Go coverprofile | `go_coverprofile` | `go-coverprofile` |
| Clover XML | `clover_xml` | `clover` |
| OpenCover XML | `opencover_xml` | `opencover` |
| SimpleCov JSON | `simplecov_json` | `simplecov` |
| SimpleCov internal (`.resultset.json`) | `simplecov_json` | `simplecov-internal` |

## How `datadog-ci` finds reports

`datadog-ci coverage upload` and the GitHub Action take files, directories or glob patterns. A directory is searched recursively, `node_modules` is skipped, and only files with these names are kept:

- `.xml` files whose name contains `coverage`, `jacoco`, `cobertura` or `clover`, and Kover `report.xml` files;
- `.json` files whose name contains `coverage`, and `.resultset.json`;
- `lcov.info`, `lcov.dat`, `lcov-report.info` and `*.lcov` files;
- `.out` files and other files whose name contains `coverage`, when they start with `mode: set`, `mode: count` or `mode: atomic`.

A file passed by its path is also recognized by its content. When a file name matches none of these patterns, pass the file path. When the content is not recognized either, pass the file path with `--format`. A search that finds nothing still exits 0 and prints `Uploaded 0 files`.

## CI-only conditions

Coverage is often configured differently in CI and on a laptop. Before you accept a coverage report, confirm that the command CI runs writes it:

- Compare the CI test command with the package script or Make target it calls. For example, `npm test` may run `jest` while only `npm run test:coverage` adds `--coverage`.
- Read the coverage configuration for conditions on `CI`, `GITHUB_ACTIONS`, `GITLAB_CI` or `TF_BUILD`: `jest.config.*`, `vitest.config.*`, `.nycrc`, `pytest.ini`, `pyproject.toml`, `setup.cfg`, `tox.ini`, `.coveragerc`, `build.gradle(.kts)`, `pom.xml` profiles, `spec_helper.rb`, `test_helper.rb` and `.simplecov`.
- When the format CI needs is written only outside CI, tell the user and ask how to resolve it, as in step 2.
