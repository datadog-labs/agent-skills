---
name: agent-skills
description: Datadog skills for AI agents. Essential monitoring, logging, tracing and observability.
metadata:
  version: "1.0.2"
---

# Datadog Skills

Essential Datadog skills for AI agents.

## Core Skills

| Skill | Description |
|-------|-------------|
| **dd-pup** | Primary CLI - all pup commands, auth, PATH setup |
| **dd-monitors** | Create, manage, mute monitors and alerts |
| **dd-logs** | Search logs, pipelines, archives |
| **dd-apm** | Traces, services, performance analysis |
| **dd-docs** | Search Datadog documentation |
| **dd-llmo** | LLM Observability traces, experiments, evals |

## Install

```bash
# Install core skills
npx skills add datadog-labs/agent-skills \
  --skill dd-pup \
  --skill dd-monitors \
  --skill dd-logs \
  --skill dd-apm \
  --skill dd-docs \
  --full-depth -y
```

## Prerequisites

See [Setup Pup](https://github.com/datadog-labs/agent-skills/tree/main?tab=readme-ov-file#setup-pup) for installation and authentication.

## Command Execution Policy

Use this order for scoped commands:

1. Check context first (conversation, prior outputs, known values).
2. Run discovery commands when required values are missing.
3. Ask the user only when values remain ambiguous.
4. Run the target command after required inputs are known.
5. Avoid speculative commands likely to fail.

## Quick Reference

| Task | Command |
|------|---------|
| Search error logs | `pup logs search --query "status:error" --from 1h` |
| List monitors | `pup monitors list` |
| Schedule monitor downtime | `pup downtime create --file downtime.json` |
| Find slow traces | `pup traces search --query "service:api @duration:>500ms" --from 1h` |
| Query metrics | `pup metrics query --query "avg:system.cpu.user{*}"` |
| Check auth | `pup auth status` |
| Refresh token | `pup auth refresh` |

## Auth

```bash
pup auth login          # OAuth2 (recommended)
pup auth status         # Check token
pup auth refresh        # Refresh expired token
```

**⚠️ Token Expiry**: OAuth tokens expire (~1 hour). Run `pup auth refresh` if commands fail with 401/403.

## More Skills

Additional skills available shortly.

```bash
npx skills add datadog-labs/agent-skills --list --full-depth
```
