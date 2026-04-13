---
name: dd-apm-linux-troubleshoot-ssi
description: Diagnose and fix Single Step Instrumentation (SSI) issues on Linux hosts — SSI automatically instruments applications for APM without code changes. Only use if the agent and SSI are configured but traces are missing or instrumentation is not working.
metadata:
  version: "1.0.0"
  author: datadog-labs
  repository: https://github.com/datadog-labs/agent-skills
  tags: datadog,apm,linux,ssi,troubleshooting,instrumentation,ld-preload
  alwaysApply: "false"
---

# Troubleshoot APM SSI on Linux

## Triggers

Invoke this skill when the user expresses intent to:
- Debug why a Linux process is not being instrumented
- Investigate why traces are not appearing in Datadog from a Linux host
- Diagnose SSI injection failures on Linux
- Follow up on failed checks from `verify-ssi`
- Report that a specific service or host has no traces

Do NOT invoke this skill if:
- SSI has not been enabled yet — run `enable-ssi` first

---

## Critical: pup First, SSH Second

**You do NOT need SSH access to start troubleshooting.** The `pup` CLI queries Datadog's backend directly. Start with pup commands immediately using information the user already gave you (hostname, service name, env). Only go to SSH if pup doesn't reveal the cause.

### pup-cli: check, install, and authenticate

### Claude runs

```bash
pup --version
```

If not found:

### Claude runs

```bash
brew tap datadog-labs/pack
brew install datadog-labs/pack/pup
```

**Auth — check in this order:**

1. Check OAuth status:
```bash
pup auth status --site <DD_SITE>
```

If authenticated — proceed directly to Step 1.

ERROR: Not authenticated — ask the user to log in:

### What you need to do in a terminal

```bash
pup auth login --site <DD_SITE>
```

2. If OAuth login is not possible (e.g., no browser access), fall back to API keys:
```bash
echo "DD_API_KEY set: $([ -n "${DD_API_KEY:-}" ] && echo yes || echo no)"
echo "DD_APP_KEY set: $([ -n "${DD_APP_KEY:-}" ] && echo yes || echo no)"
```

If `DD_API_KEY` and `DD_APP_KEY` are both set — **proceed to Step 1**. pup will use them automatically even if `pup auth status` shows unauthenticated.

---

## Context

Use what the user already provided. Do not ask for missing context upfront — resolve variables lazily, only when a specific step needs them.

| Variable | How to resolve | When needed |
|---|---|---|
| `DD_HOSTNAME` | From the user's message, or `datadog-agent status` via SSH | Step 1 — start here |
| `SERVICE_NAME` | From the user's message | Step 1 — start here |
| `ENV` | Ask the user only when a command requires it | Step 1 (`service-library-config get`), Step 3 |
| `DD_SITE` | Ask the user, or `grep "^site:" /etc/datadog-agent/datadog.yaml` via SSH | Only if pup auth check fails |
| `SSH_KEY` | From user or `/workspace/.ssh/id_ed25519` | Step 4 (SSH investigation) only |
| `SSH_USER` | From user or default `root` | Step 4 (SSH investigation) only |
| `SSH_HOST` | From user's message | Step 4 (SSH investigation) only |

**If the user has already provided `DD_HOSTNAME` and `SERVICE_NAME`, go directly to Step 1. Do not ask for ENV or SSH details first.**

---

## How SSI Works on Linux — Domain Knowledge

Read this before investigating. It gives you the mental model to reason about novel failures.

**Injection chain:**
1. Install script (with `DD_APM_INSTRUMENTATION_ENABLED=host`) installs `datadog-apm-inject` and language library packages under `/opt/datadog-packages/`
2. The inject package writes its launcher path into `/etc/ld.so.preload`
3. The Linux dynamic linker pre-loads the launcher into every new process at startup
4. The launcher detects the process language and loads the appropriate tracer `.so` from `/opt/datadog-packages/datadog-apm-library-<lang>/`
5. The tracer sends spans to the Agent at `localhost:8126`
6. The Agent forwards traces to Datadog at `intake.<DD_SITE>`

**Diagnostic layers:**
- **`pup`** — sees what Datadog's backend received + injection errors reported by the launcher. Start here.
- **`/proc/<pid>/maps`** — sees the actual shared libraries loaded into a running process. The authoritative check for whether injection succeeded.
- **`datadog-agent status`** — sees whether the local Agent is receiving traces.

**Known silent failures:**
- **musl libc (Alpine)** — launcher is glibc-compiled; musl is ABI-incompatible. Linker loads it but injection silently aborts
- **Existing ddtrace/OTel** — launcher detects user-installed tracer and silently disables itself (`already_instrumented` result class)
- **Unsupported runtime version** — silently skipped
- **Process started before SSI was enabled** — `/etc/ld.so.preload` only affects new processes
- **Static binary / Go** — Go programs link statically and ignore `LD_PRELOAD` entirely
- **SELinux/AppArmor** — can block `/etc/ld.so.preload` reads for confined processes
- **Package directory empty/corrupt** — `datadog-installer status` reflects DB registration, not actual files. A package can show as installed while its directory is empty. Always verify files exist under `/opt/datadog-packages/<package>/`

**Service name identity — important:**
With SSI, `DD_SERVICE` is often not set in the process environment. The tracer auto-detects a service name. The telemetry-reported name (what `pup fleet tracers list` and `service-library-config get` show) may not match what you expect in the APM UI:
- **JVM**: telemetry reports jar artifact name with version (e.g. `inventory-service-1.0.0`), spans use the base name (`inventory-service`)
- **Python**: telemetry may report `fastapi` or `django` rather than the app name
- **Node.js**: names typically match

If `service-library-config get` returns empty, use `pup traces search --query "host:<DD_HOSTNAME>" --from 1h --limit 5` to discover what service names have been sending traces, then retry.

---

## Step 1: Triage with pup (no SSH required)

Run these first. The answers determine everything that follows.

### Claude runs

```bash
# Check for injection errors (failures only — successful injections don't appear here)
pup apm troubleshooting list --hostname <DD_HOSTNAME>

# Check full tracer config — look at apm_enabled, trace_agent_url, site
pup apm service-library-config get --service-name <SERVICE_NAME> --env <ENV>

# Check what services have sent traces (reveals actual service names visible to backend)
pup apm services list --from 1h

# Check if traces exist at all
pup traces search --query "service:<SERVICE_NAME>" --from 15m --limit 5

# Fastest trace confirmation — metrics appear before indexed traces
pup metrics query --query "sum:trace.*.request.hits{host:<DD_HOSTNAME>,service:<SERVICE_NAME>}.as_count()" --from 15m
```

`ENV` is required for `service-library-config get`. If the user didn't provide it, ask for it before running that command.

Key values to check in `service-library-config get` output:
- `apm_enabled` — must be `true`. If `false`, the tracer won't send traces regardless of injection.
- `trace_agent_url` — must point to `http://localhost:8126` or the correct agent socket. Wrong value = tracer can't reach the Agent.
- `site` — must match your Datadog org's site.

---

## Step 2: State Your Hypotheses

Before investigating, explicitly state your ranked hypotheses based on triage output. Do not skip this step.

| Triage signal | Strong hypothesis |
|---|---|
| `pup troubleshooting list` shows `result: error`, `result_class: incorrect_installation` | Package directory empty or corrupt — verify files exist under `/opt/datadog-packages/datadog-apm-library-<lang>/`, then use remediation flow |
| `pup troubleshooting list` shows `result: error`, import/load error | Tracer library couldn't be loaded — check runtime version, libc compatibility |
| `pup troubleshooting list` shows `result: abort`, reason `already_instrumented` | Manual ddtrace/OTel already in the app — launcher silently disabled itself |
| `pup troubleshooting list` shows `result: abort`, reason `language not detected` | Expected for non-app processes (e.g., bash, cron). Not a failure. |
| `pup troubleshooting list` empty | Either no injection attempts yet (process not restarted), or injection succeeded silently |
| `service-library-config get` shows `apm_enabled: false` | Tracer is loaded but explicitly disabled — check `source` field to see who set it |
| `service-library-config get` shows `trace_agent_url` pointing to wrong host/port | Tracer can't reach the Agent — fix the URL |
| `service-library-config get` shows wrong `site` | Traces going to wrong Datadog org |
| No traces in `pup traces search`, no troubleshooting errors | Process was never injected — check: process not restarted after SSI enabled, `/etc/ld.so.preload` missing, static binary |
| Unexpected service name in `pup apm services list` results | Service name mismatch — use the actual name from trace data for subsequent config lookups |
| Traces arriving in pup | Not a real problem — likely a UI filter or time window. Tell the user and stop. |

State your top 1-3 hypotheses explicitly: *"Based on triage, I think the most likely cause is X because Y."*

---

## Step 3: Investigate with pup (deeper)

Use only the tools relevant to your hypotheses.

**Check SDK config in detail:**
```bash
# Show all config values with their source (env_var, remote_config, code, default)
pup apm service-library-config get --service-name <SERVICE_NAME> --env <ENV>

# Show only configs where instances disagree (config drift)
pup apm service-library-config get --service-name <SERVICE_NAME> --mixed
```

Key values to check:
- `apm_enabled` — if `false`, tracer won't send traces. Check `source` to see who disabled it (`code` > `env_var` > `remote_config` > `default`)
- `trace_agent_url` — should be `http://localhost:8126` or a Unix socket. Wrong value = tracer can't reach Agent
- `site` — must match your Datadog org's site. Mismatch = traces going to wrong org
- `service` — with SSI and no `DD_SERVICE` set, `source: default` is expected

**If `service-library-config get` returns empty** — the service name you're using may not match the actual name in trace data:
```bash
pup traces search --query "host:<DD_HOSTNAME>" --from 1h --limit 5
```
Use the `service` field from trace results for subsequent config lookups.

**Check injection error details:**
```bash
pup apm troubleshooting list --hostname <DD_HOSTNAME> --timeframe 4h
```

---

## Step 4: Investigate via SSH (if pup didn't reveal the cause)

**Before asking for SSH credentials, briefly explain what you need to check and why**, so the user understands the diagnostic plan before handing over access.

**Is `/etc/ld.so.preload` set?**
```bash
ssh -o StrictHostKeyChecking=no -i <SSH_KEY> <SSH_USER>@<SSH_HOST> "cat /etc/ld.so.preload"
```
If it contains a path ending in `launcher.preload.so` or `libdatadog-apm-inject.so` — launcher is armed for new processes.
ERROR: Empty or missing — SSI was not fully set up. Re-run the install script with `DD_APM_INSTRUMENTATION_ENABLED=host`.

**Is the tracer actually loaded into the running process?**

This is the authoritative injection check — use `/proc/<pid>/maps`, not environ:
```bash
ssh -o StrictHostKeyChecking=no -i <SSH_KEY> <SSH_USER>@<SSH_HOST> \
  "pgrep -a -f '<SERVICE_NAME>' | head -3"
```
Use the PID:
```bash
ssh -o StrictHostKeyChecking=no -i <SSH_KEY> <SSH_USER>@<SSH_HOST> \
  "sudo cat /proc/<PID>/maps | grep -E 'launcher|apm-library|datadog'"
```
- **Launcher + language library present** — injection succeeded for this process
- **Launcher only, no language library** — launcher ran but couldn't inject the tracer (check `pup troubleshooting list` for the reason)
- **Nothing** — `/etc/ld.so.preload` not set, process started before SSI was enabled, or static binary

**Was the process started before SSI was enabled?**
```bash
ssh -o StrictHostKeyChecking=no -i <SSH_KEY> <SSH_USER>@<SSH_HOST> \
  "ps -p <PID> -o pid,lstart,cmd; stat /etc/ld.so.preload"
```
If process started before `/etc/ld.so.preload` was written, restart the service. **Always confirm with the user before restarting production services.**

**Is the base libc musl?**
```bash
ssh -o StrictHostKeyChecking=no -i <SSH_KEY> <SSH_USER>@<SSH_HOST> \
  "ldd --version 2>&1 | head -1 && cat /etc/os-release | grep PRETTY_NAME"
```
ERROR: musl — SSI's launcher requires glibc. No workaround; must migrate to Debian/Ubuntu/RHEL/Amazon Linux.

**Is it a static binary?**
```bash
ssh -o StrictHostKeyChecking=no -i <SSH_KEY> <SSH_USER>@<SSH_HOST> \
  "file /proc/<PID>/exe; ldd /proc/<PID>/exe 2>&1"
```
ERROR: `statically linked` — SSI cannot instrument this binary. Manual instrumentation required.

**Are the APM packages actually present on disk?**

`datadog-installer status` reflects only DB registration — a package can show as installed while its directory is empty. Always verify:
```bash
ssh -o StrictHostKeyChecking=no -i <SSH_KEY> <SSH_USER>@<SSH_HOST> \
  "ls /opt/datadog-packages/ && ls /opt/datadog-packages/datadog-apm-library-<LANG>/ | head -5"
```
ERROR: Directory empty or missing — package is registered but broken on disk. Use the remediation flow.

**Does the app have existing manual instrumentation?**
```bash
ssh -o StrictHostKeyChecking=no -i <SSH_KEY> <SSH_USER>@<SSH_HOST> "
sudo cat /proc/<PID>/maps | grep -E 'ddtrace|opentelemetry|dd-trace'
"
```
Also check dependency manifests: `requirements.txt`, `package.json`, `Gemfile`, `pom.xml`.
ERROR: Found — SSI silently disabled itself. Remove manual tracer, restart the service.

**Is the Agent APM receiver listening and receiving traces?**
```bash
ssh -o StrictHostKeyChecking=no -i <SSH_KEY> <SSH_USER>@<SSH_HOST> \
  "sudo datadog-agent status 2>&1 | grep -A 15 'APM Agent'"
```
- `feature_auto_instrumentation_enabled: true` — SSI is active on the agent
- `Receiver (previous minute)` — trace count received by the agent
- `Endpoints` — where traces are forwarded

```bash
ssh -o StrictHostKeyChecking=no -i <SSH_KEY> <SSH_USER>@<SSH_HOST> \
  "sudo ss -tlnp 2>/dev/null | grep 8126 || sudo netstat -tlnp 2>/dev/null | grep 8126"
```
ERROR: Port 8126 not listening — APM receiver disabled. Check `apm_config.enabled` in `/etc/datadog-agent/datadog.yaml`.

**What service name did the tracer register?**

With SSI, `DD_SERVICE` is often not set. Read the tracer's memfd to find the real service name:
```bash
ssh -o StrictHostKeyChecking=no -i <SSH_KEY> <SSH_USER>@<SSH_HOST> "
sudo ls -la /proc/<PID>/fd/ | grep 'datadog-tracer-info'
"
```
Use the fd number:
```bash
ssh -o StrictHostKeyChecking=no -i <SSH_KEY> <SSH_USER>@<SSH_HOST> \
  "sudo cat /proc/<PID>/fd/<FD_NUM> | python3 -c \"import sys,msgpack; d=msgpack.unpackb(sys.stdin.buffer.read()); print(d)\""
```
Returns `service_name`, `service_env`, `tracer_version`.

**Is SELinux/AppArmor blocking `/etc/ld.so.preload`?**
```bash
ssh -o StrictHostKeyChecking=no -i <SSH_KEY> <SSH_USER>@<SSH_HOST> "
getenforce 2>/dev/null
ausearch -m AVC -ts recent 2>/dev/null | grep 'ld.so.preload\|datadog' | tail -10
dmesg | grep -i 'apparmor.*denied.*datadog' | tail -5
"
```
If SELinux/AppArmor is denying access, work with the user's security team. Do not disable SELinux systemwide.

---

## Step 5: Reflect Before Concluding

Before applying any fix, answer:
1. What evidence confirms my hypothesis?
2. What evidence would contradict it — and have I checked?
3. Is there a simpler explanation I haven't considered?

If the conclusion doesn't hold up, return to Step 2 with new hypotheses.

---

## Step 6: Fix

**Remediation: Reinstalling a Broken APM Package**

`datadog-installer status` reflects DB registration, not actual file presence. If `pup troubleshooting list` shows `incorrect_installation` but the installer says the package is installed, the registration is stale:

```bash
# Remove the stale registration first
ssh -o StrictHostKeyChecking=no -i <SSH_KEY> <SSH_USER>@<SSH_HOST> \
  "sudo datadog-installer remove datadog-apm-library-<LANG>"

# Re-run install — now it will actually download and extract
ssh -o StrictHostKeyChecking=no -i <SSH_KEY> <SSH_USER>@<SSH_HOST> \
  "DD_API_KEY=${DD_API_KEY} DD_SITE=${DD_SITE} DD_APM_INSTRUMENTATION_ENABLED=host bash -c \"\$(curl -L https://install.datadoghq.com/scripts/install_script_agent7.sh)\""
```

If re-running the install script is sufficient (package files are intact), use `remove` first only if the script reports success but the problem persists.

**After any config change — restart the service** (confirm with user first for production):

The user must restart the affected service for SSI to re-inject. Identify the service manager and present restart instructions — do not restart automatically unless the user explicitly asks.

Common restart commands:
```bash
# systemd
sudo systemctl restart <SERVICE_NAME>
# supervisord
sudo supervisorctl restart <PROGRAM_NAME>
# pm2
pm2 reload <APP_NAME>
```

---

## Step 7: Verify

Re-run the pup triage commands to confirm the fix worked:

### Claude runs

```bash
pup apm troubleshooting list --hostname <DD_HOSTNAME> --timeframe 15m
pup traces search --query "service:<SERVICE_NAME>" --from 15m --limit 5
pup metrics query --query "sum:trace.*.request.hits{host:<DD_HOSTNAME>,service:<SERVICE_NAME>}.as_count()" --from 15m
```

If there are no new injection errors and traces are arriving — resolved. Automatically proceed to `onboarding-summary` now — do not ask the user for permission.

ERROR: Still failing — return to Step 2 with updated hypotheses.

---

## Security constraints

- Never write a raw API key into any file or chat message
- Never disable SELinux systemwide
- Always confirm before restarting production services
- `datadog-installer remove` requires explicit confirmation — confirm with user before running
