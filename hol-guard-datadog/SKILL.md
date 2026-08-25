---
name: hol-guard-datadog
description: Protect agent-driven Datadog changes with HOL Guard before running mutating pup or Datadog workflows.
metadata:
  version: "0.1.0"
  author: hashgraph-online
  repository: https://github.com/hashgraph-online/hol-guard
  tags: datadog,security,hol-guard,agent-safety,pup
  alwaysApply: "false"
---

# HOL Guard for Datadog agent workflows

Use this skill when an AI coding agent is about to make Datadog changes through `pup`, a Datadog skill, or another local command-driven workflow and the user wants a local policy and approval boundary before tool execution.

HOL Guard protects the supported local agent harness. It does not run inside Datadog, replace Datadog RBAC or API scopes, or replace Datadog-native previews and confirmation steps.

## Set up the protected harness

Install HOL Guard in an isolated CLI environment if it is not already available:

```bash
pipx install hol-guard
hol-guard status
hol-guard detect --json
```

Use the exact supported harness identifier returned by `hol-guard detect --json`:

```bash
hol-guard install <harness>
hol-guard run <harness> --dry-run
hol-guard run <harness>
hol-guard doctor <harness> --json
```

Do not claim that the current agent session is protected merely because the CLI is installed. Perform Datadog mutations from the Guard-launched protected harness and keep the existing Datadog skill's authentication, targeting, preview, and confirmation rules intact.

## Before a Datadog mutation

For write, delete, deploy, upload, publish, mute, downtime, or other state-changing Datadog work:

1. Confirm HOL Guard status for the supported local harness.
2. Preserve the target Datadog site, organization, environment, and resource scope from the Datadog skill being used.
3. Use Datadog-native read/preview/dry-run behavior where the underlying workflow provides it.
4. If HOL Guard blocks or requests review, stop before the Datadog mutation and inspect the request.
5. Execute the Datadog action only from the protected harness after the Guard path permits it.

Never weaken Datadog permissions or bypass a Guard review just to make a command succeed.

## Review and evidence

When Guard stops or reviews work:

```bash
hol-guard approvals
hol-guard approvals open
hol-guard receipts
hol-guard diff <harness>
```

For troubleshooting and handoff evidence:

```bash
hol-guard status
hol-guard doctor <harness> --json
hol-guard receipts
hol-guard events
```

Only approve a queued request after checking both the Guard reason and the Datadog target/scope. A Guard receipt is evidence about the local agent execution boundary; it is not a substitute for Datadog Audit Trail or other Datadog-native audit records.

## Package and skill verification

HOL Guard's package scanner is a separate CLI. Use it when reviewing an Agent Skill, plugin, or MCP package before trust:

```bash
pipx install plugin-scanner
plugin-scanner lint <path>
plugin-scanner verify <path>
```

Do not describe a clean package scan as proof that a future Datadog mutation is authorized or safe. Runtime protection and package verification are separate checks.

## References

- HOL Guard: https://github.com/hashgraph-online/hol-guard
- HOL Guard Agent Skill: https://github.com/hashgraph-online/hol-guard-plugin/tree/main/skills/hol-guard
- Datadog Agent Skills: https://github.com/datadog-labs/agent-skills
