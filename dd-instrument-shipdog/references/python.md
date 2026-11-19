# Python (`ddtrace`)

## Install

`ddtrace>=4.15.0` (the first version with agentless mode) in the manifest the deploy installs from, with the project's own tool (`uv add`, `poetry add`, `pipenv install`, or `requirements.txt` plus `pip install`), then install it locally. If `requirements.txt` is generated from `requirements.in` (its header says so), add packages to `requirements.in` and rerun the command in the header. Raise an older constraint and say so in the report. Pin exactly only if the project pins everything.

## Load it first

Prefix every launch command with `ddtrace-run`. If a command can't be wrapped, make `import ddtrace.auto` the entrypoint's first import, after any docstring and `from __future__` imports.

| App | Command |
|---|---|
| Django | `ddtrace-run gunicorn <project>.wsgi` (or `uvicorn <project>.asgi:application`) |
| FastAPI, Starlette | `ddtrace-run uvicorn main:app` (multiple `--workers` are fine) |
| Flask | `ddtrace-run gunicorn app:app`, or `ddtrace-run python app.py` |
| Celery workers | `ddtrace-run celery -A <app> worker`: a separate service, set up the same way |

- Reloaders (`flask run --debug`, `uvicorn --reload`, Django `runserver`) restart the process and drop logpoints. Verify without them.
- On macOS, gunicorn's forked workers can crash under `ddtrace`. Keep `ddtrace-run gunicorn` in deploy config (production is Linux), verify with a non-forking server (`ddtrace-run flask --app <module> run --no-reload`), and report gunicorn as unverified until it runs on Linux.

## Logs

Agentless `ddtrace` doesn't send logs itself; its OpenTelemetry log export does, also without an Agent:

1. Add `opentelemetry-sdk` and `opentelemetry-exporter-otlp-proto-http` to the manifest.
2. Set `DD_LOGS_OTEL_ENABLED=true`, `OTEL_EXPORTER_OTLP_LOGS_PROTOCOL=http/protobuf`, `OTEL_EXPORTER_OTLP_LOGS_ENDPOINT=https://otlp.<DD_SITE>/v1/logs`, and `OTEL_EXPORTER_OTLP_LOGS_HEADERS=dd-api-key=$DD_API_KEY` built at launch, never as a literal.
3. Add `import opentelemetry  # noqa: F401` to the module the server loads (`app.py`, `main.py`, Django's `wsgi.py`/`asgi.py`), before other application imports but after its docstring and any `from __future__` imports, which Python requires first. If the project uses `import ddtrace.auto`, put that just before it. `ddtrace` hooks the exporter on that import; without it no logs are sent.

This covers `logging` and libraries on it (`structlog` with the stdlib backend). `print` and `loguru` sinks aren't sent; offer moving them onto `logging` as a follow-up.

## Launch command without deploy config

`ddtrace` doesn't read `.env`; don't add `load_dotenv()` above the tracer import: anything imported before it loads uninstrumented. The script below loads `.env`; if the key is in another env file, load that one. With no deploy config, commit an executable `run.sh`, adding the Git variables `dd-git-tagging` sets before `exec`:

```sh
#!/bin/sh
set -e
key=$DD_API_KEY; [ -f .env ] && { set -a; . ./.env; set +a; }; [ -n "$key" ] && export DD_API_KEY="$key"
export DD_SERVICE=<service> DD_ENV=<env> DD_SITE=<site>
export DD_AGENTLESS_ENABLED=true DD_DYNAMIC_INSTRUMENTATION_ENABLED=true DD_LOGS_OTEL_ENABLED=true
export OTEL_EXPORTER_OTLP_LOGS_PROTOCOL=http/protobuf OTEL_EXPORTER_OTLP_LOGS_ENDPOINT="https://otlp.$DD_SITE/v1/logs"
export OTEL_EXPORTER_OTLP_LOGS_HEADERS="dd-api-key=$DD_API_KEY"
[ -f .venv/bin/activate ] && . .venv/bin/activate
exec ddtrace-run <launch command>
```

Activate the project's virtualenv before `ddtrace-run`, or `python` is the system interpreter and the tracer never loads. In a Dockerfile, use `ENV` for the non-secret settings and build the OTLP header in the entrypoint from the runtime `DD_API_KEY`.

## Check

Without a key, agentless `ddtrace` refuses to start (`DD_AGENTLESS_ENABLED is set but DD_API_KEY is not`), and so does `ddtrace-run --info`; run it with the full environment and check service, env, version. With `DD_TRACE_DEBUG=true`, `OpenTelemetry Logs exporter is not configured, proceeding with ddtrace setup` confirms the log export is wired.
