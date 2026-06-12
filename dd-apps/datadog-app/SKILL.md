---
name: datadog-app
description: Guides developers building Datadog Apps with TypeScript, React, the @datadog/apps scaffolder, and @datadog/vite-plugin. Use when a user wants to scaffold, run, debug, upgrade, build, deploy, publish, deploy without publishing (draft upload), publish a specific version, set up OAuth or API key authentication, set up CI/CD, trigger/poll Workflow Automation, choose DDSQL or Action Catalog for backend data access, or query app datastores with DDSQL, including backend function and auth troubleshooting.
---

# Datadog Apps

Use this skill when a developer is building a Datadog Apps project with TypeScript, React, published packages, and the normal production Datadog site. If the user is modifying Datadog platform packages or testing package source changes, use a platform-engineer-oriented workflow instead.

## Overview

Datadog Apps are locally developed web apps built with React and TypeScript or JavaScript. Use Apps when a project needs source control, code review, CI/CD, multi-engineer collaboration, AI-assisted local development, custom UI or logic, or backend code that integrates with services beyond low-code App Builder. Apps share App Builder's permissions model and can be embedded in Datadog surfaces such as dashboards and the Internal Developer Portal.

## Reference Routing

Read only the reference needed for the user's task:

| User task | Read |
| --- | --- |
| Create, scaffold, configure prerequisites, set up OAuth or API key auth, or run locally | `references/getting-started.md` |
| Build, deploy, publish, deploy without publishing, publish a specific version, configure Datadog site, or understand deploy output | `references/build-deploy-publish.md` |
| Add or update GitHub Actions deployment | `references/cicd.md` |
| Trigger or poll Workflow Automation from a backend function | `references/workflow-http-trigger.md` |
| Get started querying data from a Datadog App | `references/querying-data/getting-started.md` |
| Understand or configure Action Catalog connections | `references/querying-data/connections.md` |
| Query Datadog App datastores with DDSQL | `references/querying-data/ddsql/datastores.md` |
| Upgrade Datadog Apps dependencies or compare with a freshly scaffolded app | `references/upgrading.md` |
| Diagnose auth, OAuth, deploy, Node, site, backend function failures, or `.env.local` credentials not being picked up | `references/troubleshooting.md` |

## Boundaries

- After scaffolding or when working inside an existing app, read the app project's `AGENTS.md` before making changes.
- For backend function implementation details, rely on the generated app project's `AGENTS.md`; this skill only covers local development credentials and troubleshooting.
- Preserve the app project's existing package manager, scripts, Datadog site, and repository conventions.
- Do not cover Datadog package/platform development in this skill.
- Low-code App Builder to Datadog Apps migration guidance is future work. Do not invent a migration process yet.
