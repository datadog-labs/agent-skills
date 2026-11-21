# Configure export to Datadog

## Contents

- Endpoints and headers
- Variables per service
- Docker Compose
- Kubernetes and Helm
- Env files
- Through a Datadog Agent

## Endpoints and headers

Every site uses `https://otlp.<DD_SITE>/v1/<signal>`, where `<DD_SITE>` is one of `datadoghq.com`, `us3.datadoghq.com`, `us5.datadoghq.com`, `datadoghq.eu`, `ap1.datadoghq.com`, `ap2.datadoghq.com`, `uk1.datadoghq.com`, `ddog-gov.com` or `us2.ddog-gov.com`. The protocol is `http/protobuf`.

| Signal | Path | Headers | Largest request |
|---|---|---|---|
| Traces | `/v1/traces` | `dd-api-key`, `compute_stats=true` | 15 MiB uncompressed |
| Metrics | `/v1/metrics` | `dd-api-key` | 5.1 MiB uncompressed |
| Logs | `/v1/logs` | `dd-api-key` | 5.1 MiB uncompressed |

Datadog maps `service.name`, `deployment.environment.name` and `service.version` to metric tags by itself. The optional `dd-otel-metric-config: {"resource_attributes_as_tags": true}` header turns every other resource attribute into a metric tag too, which can raise tag cardinality sharply, so add it only when the user asks.

## Variables per service

| Service | Variables |
|---|---|
| Code bootstrap from this skill | `DD_API_KEY`, `OTEL_SERVICE_NAME`, `OTEL_RESOURCE_ATTRIBUTES`. The bootstrap holds the endpoints, builds the headers from `DD_API_KEY` and sets delta temporality. |
| Env-configured: the Java agent, or an SDK that already reads `OTEL_*` variables | The same three, plus the block below |

```sh
OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf
OTEL_EXPORTER_OTLP_TRACES_ENDPOINT=https://otlp.<DD_SITE>/v1/traces
OTEL_EXPORTER_OTLP_TRACES_HEADERS=dd-api-key=<KEY>,compute_stats=true
OTEL_EXPORTER_OTLP_METRICS_ENDPOINT=https://otlp.<DD_SITE>/v1/metrics
OTEL_EXPORTER_OTLP_METRICS_HEADERS=dd-api-key=<KEY>
OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE=delta
# Logs in (always for Java):
OTEL_EXPORTER_OTLP_LOGS_ENDPOINT=https://otlp.<DD_SITE>/v1/logs
OTEL_EXPORTER_OTLP_LOGS_HEADERS=dd-api-key=<KEY>
# Logs out: stops an SDK that exports logs by default from sending them elsewhere
OTEL_LOGS_EXPORTER=none
```

- `<KEY>` is the layer's reference to the key, and the layer must expand it when the service starts: `${DD_API_KEY}` in Compose and in a shell script, `$(DD_API_KEY)` in Kubernetes and Helm.
- A Dockerfile `ENV` line expands `${DD_API_KEY}` at build time, to an empty value, and a systemd `Environment=` line expands no variables, so an env-configured service started from a Dockerfile or a systemd unit sends no key and gets 403. For those starts, read `DD_API_KEY` inside the process instead: a code bootstrap from this skill, or for Java the SDK in code or the Spring Boot customizer in [java.md](java.md). When neither fits, keep the headers out of the image and leave the user this run-time step, which passes the values from their shell without printing the key:

  ```sh
  export OTEL_EXPORTER_OTLP_TRACES_HEADERS="dd-api-key=$DD_API_KEY,compute_stats=true" \
    OTEL_EXPORTER_OTLP_METRICS_HEADERS="dd-api-key=$DD_API_KEY" OTEL_EXPORTER_OTLP_LOGS_HEADERS="dd-api-key=$DD_API_KEY"
  docker run -e OTEL_EXPORTER_OTLP_TRACES_HEADERS -e OTEL_EXPORTER_OTLP_METRICS_HEADERS -e OTEL_EXPORTER_OTLP_LOGS_HEADERS <image>
  ```
- `OTEL_RESOURCE_ATTRIBUTES` takes `deployment.environment.name=<env>,service.version=<version>`. Use values the project already defines, such as the version in its manifest or an environment name in its deployment config. Never commit an angle-bracket placeholder as a live value, because it becomes a real tag.
- URL-encode a header value in an `*_HEADERS` variable when it holds JSON or reserved characters, for example `dd-otel-metric-config=%7B%22resource_attributes_as_tags%22%3Atrue%7D`.
- Never pass `DD_APP_KEY` to a service: the app needs only the API key, and an application key can read and change the organization's data.

## Docker Compose

Edit the `environment:` of each in-scope service in each Compose file the project runs. When all services use the map form, one `x-otel` extension field with a YAML anchor can hold the shared entries.

```yaml
services:
  checkout:
    environment:
      DD_API_KEY: ${DD_API_KEY:-}
      OTEL_SERVICE_NAME: checkout
      OTEL_RESOURCE_ATTRIBUTES: deployment.environment.name=local,service.version=1.4.0
```

Compose fills `${DD_API_KEY}` from the shell or from the project's `.env`, which `dd-account-setup` writes, so the key never enters the Compose file. The `:-` default keeps `docker compose up` working without a key; the bootstraps then skip export. Do not add `env_file: .env` to an app service, because that file also holds `DD_APP_KEY`.

## Kubernetes and Helm

Kubernetes passes `${DD_API_KEY}` through as literal text. It expands `$(DD_API_KEY)`, but only for a variable defined earlier in the same `env:` list, so the Secret reference comes first:

```yaml
env:
  - name: DD_API_KEY
    valueFrom:
      secretKeyRef:
        name: datadog-otlp
        key: api-key
  - name: OTEL_SERVICE_NAME
    value: checkout
  - name: OTEL_RESOURCE_ATTRIBUTES
    value: deployment.environment.name=prod,service.version=1.4.0
  # env-configured services only, for example:
  - name: OTEL_EXPORTER_OTLP_TRACES_HEADERS
    value: dd-api-key=$(DD_API_KEY),compute_stats=true
```

- **Raw manifests or kustomize:** patch each container's `env:` once, preferably with one kustomize patch or a shared ConfigMap through `envFrom` for the non-secret values.
- **Helm:** add the entries to the chart's shared env block in `values.yaml`, or to a shared `envFrom` ConfigMap, so every template renders them. The same `secretKeyRef` and `$(DD_API_KEY)` rules apply.

Give the user this command to create the Secret. The key travels through a pipe, so it never appears in the process list or the shell history. Add `-n <namespace>` when the services run outside the default namespace.

```sh
printf %s "$DD_API_KEY" | kubectl create secret generic datadog-otlp --from-file=api-key=/dev/stdin
```

## Env files

- `.env` holds keys, so it stays untracked: `git ls-files --error-unmatch .env` must fail.
- When the project keeps a `.env.example`, add `DD_API_KEY=` with no value, plus the `OTEL_*` entries. Quote any value that holds `<`, `>`, spaces, braces, commas or `=`, so the file stays shell-sourceable: `OTEL_RESOURCE_ATTRIBUTES="deployment.environment.name=local,service.version=1.4.0"`.
- Startup code that loads `.env` must not replace a non-empty environment variable with a blank file value, because that erases a key the deployment set.

## Through a Datadog Agent

**Detect it.** An Agent shows up as a `DatadogAgent` resource, Datadog Helm values, or a Compose service running `datadog/agent` or `gcr.io/datadoghq/agent`. A service already sends through it when it runs a Datadog tracer (`ddtrace`, `dd-trace`, `dd-java-agent`, `dd-trace-go`), sits under SSI (`DD_APM_INSTRUMENTATION_ENABLED`, `apm.instrumentation.enabled`), or has `DD_AGENT_HOST`. The Agent's OTLP receiver is on when one of these is set:

| Agent setup | Receiver setting |
|---|---|
| Docker or Compose | any `DD_OTLP_CONFIG_RECEIVER_*` variable on the Agent, such as `DD_OTLP_CONFIG_RECEIVER_PROTOCOLS_HTTP_ENDPOINT=0.0.0.0:4318` |
| Helm | `datadog.otlp.receiver.protocols.http.enabled: true` in the values file |
| Datadog Operator | `features.otlp.receiver.protocols.http.enabled: true` in the `DatadogAgent` |
| Manual DaemonSet | the same variable on the `agent` and `trace-agent` containers, with `containerPort: 4318` and `hostPort: 4318` on the `agent` container |

Logs through the Agent also need `DD_LOGS_ENABLED=true` and `DD_OTLP_CONFIG_LOGS_ENABLED=true`, or `datadog.logs.enabled` and `datadog.otlp.logs.enabled` in Helm. Turning the receiver on is an Agent change, so make it only when the user agrees.

**Route the service to it.** Set `OTEL_EXPORTER_OTLP_ENDPOINT` on the service and send no `dd-api-key` header. The code bootstraps from this skill switch to this route when that variable is set. An env-configured SDK reads it once the per-signal endpoint and header variables are left out.

- Compose: `OTEL_EXPORTER_OTLP_ENDPOINT: http://<agent-service>:4318`.
- Kubernetes and Helm: the node's Agent listens on the node IP, port 4318, so define `HOST_IP` first and reference it:

  ```yaml
  env:
    - name: HOST_IP
      valueFrom:
        fieldRef:
          fieldPath: status.hostIP
    - name: OTEL_EXPORTER_OTLP_ENDPOINT
      value: http://$(HOST_IP):4318
  ```
