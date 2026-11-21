# Go

Open this in step 5 for a Go service outside AWS Lambda. The tracer is `dd-trace-go`, compiled into the binary. Supported frameworks: `net/http`, `gin`, `gorilla/mux`, `grpc`, `echo`, `chi`, `graphql-go`, `gqlgen`.

## Contents

- Choose the instrumentation path
- Orchestrion (APM off, no Bazel)
- APM already on
- Bazel with rules_go
- Framework integrations
- Toolchain version
- Runtime libraries with cgo off
- In a container image

## Choose the instrumentation path

APM is on only when the app starts the tracer (`tracer.Start`, or `NewTracerProvider` from `ddtrace/opentelemetry`), builds with Orchestrion, wraps a Lambda handler with `datadog-lambda-go`, or runs an image built outside this repository with Datadog tracer settings. Single Step Instrumentation cannot instrument Go, and `DD_` settings on an image built here do not put a tracer in the binary.

- Bazel with `rules_go`: manual instrumentation, below.
- APM already on: keep its instrumentation, below.
- Otherwise: Orchestrion.

## Orchestrion (APM off, no Bazel)

From the module directory that holds `go.mod`, in the source tree (never only in a Dockerfile or CI script):

```bash
go install github.com/DataDog/orchestrion@latest
orchestrion pin
```

Before you edit any build file, confirm all of these:

- `orchestrion.tool.go` exists, and `go.mod` requires `github.com/DataDog/orchestrion` and `github.com/DataDog/dd-trace-go`.
- `go.sum` has the content line for the pinned version, `github.com/DataDog/orchestrion v<version> h1:…`. A `v<version>/go.mod h1:…` line alone is not enough, and the image build fails without the content line.
- `orchestrion go build -tags appsec ./...` succeeds. On `missing go.sum entry`, re-run `orchestrion pin` and never hand-edit `go.sum`. On an error unrelated to Datadog, such as a package this machine cannot build, build the real entry point instead, such as `./cmd/<service>`, with the project's own tags.

Then change the project's existing build command from `go build` to `orchestrion go build -tags appsec`, and keep its package and `-o` output path, because `-o` with `./...` fails on a module with several packages, and a build without `-o` writes no binary where the start step expects it. A start or service script that builds the app and cannot find Orchestrion installs the pinned version and calls it by path:

```bash
ORCHESTRION_BIN="$(go env GOPATH | cut -d: -f1)/bin"
GOOS="$(go env GOHOSTOS)" GOARCH="$(go env GOHOSTARCH)" GOBIN="$ORCHESTRION_BIN" go install github.com/DataDog/orchestrion
"$ORCHESTRION_BIN/orchestrion" go build -tags appsec -o <existing output> <existing package>
```

`-tags appsec` is required with cgo off (`CGO_ENABLED=0`, or a builder without a C compiler such as `golang:*-alpine`), and harmless with cgo on, so keep it. With cgo off, it also changes what the runtime needs; see Runtime libraries with cgo off. A build that already runs `orchestrion go build` with cgo on stays unchanged.

## APM already on

Keep the existing instrumentation, whether manual `dd-trace-go`, Orchestrion, or a binary built elsewhere, and add no Orchestrion. With manual instrumentation, each recorded framework goes through its contrib integration; add one only for a framework that is not wrapped yet. Keep the build command and its output path, and add `appsec` to its existing `-tags` list (for example `-tags netgo,appsec`), or add `-tags appsec`. A second `-tags` flag replaces the first. With cgo off, check the runtime as in Runtime libraries with cgo off. When the binary is built outside this repository, tell the user to add `appsec` there.

## Bazel with rules_go

Orchestrion does not work with Bazel. Instrument manually:

1. `go get github.com/DataDog/dd-trace-go/v2/ddtrace/tracer`, then call `tracer.Start()` at start-up and `defer tracer.Stop()`.
2. Wrap each recorded framework with the integrations below.
3. Add these overrides where `go_deps` is configured (`MODULE.bazel`):

   ```bzl
   go_deps.gazelle_override(
       path = "github.com/DataDog/sketches-go",
       directives = ["gazelle:resolve go github.com/DataDog/sketches-go/ddsketch/pb/sketchpb //ddsketch/pb/sketchpb"],
   )
   go_deps.gazelle_override(
       path = "github.com/DataDog/dd-trace-go/v2",
       directives = ["gazelle:proto disable"],
   )
   ```

   For every `github.com/DataDog/datadog-agent/...` module in `go.mod`, add an override with `build_file_generation = "clean"` and `directives = ["gazelle:proto disable"]`, because their shipped `BUILD.bazel` files do not resolve outside that repository.
4. Make the `dd-trace-go` modules direct dependencies (`go mod tidy`), run `bazel mod tidy`, and confirm that `bazel build` succeeds for the service target.
5. In `BUILD.bazel` `deps`, reference each contrib package through its own repository: `@com_github_datadog_dd_trace_go_contrib_<module>_v2//:<target>`, where `<module>` is the part between `contrib/` and `/v2`, lowercased, with `/`, `.` and `-` replaced by `_`. Examples: `@com_github_datadog_dd_trace_go_contrib_go_chi_chi_v5_v2//:chi_v5`, `@com_github_datadog_dd_trace_go_contrib_net_http_v2//:http`. Follow the repository's existing target naming, such as `go_default_library`.

With `CGO_ENABLED=0`, check the runtime as in Runtime libraries with cgo off.

## Framework integrations

Use these for manual instrumentation. When the deployment sets `DD_SERVICE`, omit `WithService` options. Wrap one router per request path, never both a framework router and an outer `net/http` mux.

| Framework | `go get` | Wiring |
|---|---|---|
| `net/http` | `github.com/DataDog/dd-trace-go/contrib/net/http/v2` | `mux := httptrace.NewServeMux()`; change functions that take `*http.ServeMux` to `*httptrace.ServeMux` |
| `gin` | `github.com/DataDog/dd-trace-go/contrib/gin-gonic/gin/v2` | `r.Use(gintrace.Middleware(""))` |
| `gorilla/mux` | `github.com/DataDog/dd-trace-go/contrib/gorilla/mux/v2` | `r := muxtrace.NewRouter()` |
| `grpc` | `github.com/DataDog/dd-trace-go/contrib/google.golang.org/grpc/v2` | `grpc.NewServer(grpc.StreamInterceptor(grpctrace.StreamServerInterceptor()), grpc.UnaryInterceptor(grpctrace.UnaryServerInterceptor()))` |
| `echo` (v4) | `github.com/DataDog/dd-trace-go/contrib/labstack/echo.v4/v2` | `e.Use(echotrace.Middleware())` |
| `chi` | `.../contrib/go-chi/chi/v2` (chi v4) or `.../contrib/go-chi/chi.v5/v2` (chi v5) | `router.Use(chitrace.Middleware())` |
| `graphql-go` | `github.com/DataDog/dd-trace-go/contrib/graphql-go/graphql/v2` | `schema, err := ddgraphql.NewSchema(graphql.SchemaConfig{...})` |
| `gqlgen` | `github.com/DataDog/dd-trace-go/contrib/99designs/gqlgen/v2` | `srv.Use(gqlgentrace.NewTracer(gqlgentrace.WithService(os.Getenv("DD_SERVICE"))))`, because its default service ignores `DD_SERVICE` |

With tracer v1, the contrib path is `gopkg.in/DataDog/dd-trace-go.v1/contrib/<framework path>`, for example `.../contrib/labstack/echo.v4`.

## Toolchain version

Adding Datadog modules can raise the `go` version that `go.mod` requires. Make the toolchain that builds the deployed binary at least that version, and never downgrade one that already meets it:

- Dockerfile, CI job or service script: raise the Go version. Official `golang` images set `GOTOOLCHAIN=local`, so raise the image tag in the same repository.
- Bazel: raise `go_sdk.download(version = ...)` or `go_register_toolchains(version = ...)`. `go_sdk.from_file(go_mod = ...)` already follows `go.mod`.
- When the toolchain cannot change, run `go get go@<toolchain version>` together with explicit releases of every Datadog module you added, each with a minimum that fits (`go list -m -json <module>@<version>` shows `GoVersion`). Then `go list -m all | grep -i datadog` must show no `-dev` or `-rc` version. Never run `go get go@<version>` alone, because it can select pre-release Datadog modules.

## Runtime libraries with cgo off

With `CGO_ENABLED=0` and `-tags appsec`, the WAF library loads its C library at run time, so the binary becomes dynamically linked against glibc (`/lib64/ld-linux-x86-64.so.2`) and needs `libc.so.6`, `libpthread.so.0` and `libdl.so.2`. It no longer starts on `scratch`, `gcr.io/distroless/static*` or Alpine, even with `gcompat`: the container exits with `no such file or directory`. Check the runtime stage or host. When it lacks glibc, change the runtime base to a glibc image such as `gcr.io/distroless/base-debian12` or `debian:12-slim`, or, when the user wants to keep the base, stop and tell them why.

## In a container image

- Orchestrion path: run `orchestrion pin` in the source tree first. In the Dockerfile, copy the files it changed (`go.mod`, `go.sum`, `orchestrion.tool.go`) before `go mod download`, then install the pinned version without a version suffix, because `@latest` ignores `go.mod`:

  ```dockerfile
  RUN go install github.com/DataDog/orchestrion
  RUN orchestrion go build -tags appsec -o <existing output> <existing package>
  ```

- APM already on: apply the `appsec` tag to the Dockerfile's existing build command, and add no Orchestrion.
- Bazel: keep Bazel as the build path in the image, for example `RUN bazel build //path/to:target`, and copy the binary from `bazel-bin/`.
- On Kubernetes, build an image this way and use it as the Deployment's container image.
