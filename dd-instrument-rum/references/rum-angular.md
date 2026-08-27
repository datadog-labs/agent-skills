# Angular integration

Read `rum-core.md` first.

## Prerequisites and packages

Require Angular 15 through 22 and RxJS 7 or later. If Angular routing is used, require a matching supported `@angular/router` major. Stop without editing when these prerequisites are unmet.

Install `@datadog/browser-rum` and `@datadog/browser-rum-angular` at the same exact version.

Initialize at module scope before `bootstrapApplication(...)` or `platformBrowserDynamic().bootstrapModule(...)`:

```ts
import { datadogRum } from '@datadog/browser-rum'
import { angularPlugin } from '@datadog/browser-rum-angular'

datadogRum.init({
  // ...canonical fields from rum-core.md...
  plugins: [angularPlugin()],
})
```

## Error integration

Detect existing Angular error handling before editing.

- With no custom `ErrorHandler`, import and register `provideDatadogErrorHandler()` in the existing standalone bootstrap providers or NgModule providers.
- With a custom `ErrorHandler`, preserve it and call `addAngularError(error)` from its existing `handleError` method. Do not replace it with `provideDatadogErrorHandler()`.
- If either Datadog path already exists, do not add another error report path.

For standalone bootstrap:

```ts
bootstrapApplication(AppComponent, {
  providers: [provideDatadogErrorHandler()],
})
```

For an NgModule, add the provider to that module's existing `providers` array.

## Router integration

Enable router tracking only when the app actually configures Angular Router through `provideRouter`, `RouterModule.forRoot`, or equivalent active setup.

Use one plugin instance and one provider:

```ts
import { angularPlugin, provideDatadogRouter } from '@datadog/browser-rum-angular'

datadogRum.init({
  // ...canonical fields...
  plugins: [angularPlugin({ router: true })],
})
```

Add `provideDatadogRouter()` to the existing standalone or NgModule providers. Do not introduce Angular Router to an application that does not already use it.
