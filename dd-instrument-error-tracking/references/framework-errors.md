# Forward framework-caught errors to Datadog

Open this in step 5. The RUM Browser SDK automatically captures uncaught browser errors (`window.onerror` and `unhandledrejection`), but frontend frameworks catch component and rendering errors in their own boundaries or handlers. Wire only the detected framework below, and never report the same error twice.

## Contents

- React (plain React, not Next.js)
- Next.js (App Router and Pages Router)
- Vue (plain Vue, not Nuxt)
- Nuxt
- Angular
- SvelteKit
- Plain Svelte, vanilla JS, or CDN

## React (plain React, not Next.js)

- **Existing class error boundary (`componentDidCatch`):** preserve the boundary and its fallback UI. Inside `componentDidCatch(error, errorInfo)`, call `addReactError(error, errorInfo)` from `@datadog/browser-rum-react` when that package is installed, or `datadogRum.addError(error, { componentStack: errorInfo?.componentStack })` from `@datadog/browser-rum`.
- **Existing React 19 `createRoot` error callbacks (`onCaughtError` / `onUncaughtError`):** call `addReactError` or `datadogRum.addError` inside the existing callback and preserve its current logic.
- **Already using Datadog `ErrorBoundary` or `addReactError`:** make no change.
- **No existing error boundary:** if `@datadog/browser-rum-react` is installed, wrap the root application component in `ErrorBoundary` from `@datadog/browser-rum-react` with a simple `fallback` component (`({ error, resetError }) => ...`). If `@datadog/browser-rum-react` is not installed, add a small React class error boundary around the root `<App />` whose `componentDidCatch(error, errorInfo)` calls `datadogRum.addError(error, { componentStack: errorInfo?.componentStack })`, so no extra package install is required.

## Next.js (App Router and Pages Router)

- **App Router (`app/` or `src/app/`):** report from both root boundaries. The root `error.tsx` catches errors in route segments but not in the root `layout.tsx`; only `global-error.tsx` catches those.
  - For each of `error.tsx` and `global-error.tsx` (or `.jsx`) that already exists in the App Router root, keep its UI and `reset` button, and add a `useEffect` keyed on `[error]` that reports the error:
    - When `@datadog/browser-rum-nextjs` is installed, import `addNextjsError` from `@datadog/browser-rum-nextjs` and call `addNextjsError(error)`.
    - Otherwise, import `datadogRum` from `@datadog/browser-rum` and call `datadogRum.addError(error)`.
  - If no `error.tsx` exists in the App Router root (`app/` or `src/app/`), create `error.tsx` (or `.jsx` in a JavaScript project) with `'use client'` at the top:

    ```tsx
    'use client';

    import { useEffect } from 'react';
    import { datadogRum } from '@datadog/browser-rum';

    export default function Error({
      error,
      reset,
    }: {
      error: Error & { digest?: string };
      reset: () => void;
    }) {
      useEffect(() => {
        datadogRum.addError(error);
      }, [error]);

      return (
        <div>
          <h2>Something went wrong</h2>
          <button type="button" onClick={() => reset()}>Try again</button>
        </div>
      );
    }
    ```

  - If no `global-error.tsx` exists in the App Router root, create it from the same template, name the component `GlobalError`, and wrap the returned markup in `<html>` and `<body>`, because it replaces the root layout when it renders.
  - In both files, when `@datadog/browser-rum-nextjs` is installed in `package.json`, use `addNextjsError(error)` from `@datadog/browser-rum-nextjs` instead of `datadogRum.addError(error)`. Always pass the original `error` object so Next.js server-component `digest` metadata is preserved.
- **Pages Router (`pages/_app`):** if a custom error boundary exists, call `datadogRum.addError(error)` in `componentDidCatch`; otherwise wrap `<Component {...pageProps} />` in a small error boundary that calls `datadogRum.addError(error)` in `componentDidCatch`.

## Vue (plain Vue, not Nuxt)

After `const app = createApp(...)` and before `app.mount(...)`:

- When `@datadog/browser-rum-vue` is installed, import `addVueError` from `@datadog/browser-rum-vue` and set `app.config.errorHandler = addVueError` (or call `addVueError(err, instance, info)` inside an existing `app.config.errorHandler`).
- When only `@datadog/browser-rum` is installed, wire `app.config.errorHandler`:

  ```ts
  app.config.errorHandler = (err, _instance, info) => {
    datadogRum.addError(err, { vueErrorInfo: info });
  };
  ```

  If `app.config.errorHandler` already exists, preserve its behavior and add the `datadogRum.addError` call inside it.

## Nuxt

- When `@datadog/browser-rum-nuxt` is used, ensure `nuxtRumPlugin({ router: useRouter(), nuxtApp: useNuxtApp() })` passes `nuxtApp`.
- Otherwise, in the client-only Datadog Nuxt plugin (`plugins/*.client.ts`), register `nuxtApp.vueApp.config.errorHandler` to call `datadogRum.addError(err, { vueErrorInfo: info })`.

## Angular

- When `@datadog/browser-rum-angular` is installed:
  - With no custom `ErrorHandler`, register `provideDatadogErrorHandler()` from `@datadog/browser-rum-angular` in the `bootstrapApplication` or `@NgModule` `providers` array.
  - With an existing custom `ErrorHandler`, call `addAngularError(error)` inside its `handleError(error)` method.
- When only `@datadog/browser-rum` is installed, register an `ErrorHandler` provider in the existing bootstrap or module `providers` array:

  ```ts
  import { ErrorHandler } from '@angular/core';
  import { datadogRum } from '@datadog/browser-rum';

  class DatadogErrorHandler implements ErrorHandler {
    handleError(error: unknown): void {
      datadogRum.addError(error);
      console.error(error);
    }
  }
  ```

  Add `{ provide: ErrorHandler, useClass: DatadogErrorHandler }` to `providers` (or call `datadogRum.addError(error)` inside the app's existing custom `ErrorHandler`).

## SvelteKit

In `src/hooks.client.ts` (or `src/hooks.client.js`), export `handleError` so router and rendering errors are forwarded to Datadog:

```ts
import type { HandleClientError } from '@sveltejs/kit';
import { datadogRum } from '@datadog/browser-rum';

export const handleError: HandleClientError = ({ error, status }) => {
  if (status !== 404) {
    datadogRum.addError(error);
  }
};
```

If `src/hooks.client.*` already exports `handleError`, preserve its existing logic and add `datadogRum.addError(error)` inside it.

## Plain Svelte, vanilla JS, or CDN

Plain Svelte (Vite without SvelteKit), vanilla JavaScript, and CDN setups do not have a framework error boundary that swallows exceptions before `window.onerror`. Leave their application code unchanged once `datadogRum.init` and the application switch are verified.
