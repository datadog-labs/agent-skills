---
name: dd-instrument-product-analytics
description: Switches on Datadog Product Analytics (funnels, retention, pathways, user journeys) for a browser app that runs the Datadog RUM Browser SDK. Turns the product on for the app's RUM application, ties sessions to signed-in users with setUser, and checks that events arrive. Use when asked to add, set up, enable or verify Product Analytics or user-behavior analytics in a web frontend. Not for adding RUM itself, which dd-instrument-rum does first, or for mobile apps.
metadata:
  version: "0.1.0"
  author: datadog-labs
  repository: https://github.com/datadog-labs/agent-skills
  tags: datadog,product-analytics,rum,browser-rum,instrumentation,funnels,retention,pathways
  alwaysApply: "false"
---

# Datadog Product Analytics

Product Analytics turns the sessions, page views and clicks that the Datadog RUM Browser SDK collects into funnels, retention, pathways and per-user journeys. It uses the same SDK and the same RUM application as RUM, so there is no second SDK to install.

## How it works

- **The switch is on the RUM application, not in the code.** Product Analytics is enabled when the application has `product_analytics_retention_state: MAX`. Without it, Product Analytics stays disabled even when the code is correct.
- **RUM comes first.** Product Analytics reads what the RUM Browser SDK sends, so an app with no RUM init needs RUM set up first.
- **Identity comes from `datadogRum.setUser`.** Without it, no journey is tied to an account. From SDK v6, visitors without a login are still counted anonymously, because `trackAnonymousUser` defaults to true; v5 has no anonymous id.
- **Clicks need action collection.** `trackUserInteractions` defaults to true from SDK v6 and to false in v5. SDK v4 and older name the option `trackInteractions`.
- **Credentials.** The application ID and client token are public and already in the RUM setup. Switching the product on through the API needs an API key and an application key, and both are secret.

## Set up

1. **Inspect.** If the request or target project is a mobile app (iOS, Android, React Native, Flutter, or Kotlin Multiplatform), stop before any browser search or edit: say that this skill is for the browser product (`@datadog/browser-rum`) only, and do not give the Manage Applications enable path. Otherwise, find the browser app; in a monorepo with several, ask which one. Search source and HTML files, skipping `node_modules` and build output, for the RUM setup: an `init` call on the `datadogRum` import from `@datadog/browser-rum`, including renamed imports and wrapper modules, or a CDN `DD_RUM.init`. Record the number of inits, the SDK version, `trackUserInteractions`, the `site` (absent means `datadoghq.com`), the application ID, and how users sign in, through an auth library or the app's own session or user state. When the init reads the application ID or the site from environment variables, read only those lines of the env file, for example `grep -E '^VITE_DD_(RUM_APPLICATION_ID|SITE)=' .env.local`, because env files often hold secrets.

2. **Check what Product Analytics needs.**
   - **No RUM init:** stop before any edit. Tell the user that Product Analytics collects its data through the RUM Browser SDK, that this app has none, and that RUM must be set up first with the [`dd-instrument-rum`](https://github.com/datadog-labs/agent-skills/tree/main/dd-instrument-rum) skill. Ask whether to set up RUM now, and close with the `blocked: RUM missing` summary below (naming `dd-instrument-rum` and asking whether to run it) instead of the settings step. On yes, run that skill; when it is not installed, get it from that link or follow the [Browser Monitoring setup](https://docs.datadoghq.com/real_user_monitoring/application_monitoring/browser/setup/) docs, then restart at step 1, and if there is still no init with an application ID, stop and report it.
   - **Two or more inits, or npm and CDN setups mixed:** stop and report each location, because a second init splits sessions between configurations. Repairing that belongs to RUM setup.
   - **Site `ddog-gov.com` or `us2.ddog-gov.com`:** stop and report that Product Analytics is not available on those sites.
   - **No application ID found:** ask the user. It is listed under Real User Monitoring > Manage Applications in Datadog.

3. **Turn Product Analytics on.** Check for both keys without printing them: `[ -n "$DD_API_KEY" ] && [ -n "$DD_APP_KEY" ] && echo set`. With keys, follow [references/api-switch.md](references/api-switch.md): it reads the current state, records whether the application is in preview, and sends only `product_analytics_retention_state: MAX`. Without keys, or after a failure, give the user the settings step: in Datadog, open Digital Experience > Real User Monitoring > [Manage Applications](https://APP_HOST/rum/list), select the application, then under PRODUCT SETTINGS click Product Analytics and Enable. Build `APP_HOST` as described in Verify and view. When the Datadog MCP server has the RUM toolset, `Datadog:search_rum_applications` reports `product_analytics_retention_state`, which confirms either path.

4. **Tie sessions to users.**
   - **The app has sign-in:** call `datadogRum.setUser({ id: String(user.id), name: user.name, email: user.email })` where the signed-in user first becomes available in the browser: the nearest provider, layout or root component that holds the user, such as a `useSession()`, `useUser()` or `onAuthStateChanged` consumer, rather than a leaf component. `id` must be stable; `name` and `email` are optional. When the user object has no `id`, use another stable unique value the app holds for that user, and make no call while it is undefined, because `String(undefined)` is the literal `"undefined"`, which merges every user into one. Call `datadogRum.clearUser()` where the user signs out. The SDK runs only in the browser, so in server-rendered apps such as Next.js make these calls in a client component or effect. With a CDN setup, wrap them in `window.DD_RUM.onReady(() => …)`.
   - **The app has no sign-in:** add no `setUser` call. Leave one comment after the init that shows the call to add once a real user identity exists. On SDK v5, tell the user that counting anonymous visitors needs SDK v6 or later.
   - **A `setUser` call already exists:** keep it.

5. **Make sure clicks are collected.** On SDK v5 (see the manifest, or a CDN loader URL such as `…/us1/v5/datadog-rum.js` or `datadog-rum-v4.js`), add `trackUserInteractions: true` to the existing init when it is absent; on v4 and older, the option is `trackInteractions`. When the init sets it to `false`, ask before changing it. Leave every other init option, the credentials and the plugins as they are.

6. **Build and run.** Run the project's build, fix errors your edits caused, and run it again until it passes. Then start the app with its dev command, load a page, click an element, keep the page open for about 30 seconds, and stop the server. The SDK sends its batch every 30 seconds or when the page is hidden, so a check right after the click can find nothing.

## Verify and view

Record the highest level you actually reached:

1. **Configuration changed:** the switch result from step 3 and the code edits are in place.
2. **The app ran the action:** a page loaded in a browser and a click happened.
3. **Export accepted:** a request to a `browser-intake-…` host, or to the init's `proxy` URL, with path `/api/v2/rum`, returned 202. Check in a headless browser when one is available, or ask the user to look in DevTools > Network. An ad blocker can block the request, and a `sessionSampleRate` below 100 can leave the test session out.
4. **Datadog shows events:** `Datadog:search_datadog_rum_events`, or the [Search RUM events](https://docs.datadoghq.com/api/latest/rum/#search-rum-events) endpoint with the same keys, finds `@application.id:<APP_ID> @session.initial_view.url_host:localhost` in the last 15 minutes. Use the dev server's host, so existing traffic does not count. A new application can take a few minutes to show data, so query once a minute for up to 10 minutes after the click; if nothing arrives, stop at level 3 and give the link.

A passing build shows only that the project compiles. Name each level you could not check and the step left for the user, such as signing in once to see their own user.

The app host is `app.datadoghq.com` for `datadoghq.com`, `app.datadoghq.eu` for `datadoghq.eu`, and the site itself for the others, for example `us5.datadoghq.com`. End with this summary (when step 1 or 2 stops before step 3, use the `blocked:` status instead of the settings step and omit `View:`):

```text
Product Analytics: turned on | already on | waiting for your settings step (<the step>) | blocked: RUM missing (run dd-instrument-rum first — set up RUM now?) | blocked: <reason>
Changed: <files, or none>
Evidence: <highest level reached, or none>; not checked: <levels and the step left>
View: https://APP_HOST/product-analytics?query=@application.id:<APP_ID>
```

## Undo

Revert this run's code edits: `setUser`, `clearUser`, the comment, and `trackUserInteractions`. If this run turned Product Analytics on, follow the undo note in [references/api-switch.md](references/api-switch.md): turning it on ends a new application's preview, and undoing does not bring the preview back. Leave the RUM setup as it is.

## Gotchas

| Mistake | Symptom | Fix |
|---|---|---|
| `setUser({ id: 'anonymous' })` or another literal id | Every session merges into one user, so funnels and retention break | Use the real user id, or no call |
| `setUser` called in server code | Events carry no user | Call it in browser code |
| SDK v5 init without `trackUserInteractions` | Views arrive, clicks do not, so funnels on actions stay empty | Step 5 |
