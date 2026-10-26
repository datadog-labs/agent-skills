---
name: datadog-app
description: Guides developers building Datadog Apps with TypeScript, React, and the @datadog/apps scaffolder and CLI. Use when a user wants to scaffold, run, debug, upgrade, build, upload, or publish a Datadog App, set up CI/CD, use OAuth or API/application key auth, trigger or poll Workflow Automation from a backend function, choose DDSQL or Action Catalog for backend data access, or query app datastores with DDSQL, including backend function troubleshooting.
---

# Datadog Apps

This skill is a pointer. The guidance itself is served by Datadog, so it stays current without reinstalling this skill.

## Fetch the guidance first

```
https://api.<site>/api/unstable/app-builder-code/agents/
```

Resolve `<site>` in this order, and use the first one that applies:

1. `datadogSite` in the app project's `datadog-app.config.json`, when working in an existing app.
2. The `DD_SITE` environment variable.
3. `datadoghq.com`.

The response is a short index: a routing table naming each guidance file and when to load it, plus the boundaries to work within. Load only the files the current task needs. Every row carries a complete URL, so fetch the URL in the row rather than guessing a path.

No credentials are required to fetch it.

## If the fetch fails

Tell the user the hosted Datadog Apps guidance is unavailable, and stop.

Do not build a Datadog App from memory and do not substitute guidance from training data. The scaffolder, the CLI, and their commands change, and confidently wrong instructions cost more than no instructions. A user who cannot reach this endpoint also cannot reach the npm registry or the upload API, so there is no offline path worth describing.

## Boundaries

- The app project outranks anything fetched here. Read its `AGENTS.md` and its `docs/agents/` guides, and prefer its `package.json` scripts and `datadog-app.config.json` over any remembered defaults.
- `npm run upload` publishes the app live to users. Confirm intent before running it.
