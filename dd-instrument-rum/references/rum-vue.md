# Vue integration

Read `rum-core.md` first. Use this file for Vue applications that are not Nuxt.

## Prerequisites and packages

Require Vue 3.5 or later. When router tracking is used, require Vue Router v4 or v5. Stop without editing for Vue 2, Vue 3.4 or earlier, or an unsupported router version.

Install `@datadog/browser-rum` and `@datadog/browser-rum-vue` at the same exact version.

Initialize before `createApp(...)`:

```js
import { datadogRum } from '@datadog/browser-rum'
import { vuePlugin } from '@datadog/browser-rum-vue'

datadogRum.init({
  // ...canonical fields from rum-core.md...
  plugins: [vuePlugin()],
})
```

## Error integration

After creating the Vue app and before mounting it, register `addVueError` as the global handler:

```js
import { addVueError } from '@datadog/browser-rum-vue'

const app = createApp(App)
app.config.errorHandler = addVueError
```

If `app.config.errorHandler` already exists, preserve it and add one `addVueError(error, instance, info)` call inside that handler or a small chaining wrapper. Do not overwrite custom behavior and do not report an error twice.

## Router integration

When the application actively uses Vue Router, initialize with `vuePlugin({ router: true })` and replace only Vue Router's `createRouter` import:

```diff
- import { createRouter } from 'vue-router'
+ import { createRouter } from '@datadog/browser-rum-vue/vue-router'
```

Keep `createWebHistory`, `createWebHashHistory`, route types, and other exports imported from `vue-router`. The version-agnostic Datadog entrypoint supports the detected Vue Router v4 or v5; do not introduce the older `vue-router-v4` path in new instrumentation.

Keep initialization, router construction, `createApp`, error-handler registration, `app.use(router)`, and `app.mount(...)` in their existing lifecycle order.
