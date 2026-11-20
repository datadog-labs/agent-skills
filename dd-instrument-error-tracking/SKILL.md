---
name: dd-instrument-error-tracking
description: Sets up Datadog Browser Error Tracking for a web app that runs the Datadog RUM Browser SDK. Turns error processing on for the app's RUM application, forwards framework-caught errors (React, Next.js, Vue, Angular, SvelteKit) to Datadog, and checks that events arrive. Use when asked to add, set up, enable or verify Error Tracking or frontend exception tracking in a browser app. Not for adding RUM itself, which dd-instrument-rum does first, or for uploading source maps, which dd-browser-sdk does.
metadata:
  version: "0.2.0"
  author: datadog-labs
  repository: https://github.com/datadog-labs/agent-skills
  tags: datadog,error-tracking,rum,browser-rum,instrumentation,react,nextjs,vue,nuxt,angular,sveltekit
  alwaysApply: "false"
---

# Datadog Browser Error Tracking

Browser Error Tracking groups and triages frontend exceptions collected by the Datadog RUM Browser SDK (`@datadog/browser-rum`). It shares the same SDK and the same RUM application as Browser RUM and Product Analytics, so there is no separate Error Tracking SDK to install.

## How it works

- **The switch is on the RUM application, not in the code.** A RUM application processes errors when `rum_event_processing_state` is `ERROR_FOCUSED_MODE` or `ALL`. An application created for Product Analytics only starts at `NONE`, which drops all RUM and Error Tracking events even when the browser code is wired.
- **RUM comes first.** Browser Error Tracking reads what the RUM Browser SDK sends, so an app with no RUM init needs RUM set up first.
- **Sampling uses `sessionSampleRate`.** The RUM Browser SDK captures unhandled errors (`window.onerror`, `unhandledrejection`, `console.error`) automatically for sampled sessions. There is no `errorSampleRate` option in `RumInitConfiguration`; adding it breaks TypeScript builds.
- **Frameworks swallow component errors.** React error boundaries, Next.js `error.tsx`, Vue `app.config.errorHandler`, Angular `ErrorHandler`, and SvelteKit `handleError` intercept errors before they reach `window.onerror`, so those hooks must forward caught errors to Datadog.
- **Credentials.** The application ID and client token are public and already in the RUM setup. Switching event processing on through the API needs an API key and an application key, and both are secret.

## Set up

Copy these steps into your reply and tick them off as you go.

1. **Inspect.** Find the browser app; in a monorepo with several, ask which one. Search source and HTML files, skipping `node_modules` and build output, for the RUM setup: an `init` call on the `datadogRum` import from `@datadog/browser-rum`, including renamed imports and wrapper modules, or a CDN `DD_RUM.init`. Record the number of inits, the framework, `sessionSampleRate`, the `site` (absent means `datadoghq.com`), the application ID, any `@datadog/browser-rum-*` or `@datadog/browser-logs` packages, and any existing error boundary or framework error handler. When the init reads the application ID or the site from environment variables, read only those lines of the env file, for example `grep -E '^VITE_DD_(RUM_APPLICATION_ID|SITE)=' .env.local`, because env files often hold secrets.

2. **Check what Error Tracking needs.**
   - **No browser app,** for example a backend service or a mobile app: stop before any edit and report that this skill covers browser apps only. Do not offer Browser RUM, because it cannot collect errors from that project.
   - **No RUM init:** stop before any edit. Tell the user that Browser Error Tracking collects its data through the RUM Browser SDK, that this app has none, and that RUM must be set up first with the [`dd-instrument-rum`](https://github.com/datadog-labs/agent-skills/tree/main/dd-instrument-rum) skill. Ask whether to set up RUM now. On yes, run that skill; when it is not installed, get it from that link or follow the [Browser Monitoring setup](https://docs.datadoghq.com/real_user_monitoring/application_monitoring/browser/setup/) docs. Then restart at step 1, and if there is still no init with an application ID, stop and report it. On no, or when you cannot ask, end without changes and report that RUM is missing.
   - **Two or more inits, or npm and CDN setups mixed:** stop and report each location, because a second init splits sessions and errors between configurations. Repairing that belongs to RUM setup.
   - **No application ID found:** ask the user. It is listed under Real User Monitoring > Manage Applications in Datadog.

3. **Turn Error Tracking processing on.** Check for both keys without printing them: `[ -n "$DD_API_KEY" ] && [ -n "$DD_APP_KEY" ] && echo set`. With keys, follow [references/api-switch.md](references/api-switch.md): it reads the current state, leaves `ALL` or `ERROR_FOCUSED_MODE` untouched, and sends `rum_event_processing_state: ERROR_FOCUSED_MODE` only when the current state is `NONE`. Without keys, or after a failure, give the user the settings step: in Datadog, open Digital Experience > Real User Monitoring > [Manage Applications](https://APP_HOST/rum/list), select the application, and under Application Settings enable Error Tracking event processing. Build `APP_HOST` as described in Verify and view. When the Datadog MCP server has the RUM toolset, `Datadog:search_rum_applications` reports `rum_event_processing_state`, which confirms either path.

4. **Check session sampling and Logs forwarding.**
   - If the existing `datadogRum.init` sets `sessionSampleRate: 0`, no sessions are sampled and no errors are sent; ask before changing it to `100`. When `sessionSampleRate` is absent, leave it alone (it defaults to `100`). Never add `errorSampleRate`.
   - If `@datadog/browser-logs` is already installed and `datadogLogs.init` is present without `forwardErrorsToLogs`, add `forwardErrorsToLogs: true`. Do not install `@datadog/browser-logs` from scratch, because Browser Logs is a separate product with its own setup.
   - Leave the credentials, site, service, and unrelated options in `datadogRum.init` unchanged.

5. **Forward framework-caught errors.** Open [references/framework-errors.md](references/framework-errors.md) and wire the error hook for the detected framework (React, Next.js, Vue, Nuxt, Angular, or SvelteKit) so errors caught by the framework are reported to Datadog without replacing existing UI or reporting the same error twice. Plain JavaScript, plain Svelte, and CDN apps need no framework hook.

6. **Build and run.** Run the project's build, fix errors your edits caused, and run it again until it passes. Then start the app with its dev command, load a page, keep the page open for about 30 seconds, and stop the server. The SDK sends its batch every 30 seconds or when the page is hidden.

## Verify and view

Record the highest level you actually reached:

1. **Configuration changed:** the switch result from step 3 and the code edits are in place.
2. **The app ran:** the project built cleanly and a page loaded in a browser.
3. **Export accepted:** a request to a `browser-intake-…` host, or to the init's `proxy` URL, with path `/api/v2/rum`, returned 202. Check in a headless browser when one is available, or ask the user to look in DevTools > Network.
4. **Datadog shows events:** `Datadog:search_datadog_rum_events`, or the [Search RUM events](https://docs.datadoghq.com/api/latest/rum/#search-rum-events) endpoint with the same keys, finds `@application.id:<APP_ID> @session.initial_view.url_host:localhost` in the last 15 minutes. A new application can take a few minutes to show data, so query once a minute for up to 10 minutes; if nothing arrives, stop at level 3 and give the link.

A passing build shows only that the project compiles. Name each level you could not check and the step left for the user.

The app host is `app.datadoghq.com` for `datadoghq.com`, `app.datadoghq.eu` for `datadoghq.eu`, `app.ddog-gov.com` for `ddog-gov.com`, and the site itself for the others, for example `us5.datadoghq.com`. End with this summary:

```text
Error Tracking: turned on | already on | waiting for your settings step (<the step>)
Changed: <files, or none>
Evidence: <highest level reached>; not checked: <levels and the step left>
View: https://APP_HOST/error-tracking?query=@application.id:<APP_ID>
```

## Undo

Revert this run's code edits (framework error hook or boundary). If this run changed `rum_event_processing_state` from `NONE` to `ERROR_FOCUSED_MODE`, follow the undo note in [references/api-switch.md](references/api-switch.md). Leave the RUM setup as it is.

## Gotchas

| Mistake | Symptom | Fix |
|---|---|---|
| Sending `rum_event_processing_state: ERROR_FOCUSED_MODE` when the app is at `ALL` | Downgrades full RUM and drops non-error sessions | Read the state first; update only when it is `NONE` |
| Sending `product_analytics_retention_state: NONE` in the update | Turns Product Analytics off for the app | Omit `product_analytics_retention_state` from the PATCH body |
| Adding `errorSampleRate` to `datadogRum.init` | TypeScript build fails (`errorSampleRate` is not a valid option) | Use `sessionSampleRate` (defaults to `100`) |
| Calling `datadogRum.addError` in server-only Next.js or SvelteKit code | Server error is never sent by the browser SDK | Call it in a client error boundary (`'use client'`) or `hooks.client` |
