# React integration

Read `rum-core.md` first. Use this integration only for plain React applications, not Next.js.

## Prerequisites and packages

Require React 18 or 19. Stop before editing when the detected React major is unsupported.

Install together:

```sh
npm install --save @datadog/browser-rum @datadog/browser-rum-react
```

Keep both packages on the same exact version. Initialize at module scope in the browser entry before `createRoot(...).render(...)` or the legacy render call:

```js
import { datadogRum } from '@datadog/browser-rum'
import { reactPlugin } from '@datadog/browser-rum-react'

datadogRum.init({
  // ...canonical fields from rum-core.md...
  plugins: [reactPlugin()],
})
```

Do not add router mode when the application does not use a supported router.

## React Router

Support React Router v6, v7, and v8. Detect the installed major and the router APIs actually used. Initialize with:

```js
plugins: [reactPlugin({ router: true })]
```

Replace only these imports when they are used: `createBrowserRouter`, `createHashRouter`, `createMemoryRouter`, `useRoutes`, and `Routes`. Keep unrelated router exports such as `RouterProvider`, `Link`, hooks, and route utilities on their original package.

Choose the matching Datadog entrypoint:

| Router major | Datadog entrypoint |
|---|---|
| v6 | `@datadog/browser-rum-react/react-router-v6` |
| v7 | `@datadog/browser-rum-react/react-router-v7` |
| v8 | `@datadog/browser-rum-react/react-router-v8` |

Do not use the version-agnostic `react-router` entrypoint for a known older major because it targets the latest supported router. Preserve whether the application imports its other APIs from `react-router`, `react-router/dom`, or `react-router-dom`.

## TanStack Router

Require `@tanstack/react-router` >=1.64.0 and <2. Detect actual usage rather than enabling this path from dependency presence alone.

Use `reactPlugin({ router: true })` and replace only the `createRouter` import:

```diff
- import { createRouter } from '@tanstack/react-router'
+ import { createRouter } from '@datadog/browser-rum-react/tanstack-router'
```

Keep `RouterProvider`, `Link`, route construction helpers, and all other TanStack exports imported from `@tanstack/react-router`.

If React Router and TanStack Router dependencies are both present, instrument only the router that constructs the active application router. Stop and ask rather than guessing when both are genuinely active entrypoints.

## Error integration

Do not create a new error boundary or fallback UI solely for instrumentation.

- If the application already has a class error boundary, import `addReactError` and call it from the existing `componentDidCatch(error, errorInfo)` while preserving the handler's current behavior.
- If the application already uses React 19 `createRoot` error callbacks, call `addReactError` from those callbacks and preserve their logging/recovery logic.
- If the application already uses Datadog's `ErrorBoundary` or calls `addReactError`, do not add a second report path.
- Do not add `onCaughtError` when an existing boundary already reports the same caught error; that creates duplicates.

Core Browser RUM already captures uncaught browser errors. A project with no existing component-error surface remains valid with `reactPlugin()` and no invented UI.
