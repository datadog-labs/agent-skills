# APM Troubleshooting — System Model & Tools

When a user reports an APM issue, diagnose it using the tools and knowledge below. Do not assume the problem — outline diagnostic steps and gather evidence before concluding.

**Critical: You do NOT need SSH access or kubectl access to start troubleshooting.** The `pup` CLI queries Datadog's backend directly over the API. Start with pup commands immediately using the information the user already gave you (hostname, service name, environment).

**Your troubleshooting approach — always follow this order:**

**Step 1: pup commands (no SSH/kubectl needed — do this FIRST):**
- `pup apm troubleshooting list --hostname <hostname>` — check for SSI injection errors. Always run this first.
- `pup apm service-library-config get --service-name <service>` — check the full SDK config. Look at `apm_enabled`, `trace_agent_url`, `site`, and the `source` of each config value.
- `pup fleet tracers list --filter "hostname:<hostname>"` — see what tracers are running and their telemetry service names. If the user's service name doesn't match, this reveals the telemetry name to use.
- `pup traces search --query "service:<service>" --from 15m` — check if traces exist.
- `pup metrics query --query "sum:trace.*.request.hits{host:<hostname>,service:<service>}.as_count()" --from 15m` — check trace metrics (appear faster than indexed traces).
- For K8s: `pup fleet instrumented-pods list <cluster-name>` — check if pods were targeted for injection.

**Step 2: SSH (Linux) or kubectl (K8s) — only if pup didn't reveal the cause:**
- SSH: check `/proc/<pid>/maps`, `datadog-agent status`, process environment
- kubectl: check namespace labels, pod annotations, init containers, pod env vars

**Step 3: Diagnose and remediate:**
- Analyze what you found. Identify the specific failure.
- Suggest a targeted fix based on the evidence.
- After the fix, recommend re-running the pup diagnostic commands to verify.

The `pup` program is included in this skill's directory.

## How APM Works (What a Working Configuration Requires)

A working APM setup is a pipeline with four components that must all be healthy:

```
Datadog Backend  <──>  Agent  <──>  Application (with SDK/Tracer)
                         │
                    Host / Node
```

**On Linux hosts (SSI):**
- The Datadog Agent runs on the host and listens for traces (default: `localhost:8126`)
- SSI uses `/etc/ld.so.preload` to load a launcher into every process at startup
- The launcher detects the process language and loads the appropriate tracer library
- The tracer sends spans to the Agent, which forwards them to the Datadog backend

**On Kubernetes (SSI):**
- The Admission Controller (a MutatingAdmissionWebhook) mutates pods at scheduling time
- It injects init containers that install the tracer library and sets DD_ environment variables
- The tracer in the pod sends traces to the Agent (running as a DaemonSet or sidecar)
- The Agent forwards to the Datadog backend

**For traces to appear in Datadog, every link in this chain must work:**
1. Agent is running and connected to the backend
2. Application is instrumented (tracer injected and loaded)
3. Tracer is configured correctly (APM enabled, correct agent URL, correct site)
4. Tracer can reach the Agent
5. Application is receiving traffic (traces only appear when requests are processed)

## Tools for Inspecting the Pipeline

### pup — Datadog CLI (requires auth)

**Authentication:**
```bash
pup auth status              # Check if authenticated; extract site field
pup auth login               # OAuth login via browser
```

**Injection errors** (failures only — successful injections don't appear here):
```bash
pup apm troubleshooting list --hostname <dd_hostname>
pup apm troubleshooting list --hostname <dd_hostname> --timeframe 4h
```
Returns `result` (error/abort), `result_class`, and `result_reason` per injection attempt.

Common result_class values and what they mean:
- `incorrect_installation` — required file missing or package directory empty/corrupt on disk. Note: `datadog-installer status` reflects DB registration, not actual file presence. Always verify files exist under `/opt/datadog-packages/<package>/`.
- `already_instrumented` — process was already instrumented (usually benign)
- Import/load errors — tracer library couldn't be loaded. Language compatibility issue or corrupt package.
- Abort with "exclusion rule" — process matched DD_APM_INSTRUMENTATION_LIBRARIES_EXCLUDE or similar
- Abort with "language not detected" — expected for non-application processes

**SDK configuration** (what the tracer is actually configured to do):
```bash
# All tracer configs for a service
pup apm service-library-config get --service-name <service>

# Filter by env and language
pup apm service-library-config get --service-name <service> --env <env> --language <lang>

# Only show configs where instances disagree (config drift)
pup apm service-library-config get --service-name <service> --mixed
```
Returns every config key grouped by service instance. Each entry has:
- `name` — config key (e.g., `apm_enabled`, `service`, `trace_agent_url`, `env`, `site`)
- `value` — current value
- `source` — origin: `env_var`, `remote_config`, `code`, or `default`

Config source priority: `code` > `env_var` > `remote_config` > `default`. A common issue is one source silently overriding another.

**Important note on service identity:** The `service_name`, `service_env`, and `language_name` values in this endpoint come from the **SDK telemetry pipeline** — they reflect what the tracer itself reports based on its runtime configuration. These values may not match what appears in the Service Catalog, which can aggregate data from multiple sources (APM spans, USM, infrastructure tags, manual service definitions). The telemetry values are determined by:
- **service_name**: `DD_SERVICE` env var, or code-level configuration, or auto-detected by the tracer from the process/framework (SSI default). On K8s, the Admission Controller may inject `DD_SERVICE` into the pod env. On Linux SSI, it's typically auto-detected from the process name.
- **service_env**: `DD_ENV` env var, or code-level configuration. On K8s, often injected by the Admission Controller. On Linux, set system-wide or per-service.
- **language_name**: Detected by the injector/launcher at injection time based on the process runtime. Not user-configurable.

When troubleshooting "service not appearing" issues, check whether the telemetry-reported name matches what the user expects — the Service Catalog may show a different name than what the tracer is actually sending.

**Known per-language name divergence (telemetry name vs span name):**
- **JVM**: Telemetry reports jar artifact name with version (e.g., `inventory-service-1.0.0`), spans use the base name (`inventory-service`). Happens when `DD_SERVICE` is not set.
- **Python**: Telemetry reports the `DD_SERVICE` value or inferred name (e.g., `order-service`), but spans may use the framework name (`fastapi`, `django`). Same process, different names — verified via runtime-id correlation.
- **Node.js**: Names typically match between telemetry and spans.

If a config lookup returns empty, use `pup fleet tracers list --filter "hostname:<host>"` to discover the telemetry names, then use those for `service-library-config get`.

Key configs to understand:
- `apm_enabled` — if false, tracer won't send traces. Check source to understand who disabled it.
- `trace_agent_url` — where the tracer sends spans. Should be `http://localhost:8126` or a Unix socket on Linux, or the Agent service address on K8s.
- `site` — must match the Datadog site from `pup auth status`. Mismatch = traces going to wrong org.
- `service` — with SSI, `source: default` is expected (tracer auto-detects). May not match user expectations. See identity note above.
- `env` — if unset, traces won't appear under the expected environment in Datadog UI.

**Service config** (broader view of where a service is running):
```bash
pup apm service-config get --service-name <service> --env <env>
```
Returns service instances, config IDs, and hostnames.

**Trace metrics** (fastest way to confirm traces are flowing):
```bash
pup metrics query --query "sum:trace.*.request.hits{host:<dd_hostname>,service:<service_name>}.as_count()" --from 15m
```
Trace metrics appear faster than indexed traces. Common operation names: `trace.flask.request`, `trace.django.request`, `trace.servlet.request`, `trace.express.request`.

**Trace search:**
```bash
pup traces search --query "service:<service_name>" --from 15m
pup traces search --query "host:<dd_hostname>" --from 15m
```
May lag behind metrics by 30-60 seconds due to indexing.

**Fleet / infrastructure:**
```bash
pup fleet agents list --filter "<hostname>"     # Agent status, RC status, instrumentation status
pup infrastructure hosts list --filter "<hostname>"  # Fallback if fleet errors
```
Always use `--filter` — without it, these commands timeout (504).

**Fleet tracers** (telemetry-derived service names — needed to bridge the name mismatch):
```bash
# List tracers by hostname — returns telemetry service name, language, tracer version, runtime_ids
pup fleet tracers list --filter "hostname:<hostname>"

# List tracers by environment
pup fleet tracers list --filter "env:<env>"
```
Use this when `service-library-config get` returns empty — the service name the user gave you may be the span name, not the telemetry name. Fleet tracers shows the telemetry names you need for config queries.

**Instrumented pods** (K8s — confirms Admission Controller injected into pods):
```bash
pup fleet instrumented-pods list <cluster-name>
```
Returns which pods were targeted for injection by the Admission Controller. Use this as the first check for K8s troubleshooting — if a pod isn't listed here, it was never targeted.

### SSH — Direct host inspection (Linux)

**Agent status:**
```bash
ssh <user>@<host> "sudo datadog-agent status"
```
APM Agent section shows: `feature_auto_instrumentation_enabled`, `Receiver (previous minute)` (trace count), `Endpoints` (where traces are forwarded).

**Agent connectivity diagnostics:**
```bash
ssh <user>@<host> "sudo datadog-agent diagnose --include connectivity-datadog-core-endpoints"
```

**Verify injection at process level:**
```bash
ssh <user>@<host> "sudo cat /proc/<pid>/maps | grep -E 'launcher|apm-library'"
```
- Launcher + language library present → injection succeeded
- Launcher only, no library → launcher ran but injection failed
- Nothing → `/etc/ld.so.preload` not set. Check with `cat /etc/ld.so.preload`

**Read tracer's registered service name** (SSI doesn't set DD_SERVICE — tracer auto-detects):
```bash
# Find the tracer memfd
sudo ls -la /proc/<pid>/fd/ | grep "datadog-tracer-info"
# Decode it (MessagePack)
sudo cat /proc/<pid>/fd/<fd_num> | python3 -c "import sys,msgpack; d=msgpack.unpackb(sys.stdin.buffer.read()); print(d)"
```
Returns `service_name`, `service_env`, `service_version`, `tracer_version`.

**Check process environment for DD_ variables:**
```bash
ssh <user>@<host> "sudo cat /proc/<pid>/environ | tr '\0' '\n' | grep DD_"
```

**Discover listening services on a host:**
```bash
ssh <user>@<host> "sudo ss -lntp"
```

**Check installed APM packages:**
```bash
ssh <user>@<host> "sudo datadog-installer status"      # Registration only — verify files exist too
ssh <user>@<host> "ls /opt/datadog-packages/"           # Actual packages on disk
```

APM packages managed by the installer (all under `/opt/datadog-packages/`):
- `datadog-apm-inject` — the launcher (`/etc/ld.so.preload` hook)
- `datadog-apm-library-python`, `-java`, `-ruby`, `-js`, `-dotnet`, `-php`

### K8s SSI Verification Workflow (pup + kubectl)

1. `pup fleet instrumented-pods list <cluster-name>` → confirms Admission Controller injected into pods
2. `pup fleet tracers list --filter "env:<env>"` → confirms tracer is running and reporting telemetry
3. `pup apm service-library-config get --service-name <name> --env <env>` → confirms tracer config is correct

If step 1 shows the pod but step 2 doesn't show a tracer, the init containers may have failed. If step 2 shows a tracer but step 3 returns empty, there's a service name mismatch (see identity note above).

### kubectl — Kubernetes inspection

**Admission Controller running?**
```bash
kubectl get mutatingwebhookconfigurations | grep datadog
```
If missing, SSI won't work on K8s.

**Namespace targeting** (SSI only mutates pods in labeled namespaces):
```bash
kubectl get namespaces --show-labels | grep admission.datadoghq.com
```
Look for `admission.datadoghq.com/mutate-pods: "true"`.

**Pod mutation** (did the Admission Controller inject into this pod?):
```bash
kubectl get pod <pod> -n <ns> -o jsonpath='{.metadata.annotations}' | python3 -m json.tool
```
Look for `admission.datadoghq.com` annotations. If absent, pod was not mutated.

**Init containers** (did SSI injection containers run?):
```bash
kubectl get pod <pod> -n <ns> -o jsonpath='{.status.initContainerStatuses[*].name}'
kubectl get pod <pod> -n <ns> -o jsonpath='{.status.initContainerStatuses}'
```
Check if Datadog init containers are present and terminated successfully.

**Pod environment** (what DD_ vars did the Admission Controller inject?):
```bash
kubectl exec <pod> -n <ns> -- env | grep DD_
```
Key: `DD_TRACE_AGENT_URL` or `DD_AGENT_HOST`, `DD_SERVICE`, `DD_ENV`, `DD_VERSION`.

**Agent DaemonSet running?**
```bash
kubectl get pods -l app=datadog-agent --all-namespaces
```

## Remediation: Reinstalling a Broken APM Package (Linux)

`datadog-installer status` reflects what's registered in `packages.db`, not whether files actually exist on disk. If injection fails with `incorrect_installation` but the installer says the package is installed, the registration is stale:

```bash
# Remove the stale registration
ssh <user>@<host> "sudo datadog-installer remove datadog-apm-library-<lang>"

# Re-run install — now it will actually download
ssh <user>@<host> "sudo DD_API_KEY=${DD_API_KEY} DD_SITE=${DD_SITE} DD_APM_INSTRUMENTATION_ENABLED=host bash -c \"\$(curl -L https://install.datadoghq.com/scripts/install_script_agent7.sh)\""
```

After any remediation, affected services must be restarted for SSI to re-inject.

## Known Limitations

- `pup apm service-library-config get` — backed by **unstable endpoint**. May change or be unavailable. Fallback: check config via SSH process environ.
- `pup apm troubleshooting list` — requires `--hostname`. Reports failures only, not successes. Data has 7-day TTL, 4h default window.
- SDK config data freshness — no TTL on UKV storage, data dies out between deployments. App-extended-heartbeat implementation in progress across SDKs.
- No direct API for "which services are SSI vs manual" — span tag inference (`svc.auto`, `entrypoint.*`) is unreliable.
- No aggregated API for pod mutation status — Admission Controller logs at DEBUG level only.
- `pup fleet agents list` — always use `--filter`. Without it, times out (504).

## Common Troubleshooting Error Types (Linux SSI)

These are the error types reported by `pup apm troubleshooting list`. Each has a `result_reason` field with a customer-facing explanation:

- **Wrong Python version** — SSI requires a compatible Python version. Check `result_reason` for specifics.
- **ddtrace already installed in app** — Application has its own ddtrace install that conflicts with the SSI-injected one. The `already_instrumented` result_class covers this.
- **Package directory empty/corrupt** — `incorrect_installation` class. Use the remediation flow above.
- **Language not supported** — Launcher couldn't find a matching tracer library for the detected language.
