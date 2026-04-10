---
name: dd-apm-linux-onboarding-summary
description: Generate a live APM onboarding confirmation report for Linux hosts with deep links into the Datadog UI. Collects real data from the host and Datadog backend to confirm everything is working end-to-end.
metadata:
  version: "1.0.0"
  author: datadog-labs
  repository: https://github.com/datadog-labs/agent-skills
  tags: datadog,apm,linux,ssi,summary,verification
  alwaysApply: "false"
---

# APM Onboarding Summary — Linux Host

## Triggers

Invoke this skill when:
- All steps in `verify-ssi` have passed
- All checks in `troubleshoot-ssi` have been resolved
- The user asks "is everything working?", "show me the status", or "confirm APM is set up"

Do NOT invoke this skill if any verification or troubleshooting check is still failing — resolve those first.

---

## Context to resolve before acting

| Variable | How to resolve |
|---|---|
| `HOSTNAME` | `hostname -f` on the target host |
| `DD_HOSTNAME` | Hostname as Datadog sees it — from `sudo datadog-agent status` |
| `SERVICE_NAME` | `DD_SERVICE` value from `/proc/<PID>/environ` or the systemd unit |
| `ENV` | `DD_ENV` value from `/proc/<PID>/environ` or the systemd unit |
| `DD_SITE` | `grep "^site:" /etc/datadog-agent/datadog.yaml` |
| `SSH_KEY` | Path to SSH private key |
| `SSH_USER` | SSH username |
| `SSH_HOST` | Hostname or IP of the target host |

---

## Prerequisites

### Claude runs

```bash
pup auth status --site <DD_SITE>
```

✅ Valid token — proceed.

❌ Not authenticated:

### What you need to do in a terminal

```bash
pup auth login --site <DD_SITE>
```

Confirm with `pup auth status --site <DD_SITE>` before continuing.

---

## Collect live confirmation data

Run all of the following. Each populates a row in the final report.

### Claude runs

```bash
# Agent version and status
sudo datadog-agent status 2>&1 | grep -E "Agent \(v|Status:|API Keys status"

# Inject library armed in ld.so.preload
ssh -o StrictHostKeyChecking=no -i <SSH_KEY> <SSH_USER>@<SSH_HOST> "cat /etc/ld.so.preload"

# Process confirmed injected — launcher + language library in /proc/<PID>/maps
ssh -o StrictHostKeyChecking=no -i <SSH_KEY> <SSH_USER>@<SSH_HOST> \
  "pgrep -a -f '<SERVICE_NAME>' | head -3"
```

Use the PID from above:

```bash
ssh -o StrictHostKeyChecking=no -i <SSH_KEY> <SSH_USER>@<SSH_HOST> \
  "sudo cat /proc/<PID>/maps | grep -E 'launcher|apm-library|datadog'"

# UST vars in process environment
ssh -o StrictHostKeyChecking=no -i <SSH_KEY> <SSH_USER>@<SSH_HOST> \
  "sudo cat /proc/<PID>/environ | tr '\0' '\n' | grep -E 'DD_SERVICE|DD_ENV|DD_VERSION'"

# Agent APM receiver — trace counts
ssh -o StrictHostKeyChecking=no -i <SSH_KEY> <SSH_USER>@<SSH_HOST> \
  "sudo datadog-agent status 2>&1 | grep -A 10 'Receiver (previous minute)'"

# Service visible and traced in APM backend
DD_SITE=<DD_SITE> pup apm services list --env <ENV> --from 1h

# Traces arriving in the last hour
DD_SITE=<DD_SITE> pup traces search --query "service:<SERVICE_NAME>" --from 1h --limit 5
```

---

## Present the report

Fill in every value from live command output. Do not leave any placeholder unfilled. If a value cannot be confirmed, mark that row ❌ and link to `troubleshoot-ssi`.

---

**APM onboarding complete**

| Check | Detail | Status |
|---|---|---|
| Datadog Agent | v`<VERSION>` running on `<HOSTNAME>`, API key valid | ✅ |
| SSI armed | `/etc/ld.so.preload` contains launcher path | ✅ |
| Process injected | launcher + language library in `/proc/<PID>/maps` for `<SERVICE_NAME>` | ✅ |
| Unified Service Tags | `DD_SERVICE=<SERVICE_NAME>` `DD_ENV=<ENV>` `DD_VERSION=<VERSION>` | ✅ |
| Agent receiving traces | `<N>` trace(s)/min in APM receiver | ✅ |
| APM service visible | `<SERVICE_NAME>` in env `<ENV>` | ✅ |
| Traces arriving | `<N>` trace(s) found in the last hour | ✅ |

---

**Your service in Datadog — click to open:**

Construct each URL by substituting real values. Do not print placeholder URLs.

| View | URL |
|---|---|
| Service overview | `https://app.<DD_SITE>/apm/services/<SERVICE_NAME>?env=<ENV>` |
| Traces explorer | `https://app.<DD_SITE>/apm/traces?query=service:<SERVICE_NAME>%20env:<ENV>` |
| Service map | `https://app.<DD_SITE>/apm/map?env=<ENV>&service=<SERVICE_NAME>` |
| Infrastructure host | `https://app.<DD_SITE>/infrastructure?q=host:<HOSTNAME>` |
| Agent fleet | `https://app.<DD_SITE>/fleet-automation` |

---

## Security constraints

- Never write a raw API key into any file or chat message
