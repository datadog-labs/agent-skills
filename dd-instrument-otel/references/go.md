# Go

## Contents

- Modules
- Bootstrap
- Wire it into main
- Logs

Go has no automatic instrumentation, so the bootstrap goes in code and HTTP handlers are wrapped.

## Modules

- Traces and metrics: `go.opentelemetry.io/otel`, `go.opentelemetry.io/otel/sdk`, `go.opentelemetry.io/otel/sdk/metric`, `go.opentelemetry.io/otel/exporters/otlp/otlptrace/otlptracehttp` and `go.opentelemetry.io/otel/exporters/otlp/otlpmetric/otlpmetrichttp`.
- HTTP server spans: `go.opentelemetry.io/contrib/instrumentation/net/http/otelhttp`, or the router's own contrib package, such as `.../github.com/gin-gonic/gin/otelgin`.
- Logs, only when step 4 of `SKILL.md` included them: `go.opentelemetry.io/otel/sdk/log`, `go.opentelemetry.io/otel/log`, `go.opentelemetry.io/otel/exporters/otlp/otlplog/otlploghttp` and, for `log/slog`, `go.opentelemetry.io/contrib/bridges/otelslog`.

Each OTel module release, `contrib` modules and bridges included, requires a minimum Go version, and `go get` raises the `go` line in `go.mod` to match. For example, `otelslog` v0.21 needs Go 1.26, and v0.20 needs Go 1.25. After `go get`, compare that line with its old value. When it rose, revert and pick older releases whose `.mod` files (`https://proxy.golang.org/<module>/@v/<version>.mod`) allow the project's Go version, or ask the user before upgrading Go. Check the build image too: the official `golang` images set `GOTOOLCHAIN=local`, so a `FROM golang:<version>` older than the `go` line fails to build instead of downloading a newer toolchain. Keep all `go.opentelemetry.io/otel*` modules on the same release, and `contrib` on the release that requires it.

## Bootstrap

Create `otel.go` in package `main`. Replace `<DD_SITE>`, `<service-name>`, `<env>` (the environment name the project uses, or `local`) and `<version>` (the release version).

```go
package main

import (
	"context"
	"errors"
	"fmt"
	"os"
	"strings"

	"go.opentelemetry.io/otel"
	"go.opentelemetry.io/otel/attribute"
	"go.opentelemetry.io/otel/exporters/otlp/otlpmetric/otlpmetrichttp"
	"go.opentelemetry.io/otel/exporters/otlp/otlptrace/otlptracehttp"
	"go.opentelemetry.io/otel/propagation"
	sdkmetric "go.opentelemetry.io/otel/sdk/metric"
	"go.opentelemetry.io/otel/sdk/metric/metricdata"
	"go.opentelemetry.io/otel/sdk/resource"
	sdktrace "go.opentelemetry.io/otel/sdk/trace"
)

const otlpBase = "https://otlp.<DD_SITE>"

// deltaSelector makes counters and histograms delta, as Datadog's intake requires.
func deltaSelector(kind sdkmetric.InstrumentKind) metricdata.Temporality {
	switch kind {
	case sdkmetric.InstrumentKindUpDownCounter, sdkmetric.InstrumentKindObservableUpDownCounter:
		return metricdata.CumulativeTemporality
	default:
		return metricdata.DeltaTemporality
	}
}

// setupOTel starts export to Datadog. Without a key or an Agent endpoint it starts nothing.
func setupOTel(ctx context.Context) (func(context.Context) error, error) {
	noop := func(context.Context) error { return nil }
	// OTEL_EXPORTER_OTLP_ENDPOINT is set only on the route through a Datadog Agent.
	agent := strings.TrimSuffix(os.Getenv("OTEL_EXPORTER_OTLP_ENDPOINT"), "/")
	apiKey := os.Getenv("DD_API_KEY")
	if agent == "" && apiKey == "" {
		return noop, nil
	}
	base, headers, traceHeaders := agent, map[string]string{}, map[string]string{}
	if agent == "" {
		base = otlpBase
		headers = map[string]string{"dd-api-key": apiKey}
		traceHeaders = map[string]string{"dd-api-key": apiKey, "compute_stats": "true"}
	}
	// Report export errors on stderr even after slog.SetDefault redirects the log package.
	otel.SetErrorHandler(otel.ErrorHandlerFunc(func(err error) {
		fmt.Fprintln(os.Stderr, "opentelemetry:", err)
	}))
	res, err := resource.New(ctx,
		resource.WithAttributes( // defaults; OTEL_SERVICE_NAME and OTEL_RESOURCE_ATTRIBUTES override them
			attribute.String("service.name", "<service-name>"),
			attribute.String("deployment.environment.name", "<env>"),
			attribute.String("service.version", "<version>"),
		),
		resource.WithHost(),
		resource.WithContainer(),
		resource.WithProcessPID(),
		resource.WithFromEnv(),
		resource.WithTelemetrySDK(),
	)
	if err != nil {
		return noop, err
	}
	traceExp, err := otlptracehttp.New(ctx,
		otlptracehttp.WithEndpointURL(base+"/v1/traces"),
		otlptracehttp.WithHeaders(traceHeaders),
	)
	if err != nil {
		return noop, err
	}
	metricExp, err := otlpmetrichttp.New(ctx,
		otlpmetrichttp.WithEndpointURL(base+"/v1/metrics"),
		otlpmetrichttp.WithHeaders(headers),
		otlpmetrichttp.WithTemporalitySelector(deltaSelector),
	)
	if err != nil {
		return noop, err
	}
	tp := sdktrace.NewTracerProvider(sdktrace.WithBatcher(traceExp), sdktrace.WithResource(res))
	mp := sdkmetric.NewMeterProvider(
		sdkmetric.WithReader(sdkmetric.NewPeriodicReader(metricExp)),
		sdkmetric.WithResource(res),
	)
	otel.SetTracerProvider(tp)
	otel.SetMeterProvider(mp)
	otel.SetTextMapPropagator(propagation.NewCompositeTextMapPropagator(propagation.TraceContext{}, propagation.Baggage{}))
	return func(ctx context.Context) error {
		return errors.Join(tp.Shutdown(ctx), mp.Shutdown(ctx))
	}, nil
}
```

`WithEndpointURL` takes the full URL, path included, and an `http://` Agent URL turns off TLS. The periodic reader reads `OTEL_METRIC_EXPORT_INTERVAL`, which the smoke test lowers. `WithHost` and `WithContainer` add the host and container identity Datadog uses to tell replicas apart. `WithProcess` is left out because it also sends the command-line arguments, which can hold secrets.

## Wire it into main

Call `setupOTel` first in `main`, and run the returned shutdown when the server stops, so the last batch is flushed. When `main` already handles `SIGTERM` or uses `signal.NotifyContext`, call the shutdown there; a plain `defer` runs only when `main` returns. Log a setup error and keep serving, because telemetry must not stop the app.

Wrap the root handler where the server is built, for example `http.ListenAndServe(addr, otelhttp.NewHandler(mux, "server"))`, or add the router's contrib middleware.

## Logs

Only when step 4 of `SKILL.md` included logs, and only for an app that logs with `log/slog`. Import `go.opentelemetry.io/otel/exporters/otlp/otlplog/otlploghttp`, `go.opentelemetry.io/otel/log/global` and `sdklog "go.opentelemetry.io/otel/sdk/log"`, then add this inside `setupOTel`, after the resource:

```go
logExp, err := otlploghttp.New(ctx,
	otlploghttp.WithEndpointURL(base+"/v1/logs"),
	otlploghttp.WithHeaders(headers),
)
if err != nil {
	return noop, err
}
lp := sdklog.NewLoggerProvider(sdklog.WithProcessor(sdklog.NewBatchProcessor(logExp)), sdklog.WithResource(res))
global.SetLoggerProvider(lp)
```

Add `lp.Shutdown(ctx)` to the returned shutdown. Then send slog records to `otelslog.NewHandler("<service-name>")`. That handler replaces console output, so when the app also needs its existing console handler, fan records out to both handlers rather than dropping one. Keep the bootstrap's `otel.SetErrorHandler` call: once `slog.SetDefault` takes over the `log` package, OTel's default error handler would send export errors into the OTLP pipeline, and Verify would find none on stderr. For other logging libraries, record logs as not configured.
