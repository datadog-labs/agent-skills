---
name: apm-onboarding
description: >
  Onboard a user's Linux hosts to Datadog APM using pup. Use this skill when the user wants to
  instrument their infrastructure with Datadog APM, set up SSI (Single Step Instrumentation),
  install the Datadog agent on hosts, or troubleshoot APM instrumentation issues. Trigger when
  the user says things like "set up APM", "instrument my hosts", "install datadog agent",
  "onboard to APM", "set up tracing", "troubleshoot instrumentation", or "why isn't APM working".
  Also trigger when the user mentions SSI, auto-instrumentation, or injection errors.
---

# APM Onboarding (Linux Hosts)

Guide the user through installing the Datadog agent with APM Single Step Instrumentation on their Linux hosts, then verify that instrumentation is working.


## Phase 1: Gather Infrastructure Info

Ask the user:
1. **What hosts** do you want to instrument? Get a list of IPs or hostnames.
2. **How do I SSH to them?** Get the SSH user, key path, and any jump host/bastion configuration.
3. **Do any hosts already have the Datadog agent installed?** If so, we can skip install and go straight to SSI verification.

Verify SSH access works by running a quick `ssh <host> "hostname"` for each host before proceeding.

Once you have the host list and SSH details confirmed, **present a plan to the user and wait for their go-ahead before proceeding.** For example:

```
Here's what I'm going to do:
  1. Check pup authentication and detect your Datadog site
  2. Install the Datadog agent with SSI on: <host1>, <host2>, ...
  3. Wait for each agent to appear in Datadog
  4. Discover listening services on each host and tell you what needs restarting
  5. After you restart services, verify instrumentation and traces

Ready to proceed?
```

Don't start Phase 2 until the user confirms.

## Phase 2: Authenticate pup and Detect Site

### Step 1 — Ensure pup is authenticated

Run `pup auth status` and check the JSON response:

```bash
pup auth status
```

- If `"authenticated": true` — extract `site` from the response and proceed to Step 3.
- If not authenticated — proceed to Step 2.

### Step 2 — Log in (no questions needed)

Just run it. The browser will open and the user completes the OAuth flow:

```bash
pup auth login
```

After the user completes the browser flow, run `pup auth status` again and extract `site` from the response.

**Multiple sessions:** If `pup auth list` shows more than one session, show the list and ask the user which org to use. That is the only question to ask — do not ask about the site itself.

### Step 3 — Detect the site automatically

The `site` field in `pup auth status` output is the Datadog site for this session. Use it for all subsequent pup calls and for the agent install script. Do not ask the user what site they're on.

### Step 4 — Get an API key

The agent install script requires an API key (OAuth tokens cannot be used for agent auth).

1. Try `pup api-keys list` — may return 403 or 503 in some orgs, that's okay.
2. If unavailable, ask the user to provide one from the web app: /organization-settings/api-keys

Confirm the key with the user before using it. The API key must belong to the same org as the pup session — the site detected in Step 3 confirms they match.

Store for use throughout the session:
```bash
export DD_API_KEY="<key>"
export DD_SITE="<site from pup auth status>"
```

## Phase 3: Install Agent with APM

For each host that doesn't already have the agent, SSH in and run the install script with SSI enabled:

```bash
ssh <user>@<host> "DD_API_KEY=${DD_API_KEY} DD_SITE=${DD_SITE} DD_APM_INSTRUMENTATION_ENABLED=host bash -c \"\$(curl -L https://install.datadoghq.com/scripts/install_script_agent7.sh)\""
```

Key environment variables for the install script:
- `DD_API_KEY` — required
- `DD_SITE` — required (defaults to datadoghq.com if not set)
- `DD_APM_INSTRUMENTATION_ENABLED=host` — enables SSI for host-level instrumentation

**If the install fails**, check:
- Does the host have internet access? Can it reach `install.datadoghq.com`?
- Does the SSH user have sudo access? The install script requires root.
- Is there a proxy? Set `DD_PROXY` if needed.

## Phase 4: Wait for Agents to Report

For each host, poll until the agent appears in Datadog. Fleet agents list gives you the Datadog-reported hostname and agent status in one call:

```bash
pup fleet agents list --filter "<hostname_or_ip>"
```

Check every 30-60 seconds. Agents typically take 1-2 minutes to appear after install.

When each agent appears, record:
- `hostname` field — the Datadog-reported hostname (may differ from the SSH hostname on cloud instances)
- `rc_status` — should be `connected`
- `instrumentation_status` — initial value; will update after services are restarted

**If `pup fleet agents list` errors**, fall back to:
```bash
pup infrastructure hosts list --filter "<hostname_or_ip>"
# And get the DD hostname via SSH:
ssh <user>@<host> "sudo datadog-agent hostname"
```

**If a host's agent doesn't appear after 5 minutes:**
1. SSH to the host: `sudo systemctl status datadog-agent`
2. Check logs: `sudo journalctl -u datadog-agent --no-pager -n 50`
3. Verify API key: `sudo datadog-agent configcheck 2>&1 | head -20`
4. Check connectivity: `sudo datadog-agent diagnose --include connectivity-datadog-core-endpoints`

**Important:** If you corrected a wrong API key after initial install, update `/etc/datadog-agent/datadog.yaml` and restart the agent on each affected host. Then restart all application services so injection telemetry is sent to the correct org.

## Phase 5: Discover Listening Services

SSI only injects into processes at startup — existing processes are not affected. Before verification can happen, the user's services need to be restarted. This phase discovers what's running on each host so the user knows what to restart.

### Step 1 — Find listening network services

```bash
ssh <user>@<host> "sudo ss -lntp"
```

This shows all listening TCP services with their ports and PIDs. Filter out well-known system services (sshd, systemd, chronyd, etc.) and focus on application-level listeners.

### Step 2 — Determine how each service is managed

For each application PID found, gather context without being invasive:

```bash
ssh <user>@<host> "
# Command line of the process
sudo cat /proc/<pid>/cmdline | tr '\0' ' '

# Check if it's a systemd service
sudo systemctl status <pid> 2>/dev/null | head -3

# Parent process (to detect supervisord, screen, tmux, etc.)
PPID=\$(sudo cat /proc/<pid>/status | grep PPid | awk '{print \$2}')
sudo cat /proc/\$PPID/cmdline | tr '\0' ' '
"
```

**Common management patterns:**
- **systemd** — `systemctl status <pid>` shows a unit name. Note the unit name for the user.
- **supervisord** — parent process is `supervisord`. Config typically in `/etc/supervisor/` or `/etc/supervisord.conf`.
- **Docker** — parent is `containerd-shim` or `docker`. SSI host-mode does not inject into containers — skip these.
- **screen/tmux** — parent is a multiplexer. Process was started manually in a session.
- **Direct/ad-hoc** — parent is a shell (`bash`, `sh`, `zsh`). Started manually with no automatic restart mechanism.

### Step 3 — Present findings and hand off to the user

Present a clear summary of what was found on each host. For example:

```
I found the following application services listening on network ports on <host>:

  Port 8080 — PID 1234 — /usr/bin/python3 /home/ec2-user/app.py
    Managed by: systemd unit flask-app.service

  Port 3000 — PID 5678 — node /app/server.js
    Managed by: supervisord (config likely in /etc/supervisor/)

  Port 8443 — PID 9012 — java -jar /opt/myapp/app.jar
    Managed by: started directly from a shell session (no automatic restart)

These services need to be restarted for Datadog SSI to inject into them.
Restart them however is appropriate for your environment, then let me know
and I'll verify the instrumentation.
```

**Do not offer to restart services. Do not restart services unless the user explicitly asks.** If they do ask, make sure they understand: restarting a production service causes a brief outage, and any service started ad-hoc from a shell session will need to be re-launched manually.

## Phase 6: Verify APM Instrumentation

Once the user has restarted their services, verify injection worked on each host.

**How SSI works:** SSI uses `/etc/ld.so.preload` to load the launcher into every process at startup. The launcher then loads the appropriate language tracer. Injection happens at process startup only — existing processes are not affected.

### Step 1 — Confirm the injector loaded into each process

Check `/proc` directly for the services that were restarted:

```bash
ssh <user>@<host> "sudo cat /proc/<pid>/maps | grep -E 'launcher|apm-library'"
```

- **Launcher present + language library present** — injection succeeded for that process. The tracer is running.
- **Launcher present, no language library** — the launcher ran but couldn't inject. Check the troubleshooting endpoint for the failure reason.
- **Nothing** — `/etc/ld.so.preload` may not be set. Check with `cat /etc/ld.so.preload`.

### Step 2 — Check the agent for reporting services and trace activity

```bash
ssh <user>@<host> "sudo datadog-agent status"
```

Look at the **APM Agent** section:
- `feature_auto_instrumentation_enabled: true` — SSI is active on the agent
- `Receiver (previous minute)` — shows whether any traces have arrived at the agent
- `Endpoints` — confirms where traces are being forwarded

If the receiver is getting traces, the tracer is running and connected.

### Step 3 — Check for injection failures

The troubleshooting endpoint **only reports failures** — successful injections do not appear here. Use it to catch problems, not to confirm success.

```bash
pup apm troubleshooting list --hostname <dd_hostname>
pup apm troubleshooting list --hostname <dd_hostname> --timeframe 4h
```

**Results with `result: error`:**
- `result_reason` contains a customer-facing explanation.
- `result_class` categorizes the failure:
  - `incorrect_installation` — a required file is missing or the package directory is empty/corrupt. **Do not trust `datadog-installer status` here** — it reflects DB registration, not whether files are actually present. Check the directory under `/opt/datadog-packages/datadog-apm-library-<lang>/` manually. If empty or missing, use `sudo datadog-installer remove datadog-apm-library-<lang>` first, then re-run the install script (see Remediation below).
  - `already_instrumented` — process was already instrumented (usually fine).
  - Import/load errors — tracer library couldn't be loaded. Check language compatibility.

**Results with `result: abort`:**
- Injection was intentionally skipped. `result_reason` explains why.
- Common: process matched an exclusion rule, or the language wasn't detected.

### Step 4 — Determine the service name

With SSI, `DD_SERVICE` is never set in the process environment — the tracer determines the service name itself. To find what name it registered, read the tracer's memfd directly from the process.

Each injected process has two anonymous memory files (memfds) accessible via `/proc/<pid>/fd/`:

- **`/memfd:dd_inject_info`** — written by the injector (JSON): `language_name`, `language_version`, `runtime_id`
- **`/memfd:datadog-tracer-info-*`** — written by the tracer (MessagePack): `service_name`, `service_env`, `service_version`, `hostname`, `tracer_version`

To read the service name:

```bash
# Find the tracer memfd fd number
sudo ls -la /proc/<pid>/fd/ | grep "datadog-tracer-info"

# Decode it (msgpack is available since the tracer is running)
sudo cat /proc/<pid>/fd/<fd_num> | python3 -c "import sys,msgpack; d=msgpack.unpackb(sys.stdin.buffer.read()); print(d)"
```

The `service_name` field in the decoded output is what the tracer registered with Datadog.

**Alternative — discover via metrics without SSH:**

If you don't want to SSH in, query the trace metrics grouped by service to discover the name:

```bash
pup metrics query --query "sum:trace.*.request.hits{host:<dd_hostname>} by {service}.as_count()" --from 5m
```

The `scope` field on each returned series will show `host:<dd_hostname>,service:<service_name>`.

### Step 5 — Generate traffic and wait

Traces only appear when requests hit the service. Before generating any traffic, ask the user:

> "To verify tracing is working, I need some requests to hit your services. Is it OK for me to send a small amount of test traffic, or would you prefer to generate it yourself? If it's a production service, generating it yourself might be safer — just let me know when you've sent some requests."

If they give the go-ahead, generate a small number of requests:

```bash
ssh <user>@<host> "for i in \$(seq 1 20); do curl -s http://localhost:<port>/ > /dev/null; done"
```

If they prefer to generate traffic themselves, wait for their confirmation before proceeding.

**After traffic is generated, wait at least 30 seconds before checking anything.** The pipeline has multiple stages:
- Tracer flushes to the trace-agent every ~1 second
- The trace-agent batches and forwards to the backend over the next few seconds
- Trace metrics (`trace.*.hits`) are on a ~10-second stats flush cycle
- Backend indexing adds another 15-30 seconds before `pup traces search` returns results

Do not check immediately after generating traffic. Run a `sleep 30` first.

### Step 6 — Verify activity using trace metrics

Trace metrics appear faster than indexed traces. Check them first:

```bash
pup metrics list --filter "trace." | grep "\.hits"
pup metrics query --query "sum:trace.<operation>.hits{host:<dd_hostname>,service:<service_name>}.as_count()" --from 5m
```

If the query returns data points, the tracer on that host is running and emitting. The `host:` + `service:` combination scopes precisely to this host.

Common operation names: `trace.flask.request`, `trace.django.request`, `trace.servlet.request` (Java), `trace.express.request` (Node), `trace.sinatra.request` (Ruby).

**If metrics are still empty after 30 seconds**, wait another 30 seconds and retry before concluding there's a problem.

### Step 7 — Confirm with trace search

Once metrics show data, verify traces are searchable:

```bash
pup traces search --query "host:<dd_hostname>" --from 5m
```

The `service` field should match what you found in the tracer memfd.

**If metrics have data but traces search returns nothing**, it's still indexing — wait another 30 seconds and retry. Do not start debugging until at least 60 seconds have passed since traffic was generated.

**If the agent receiver shows traffic but metrics are empty after a minute**, the tracer may not be connecting to the agent socket. Enable debug logging by adding `DD_TRACE_DEBUG=true` and `DD_APM_INSTRUMENTATION_DEBUG=true` to the service environment, restart, and check `sudo journalctl -u <service> -n 50` for the agent URL and any connection errors.

### Remediation: Reinstalling a Broken APM Package

**Important caveat about `datadog-installer status`:** The status command reflects what's registered in `packages.db`, not whether the package files are actually present or intact. A package can show as installed while its directory is empty or corrupted. Don't rely on `status` alone to confirm a package is healthy.

**If re-running the install script doesn't fix it**, the package may be registered in `packages.db` but broken on disk (empty directory, partial install, etc.). In that case the installer skips the download because it sees the package as already registered — and reports success misleadingly. The fix is to remove the registration first:

```bash
# Remove the broken package registration
ssh <user>@<host> "sudo datadog-installer remove datadog-apm-library-<lang>"

# Then re-run the install script — now it will properly download and extract
ssh <user>@<host> "sudo DD_API_KEY=${DD_API_KEY} DD_SITE=${DD_SITE} DD_APM_INSTRUMENTATION_ENABLED=host bash -c \"\$(curl -L https://install.datadoghq.com/scripts/install_script_agent7.sh)\""
```

If the package is healthy, just re-running the install script is sufficient (it's idempotent). Only use `remove` first if the install script reports success but the problem persists.

Note: `datadog-installer install <oci-url>` is not a viable manual repair path — package URLs come from Remote Config and can't be guessed or constructed from public registries.

**APM packages managed by the installer** (all under `/opt/datadog-packages/`):
- `datadog-apm-inject` — the launcher (`/etc/ld.so.preload` hook)
- `datadog-apm-library-python`
- `datadog-apm-library-java`
- `datadog-apm-library-ruby`
- `datadog-apm-library-js`
- `datadog-apm-library-dotnet`
- `datadog-apm-library-php`

Check registered packages: `sudo datadog-installer status` (registration only — verify actual files exist under `/opt/datadog-packages/<package>/` if something looks wrong)

### Remediation Loop

If there are errors:
1. Explain what the errors mean based on `result_reason` and `result_class`.
2. Suggest fixes (re-run install script, check language support).
3. Tell the user which services need to be restarted after the fix.
4. After restarts, re-check: `pup apm troubleshooting list --hostname <dd_hostname> --timeframe 15m`
5. Repeat until the user is satisfied.

## Summary Checklist

At the end, present the user with a summary:

```
Host Status:
  <ssh_host_1> (<dd_hostname_1>): Agent ✓ | SSI ✓ | Instrumentation: <status>
    Services instrumented: <list of services/ports>
  <ssh_host_2> (<dd_hostname_2>): Agent ✓ | SSI ✓ | Instrumentation: <status>
    Services instrumented: <list of services/ports>
  ...
```

For each instrumented service, provide a direct link to its APM page:

```
https://app.<site>/apm/services/<service_name>
```

Where `<site>` comes from `pup auth status` and `<service_name>` was determined in Phase 6 Step 4. If the service has an env tag, append `?env=<env>`.

Suggest next steps:
- View traces: `pup traces search --query "service:<service_name>" --from 1h`
- View APM services: `pup apm services list --env <env> --from 1h`
- Set up monitors for the new services

## Troubleshooting: pup Commands That May Return Errors

Some `pup` commands may return errors depending on org configuration:
- `pup fleet agents list` — always use `--filter "<hostname>"`. Without a filter it will time out (504). If it errors with a filter, fall back to `pup infrastructure hosts list --filter "<hostname>"`.
- `pup api-keys list` — may return 403 or 503 in some orgs; direct the user to the web UI at /organization-settings/api-keys.
- `pup apm services list` — may lag behind trace ingestion; empty results don't mean instrumentation failed. Use `pup traces search` for immediate confirmation.
