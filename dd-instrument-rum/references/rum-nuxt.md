# Nuxt integration

Read `rum-core.md` first. Use the Nuxt package rather than the plain Vue package.

## Prerequisites and packages

Require Nuxt 3 or 4, Vue 3.5 or later, and Vue Router v4 or v5. Stop without editing when a prerequisite is unmet.

Install `@datadog/browser-rum` and `@datadog/browser-rum-nuxt` at the same exact version.

## Client plugin

Create or extend one client-only Nuxt plugin, conventionally `plugins/datadog-rum.client.ts`:

```ts
import { datadogRum } from '@datadog/browser-rum'
import { nuxtRumPlugin } from '@datadog/browser-rum-nuxt'
import { defineNuxtPlugin, useNuxtApp, useRouter } from 'nuxt/app'

export default defineNuxtPlugin({
  name: 'datadog-rum',
  enforce: 'pre',
  setup() {
    datadogRum.init({
      // ...canonical fields from rum-core.md...
      plugins: [
        nuxtRumPlugin({
          router: useRouter(),
          nuxtApp: useNuxtApp(),
        }),
      ],
    })
  },
})
```

Keep `enforce: 'pre'` so the integration can observe startup errors from later plugins. Pass both `router` and `nuxtApp`; the latter enables Nuxt and Vue error reporting. Do not also install `@datadog/browser-rum-vue`, replace Nuxt's router, or add a separate Vue error handler.

If an existing Nuxt plugin contains the only RUM init, enrich that init in place and preserve its current plugin metadata and configuration.

Use public runtime configuration only when the project already exposes client configuration that way. Never read server-only secrets from the client plugin.
