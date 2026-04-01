---
name: dd-monitors
description: Monitor management - list, search, file-based create, and alerting best practices.
metadata:
  version: "1.0.1"
  author: datadog-labs
  repository: https://github.com/datadog-labs/agent-skills
  tags: datadog,monitors,alerting,alerts,dd-monitors
  globs: "**/datadog*.yaml,**/*monitor*"
  alwaysApply: "false"
---

# Datadog Monitors

Create, manage, and maintain monitors for alerting.


## Prerequisites
This requires pup in your path. See [Setup Pup](https://github.com/datadog-labs/agent-skills/tree/main?tab=readme-ov-file#setup-pup).

## Command Execution Order (Token-Efficient)

For scoped commands, use this order:

1. Check context first (prior outputs, conversation, saved values).
2. If a required value is missing, run a discovery command first.
3. If still ambiguous, ask the user to confirm.
4. Then run the target command.
5. Avoid speculative commands likely to fail.


## Quick Start

```bash
pup auth login
```

## Common Operations

### List Monitors

```bash
pup monitors list
pup monitors list --tags "team:platform"
```

### Get Monitor

```bash
pup monitors get <id>
```

### Create Monitor

```bash
pup monitors create --file monitor.json
```

### Silence Alerts (Downtime)

```bash
# No pup monitors mute/unmute commands.
# Use downtime payloads to silence monitor notifications.
pup downtime create --file downtime.json
pup downtime cancel <downtime_id>
```

## ⚠️ Monitor Creation Best Practices

### 1. Avoid Alert Fatigue

| Rule | Why |
|------|-----|
| **No flapping alerts** | Use `last_Xm` not `last_1m` |
| **Meaningful thresholds** | Based on SLOs, not guesses |
| **Actionable alerts** | If no action needed, don't alert |
| **Include runbook** | `@runbook-url` in message |

```python
# WRONG - will flap constantly
query = "avg(last_1m):avg:system.cpu.user{*} > 50"  # ❌ Too sensitive

# CORRECT - stable alerting
query = "avg(last_5m):avg:system.cpu.user{env:prod} by {host} > 80"  # ✅ Reasonable window
```

### 2. Use Proper Scoping

```python
# WRONG - alerts on everything
query = "avg(last_5m):avg:system.cpu.user{*} > 80"  # ❌ No scope

# CORRECT - scoped to what matters
query = "avg(last_5m):avg:system.cpu.user{env:prod,service:api} by {host} > 80"  # ✅
```

### 3. Set Recovery Thresholds

```python
monitor = {
    "query": "avg(last_5m):avg:system.cpu.user{env:prod} > 80",
    "options": {
        "thresholds": {
            "critical": 80,
            "critical_recovery": 70,  # ✅ Prevents flapping
            "warning": 60,
            "warning_recovery": 50
        }
    }
}
```

### 4. Include Context in Messages

```python
message = """
## High CPU Alert

Host: {{host.name}}
Current Value: {{value}}
Threshold: {{threshold}}

### Runbook
1. Check top processes: `ssh {{host.name}} 'top -bn1 | head -20'`
2. Check recent deploys
3. Scale if needed

@slack-ops @pagerduty-oncall
"""
```

## ⚠️ NEVER Delete Monitors Directly

Use safe deletion workflow (same as dashboards):

```python
def safe_mark_monitor_for_deletion(monitor_id: str, client) -> bool:
    """Mark monitor instead of deleting."""
    monitor = client.get_monitor(monitor_id)
    name = monitor.get("name", "")
    
    if "[MARKED FOR DELETION]" in name:
        print(f"Already marked: {name}")
        return False
    
    new_name = f"[MARKED FOR DELETION] {name}"
    client.update_monitor(monitor_id, {"name": new_name})
    print(f"✓ Marked: {new_name}")
    return True
```

## Monitor Types

| Type | Use Case |
|------|----------|
| `metric alert` | CPU, memory, custom metrics |
| `query alert` | Complex metric queries |
| `service check` | Agent check status |
| `event alert` | Event stream patterns |
| `log alert` | Log pattern matching |
| `composite` | Combine multiple monitors |
| `apm` | APM metrics |

## Audit Monitors

```bash
# Find monitors without owners
pup monitors list | jq '.[] | select(.tags | contains(["team:"]) | not) | {id, name}'

# Find noisy monitors (high alert count)
pup monitors list | jq 'sort_by(.overall_state_modified) | .[:10] | .[] | {id, name, status: .overall_state}'
```

## Downtime vs Muting

| Use | When |
|-----|------|
| **Downtime** | Any planned silence window |
| **Monitor edit** | Query/threshold behavior changes |

```bash
# Downtime (preferred)
pup downtime create --file downtime.json
```

## Failure Handling

| Problem | Fix |
|---------|-----|
| Alert not firing | Check query returns data, thresholds |
| Too many alerts | Increase window, add recovery threshold |
| No data alerts | Check agent connectivity, metric exists |
| Auth error | `pup auth refresh` |

## References

- [Monitor Types](https://docs.datadoghq.com/monitors/types/)
- [Alerting Best Practices](https://docs.datadoghq.com/monitors/guide/)
- [SLO Monitors](https://docs.datadoghq.com/service_management/service_level_objectives/)
