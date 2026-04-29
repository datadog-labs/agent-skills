---
name: dd-browser-sdk
description: >
  Datadog Browser SDK — RUM, Logs, Session Replay, profiling, product analytics, and error tracking
  setup, configuration, and migration. Use when upgrading Browser SDK versions, setting up RUM or
  Logs, or troubleshooting browser-side Datadog instrumentation.
metadata:
  version: "1.0.0"
  author: datadog-labs
  repository: https://github.com/datadog-labs/agent-skills
  tags: datadog,browser-sdk,rum,logs,session-replay,profiling,product-analytics,error-tracking,cdn,npm,migration
  globs: "**/@datadog/browser-*,**/datadog-rum*,**/datadog-logs*"
---

# Datadog Browser SDK

RUM, Logs, and Session Replay instrumentation for browser applications.

## Skills

| Task | Skill |
|------|-------|
| Upgrade from v6 to v7 | `dd-browser-sdk/upgrade-v7` |

## Routing

**Upgrading from v6 to v7** (removed options like `betaEncodeCookieOptions`, `allowFallbackToLocalStorage`, `trackBfcacheViews`, `usePciIntake`, or `/v6/` CDN paths):

**Immediately read** `.claude/skills/dd-browser-sdk/upgrade-v7/SKILL.md` — do not proceed from memory.
