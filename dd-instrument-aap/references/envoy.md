# Envoy

Open this in step 5 to protect traffic at Envoy on Linux, Docker or Kubernetes. Envoy sends request and response data to the Datadog security processor through its external processing filter. Install no tracer in the upstream app. Merge every change into the Envoy configuration source the repository already uses (`envoy.yaml`, its ConfigMap, or the template that renders it), and never generate the Datadog filter or cluster at start-up, because generated configuration bypasses the reviewed source.

## Contents

- Before you start
- Deploy the processor
- Add the filter
- Define the cluster
- Observability mode
- Verify

## Before you start

Find the listener, route and HTTP filter chain that carry the protected upstream's traffic. Confirm the `DD_SERVICE` and `DD_ENV` tags the processor will report. The processor needs a reachable Datadog Agent with a valid API key that accepts traces from other hosts and containers (`apm_config.apm_non_local_traffic: true`, or `DD_APM_NON_LOCAL_TRAFFIC=true`), because by default the Agent receives traces on localhost only. UI-driven blocking needs Remote Configuration on that Agent. List each Agent setting you cannot check as a step left for the user.

## Deploy the processor

Use the latest released image, `ghcr.io/datadog/dd-trace-go/service-extensions-callout:latest`. Pin an older version only when the user asks, because documentation examples lag the released image. Its settings:

| Variable | Use |
|---|---|
| `DD_AGENT_HOST` | Host or IP of the Datadog Agent |
| `DD_SERVICE`, `DD_ENV` | Service and env tags shown in Datadog |
| `DD_SERVICE_EXTENSION_HOST`, `DD_SERVICE_EXTENSION_PORT` | gRPC listen address and port (default `443`). Keep it private. |
| `DD_SERVICE_EXTENSION_UDS_PATH` | Unix socket path, for Envoy on the same host |
| `DD_SERVICE_EXTENSION_HEALTHCHECK_PORT` | HTTP health port (default `80`) |
| `DD_TRACE_AGENT_PORT` | Only for a non-default Agent trace port |

- **Docker:** add a `datadog-aap-security-processor` service with the image, `DD_AGENT_HOST`, `DD_SERVICE` and `DD_ENV`, on the same private network as Envoy. Publish no processor ports; if publishing is unavoidable, bind to loopback only, such as `127.0.0.1:443:443`. Without Compose, apply the same image, variables and network to the existing Docker workflow.
- **Kubernetes:** add a `datadog-aap-security-processor` Deployment (container ports `443` named `grpc` and `80` named `health`, the variables above, and `tags.datadoghq.com/service` and `/env` labels) and a Service on port `443`. Follow the cluster's labels, namespace and resource conventions.
- **Linux:** run the processor as a host-managed container that writes a Unix socket under `/run/datadog-aap`, for example from a systemd unit with `RuntimeDirectory=datadog-aap`, `Restart=always`, and the command below. Do not use `RuntimeDirectory=datadog`, because the Agent's own unit declares it, and systemd removes the directory when either unit stops:

  ```bash
  docker run --rm --name datadog-aap-security-processor --add-host host.docker.internal:host-gateway \
    -v /run/datadog-aap:/run/datadog-aap -e DD_SERVICE_EXTENSION_UDS_PATH=/run/datadog-aap/aap-security-processor.sock \
    -e DD_AGENT_HOST=host.docker.internal -e DD_SERVICE=<service> -e DD_ENV=<env> \
    ghcr.io/datadog/dd-trace-go/service-extensions-callout:latest
  ```

  When one script starts both the processor and Envoy, run Envoy without `exec` and trap `EXIT` and `TERM` to stop the processor, because `exec` drops the trap. Fail with an error when the socket does not appear. Give Envoy access to the socket through a shared group with mode `0660`, and never make it world-writable, because any local process could then use it.

## Add the filter

Add `envoy.filters.http.ext_proc` as the first filter of the HTTP filter chain, with the integration metadata so Datadog identifies Envoy:

```yaml
http_filters:
  - name: envoy.filters.http.ext_proc
    typed_config:
      "@type": type.googleapis.com/envoy.extensions.filters.http.ext_proc.v3.ExternalProcessor
      grpc_service:
        envoy_grpc:
          cluster_name: datadog_aap_ext_proc_cluster
        initial_metadata:
          - key: x-datadog-envoy-integration
            value: "1"
      failure_mode_allow: true
      processing_mode:
        request_header_mode: SEND
        response_header_mode: SEND
      allow_mode_override: true
  # ... the existing filters
```

`failure_mode_allow: true` lets traffic through when the processor is down; keep it only when availability matters more than blocking. `allow_mode_override: true` is required for body processing.

## Define the cluster

The cluster must use HTTP/2, because Envoy calls the processor over gRPC, and with `failure_mode_allow` a cluster without HTTP/2 passes every request unprotected. Remove a deprecated cluster-level `http2_protocol_options` left from an earlier setup.

```yaml
clusters:
  - name: datadog_aap_ext_proc_cluster
    type: STRICT_DNS
    lb_policy: ROUND_ROBIN
    typed_extension_protocol_options:
      envoy.extensions.upstreams.http.v3.HttpProtocolOptions:
        "@type": type.googleapis.com/envoy.extensions.upstreams.http.v3.HttpProtocolOptions
        explicit_http_config:
          http2_protocol_options: {}
    transport_socket:
      name: envoy.transport_sockets.tls
      typed_config:
        "@type": type.googleapis.com/envoy.extensions.transport_sockets.tls.v3.UpstreamTlsContext
        sni: "localhost"
    load_assignment:
      cluster_name: datadog_aap_ext_proc_cluster
      endpoints:
        - lb_endpoints:
            - endpoint:
                address:
                  socket_address:
                    address: datadog-aap-security-processor   # on Kubernetes: datadog-aap-security-processor.<namespace>.svc.cluster.local
                    port_value: 443
```

On Linux with the Unix socket, drop `type`, `lb_policy` and `transport_socket`, add `connect_timeout: 1s`, and use `address: { pipe: { path: /run/datadog-aap/aap-security-processor.sock } }`. On Docker and Kubernetes, set certificate validation to match the user's Envoy TLS policy.

## Observability mode

Only when the user asks for non-blocking analysis, set `DD_SERVICE_EXTENSION_OBSERVABILITY_MODE=true` on the processor and add `observability_mode: true` and `deferred_close_timeout: 5s` to the filter's `typed_config`. This mode disables blocking.

## Verify

Send the test traffic from Verify and view through Envoy's listener, not straight to the upstream. Signals appear under the processor's `DD_SERVICE`.
