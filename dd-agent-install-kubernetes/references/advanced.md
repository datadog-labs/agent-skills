# Advanced configuration

APM Single Step Instrumentation (with library pinning and namespace scoping), FIPS mode, and private
registries. Use these only when the user selected them in SKILL.md Phase 1.

## APM Single Step Instrumentation, library pinning, and namespace scoping

Only when the user explicitly opted in to Single Step Instrumentation. Use the namespace allowlist /
denylist and any verified tracer versions captured in SKILL.md Phase 1.

### Operator path (merge into `spec.features`)

```yaml
apm:
  enabled: true
  instrumentation:
    enabled: true
    disabledNamespaces:
      - <NS_TO_SKIP>
    targets:
      - name: default-target
        namespaceSelector:
          matchNames:
            - <NS1>
            - <NS2>
        ddTraceVersions:
          java: "<VERIFIED_VERSION>"
          python: "<VERIFIED_VERSION>"
          js: "<VERIFIED_VERSION>"
          dotnet: "<VERIFIED_VERSION>"
          ruby: "<VERIFIED_VERSION>"
        ddTraceConfigs:
          - name: DD_PROFILING_ENABLED
            value: auto
```
Drop `ddTraceVersions` when no versions were pinned. Drop `namespaceSelector` when no allowlist
was given. Drop `disabledNamespaces` when no denylist was given. Drop `ddTraceConfigs` unless
the user selected profiling or another tracer configuration.

Install the Datadog Agent in the separate `datadog` namespace. Single Step Instrumentation does
not instrument applications in the Agent's own namespace. Profiling is a separate user choice.

### Helm chart path

```yaml
datadog:
  apm:
    instrumentation:
      enabled: true
      disabledNamespaces:
        - <NS_TO_SKIP>
      targets:
        - name: default-target
          namespaceSelector:
            matchNames:
              - <NS1>
              - <NS2>
          ddTraceVersions:
            java: "<VERIFIED_VERSION>"
            python: "<VERIFIED_VERSION>"
            js: "<VERIFIED_VERSION>"
            dotnet: "<VERIFIED_VERSION>"
            ruby: "<VERIFIED_VERSION>"
          ddTraceConfigs:
            - name: DD_PROFILING_ENABLED
              value: auto
```

## FIPS mode

### Operator path

Add to `spec.global`:
```yaml
useFIPSAgent: true
```
Confirm the Datadog organization uses a currently supported FED site, the selected nodes and cloud
region meet the FIPS requirements, and the chosen Agent version supports the FIPS image before
applying this setting.
Verify those conditions against the current FIPS supported-platform documentation; the presence of
the value alone does not mean the selected distribution, mode, region, and products are supported.

### Helm chart path

```yaml
useFIPSAgent: true
```

## Private registries

### Operator image (Operator path and Terraform)

For a restricted-network install, mirror the Operator image as well as the Agent images. The
`DatadogAgent` registry setting does not affect the Operator deployment. Ensure the pull secret
already exists in the `datadog` namespace, then create `datadog-operator-values.yaml`:

```yaml
image:
  repository: <PRIVATE_REGISTRY_PREFIX>/operator
imagePullSecrets:
  - name: <PULL_SECRET_NAME>
```

Kubernetes Secrets are namespace-scoped. Do not ask for a registry password in chat or place it on
a command line; create the `datadog` namespace first, then use the user's approved secret-management
workflow to create the secret. Add `-f datadog-operator-values.yaml` to the install command when
using the private mirror.

### Operator path (`DatadogAgent`)

The registry URL is a `spec.global` field, but the DatadogAgent CRD
exposes pull secrets per-component via `pullSecrets` on each `override.<component>.image`
block (not as a global `imagePullSecrets`). Set the registry once globally, then attach the
same pull secret on every component that pulls the agent image:
```yaml
spec:
  global:
    registry: <PRIVATE_REGISTRY_HOST>/datadog
  override:
    nodeAgent:
      image:
        pullSecrets:
          - name: <PULL_SECRET_NAME>
    clusterAgent:
      image:
        pullSecrets:
          - name: <PULL_SECRET_NAME>
    clusterChecksRunner:
      image:
        pullSecrets:
          - name: <PULL_SECRET_NAME>
```
Drop the `clusterChecksRunner` block if cluster-check runners aren't enabled.

The blocks above only cover the Agent components. When APM Single Step Instrumentation is enabled,
the admission controller also injects an `apm-inject` init container and per-language tracer init
containers (`dd-lib-java-init`, `dd-lib-python-init`, `dd-lib-js-init`, `dd-lib-dotnet-init`,
`dd-lib-ruby-init`, `dd-lib-php-init`) that pull from `gcr.io/datadoghq` by default - a public
registry a restricted-network cluster can't reach. Mirror the images for the languages in use, then
point the admission controller at the mirror through its first-class registry field:
```yaml
spec:
  features:
    admissionController:
      registry: <PRIVATE_REGISTRY_HOST>/datadog
```
For EKS Fargate, also set
`spec.features.admissionController.agentSidecarInjection.registry` to the same mirror explicitly.
Mirrored tracer image tags must match the requested `ddTraceVersions` (an unset version resolves to
tag `0`). The registry override changes image locations but does not provide credentials to the
injected workload. Ensure the pull secret exists in every namespace selected for Single Step
Instrumentation and that each workload service account references it through `imagePullSecrets`.
Ask before patching a shared or default service account.

### Helm chart path

```yaml
registry: <PRIVATE_REGISTRY_HOST>/datadog
agents:
  image:
    pullSecrets:
      - name: <PULL_SECRET_NAME>
clusterAgent:
  image:
    pullSecrets:
      - name: <PULL_SECRET_NAME>
clusterChecksRunner:
  image:
    pullSecrets:
      - name: <PULL_SECRET_NAME>
```

With APM Single Step Instrumentation enabled, also mirror the injection images (`apm-inject` and the
`dd-lib-<lang>-init` tracer init containers for the languages in use, which default to
`gcr.io/datadoghq`) and point the admission controller at the mirror:
```yaml
clusterAgent:
  admissionController:
    containerRegistry: <PRIVATE_REGISTRY_HOST>/datadog
```
Ensure the pull secret exists in every namespace selected for Single Step Instrumentation and that
each workload service account references it through `imagePullSecrets`. Ask before patching a
shared or default service account. The same requirement applies to Fargate workload namespaces
because the injected Agent sidecar is pulled by the workload pod.
