---
name: dd-instrument-rum
description: Instrument browser-based web applications with Datadog Browser RUM. Detect the application framework, router, package manager, bundler, entrypoint, credentials, and existing RUM setup; add or safely complete classic Browser RUM instrumentation for React, Next.js App or Pages Router, Angular, Vue, Nuxt, Svelte, vanilla JavaScript, SPAs, and iframe-hosted apps; avoid duplicate initialization; and verify the application still builds. Use when asked to add, set up, instrument, repair, or verify Datadog RUM, Browser Monitoring, Session Replay, or framework-specific Browser RUM plugins.
metadata:
  version: "0.1.0"
  author: datadog-labs
  repository: https://github.com/datadog-labs/agent-skills
  tags: datadog,rum,browser-rum,instrumentation,session-replay,react,nextjs,angular,vue,nuxt,svelte
  alwaysApply: "false"
---

# Datadog Browser RUM instrumentation

Instrument only the browser application in scope. Do not add Datadog APM, tracing, LLM Observability, Logs, source-map upload, user identification, Vercel metadata, Shopify, Salesforce, or unrelated Datadog products.

Use normal file inspection, editing, and command tools for every step except optional RUM application creation. Use only `CreateRumApplication` for that operation, as described in `references/common-credentials.md`; never invent Datadog tool names.

## Ground rules

- Inspect before editing. Base every framework, version, entrypoint, command, and configuration decision on project files.
- Copy package names, import paths, exported symbols, and init option keys exactly from the applicable references. Do not substitute package aliases or recreate SDK APIs from memory.
- Post a short checklist before making changes and update it as work completes.
- Initialize RUM exactly once and as early as safely possible in the browser lifecycle. Never put `init()` in a component render, lifecycle hook, route handler, or repeated callback.
- Preserve an existing valid init configuration. Add missing compatible framework plugin wiring to that init in place; never create a competing init.
- **Existing-init credential guardrail:** when one valid RUM init already exists, treat its `applicationId`, `clientToken`, `site`, service tags, sampling, privacy, tracking, and every other existing option as immutable. Credentials supplied by the task are for a new init only; they are not an override for an existing valid init. In this branch, do not rewrite credential lines while adding plugin wiring.
- Stop without editing when required credentials cannot be resolved, `site` is invalid, framework/plugin prerequisites are unmet, permissions prevent the work, or multiple conflicting init calls cannot be safely consolidated.
- Install only packages required for Browser RUM. Keep `@datadog/browser-rum` and every `@datadog/browser-rum-*` integration package on the same exact SDK version.
- Persist dependencies in the manifest and lockfile used by the real build. Do not rely on packages that happen to exist in `node_modules`.
- Preserve application behavior, existing custom error handling, formatting conventions, and unrelated code.
- Apply edits with the available file-editing tool. If an expected text match fails, re-read the file and adapt to its current contents instead of retrying the same edit.
- Do not write project paths, framework details, credentials, client tokens, or RUM application IDs to persistent memory.
- Run a terminating build before reporting success. Never claim telemetry was received unless it was actually observed.

## Phase 1: analyze the target

### Locate the browser application

Identify the project root that produces browser code. In a monorepo, inspect workspace configuration, scripts, Dockerfiles, and CI/deployment configuration to find the frontend manifest used by the real build rather than editing the repository root by assumption. Record that manifest path and its package manager before editing. If several independent browser applications are plausible and the user did not select one, ask which application to instrument.

Reject non-browser applications such as Ink CLIs and projects with no HTML/browser build target.

### Detect the framework and runtime shape

Inspect dependencies and source layout in this order so meta-frameworks win over their underlying UI library:

1. `nuxt` -> Nuxt
2. `next` -> Next.js; distinguish App Router from Pages Router by source layout
3. `@angular/core` -> Angular; distinguish standalone bootstrap from NgModule
4. `vue` -> Vue
5. `svelte` or `@sveltejs/kit` -> Svelte or SvelteKit
6. `react` -> React
7. Browser entrypoint without the above -> generic/vanilla browser application

Also detect:

- Exact framework, React Router, TanStack Router, Vue Router, and Node versions.
- TypeScript when a `tsconfig.json` or TypeScript dependency is present; otherwise preserve JavaScript.
- Package manager from the deployed manifest's `packageManager` field first, then its adjacent lockfile: `bun.lock` or `bun.lockb`, `pnpm-lock.yaml`, `yarn.lock`, or `package-lock.json`. Use npm only when no stronger project signal exists, and never mix managers.
- Bundler from configuration and dependencies: Vite, Webpack, Rollup, esbuild, Rspack, or Create React App (`react-scripts`). Treat unmanaged HTML as CDN only when no package/bundler build owns it.
- Browser entrypoint from build configuration, HTML script targets, framework conventions, and the import graph rather than filename alone.
- The normal terminating build/typecheck command.
- Existing public environment-variable conventions.

Read `references/rum-core.md`, then read exactly the applicable framework reference:

| Target | Reference |
|---|---|
| React, React Router, TanStack Router | `references/rum-react.md` |
| Next.js App or Pages Router | `references/rum-nextjs.md` |
| Angular | `references/rum-angular.md` |
| Vue | `references/rum-vue.md` |
| Nuxt | `references/rum-nuxt.md` |
| Svelte, vanilla, generic SPA, iframe-hosted app | `references/rum-other-frameworks.md` |

Validate every prerequisite in the selected reference before provisioning credentials or editing. Do not upgrade a framework and do not silently fall back to core-only RUM when a detected framework plugin is incompatible.

### Detect existing RUM instrumentation

Search source, HTML, and configuration files while excluding dependency, build, generated, and coverage directories such as `node_modules`, `dist`, `build`, `.next`, `.nuxt`, and `coverage`.

Check for:

- Imports or requires from `@datadog/browser-rum`, including renamed and namespace bindings.
- Imports from any `@datadog/browser-rum-*` integration package.
- Calls bound to the imported `datadogRum.init`, including aliases and local wrapper modules.
- `DD_RUM.init`, `window.DD_RUM.init`, `DD_RUM.onReady`, and Datadog CDN loader URLs.
- Existing plugin registrations, router wrappers/providers/components, and framework error hooks.

Treat package presence alone as insufficient: a dependency may be unused.

- **No init:** add one using the selected reference.
- **One valid init:** keep its location and all existing values. Do not resolve or apply replacement credentials. Add only missing compatible plugin/router/error wiring to it.
- **One init with missing or invalid required credentials/site:** stop and report the invalid configuration; do not layer another init over it.
- **Multiple init calls or mixed npm/CDN setups:** stop and report every location unless they can be proven to be one mutually exclusive setup. Do not guess which setup should win.
- **Complete setup:** make no changes; still run the applicable verification.

## Phase 2: instrument

Resolve credentials through `references/common-credentials.md` only after analysis succeeds. If analysis found one valid existing init, reuse its existing credentials and skip credential replacement entirely. Supplied task credentials may be used only when creating a new init. Then:

1. Install the core SDK and compatible framework package(s) with the detected package manager, using the deployed frontend manifest. For an existing valid init, install only a missing integration package; never replace already-valid RUM packages or configuration.
2. Apply the canonical configuration and install method from `references/rum-core.md`. For an existing valid init, apply only missing compatible fields to that same object; do not copy credential or canonical-option placeholders over existing values.
3. Apply the selected framework reference, including router tracking and safe framework error integration when those surfaces exist.
4. Update the lockfile with the same package manager. Never hand-edit a generated lockfile.
5. Detect configured formatting from manifest scripts such as `lint:fix`, `fix`, or `format` and from ESLint/Prettier configuration. Run the project's formatter or fix command only for files changed by this work.

Do not add `allowedTracingUrls` or any backend/CORS/tracing configuration; those belong to an APM onboarding workflow.

## Phase 3: verify and report

Follow `references/common-verify-report.md`. Before reporting success, inspect the final diff against the pre-edit project. When an existing valid init was found, verify that every pre-existing init value—including both credentials—remains byte-for-byte unchanged and that the only setup changes are the required missing integration wiring (plus any strictly necessary dependency/lockfile change). If an existing value changed, restore it before reporting success. Do not report success unless required packages are persisted, the resulting setup contains exactly one init, and the normal build succeeds.
