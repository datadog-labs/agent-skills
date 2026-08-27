# Next.js integration

Read `rum-core.md` first. Use `@datadog/browser-rum-nextjs`, not the plain React plugin.

## Prerequisites and packages

Require Next.js 15.3 or later and React 18 or later. Confirm the configured Node runtime satisfies the detected Next.js package's engine requirement. If any prerequisite fails, stop without editing and report the detected and required versions. Do not upgrade Next.js and do not fall back to a client component or CDN setup.

Install together at the same exact version:

```sh
npm install --save @datadog/browser-rum @datadog/browser-rum-nextjs
```

## Initialize once

Create `instrumentation-client.ts` or `.js` at the Next.js source root required by the project's layout. Put the canonical `datadogRum.init()` from `rum-core.md` at module scope with `plugins: [nextjsPlugin()]`.

For an App Router application:

```ts
import { datadogRum } from '@datadog/browser-rum'
import { nextjsPlugin, onRouterTransitionStart } from '@datadog/browser-rum-nextjs'

export { onRouterTransitionStart }

datadogRum.init({
  // ...canonical fields from rum-core.md...
  plugins: [nextjsPlugin()],
})
```

For a Pages Router-only application, omit `onRouterTransitionStart`:

```ts
import { datadogRum } from '@datadog/browser-rum'
import { nextjsPlugin } from '@datadog/browser-rum-nextjs'

datadogRum.init({
  // ...canonical fields from rum-core.md...
  plugins: [nextjsPlugin()],
})
```

Use `NEXT_PUBLIC_` variables for values that must be read by this client module when following an existing environment-variable convention.

## Wire the router

For App Router, render `DatadogAppRouter` exactly once in the root `app/layout` body without converting the layout to a client component:

```tsx
import { DatadogAppRouter } from '@datadog/browser-rum-nextjs'

<body>
  <DatadogAppRouter />
  {children}
</body>
```

For Pages Router, render `DatadogPagesRouter` exactly once in the existing custom `pages/_app` alongside the page component:

```tsx
import { DatadogPagesRouter } from '@datadog/browser-rum-nextjs'

<>
  <DatadogPagesRouter />
  <Component {...pageProps} />
</>
```

If both route trees are genuinely used, export `onRouterTransitionStart` and wire each router component in its own existing root. Keep a single init in `instrumentation-client`.

## Error integration

Do not create an error page, error boundary, reset button, or fallback UI solely for RUM.

- When an App Router `error.tsx` or `global-error.tsx` already exists, call `addNextjsError(error)` from an effect keyed by `error`, preserving the existing UI and reset behavior. Do not add another call if already present.
- When Pages Router already has a custom error boundary, use the package's `addNextjsError` or `ErrorBoundary` in that existing surface without creating a second reporting path.
- Preserve the `digest` attached to App Router server-component errors; pass the original error object.

Do not add `@datadog/browser-rum-react`, CDN `Script` tags, server tracing, Vercel metadata, or source-map upload.
