# RUM application credentials

Resolve credentials only after framework/plugin prerequisites and existing-instrumentation checks pass. Do not partially instrument a project that cannot receive a valid configuration.

## Reuse supplied values

Use credentials already supplied by the user, task environment, or existing project configuration. Browser RUM requires:

- `applicationId`
- `clientToken`
- `site`

The application ID and client token are browser-visible identifiers, not Datadog API keys. Never request or embed a Datadog API key for Browser RUM.

Accept these public Datadog site parameters:

- `datadoghq.com`
- `us3.datadoghq.com`
- `us5.datadoghq.com`
- `datadoghq.eu`
- `ap1.datadoghq.com`
- `ap2.datadoghq.com`
- `uk1.datadoghq.com`
- `ddog-gov.com`
- `us2.ddog-gov.com`

If a supplied `DD_SITE` or `site` value is empty or not one of these public sites, stop before editing and ask for the correct site. Do not silently default an explicitly incorrect value to US1.

## Create a RUM application when supported

If no credentials were supplied, ask whether the user already has a RUM application for this frontend. If not, use the local tool exactly as follows when it is available:

```text
CreateRumApplication(
  name: string,
  type: "react" | "browser"
)
```

Use a substantiated application name from the deployed frontend service/package/folder. Use `type: "react"` only for a plain React application. Use `type: "browser"` for Next.js, Angular, Vue, Nuxt, Svelte, vanilla, generic SPA, and iframe-hosted applications.

On success, use the returned `applicationId`, `clientToken`, and `site`.

If the tool is unavailable, permission is denied, or creation fails, stop without editing and ask the user to create or select a Browser RUM application at `https://app.datadoghq.com/rum/application/create`, then provide its Application ID, Client Token, and Datadog site.

## Put values in the application

Follow an existing public client environment-variable convention when the project already has one. Use these prefixes when needed:

| Runtime | Prefix |
|---|---|
| Vite | `VITE_` |
| Create React App | `REACT_APP_` |
| Next.js client | `NEXT_PUBLIC_` |
| Vue CLI | `VUE_APP_` |

Use names such as `VITE_DD_RUM_APPLICATION_ID`, `VITE_DD_RUM_CLIENT_TOKEN`, and `VITE_DD_SITE`. Confirm the selected variables are exposed to browser code by that framework.

Treat an environment-variable reference as configured only when its non-empty value is present in an applicable local/deployment configuration or the user confirms it is provisioned there. If a required variable is missing or its site value cannot be validated, stop before editing rather than producing an init that receives `undefined` at runtime.

If the project has no environment-variable convention, place the returned Application ID, Client Token, and site directly in the browser init configuration, matching the official setup snippet. Do not create an environment system solely to hide browser-visible identifiers.

Never replace valid credentials in an existing init while adding plugin wiring.
