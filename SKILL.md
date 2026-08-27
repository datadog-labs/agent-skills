---
name: agent-skills
description: Datadog skills for AI agents. Essential monitoring, logging, tracing and observability.
metadata:
  version: "1.0.3"
---

# Datadog Skills

Essential Datadog skills for AI agents.

## Core Skills

| Skill | Description |
|-------|-------------|
| **dd-account-setup** | Ensure an authenticated Datadog account with a valid API key on the right region; validates keys, fixes wrong-region 403s, signs in or creates an account |
| **dd-apm** | Traces, services, performance analysis |
| **dd-apps**              | Build Datadog Apps — scaffold, run, upload, publish, CI/CD |
| **dd-aws-integration** | Connect an AWS account to Datadog with Terraform - cross-account IAM role, metrics and resource collection |
| **dd-azure-integration** | Connect Azure subscriptions or management groups to Datadog with Terraform - Entra app registration, Monitoring Reader |
| **dd-browser-sdk** | Browser SDK setup, RUM, Logs, Session Replay, version migration |
| **dd-docs** | Search Datadog documentation |
| **dd-gcp-integration** | Connect GCP projects or folders to Datadog with Terraform - keyless service-account impersonation |
| **dd-llmo** | LLM Observability traces, experiments, evals |
| **dd-logs** | Search logs, pipelines, archives |
| **dd-monitors** | Create, manage, mute monitors and alerts |
| **dd-oci-integration** | Connect an Oracle Cloud tenancy to Datadog with Terraform - Datadog's official OCI module |
| **dd-product-recommender** | Recommend the right Datadog products for a codebase and/or goal (recommendation only) |
| **dd-pup** | Primary CLI - all pup commands, auth, PATH setup |
| **dd-software-delivery** | CI/CD workflow skills — unblock PR, triage flaky tests |
| **dd-instrument-rum** | Instrument browser apps with Datadog Browser RUM — React, Next.js, Angular, Vue, Nuxt, Svelte, vanilla |

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

# Install CI/CD workflow skills
npx skills add datadog-labs/agent-skills \
  --skill dd-software-delivery/unblock-pr \
  --skill dd-software-delivery/triage-flaky-test \
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

**Token Expiry**: OAuth tokens expire (~1 hour). Run `pup auth refresh` if commands fail with 401/403.

## More Skills

Additional skills available shortly.

```bash
npx skills add datadog-labs/agent-skills --list --full-depth
```
