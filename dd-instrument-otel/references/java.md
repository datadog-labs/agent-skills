# Java

## Contents

- Choose the path
- Java agent
- SDK in code
- Spring Boot starter

Java traces, metrics and logs are all Stable, so no opt-in question applies to Java.

## Choose the path

- **Java agent** (preferred): it instruments common frameworks, HTTP clients and logging libraries with no code change, and reads its settings, the key header included, from environment variables as literal text. Use it when the service starts from a layer that expands `DD_API_KEY` at run time: Compose, Kubernetes, Helm or a shell script.
- **Started from a Dockerfile alone or a systemd unit:** those layers cannot put the key into a header variable at run time (see [deployment.md](deployment.md)), so read `DD_API_KEY` inside the process: the SDK in code, or the Spring Boot starter with its customizer. When neither fits, use the agent and leave the user the `docker run` step from [deployment.md](deployment.md).
- **SDK in code:** use it for those starts, when the JVM cannot take a `-javaagent` flag, or when the app already builds its own `OpenTelemetrySdk`. It produces only the spans the app or its instrumentation libraries create.
- **An app that already uses the OpenTelemetry Spring Boot starter** keeps it; see [Spring Boot starter](#spring-boot-starter).

## Java agent

1. **Fetch the jar at build time, pinned.** Find the newest version in `https://repo1.maven.org/maven2/io/opentelemetry/javaagent/opentelemetry-javaagent/maven-metadata.xml` (the `<release>` element). Do not commit the jar, because the build can fetch this large binary itself. In a Dockerfile:

   ```dockerfile
   ARG OTEL_JAVAAGENT_VERSION=<version>
   ADD https://repo1.maven.org/maven2/io/opentelemetry/javaagent/opentelemetry-javaagent/${OTEL_JAVAAGENT_VERSION}/opentelemetry-javaagent-${OTEL_JAVAAGENT_VERSION}.jar /otel/opentelemetry-javaagent.jar
   ENV JAVA_TOOL_OPTIONS="-javaagent:/otel/opentelemetry-javaagent.jar"
   ```

   Without a Dockerfile, copy the same artifact (`io.opentelemetry.javaagent:opentelemetry-javaagent`) into the build output with the build tool, for example Maven's `dependency:copy` goal or a Gradle `Copy` task.
2. **Attach it without changing the start command.** Set `JAVA_TOOL_OPTIONS=-javaagent:<path to jar>` in the start surface. When `JAVA_TOOL_OPTIONS` already has a value, append to it.
3. **Configure it with the env-configured block** from [deployment.md](deployment.md), including the logs variables. The agent exports traces, metrics and logs over OTLP by default, and reads `OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE=delta`.

The agent needs Java 8 or later.

## SDK in code

Import the BOM `io.opentelemetry:opentelemetry-bom` and add `io.opentelemetry:opentelemetry-sdk` and `io.opentelemetry:opentelemetry-exporter-otlp`, which holds the HTTP exporters. Build the SDK once at startup, before the app serves requests. Replace `<DD_SITE>`, `<service-name>`, `<env>` and `<version>`.

```java
import io.opentelemetry.api.baggage.propagation.W3CBaggagePropagator;
import io.opentelemetry.api.common.AttributeKey;
import io.opentelemetry.api.common.Attributes;
import io.opentelemetry.api.trace.propagation.W3CTraceContextPropagator;
import io.opentelemetry.context.propagation.ContextPropagators;
import io.opentelemetry.context.propagation.TextMapPropagator;
import io.opentelemetry.exporter.otlp.http.logs.OtlpHttpLogRecordExporter;
import io.opentelemetry.exporter.otlp.http.logs.OtlpHttpLogRecordExporterBuilder;
import io.opentelemetry.exporter.otlp.http.metrics.OtlpHttpMetricExporter;
import io.opentelemetry.exporter.otlp.http.metrics.OtlpHttpMetricExporterBuilder;
import io.opentelemetry.exporter.otlp.http.trace.OtlpHttpSpanExporter;
import io.opentelemetry.exporter.otlp.http.trace.OtlpHttpSpanExporterBuilder;
import io.opentelemetry.sdk.OpenTelemetrySdk;
import io.opentelemetry.sdk.logs.SdkLoggerProvider;
import io.opentelemetry.sdk.logs.export.BatchLogRecordProcessor;
import io.opentelemetry.sdk.metrics.SdkMeterProvider;
import io.opentelemetry.sdk.metrics.export.AggregationTemporalitySelector;
import io.opentelemetry.sdk.metrics.export.PeriodicMetricReader;
import io.opentelemetry.sdk.resources.Resource;
import io.opentelemetry.sdk.trace.SdkTracerProvider;
import io.opentelemetry.sdk.trace.export.BatchSpanProcessor;

public final class OtelSetup {
  private static final String OTLP = "https://otlp.<DD_SITE>";

  /** Builds and registers the SDK. Without a key or an Agent endpoint it does nothing. */
  public static void init() {
    // OTEL_EXPORTER_OTLP_ENDPOINT is set only on the route through a Datadog Agent.
    String agent = System.getenv("OTEL_EXPORTER_OTLP_ENDPOINT");
    String apiKey = System.getenv("DD_API_KEY");
    boolean viaAgent = agent != null && !agent.isEmpty();
    if (!viaAgent && (apiKey == null || apiKey.isEmpty())) {
      return;
    }
    String base = viaAgent ? agent.replaceAll("/$", "") : OTLP;
    Resource resource = Resource.getDefault().merge(Resource.create(Attributes.of(
        AttributeKey.stringKey("service.name"),
        System.getenv().getOrDefault("OTEL_SERVICE_NAME", "<service-name>"),
        AttributeKey.stringKey("deployment.environment.name"), "<env>",
        AttributeKey.stringKey("service.version"), "<version>")));

    OtlpHttpSpanExporterBuilder spans = OtlpHttpSpanExporter.builder().setEndpoint(base + "/v1/traces");
    OtlpHttpMetricExporterBuilder metrics = OtlpHttpMetricExporter.builder()
        .setEndpoint(base + "/v1/metrics")
        .setAggregationTemporalitySelector(AggregationTemporalitySelector.deltaPreferred());
    OtlpHttpLogRecordExporterBuilder logs = OtlpHttpLogRecordExporter.builder().setEndpoint(base + "/v1/logs");
    if (!viaAgent) {
      spans.addHeader("dd-api-key", apiKey).addHeader("compute_stats", "true");
      metrics.addHeader("dd-api-key", apiKey);
      logs.addHeader("dd-api-key", apiKey);
    }

    OpenTelemetrySdk sdk = OpenTelemetrySdk.builder()
        .setTracerProvider(SdkTracerProvider.builder()
            .setResource(resource)
            .addSpanProcessor(BatchSpanProcessor.builder(spans.build()).build())
            .build())
        .setMeterProvider(SdkMeterProvider.builder()
            .setResource(resource)
            .registerMetricReader(PeriodicMetricReader.builder(metrics.build()).build())
            .build())
        .setLoggerProvider(SdkLoggerProvider.builder()
            .setResource(resource)
            .addLogRecordProcessor(BatchLogRecordProcessor.builder(logs.build()).build())
            .build())
        // Without propagators, no traceparent header is read or sent, and traces break between services.
        .setPropagators(ContextPropagators.create(TextMapPropagator.composite(
            W3CTraceContextPropagator.getInstance(), W3CBaggagePropagator.getInstance())))
        .buildAndRegisterGlobal();
    Runtime.getRuntime().addShutdownHook(new Thread(sdk::close));
  }
}
```

The plain SDK does not read `OTEL_RESOURCE_ATTRIBUTES`, so take `<env>` and `<version>` from the app's own configuration. Application logs reach the logger provider only through an appender, such as the OTel Logback or Log4j appender, so record logs as not configured when the app has none.

## Spring Boot starter

Put the non-secret settings in the env block or `application.properties`: `otel.exporter.otlp.protocol=http/protobuf`, the per-signal endpoints and `otel.exporter.otlp.metrics.temporality.preference=delta`. Do not put the key header there as `otel.exporter.otlp.headers=dd-api-key=${DD_API_KEY:}`, because the starter can send that placeholder as literal text, and every export then gets 403. Add the headers in code with an `AutoConfigurationCustomizerProvider` bean instead:

```java
import io.opentelemetry.exporter.otlp.http.logs.OtlpHttpLogRecordExporter;
import io.opentelemetry.exporter.otlp.http.metrics.OtlpHttpMetricExporter;
import io.opentelemetry.exporter.otlp.http.trace.OtlpHttpSpanExporter;
import io.opentelemetry.sdk.autoconfigure.spi.AutoConfigurationCustomizerProvider;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

@Configuration
public class DatadogOtlpHeaders {
  @Bean
  public AutoConfigurationCustomizerProvider datadogHeaders() {
    String key = System.getenv("DD_API_KEY");
    if (key == null || key.isEmpty()) {
      return customizer -> {};
    }
    return customizer -> customizer
        .addSpanExporterCustomizer((exporter, config) -> exporter instanceof OtlpHttpSpanExporter
            ? ((OtlpHttpSpanExporter) exporter).toBuilder()
                .addHeader("dd-api-key", key).addHeader("compute_stats", "true").build()
            : exporter)
        .addMetricExporterCustomizer((exporter, config) -> exporter instanceof OtlpHttpMetricExporter
            ? ((OtlpHttpMetricExporter) exporter).toBuilder().addHeader("dd-api-key", key).build()
            : exporter)
        .addLogRecordExporterCustomizer((exporter, config) -> exporter instanceof OtlpHttpLogRecordExporter
            ? ((OtlpHttpLogRecordExporter) exporter).toBuilder().addHeader("dd-api-key", key).build()
            : exporter);
  }
}
```
