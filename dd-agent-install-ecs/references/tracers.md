# Tracers

APM needs two halves: the Agent side from the method file, and a tracer that loads inside the app's process. `DD_TRACE_AGENT_URL` alone produces no traces. Change the app's Dockerfile and dependency files; when the Dockerfile is not in this repository, report that the tracer must be added where the image is built. If the app already loads a Datadog tracer in code, keep it and change only what is missing.

## Contents

- Pin the version
- Python
- Node.js
- Java
- Ruby
- Go
- .NET
- PHP
- After the edit

## Pin the version

Always pin an exact version, looked up before you edit, so the next build cannot pick up a different tracer. Never guess a version or reuse one from memory, because versions change often. If a lookup fails twice, ask the user for the version.

```bash
# Python ddtrace: version and supported Python range
curl -fsS https://pypi.org/pypi/ddtrace/json | python3 -c 'import json,sys; i=json.load(sys.stdin)["info"]; print(i["version"], i["requires_python"])'
# Node.js dd-trace: one version per supported Node.js range
curl -fsS https://registry.npmjs.org/dd-trace | python3 -c 'import json,sys; d=json.load(sys.stdin); [print(t, v, d["versions"][v].get("engines")) for t,v in d["dist-tags"].items() if t.startswith("latest")]'
# Java agent
curl -fsS https://repo1.maven.org/maven2/com/datadoghq/dd-java-agent/maven-metadata.xml | sed -n 's:.*<release>\(.*\)</release>.*:\1:p'
# Ruby datadog gem
curl -fsS https://rubygems.org/api/v1/versions/datadog/latest.json
# Go dd-trace-go v2: the newest release whose go.mod fits GO_VERSION (see below)
GO_VERSION=1.24
curl -fsS 'https://proxy.golang.org/github.com/!data!dog/dd-trace-go/v2/@v/list' | python3 -c '
import re, sys, urllib.request
want = tuple(int(x) for x in sys.argv[1].split(".")[:2])
base = "https://proxy.golang.org/github.com/!data!dog/dd-trace-go/v2/@v/"
vs = sorted((v for v in sys.stdin.read().split() if "-" not in v), key=lambda v: tuple(map(int, v[1:].split("."))), reverse=True)
for v in vs:
    m = re.search(r"^go (\d+)\.(\d+)", urllib.request.urlopen(base + v + ".mod").read().decode(), re.M)
    if m and (int(m[1]), int(m[2])) <= want:
        print(v); break
' "$GO_VERSION"
# .NET tracer (drop the leading v)
curl -fsSI https://github.com/DataDog/dd-trace-dotnet/releases/latest | grep -io '/tag/[^[:space:]]*' | cut -d/ -f3
# PHP tracer
curl -fsSI https://github.com/DataDog/dd-trace-php/releases/latest | grep -io '/tag/[^[:space:]]*' | cut -d/ -f3
```

For Node.js, take the `latest…` entry whose `engines` range includes the Node.js version in the app's base image; the newest major can require a newer Node.js than the app runs. For Python, stop for that container and ask when the app's Python version is outside `requires_python`. For Go, set `GO_VERSION` to the Go version that builds the app: the tag of the `golang` build image (`golang:1.24` is `1.24`), or else the `go` line of `go.mod`. A newer dd-trace-go can require a newer Go, and the official `golang` images set `GOTOOLCHAIN=local`, so they fail instead of downloading it.

Start commands change in the Dockerfile `CMD` or `ENTRYPOINT`. When the task definition overrides the start command with `command`, change it there instead.

## Python

- Add `ddtrace==<VERSION>` to `requirements.txt`, or to `pyproject.toml` with the project's own pin syntax.
- Prefix the start command with `ddtrace-run`: `CMD ["ddtrace-run", "gunicorn", "app:app"]`, or `ddtrace-run python app.py`. Keep the rest of the command as it is.

## Node.js

- Add `"dd-trace": "<VERSION>"` to `dependencies` in `package.json`, with no `^` or `~`.
- When the start command runs `node` directly, add `--require dd-trace/init`: `CMD ["node", "--require", "dd-trace/init", "server.js"]`. For an ES module app, use `--import dd-trace/initialize.mjs` instead.
- When it runs a script such as `npm start`, set `ENV NODE_OPTIONS="--require dd-trace/init"` in the Dockerfile, appended to any existing `NODE_OPTIONS`.

## Java

Download the pinned agent into the final image stage and load it with `JAVA_TOOL_OPTIONS`:

```dockerfile
ADD --chmod=0644 https://repo1.maven.org/maven2/com/datadoghq/dd-java-agent/<VERSION>/dd-java-agent-<VERSION>.jar /app/dd-java-agent.jar
ENV JAVA_TOOL_OPTIONS="-javaagent:/app/dd-java-agent.jar"
```

- `--chmod` needs BuildKit. On a legacy builder, drop it and add `RUN chmod 0644 /app/dd-java-agent.jar` before any `USER` line. A file added from a URL is otherwise readable only by root.
- When `JAVA_TOOL_OPTIONS` is already set, append `-javaagent:/app/dd-java-agent.jar` to it, separated by a space.

## Ruby

Add the gem with automatic instrumentation:

```ruby
gem 'datadog', '<VERSION>', require: 'datadog/auto_instrument'
```

This loads the tracer in apps that call `Bundler.require`, such as Rails. Otherwise, use `gem 'datadog', '<VERSION>'` and add `require 'datadog/auto_instrument'` as the first `require` of the entry file, such as `config.ru`.

## Go

- Add `require github.com/DataDog/dd-trace-go/v2 v<VERSION>` to `go.mod`, with the version the Go-aware lookup printed. Use the v2 path, not `gopkg.in/DataDog/dd-trace-go.v1`.
- Start the tracer at the top of `main()`:

  ```go
  import "github.com/DataDog/dd-trace-go/v2/ddtrace/tracer"

  func main() {
      tracer.Start()
      defer tracer.Stop()
      // existing body
  }
  ```

- Go does not patch libraries automatically, so `tracer.Start()` alone creates no request spans. For a `net/http` server, add `require github.com/DataDog/dd-trace-go/contrib/net/http/v2 v<VERSION>` (the same version) and import it as `httptrace "github.com/DataDog/dd-trace-go/contrib/net/http/v2"`. Replace `http.NewServeMux()` with `httptrace.NewServeMux()`, or wrap another handler with `httptrace.WrapHandler(h, "<SERVICE>", "<resource>")`.
- For another router, such as gin, echo or chi, tell the user that `dd-trace-go` has a contrib package for it, and add it only if they ask.

## .NET

Install the pinned tracer package in the final image stage, and turn on the CLR profiler. On a glibc image (Debian or Ubuntu based), with `curl` installed:

```dockerfile
ARG TRACER_VERSION=<VERSION>
RUN curl -fsSL "https://github.com/DataDog/dd-trace-dotnet/releases/download/v${TRACER_VERSION}/datadog-dotnet-apm_${TRACER_VERSION}_amd64.deb" -o /tmp/datadog-dotnet-apm.deb \
 && dpkg -i /tmp/datadog-dotnet-apm.deb && rm /tmp/datadog-dotnet-apm.deb
ENV CORECLR_ENABLE_PROFILING=1 \
    CORECLR_PROFILER={846F5F1C-F9AE-4B07-969E-05C26BC060D8} \
    CORECLR_PROFILER_PATH=/opt/datadog/Datadog.Trace.ClrProfiler.Native.so \
    DD_DOTNET_TRACER_HOME=/opt/datadog
```

- For an ARM64 task (`cpuArchitecture` `ARM64`), use `_arm64.deb` in the file name.
- On an Alpine x64 image, download `datadog-dotnet-apm-${TRACER_VERSION}-musl.tar.gz`, extract it to `/opt/datadog`, and set `CORECLR_PROFILER_PATH=/opt/datadog/linux-musl-x64/Datadog.Trace.ClrProfiler.Native.so`. Stop and ask for Alpine on ARM64.
- The `/opt/datadog` paths belong to this package. The `Datadog.Trace.Bundle` NuGet package uses other paths, under the app's `datadog` folder; do not mix the two.

## PHP

Install the pinned extension in the final image stage. The installer needs `curl`, and on Alpine also `libgcc`:

```dockerfile
ARG DD_TRACE_PHP_VERSION=<VERSION>
RUN curl -fsSLO "https://github.com/DataDog/dd-trace-php/releases/download/${DD_TRACE_PHP_VERSION}/datadog-setup.php" \
 && php datadog-setup.php --php-bin=all && rm datadog-setup.php
```

Do not add `--enable-appsec` or `--enable-profiling` unless the user asks, because they turn on other products.

PHP-FPM clears the workers' environment by default (`clear_env`), so the workers would not see `DD_TRACE_AGENT_URL` or the tags. In the pool file the image uses, usually `www.conf`, pass them through for each variable you set:

```ini
env[DD_TRACE_AGENT_URL] = $DD_TRACE_AGENT_URL
env[DD_SERVICE] = $DD_SERVICE
env[DD_ENV] = $DD_ENV
env[DD_VERSION] = $DD_VERSION
```

For Apache with `mod_php`, add `PassEnv DD_TRACE_AGENT_URL DD_SERVICE DD_ENV DD_VERSION` to the server or VirtualHost configuration instead. Skip both when the pool file sets `clear_env = no`.

## After the edit

Lockfiles and `go.sum` must match the manifests before the next image build, and updating them downloads packages, which this skill does not do. Put the project's own command in the deploy steps, before the image build: `npm install`, `pip-compile` or `poetry lock`, `bundle install`, `go mod tidy`. Never edit a lockfile by hand: its hashes and resolved versions must come from the package manager.
