# Classic Browser RUM setup

Read this file for every target, then apply the framework-specific reference selected by `SKILL.md`.

## Install method

Prefer the npm package for projects with a package manager and browser bundler:

```sh
npm install --save @datadog/browser-rum
```

Use the detected equivalent (`yarn add`, `pnpm add`, or `bun add`). Install the framework integration package in the same command so all Datadog Browser packages resolve to the same version. If `@datadog/browser-rum` already exists, add the integration package at that same exact version.

Use the CDN only for unmanaged HTML without a package manager/bundler. Do not introduce a package manager or bundler just for RUM.

## Canonical npm configuration

Initialize at module scope in the earliest browser-only entrypoint:

```js
import { datadogRum } from '@datadog/browser-rum'

datadogRum.init({
  applicationId: '<APPLICATION_ID>',
  clientToken: '<CLIENT_TOKEN>',
  site: '<DATADOG_SITE>',
  service: '<SERVICE_NAME>',
  env: '<ENVIRONMENT>',
  version: '<VERSION>',
  sessionSampleRate: 100,
  sessionReplaySampleRate: 20,
  defaultPrivacyLevel: 'mask-user-input',
  trackUserInteractions: true,
  trackResources: true,
  trackLongTasks: true,
})
```

Add the selected framework's `plugins` field to this same object. Do not create a second init.

Substantiate `service`, `env`, and `version` from package metadata, existing unified-service-tagging variables, deployment configuration, or user input. When a non-interactive task requires progress and no value can be substantiated, use an obvious quoted placeholder and list it in the report. Never invent a production environment or deployment version.

Do not add `allowedTracingUrls`, trace propagation, backend headers, source-map upload, user identity, or unrelated optional features.

## Public environment variables

Reuse the application's established public configuration mechanism. Common names before adding a framework prefix are:

| Init option | Environment variable |
|---|---|
| `applicationId` | `DD_RUM_APPLICATION_ID` |
| `clientToken` | `DD_RUM_CLIENT_TOKEN` |
| `site` | `DD_SITE` |
| `service` | `DD_SERVICE` |
| `env` | `DD_ENV` |
| `version` | `DD_VERSION` |
| `sessionSampleRate` | `DD_SESSION_SAMPLE_RATE` |
| `sessionReplaySampleRate` | `DD_SESSION_REPLAY_SAMPLE_RATE` |
| `defaultPrivacyLevel` | `DD_DEFAULT_PRIVACY_LEVEL` |

Expose them using the project's convention: `VITE_` for Vite, `NEXT_PUBLIC_` for Next.js, `REACT_APP_` for Create React App, or `VUE_APP_` for Vue CLI. Webpack, Rollup, esbuild, and Rspack have no universal browser environment prefix; follow their existing define/injection configuration and do not assume `process.env` is available in browser code.

Environment values are strings. Convert sample rates to numbers and validate them before passing them to `init`, or retain literal numeric defaults. Never pass an unvalidated string where the SDK expects a number. Do not put Datadog API keys or application keys in browser-visible configuration; Browser RUM uses the client token and application ID.

## CDN async configuration

Add this loader near the start of `<head>` on every HTML entry page:

```html
<script>
  (function(h,o,u,n,d) {
    h=h[d]=h[d]||{q:[],onReady:function(c){h.q.push(c)}}
    d=o.createElement(u);d.async=1;d.src=n,d.crossOrigin=''
    n=o.getElementsByTagName(u)[0];n.parentNode.insertBefore(d,n)
  })(window,document,'script','<BROWSER_AGENT_URL>','DD_RUM')

  window.DD_RUM.onReady(function() {
    window.DD_RUM.init({
      applicationId: '<APPLICATION_ID>',
      clientToken: '<CLIENT_TOKEN>',
      site: '<DATADOG_SITE>',
      service: '<SERVICE_NAME>',
      env: '<ENVIRONMENT>',
      version: '<VERSION>',
      sessionSampleRate: 100,
      sessionReplaySampleRate: 20,
      defaultPrivacyLevel: 'mask-user-input',
      trackUserInteractions: true,
      trackResources: true,
      trackLongTasks: true,
    })
  })
</script>
```

Use the current v7 URL for the selected site:

| Site parameter | Browser agent URL |
|---|---|
| `datadoghq.com` | `https://www.datadoghq-browser-agent.com/us1/v7/datadog-rum.js` |
| `datadoghq.eu` | `https://www.datadoghq-browser-agent.com/eu1/v7/datadog-rum.js` |
| `us3.datadoghq.com` | `https://www.datadoghq-browser-agent.com/us3/v7/datadog-rum.js` |
| `us5.datadoghq.com` | `https://www.datadoghq-browser-agent.com/us5/v7/datadog-rum.js` |
| `ap1.datadoghq.com` | `https://www.datadoghq-browser-agent.com/ap1/v7/datadog-rum.js` |
| `ap2.datadoghq.com` | `https://www.datadoghq-browser-agent.com/ap2/v7/datadog-rum.js` |
| `uk1.datadoghq.com` | `https://www.datadoghq-browser-agent.com/uk1/v7/datadog-rum.js` |
| `ddog-gov.com` or `us2.ddog-gov.com` | `https://www.datadoghq-browser-agent.com/datadog-rum-v7.js` |

Preserve a valid existing synchronous CDN installation rather than converting it. For a new unmanaged site, use the async form unless the user explicitly prioritizes capturing the earliest events over page-load impact.

## Existing initialization

When one valid init exists, preserve its credentials, sampling, privacy, service tagging, and optional settings. Merge a missing `plugins` entry into the existing object without reordering or rewriting unrelated values. If `plugins` already exists, append the one selected framework plugin without duplicating existing entries.

Do not migrate between npm and CDN as part of plugin enrichment. Framework integration packages require the npm setup; if a framework app already uses CDN RUM and requests plugin onboarding, stop and ask whether the user wants a separate migration.
