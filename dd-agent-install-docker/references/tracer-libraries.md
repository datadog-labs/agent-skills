# Tracer libraries for Compose services

## Contents

- Before you add a tracer
- Versions
- Python, Node.js, Java, PHP, .NET, Ruby, Go, C++

## Before you add a tracer

Compose services get traces from a Datadog tracer library inside the application, plus the
connection settings that point it at the Agent. Every traced service gets these environment
variables (the prepare mode writes them in the overlay; install now adds them to the service):

```yaml
      - DD_AGENT_HOST=datadog-agent
      - DD_TRACE_AGENT_PORT=8126
      - DD_ENV=<env>
```

Add `DD_SERVICE=<service>` and `DD_VERSION=<version>` only when the user gives them.

Find the language from the service's own build context (`build:` or its Dockerfile's directory):
`requirements.txt`, `pyproject.toml` or `setup.py` for Python, `package.json` for Node.js,
`pom.xml` or `build.gradle` for Java, `composer.json` for PHP, `*.csproj` for .NET, `Gemfile` for
Ruby, `go.mod` for Go, and `CMakeLists.txt` for C++. A shared context with a per-service
Dockerfile path belongs to the Dockerfile's directory, not the shared root.

Skip a service when its dependency manifest and start command already load a Datadog tracer. When
the tracer is still missing, do not report APM as working: traces start only after the user adds it.

**Who makes the change.** The tracer step edits application files (a dependency manifest, a
Dockerfile or the start command). In the prepare mode, leave those files alone and give the step as
a next step labeled "Add the Datadog tracing library to your application". In install now, show the
change and apply it only after the user agrees, then rebuild and recreate that service.

## Versions

When the run settings or the user give versions (for example `python:v4,js:v5`), strip the
`v` and pin that major version. Otherwise use the newest release.

| Language | Package | Pin for major `N` |
|---|---|---|
| Python | `ddtrace` on PyPI | `ddtrace>=N,<N+1` |
| Node.js (`js`) | `dd-trace` on npm | `dd-trace@N` |
| Java | `com.datadoghq:dd-java-agent` on Maven Central | the newest `N.x` jar |
| PHP | `datadog-setup.php` from the DataDog/dd-trace-php GitHub releases | the newest `N.x` release |
| .NET | `datadog-dotnet-apm` packages from the DataDog/dd-trace-dotnet GitHub releases | the newest `vN.x` release |
| Ruby | `datadog` on RubyGems | `gem 'datadog', '~> N.0'` |
| Go | `github.com/DataDog/dd-trace-go/v2` | the major is part of the module path |
| C++ | `dd-trace-cpp` from its GitHub releases | the newest `vN.x` tag |

## Python

Add `ddtrace` to the dependency file, and start the app through `ddtrace-run`, for example
`CMD ["ddtrace-run", "gunicorn", "app:app"]` or `command: ddtrace-run python app.py`. `ddtrace` 4.x
supports Python 3.9 and later.

## Node.js

Install `dd-trace` with the project's package manager. Load it before any other module: set
`NODE_OPTIONS=--require dd-trace/init` on the service, or put `require('dd-trace').init()` on the
first line of the entry point. For Next.js, initialize it in `instrumentation.ts` (or `.js`) instead.
`dd-trace` 6.x needs Node.js 22 or later; for Node.js 18 to 21, pin `dd-trace@5`.

## Java

Fetch the agent jar in the image build, for example
`ADD https://repo1.maven.org/maven2/com/datadoghq/dd-java-agent/<version>/dd-java-agent-<version>.jar /opt/datadog/dd-java-agent.jar`
(`https://dtdg.co/latest-java-tracer` always serves the newest jar). Attach it with
`JAVA_TOOL_OPTIONS=-javaagent:/opt/datadog/dd-java-agent.jar` on the service.

## PHP

The tracer is a PHP extension, not a Composer package. Install it in the Dockerfile:

```dockerfile
RUN curl -LO https://github.com/DataDog/dd-trace-php/releases/download/<version>/datadog-setup.php \
    && php datadog-setup.php --php-bin=all \
    && rm datadog-setup.php
```

It instruments supported frameworks (Laravel, Symfony, WordPress and others) without code changes.

## .NET

Install the tracer package in the runtime image and enable the CLR profiler:

```dockerfile
ARG DD_TRACER_VERSION=<version>
RUN ARCH=$(dpkg --print-architecture) \
    && curl -Lo /tmp/datadog-dotnet-apm.deb \
       "https://github.com/DataDog/dd-trace-dotnet/releases/download/v${DD_TRACER_VERSION}/datadog-dotnet-apm_${DD_TRACER_VERSION}_${ARCH}.deb" \
    && dpkg -i /tmp/datadog-dotnet-apm.deb && rm /tmp/datadog-dotnet-apm.deb
ENV CORECLR_ENABLE_PROFILING=1 \
    CORECLR_PROFILER={846F5F1C-F9AE-4B07-969E-05C26BC060D8} \
    CORECLR_PROFILER_PATH=/opt/datadog/Datadog.Trace.ClrProfiler.Native.so \
    DD_DOTNET_TRACER_HOME=/opt/datadog
```

`dpkg --print-architecture` picks `amd64` or `arm64`. Alpine (musl) images cannot install the
`.deb`; they need the release's `-musl.tar.gz` asset.

## Ruby

Add `gem 'datadog', require: 'datadog/auto_instrument'` to the `Gemfile`, or add `gem 'datadog'` and
call `Datadog.configure { |c| c.tracing.instrument :rails }` (or `:sinatra`, `:rack`, `:grape`) in an
initializer. The gem builds native extensions, so a slim base image needs `build-essential` before
`bundle install`. Sinatra 4 also needs `rackup` and `puma` in the `Gemfile`.

## Go

Add the module and the contrib package for the framework, then run `go mod tidy` so `go.mod` and
`go.sum` are complete before the image build:

```bash
go get github.com/DataDog/dd-trace-go/v2/ddtrace/tracer
go get github.com/DataDog/dd-trace-go/contrib/net/http/v2   # or contrib/gin-gonic/gin/v2, ...
go mod tidy
```

Call `tracer.Start()` at startup and `defer tracer.Stop()`. Wrap the router with the contrib
package: `httptrace.NewServeMux()` for `net/http`, or `r.Use(gintrace.Middleware("<service>"))` for
Gin. `go mod tidy` can raise the `go` line in `go.mod`; match the builder image tag to it.

## C++

There is no automatic instrumentation; the app creates spans with the tracer API. Build
`dd-trace-cpp` from a pinned release tag in the Dockerfile with CMake 3.28 or later and a C++17
compiler (`ubuntu:24.04` ships a recent enough CMake), then link it and create spans as that tag's
README and `examples/` directory show. The API changes between releases, so follow the pinned tag.
