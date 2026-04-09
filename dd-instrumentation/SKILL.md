---
name: dd-onboarding-apm
description: >
  Onboard and troubleshoot Datadog APM on Linux hosts and Kubernetes clusters
  using Single Step Instrumentation (SSI). Use when the user wants to set up APM,
  instrument services, enable tracing, or diagnose why traces aren't appearing.
  Trigger on: "set up APM", "instrument my hosts", "install datadog agent",
  "onboard to APM", "set up tracing", "troubleshoot instrumentation",
  "why isn't APM working", "no traces", "missing services", SSI, injection errors.
metadata:
  version: "1.0.0"
  author: datadog-labs
  repository: https://github.com/datadog-labs/agent-skills
  tags: apm, onboarding, ssi, instrumentation, troubleshooting, kubernetes, linux
  globs: "**/datadog*.yaml,**/ddtrace*,**/helm*"
  alwaysApply: "false"
---

# Datadog APM Onboarding & Troubleshooting

Determine what the user needs, then read ONLY the relevant file:

## Onboarding
The user wants to **set up** Datadog APM on their infrastructure (install agent, enable SSI, instrument services).
→ Read [onboarding.md](onboarding.md) for full instructions.

## Troubleshooting
The user has APM set up but something **isn't working** (no traces, missing services, injection errors, wrong service names).
→ Read [troubleshooting.md](troubleshooting.md) for diagnostic tools and system model.

**Do not read both files.** Pick the one that matches the user's intent.
