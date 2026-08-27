# Core-only browser frameworks

Read `rum-core.md` first. These targets use `@datadog/browser-rum` without a framework integration package.

## Svelte and SvelteKit

For a client-only Svelte/Vite application, initialize at module scope in the browser entry before mounting the root component.

For SvelteKit, keep initialization out of server execution. Create a small module guarded by `$app/environment` and import it once from the root layout:

```ts
import { browser } from '$app/environment'
import { datadogRum } from '@datadog/browser-rum'

if (browser) {
  datadogRum.init({
    // ...canonical fields from rum-core.md...
  })
}
```

Import that module once from the root `+layout.svelte` script. Do not call init from `onMount`, a component render, or both server and browser contexts. If the project has no package manager/bundler and only serves generated HTML, use the CDN path instead.

## Vanilla or generic SPA with a bundler

Install `@datadog/browser-rum` and put the canonical init at module scope in the earliest browser entry referenced by the HTML or bundler configuration. Do not create a React/Vue-style plugin abstraction for a frameworkless app.

## Unmanaged vanilla HTML

When there is no package manifest or bundler, add the async CDN loader from `rum-core.md` near the start of `<head>` in every HTML entry page. Do not create `package.json`, add imports the browser cannot resolve, or introduce a build system.

## SPA routing without a supported Datadog framework plugin

Classic RUM automatically observes URL changes, but it cannot infer every application's parameterized route names. Do not add manual `startView` behavior as part of basic onboarding unless the user explicitly requests custom view naming and provides the route semantics.

## Iframe-hosted applications

Treat an application that runs inside an iframe as its own browser application and initialize RUM once inside that application's document/bundle. A host page and an embedded application are separate instrumentation targets.

- If the target repository owns only the iframe application, instrument only that application.
- If it owns only a host that embeds a third-party or cross-origin iframe, do not inject code into content it does not control.
- If it owns both host and iframe applications, do not initialize both under one ambiguous request; ask which surface or surfaces need separate RUM applications/configuration.
- Do not add parent/child cross-window messaging or specialized Shopify/Salesforce bundles.
