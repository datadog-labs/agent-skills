---
name: dd-instrument-otel
description: Instruments a project's server-side services with OpenTelemetry SDKs that send traces, metrics and logs straight to Datadog's OTLP intake, with no Collector. Covers Node.js, Python (Django, Flask, FastAPI), Java and Go on hosts and containers; sets per-site endpoints, headers and delta temporality, and checks export. Use when asked to add or verify OpenTelemetry, OTel or OTLP export to Datadog. Not for serverless (dd-instrument-lambda), ddtrace APM (dd-apm) or browsers (dd-instrument-rum).
metadata:
  version: "0.1.0"
  author: datadog-labs
  repository: https://github.com/datadog-labs/agent-skills
  tags: datadog,opentelemetry,otel,otlp,instrumentation,traces,metrics,logs,nodejs,python,java,go
  alwaysApply: "false"
---

# Datadog OpenTelemetry (direct OTLP)

This skill adds OpenTelemetry (OTel) SDKs to an app's server-side services. Each service exports over HTTPS to Datadog's OTLP intake from its own process, with no Collector.

## How it works

- **Export turns on with the endpoint and the key header.** Each signal posts OTLP over `http/protobuf` to `https://otlp.<DD_SITE>/v1/<signal>` with a `dd-api-key` header. A key or endpoint for another site gets 403, and gRPC is refused.
- **Two settings make the data useful.** Traces need the `compute_stats=true` header, or Datadog computes no request, error or latency metrics for them. Metrics need delta temporality, because the intake rejects the cumulative default of most SDKs.
- **Identity comes from resource attributes.** `OTEL_SERVICE_NAME` becomes the `service` tag, and `OTEL_RESOURCE_ATTRIBUTES` carries `deployment.environment.name` and `service.version`, which become `env` and `version`.
- **An existing Datadog path changes the plan.** When a Datadog Agent, Single Step Instrumentation (SSI) or a Datadog tracer already sends this data, a second, direct path duplicates spans, and SSI turns itself off in a process that loads an OTel SDK.
- **Stability is judged per runtime and signal** from the [OTel status table](https://opentelemetry.io/docs/languages/#status-and-releases): traces and metrics are Stable in all four runtimes, and logs only in Java.
- **Only `DD_API_KEY` reaches the app, and only server-side.** Browsers block the custom headers with a CORS preflight, and a key in browser code is public.

## Set up

Copy these steps into your reply and tick them off as you go.

1. **Scope the services.** List every service: the repo root, or each folder with its own manifest (`package.json`, `requirements.txt`, `pyproject.toml`, `go.mod`, `pom.xml`, `build.gradle`, `*.csproj`). Record each one's language, entry point and what starts it (Dockerfile, systemd, Compose, package scripts), and the deployment layers: Compose, Kubernetes or kustomize, and Helm. Every in-scope service ends instrumented or with a recorded skip reason; a sample is not a finished run.
   - **Serverless or managed platform:** stop before any edit for a service that deploys to AWS Lambda (`serverless.yml`, a SAM `template.yaml`, a Lambda handler), Cloud Run, Azure Functions (`host.json`), App Service, Container Apps, ECS Fargate, Vercel (`vercel.json`), Netlify (`netlify.toml`) or Heroku (`heroku.yml`, `app.json`). Datadog documents a different OTLP setup there, and a batch exporter loses data when a function freezes. Point to [`dd-instrument-lambda`](https://github.com/datadog-labs/agent-skills/tree/main/dd-instrument-lambda), [`dd-instrument-cloud-run`](https://github.com/datadog-labs/agent-skills/tree/main/dd-instrument-cloud-run) or [`dd-instrument-azure-serverless`](https://github.com/datadog-labs/agent-skills/tree/main/dd-instrument-azure-serverless), and for Fargate and managed platforms to Datadog's [serverless](https://docs.datadoghq.com/opentelemetry/setup/otlp_ingest/serverless/) and [managed platform](https://docs.datadoghq.com/opentelemetry/setup/otlp_ingest/managed_platforms/) OTLP pages.
   - **Browser-only app:** stop before any edit, because direct export needs a server-side runtime; browser telemetry belongs to RUM ([`dd-instrument-rum`](https://github.com/datadog-labs/agent-skills/tree/main/dd-instrument-rum)). With a backend too, instrument only the backend.

2. **Check for an existing path.** Search for a Datadog Agent and whether its OTLP receiver is on, SSI settings, a Datadog tracer or `DD_AGENT_HOST` on a service, as "Through a Datadog Agent" in [references/deployment.md](references/deployment.md) lists, and in each service for `opentelemetry`, `io.opentelemetry`, `go.opentelemetry.io` and `OTEL_`.
   - **A service that already sends through the Agent** (a tracer, SSI or `DD_AGENT_HOST`): add no direct path, because a second path duplicates its data. When the user explicitly wants OTLP as well, send it to the Agent's receiver.
   - **An Agent with its OTLP receiver on, and a service with no tracer:** send that service's OTLP to the Agent, not to the intake. An Agent whose receiver is off does not stop direct export.
   - **OTel that already exports to Datadog's intake:** change nothing for that service.
   - **OTel that exports elsewhere,** such as to a Collector: keep its SDK, and repoint its export only after the user agrees, because repointing bypasses whatever the Collector does. When you cannot ask, record it as skipped.

3. **Check credentials and the site** without printing the key: `[ -n "$DD_API_KEY" ] && echo set`, or `grep -c '^DD_API_KEY=' .env`, and `grep '^DD_SITE=' .env`. When either is missing, run [`dd-account-setup`](https://github.com/datadog-labs/agent-skills/tree/main/dd-account-setup), which writes `DD_API_KEY`, `DD_APP_KEY` and `DD_SITE` to a gitignored `.env`. When your run context supplies credentials, use those. `DD_APP_KEY` serves only the optional check in Verify, so never pass it to the app.

4. **Choose the signals, and ask once about non-Stable libraries.** Configure the signals the user asked for: traces, metrics and logs for a general request, or only the named ones. Before installing anything, list the non-Stable pairs in scope with their status, such as `Python logs: Development` or `Go logs: Release candidate`, and ask once per run: "Some OpenTelemetry libraries for this setup are experimental (not Stable), but we think they are reasonable to use for best-effort instrumentation. Include them?" Offer **Yes, include them** and **No, exclude them**, and apply the answer to every service. When no one can answer, as in a headless run, include them, so the run still covers those signals. Name each non-Stable library in `OPENTELEMETRY.md` and the summary. When the answer excludes every requested signal, as with "Python logs, Stable libraries only", stop with no edits and say why.

5. **Install and bootstrap each service** with [references/nodejs.md](references/nodejs.md), [references/python.md](references/python.md), [references/java.md](references/java.md) or [references/go.md](references/go.md). For another server runtime, such as .NET, Ruby or PHP, follow its OTel docs with the same endpoints, headers, temporality and stability rule, and skip a signal with no documented OTLP path. In every runtime:
   - Keep the run, dev, test and deploy commands unchanged: add no wrapper script or new launcher, and load the bootstrap from the existing entry point or startup surface.
   - Persist each package in the manifest the build installs from.
   - Start no exporter when neither `DD_API_KEY` nor `OTEL_EXPORTER_OTLP_ENDPOINT` is set, so the app still starts cleanly. The bootstraps treat `OTEL_EXPORTER_OTLP_ENDPOINT` as the Agent route, so set it only there.

6. **Configure export** with [references/deployment.md](references/deployment.md): one env edit per deployment layer, covering every in-scope service, and a Kubernetes Secret for the key. Configure no Collector or Collector pipeline. Put no key in committed files, browser code or public variables such as `VITE_DD_API_KEY`, `NEXT_PUBLIC_DD_API_KEY` or `REACT_APP_DD_API_KEY`.

7. **Document coverage** in `OPENTELEMETRY.md` at the project root, not the README: files and dependencies changed, how the existing commands start telemetry, the signals and non-Stable libraries, the required variables and how to validate. Include these tables, without the second for a single-service repo:

   ```markdown
   ## Services instrumented
   | Service | Language | Entry point | Bootstrap / config location | Signals |
   |---|---|---|---|---|

   ## Services not instrumented
   | Service | Language | Reason |
   |---|---|---|
   ```

8. **Build and check** with [references/verify.md](references/verify.md): build, smoke test, export check and no-secrets check. Fix errors your edits caused and rerun until the build passes.

## Verify and view

Record the highest level you actually reached:

1. **Configuration changed:** packages, bootstraps and env edits are in place.
2. **The app ran:** the build passed and a smoke request got a response.
3. **Export not rejected:** the run's output shows no export error after the wait. A 403 means a wrong key or site; a 413 means an oversized batch.
4. **Datadog shows spans:** `Datadog:search_datadog_spans`, or the API call in [references/verify.md](references/verify.md), finds `service:<service>` in the last 15 minutes.

Name each level you could not check and the step left for the user, such as passing `DD_API_KEY` to the app at start.

The app host is `app.datadoghq.com` for `datadoghq.com`, `app.datadoghq.eu` for `datadoghq.eu`, `app.ddog-gov.com` for `ddog-gov.com`, and the site itself for the others, for example `us5.datadoghq.com`. End with this summary:

```text
OpenTelemetry: configured | already configured | routed through the Datadog Agent | stopped (<reason>)
Services: <n> instrumented, <n> skipped; signals: <traces, metrics, logs>; non-Stable: <each pair, such as Python logs (Development), and whether it was included or excluded, or none>
Changed: <files, or none>
Evidence: <highest level reached>; not checked: <levels and the step left>
View: https://APP_HOST/apm/traces?query=service:<service>
```

## Undo

Revert this run's edits: packages and lockfile changes, bootstrap files and imports, env entries in each deployment layer, and `OPENTELEMETRY.md`. If the user created the Kubernetes Secret, give them the `kubectl delete secret` command. Leave earlier Datadog or OTel setup as it was.

## Gotchas

| Mistake | Symptom | Fix |
|---|---|---|
| Cumulative metrics, the SDK default | No metrics arrive | Delta temporality, as each runtime reference sets it |
| No `compute_stats=true` on traces | Spans arrive, but the service has no request, error or latency metrics | Add the header |
| `dd-api-key=${DD_API_KEY}` in a Kubernetes `env` value or a Dockerfile `ENV` | The key is sent as literal text or as an empty value, so every request gets 403 | Expand it at run time, as `deployment.md` shows |
| `env_file: .env` on an app service | `DD_APP_KEY` reaches the app | Pass only `DD_API_KEY` |
| Bootstrap loaded after the app's modules | No HTTP or framework spans | Load it first, as each runtime reference shows |
