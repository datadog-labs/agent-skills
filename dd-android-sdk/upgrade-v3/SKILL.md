---
name: dd-android-sdk-v3-upgrade
description: Upgrade an Android application from Datadog's v2 mobile RUM SDK to the latest stable v3 SDK, including Core, RUM, Logs, Trace, OkHttp, and Session Replay breaking changes.
metadata:
  version: "1.0.0"
  author: datadog-labs
  repository: https://github.com/datadog-labs/agent-skills
  tags: datadog,android-sdk,rum,logs,trace,session-replay,migration,v3,upgrade
---

# Upgrade Datadog RUM Android SDK from v2 to v3

Use this skill when an Android project uses Datadog Mobile SDK 2.x and must be
upgraded to the latest stable 3.x release. The v3 migration removes legacy OpenTracing
support and updates APIs across the Android SDK modules.

This skill incorporates the Android v2-to-v3 migration guidance from
[`DataDog/documentation`][documentation-source], reviewed at commit
`a27f7562165b9058f8cd597b48473cc7ecbeda81` on 2026-09-25. Use the instructions
below as the migration source of truth; consult the live documentation only when
the project contains an API not covered here or the requested target is newer
than this reviewed revision.

## 1. Establish the migration scope

Before editing, inspect the entire Android project for:

- every `com.datadoghq:dd-sdk-android-*` dependency, including dependencies in
  version catalogs, convention plugins, and included builds;
- every `com.datadog.android` and `io.opentracing` import;
- calls that initialize Datadog or enable RUM, Logs, Trace, Crash Reporting,
  Session Replay, WebView tracking, or NDK crash reporting;
- custom endpoints passed to `useCustomEndpoint`;
- manual RUM resource calls and custom tracing;
- `DatadogInterceptor` and `TracingInterceptor` construction;
- the project's `minSdk` and Kotlin version.

Do not remove a Datadog feature, instrumentation path, or test merely to make
the upgrade compile. Preserve the application's existing telemetry behavior.

## 2. Satisfy v3 platform requirements

- Set the Android minimum API level to at least 23. If the application cannot
  raise `minSdk` to 23, stop and report that it cannot adopt SDK v3.
- Use Kotlin 1.9 or newer.
- If the project uses Jetpack Compose with Kotlin below 2.0, update Kotlin and
  `composeOptions.kotlinCompilerExtensionVersion` together using the official
  [Compose-to-Kotlin compatibility map][compose-kotlin-map]. Do not suppress
  the compatibility check.
- For Kotlin 2.0 or newer, use the Compose Compiler Gradle plugin as directed
  by the compatibility documentation instead of choosing a version from the
  legacy compatibility table.
- Remove direct OpenTracing dependencies after migrating all OpenTracing code.
- If the preferred OpenTelemetry trace migration is used with `minSdk` below
  26, enable core-library desugaring as required by OpenTelemetry Java.

## 3. Upgrade the SDK dependencies

Before editing dependencies, query the official
[`DataDog/dd-sdk-android` GitHub releases][github-releases] and select the
highest stable semantic version matching `3.x.y`. Ignore drafts, prereleases,
release candidates, `-SNAPSHOT` versions, and releases from other major
versions. The GitHub Releases API can be queried at:

```text
https://api.github.com/repos/DataDog/dd-sdk-android/releases?per_page=100
```

Use each release object's `tag_name`, `draft`, and `prerelease` fields rather
than assuming the first tag or the repository's development changelog is a
stable release. If GitHub cannot be reached or the response cannot be parsed,
use `3.14.1` as the fallback version and report that fallback explicitly.

Change every Datadog Android SDK artifact from 2.x to the selected concrete
3.x version. Follow the project's existing version-management convention and
update the lockfile or version catalog where applicable.

Do not introduce moving versions such as `3.+` or `latest.release`, and do not
leave a mixture of v2 and v3 Datadog artifacts in the resolved dependency graph.

If the application uses Jetpack Compose and enables Compose instrumentation in
the Datadog Gradle plugin, add the v3 Compose integration at the same SDK
version:

```kotlin
implementation("com.datadoghq:dd-sdk-android-compose:<datadog-v3-version>")
```

Leaving Compose instrumentation enabled without this artifact causes the
Datadog compiler plugin to fail compilation with a missing-dependency error.

## 4. Migrate Core

The user ID passed to `Datadog.setUserInfo` is mandatory in v3. Replace `null`
with the application's stable user identifier:

```kotlin
// v2
Datadog.setUserInfo(null, "Jane Smith", "jane@example.com")

// v3
Datadog.setUserInfo("user123", "Jane Smith", "jane@example.com")
```

Do not invent a production identity value. Reuse the application's actual user
identifier or preserve its existing anonymous-user strategy with an appropriate
non-null identifier.

## 5. Migrate RUM

### Custom endpoint

`RumConfiguration.Builder.useCustomEndpoint` must receive the complete RUM
upload URL rather than only a host:

```kotlin
Rum.enable(
    RumConfiguration.Builder("<application-id>")
        .useCustomEndpoint("https://example.com/rum/upload")
        .build()
)
```

Preserve the project's custom host and path.

### Manual resources

Replace the string HTTP method argument of `startResource` with
`RumResourceMethod`:

```kotlin
import com.datadog.android.rum.RumResourceMethod

GlobalRumMonitor.get().startResource(
    key = "resource-key",
    method = RumResourceMethod.GET,
    url = "https://example.com/api/resource",
    attributes = mapOf("source" to "manual")
)
```

Use the enum member corresponding to the original method; do not default every
resource to `GET`.

### Global monitor rename

Update the global RUM symbol:

| v2 | v3 |
|---|---|
| `com.datadog.android.rum.GlobalRum` | `com.datadog.android.rum.GlobalRumMonitor` |

Use `GlobalRumMonitor.get()` to access the registered monitor. Do not replace it
with `RumMonitor.get()`; `RumMonitor` does not provide that v3 accessor.

### Optional attributes

In v3, the `attributes: Map<String, Any?>` parameter is optional for these APIs:

- `RumMonitor.addAction`
- `RumMonitor.startAction`
- `RumMonitor.stopResource`
- `RumMonitor.addError`
- `RumMonitor.addErrorWithStacktrace`
- `AdvancedNetworkRumMonitor.stopResource`

Existing attribute maps remain valid. Remove redundant empty maps only when it
improves the migrated code without changing behavior.

## 6. Migrate Logs and crash Error Tracking

Logs no longer reports fatal errors on its own. If the existing application
expects crashes to appear in Error Tracking, keep RUM enabled and enable Crash
Reporting explicitly in addition to Logs.

`LogsConfiguration.Builder.useCustomEndpoint` must receive the complete Logs
upload URL:

```kotlin
Logs.enable(
    LogsConfiguration.Builder()
        .useCustomEndpoint("https://example.com/logs/upload")
        .build()
)
```

Preserve all products that were enabled before the migration. In particular,
do not remove `Rum.enable(...)` or `Logs.enable(...)` to resolve compilation
failures.

## 7. Migrate Trace

`TraceConfiguration.Builder.useCustomEndpoint` must receive the complete Trace
upload URL:

```kotlin
Trace.enable(
    TraceConfiguration.Builder()
        .useCustomEndpoint("https://example.com/trace/upload")
        .build()
)
```

SDK v3 removes OpenTracing. Choose one of the following migrations based on the
project's constraints.

### Preferred: migrate to OpenTelemetry

Add the v3 OpenTelemetry bridge using the same SDK version as the other Datadog
artifacts:

```kotlin
implementation("com.datadoghq:dd-sdk-android-trace-otel:<datadog-v3-version>")
```

For the simple case, replace global tracer registration:

```kotlin
// v2 OpenTracing
GlobalTracer.registerIfAbsent(
    AndroidTracer.Builder()
        .setService(BuildConfig.APPLICATION_ID)
        .build()
)

// v3 OpenTelemetry
GlobalOpenTelemetry.set(
    DatadogOpenTelemetry(BuildConfig.APPLICATION_ID)
)
```

`DatadogOpenTelemetry` is a convenience wrapper. Its constructor accepts only
the service name; do not pass an `SdkCore` to it. When the v2 tracer has custom
builder configuration, such as an SDK instance or partial-flush threshold,
build the provider directly instead:

```kotlin
val tracerProvider = OtelTracerProvider.Builder(sdkCore)
    .setService(BuildConfig.APPLICATION_ID)
    .setPartialFlushThreshold(4)
    .build()

GlobalOpenTelemetry.set(object : OpenTelemetry {
    override fun getTracerProvider(): TracerProvider = tracerProvider

    override fun getPropagators(): ContextPropagators = ContextPropagators.noop()
})

val tracer = tracerProvider.get("SampleApplicationTracer")
```

`OtelTracerProvider.Builder(...).build()` returns a `TracerProvider`, not an
`OpenTelemetry` instance. Do not pass the provider directly to
`GlobalOpenTelemetry.set(...)`; wrap it as shown above.

`setPartialFlushThreshold` belongs to `OtelTracerProvider.Builder`, not to the
built `OtelTracerProvider`. Configure it before calling `build()`; do not cast
`openTelemetry.tracerProvider` and attempt to mutate it afterward.

Replace manual tracing with the OpenTelemetry API:

```kotlin
val tracer: Tracer = GlobalOpenTelemetry
    .get()
    .getTracer("SampleApplicationTracer")

val span = tracer
    .spanBuilder("Executing operation")
    .startSpan()

// Instrumented operation

span.end()
```

Apply these type and API mappings:

| v2 OpenTracing | v3 OpenTelemetry |
|---|---|
| `io.opentracing.util.GlobalTracer` | `io.opentelemetry.api.GlobalOpenTelemetry` |
| `com.datadog.android.trace.AndroidTracer` | `io.opentelemetry.api.trace.Tracer` |
| `io.opentracing.Span` | `io.opentelemetry.api.trace.Span` |
| `io.opentracing.Scope` | `io.opentelemetry.context.Scope` |
| `io.opentracing.SpanContext` | `io.opentelemetry.api.trace.SpanContext` |
| `AndroidTracer.Builder(...).setPartialFlushThreshold(Int)` | `OtelTracerProvider.Builder(...).setPartialFlushThreshold(Int)` |
| `SpanContext.toTraceId()` | `SpanContext.traceId` (Kotlin) / `SpanContext.getTraceId()` (Java) |
| `Span.setError()` | `Span.recordException()` |

### Transitional fallback: migrate to the Datadog tracing API

Use this path only when the project cannot enable the desugaring required by
OpenTelemetry. It exists to ease migration and may be removed in a future major
SDK version.

Replace global tracer registration:

```kotlin
// v2 OpenTracing
GlobalTracer.registerIfAbsent(
    AndroidTracer.Builder()
        .setService(BuildConfig.APPLICATION_ID)
        .build()
)

// v3 Datadog tracing API
GlobalDatadogTracer.registerIfAbsent(
    DatadogTracing.newTracerBuilder(sdkCore)
        .withServiceName(BuildConfig.APPLICATION_ID)
        .withPartialFlushMinSpans(4)
        .build()
)
```

Replace manual tracing:

```kotlin
val tracer = GlobalDatadogTracer.get()

val span = tracer
    .buildSpan("Executing operation")
    .start()

val scope = tracer.activateSpan(span)
try {
    // Instrumented operation
    val traceId = span.context().traceId.toString()
} finally {
    scope?.close()
    span.finish()
}
```

`DatadogTracer.activateSpan()` returns a nullable `DatadogScope`; close it with
`scope?.close()`.

`DatadogSpan.context()` is a function, not a Kotlin property. Call it with
parentheses: use `span.context().traceId`, not `span.context.traceId`.

Apply these type and API mappings:

| v2 OpenTracing | v3 Datadog tracing API |
|---|---|
| `io.opentracing.util.GlobalTracer` | `com.datadog.android.trace.GlobalDatadogTracer` |
| `com.datadog.android.trace.AndroidTracer` | `com.datadog.android.trace.api.tracer.DatadogTracer` |
| `io.opentracing.Span` | `com.datadog.android.trace.api.span.DatadogSpan` |
| `io.opentracing.Scope` | `com.datadog.android.trace.api.scope.DatadogScope` |
| `io.opentracing.SpanContext` | `com.datadog.android.trace.api.span.DatadogSpanContext` |
| `AndroidTracer.Builder().build()` | `DatadogTracing.newTracerBuilder().build()` |
| `AndroidTracer.Builder(...).setService(String)` | `DatadogTracerBuilder.withServiceName(String)` |
| `AndroidTracer.Builder(...).setPartialFlushThreshold(Int)` | `DatadogTracerBuilder.withPartialFlushMinSpans(Int)` |
| `SpanContext.toTraceId()` | `DatadogSpanContext.traceId.toString()` |
| `Span.setError()` | `DatadogSpan.addThrowable()` |

After either path, verify that no source or dependency still relies on
`io.opentracing`.

## 8. Migrate OkHttp instrumentation

The `com.datadoghq:dd-sdk-android-okhttp` artifact itself does not require
desugaring. Replace the removed positional constructors with builders while
preserving service names, traced hosts and header types, listeners, resource
attribute providers, and samplers.

| Removed v2 constructor family | v3 replacement |
|---|---|
| `TracingInterceptor(...)` | `TracingInterceptor.Builder(tracedHosts)` |
| `DatadogInterceptor(...)` | `DatadogInterceptor.Builder(tracedHosts)` |

Search every overload, including constructors that accept any combination of:

- a nullable service name;
- `List<String>` traced hosts;
- `Map<String, Set<TracingHeaderType>>` traced-host configuration;
- `TracedRequestListener`;
- `RumResourceAttributesProvider`;
- `Sampler<Span>`.

The builder requires either `List<String>` or
`Map<String, Set<TracingHeaderType>>` in its constructor. There is no
zero-argument builder and no `setTracedHostsWithHeaderType` method. Do not drop
constructor arguments during the conversion; map each behavior to the
corresponding builder option:

```kotlin
val datadogInterceptor = DatadogInterceptor.Builder(tracedHostsWithHeaderType)
    .setSdkInstanceName(sdkCore.name)
    .setTracedRequestListener(tracedRequestListener)
    .setRumResourceAttributesProvider(resourceAttributesProvider)
    .setTraceSampler(DeterministicTraceSampler(20f))
    .build()

val tracingInterceptor = TracingInterceptor.Builder(tracedHostsWithHeaderType)
    .setSdkInstanceName(sdkCore.name)
    .setTracedRequestListener(tracedRequestListener)
    .setTraceSampler(DeterministicTraceSampler(20f))
    .build()
```

In v3, `TracedRequestListener` receives a Datadog span, even when the
application's manual tracing uses OpenTelemetry:

```kotlin
val tracedRequestListener = object : TracedRequestListener {
    override fun onRequestIntercepted(
        request: Request,
        span: DatadogSpan,
        response: Response?,
        throwable: Throwable?
    ) {
        span.setTag("request.method", request.method)
    }
}
```

Update custom samplers from `Sampler<io.opentracing.Span>` to
`Sampler<DatadogSpan>`.

## 9. Migrate Session Replay

`SessionReplayConfiguration.Builder.useCustomEndpoint` must receive the full
Session Replay upload URL:

```kotlin
SessionReplay.enable(
    SessionReplayConfiguration.Builder(...)
        .useCustomEndpoint("https://example.com/session_replay/upload")
        .build()
)
```

Preserve the application's existing Session Replay privacy and sampling
configuration.

## 10. Verify the result

Run the project's normal Gradle compilation task. Prefer the same compilation
command used by the project's CI.

Before finishing, confirm:

- all resolved `com.datadoghq:dd-sdk-android-*` artifacts are on one stable 3.x
  version;
- `minSdk` is at least 23 and Kotlin is at least 1.9;
- no `io.opentracing` dependency or import remains;
- every user ID is non-null;
- manual RUM resources use `RumResourceMethod`;
- global RUM access uses `GlobalRumMonitor.get()`;
- every custom endpoint is a full product-specific upload URL;
- every previously enabled Datadog product remains enabled;
- migrated OkHttp interceptors preserve their previous configuration;
- Gradle compilation exits successfully.

Fix only migration-related failures. Do not rewrite unrelated application code
or remove instrumentation to obtain a successful compilation.

Report the original and resulting SDK versions, the migration path chosen for
tracing, the files changed, and the exact Gradle compilation command that passed.

[documentation-source]: https://github.com/DataDog/documentation/blob/a27f7562165b9058f8cd597b48473cc7ecbeda81/hugo/content/en/real_user_monitoring/guide/mobile-sdk-upgrade.md#from-v2-to-v3
[github-releases]: https://github.com/DataDog/dd-sdk-android/releases
[compose-kotlin-map]: https://developer.android.com/jetpack/androidx/releases/compose-kotlin
