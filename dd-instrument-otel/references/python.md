# Python (Django, Flask, FastAPI)

## Contents

- Packages
- Bootstrap
- Wire it into the framework
- Logs

## Packages

Find the manifest the build installs from (`pyproject.toml` with its lockfile, `requirements.txt`, `Pipfile`, `setup.py` or `setup.cfg`) and add the packages with the project's manager (`uv add`, `poetry add`, `pipenv install`), or edit the manifest by hand when the project uses plain `pip`.

- Always: `opentelemetry-sdk` and `opentelemetry-exporter-otlp-proto-http`.
- The framework's instrumentation: `opentelemetry-instrumentation-django`, `opentelemetry-instrumentation-flask` or `opentelemetry-instrumentation-fastapi`.
- Logs, only when step 4 of `SKILL.md` included them: `opentelemetry-instrumentation-logging`.
- `python-dotenv`, only when the app relies on a local `.env` and nothing in its startup path loads it.

The `opentelemetry-instrumentation-*` packages are 0.x Beta releases, and each one pairs with a single SDK release (for example, 0.66b2 with 1.45.2). Install them in one command and let the resolver pick the pair, or pin the pair the resolver chose. Pinning one from another release makes the resolver fail or downgrade the SDK. Record the Beta status in `OPENTELEMETRY.md`; the opt-in question still follows the status table, where Python traces and metrics are Stable.

## Bootstrap

Create `otel_setup.py` in the app's package, next to the module that creates the app. Replace `<DD_SITE>`, `<service-name>`, `<env>` (the environment name the project uses, or `local`) and `<version>` (the manifest's version). The module is idempotent, so importing it from several entry points is safe.

```python
"""OpenTelemetry bootstrap: exports to Datadog."""
import os
import socket

from opentelemetry import metrics, trace
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.metrics import (
    Counter,
    Histogram,
    MeterProvider,
    ObservableCounter,
    ObservableGauge,
    ObservableUpDownCounter,
    UpDownCounter,
)
from opentelemetry.sdk.metrics.export import AggregationTemporality, PeriodicExportingMetricReader
from opentelemetry.sdk.resources import ProcessResourceDetector, Resource, get_aggregated_resources
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

OTLP = "https://otlp.<DD_SITE>"
DELTA = {
    Counter: AggregationTemporality.DELTA,
    UpDownCounter: AggregationTemporality.CUMULATIVE,
    Histogram: AggregationTemporality.DELTA,
    ObservableCounter: AggregationTemporality.DELTA,
    ObservableUpDownCounter: AggregationTemporality.CUMULATIVE,
    ObservableGauge: AggregationTemporality.CUMULATIVE,
}
_configured = False


def configure():
    """Set up export once. Without a key or an Agent endpoint it creates no exporter."""
    global _configured
    if _configured:
        return
    _configured = True
    # OTEL_EXPORTER_OTLP_ENDPOINT is set only on the route through a Datadog Agent.
    agent = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT")
    api_key = os.environ.get("DD_API_KEY")
    if not (agent or api_key):
        return
    base = (agent or OTLP).rstrip("/")
    headers = {} if agent else {"dd-api-key": api_key}
    trace_headers = {} if agent else {**headers, "compute_stats": "true"}

    # Defaults for a start without OTEL_RESOURCE_ATTRIBUTES; Resource.create reads that
    # variable, and its values win in the merge.
    defaults = Resource({
        "host.name": socket.gethostname(),
        "deployment.environment.name": "<env>",
        "service.version": "<version>",
    })
    resource = get_aggregated_resources([ProcessResourceDetector()], defaults).merge(
        Resource.create({"service.name": os.environ.get("OTEL_SERVICE_NAME", "<service-name>")})
    )

    tracer_provider = TracerProvider(resource=resource)
    tracer_provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(
        endpoint=f"{base}/v1/traces", headers=trace_headers,
    )))
    trace.set_tracer_provider(tracer_provider)

    reader = PeriodicExportingMetricReader(OTLPMetricExporter(
        endpoint=f"{base}/v1/metrics", headers=headers, preferred_temporality=DELTA,
    ))
    metrics.set_meter_provider(MeterProvider(resource=resource, metric_readers=[reader]))
```

- Import the instrument classes in `DELTA` from `opentelemetry.sdk.metrics`. The same names in `opentelemetry.metrics` are API classes, and the reader rejects them with `Invalid instrument class`.
- The reader exports every 60 seconds by default and reads `OTEL_METRIC_EXPORT_INTERVAL`, which the smoke test lowers.
- When the app loads `.env`, load it before `configure()` runs, with `load_dotenv(override=False)` or the project's equivalent, so a real environment variable is never replaced by a blank file value.

## Wire it into the framework

Call `configure()` before the app handles requests, in the module the existing command already imports. Register the framework instrumentation even when there is no key; it costs nothing without exporters.

| Framework | Where | Code |
|---|---|---|
| Django | `manage.py` before `execute_from_command_line`, and `wsgi.py` or `asgi.py` before the application is created | `configure()` then `DjangoInstrumentor().instrument()`, from `opentelemetry.instrumentation.django` |
| Flask | right after `app = Flask(...)`, at module level or inside `create_app()` | `configure()` then `FlaskInstrumentor().instrument_app(app)`, from `opentelemetry.instrumentation.flask` |
| FastAPI | right after `app = FastAPI(...)` in the module that `uvicorn` or `gunicorn` loads | `configure()` then `FastAPIInstrumentor.instrument_app(app)`, from `opentelemetry.instrumentation.fastapi` |

A second `instrument()` call in the same process only logs a warning, so calling it from both `manage.py` and `wsgi.py` is safe.

## Logs

Only when step 4 of `SKILL.md` included logs. Add this at the end of `configure()`. The underscore modules mark the logs API as not yet Stable.

```python
from opentelemetry._logs import set_logger_provider
from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
from opentelemetry.instrumentation.logging import LoggingInstrumentor
from opentelemetry.sdk._logs import LoggerProvider
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor

logger_provider = LoggerProvider(resource=resource)
logger_provider.add_log_record_processor(BatchLogRecordProcessor(OTLPLogExporter(
    endpoint=f"{base}/v1/logs", headers=headers,
)))
set_logger_provider(logger_provider)
LoggingInstrumentor().instrument()  # adds a handler to the root logger
```

Records from the standard `logging` module are exported. Use this handler rather than `LoggingHandler` from `opentelemetry.sdk._logs`, which is deprecated.

Only records at or above the root logger's level are exported, and that level defaults to `WARNING`, so INFO logs need the app's own `logging.basicConfig(level=...)` or `dictConfig` to run. The instrumentation's handler re-applies those calls when they run after it. A handler attached by hand, such as the SDK's `LoggingHandler`, makes a later `basicConfig` do nothing, so the level stays `WARNING`; in that case, call `configure()` after the app's logging setup.
