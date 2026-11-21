# RUM application credentials

Resolve credentials only after framework/plugin prerequisites and existing-instrumentation checks pass. Do not partially instrument a project that cannot receive a valid configuration.

## Reuse supplied values

Use credentials already supplied by the user, task environment, or existing project configuration. RUM requires:

- `applicationId`
- `clientToken`
- `site`

The application ID and client token are public identifiers that ship in the app, not Datadog API keys. Never request or embed a Datadog API key or application key in the app. `rum-application.md` uses those keys only in shell commands against the Datadog API.

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

## Get or create the RUM application

Follow `rum-application.md`. With an application ID, it returns the client token, so the user does not need to find it. Without one, it creates the application. When you can ask, first ask whether the user already has a RUM application for this app; when you cannot ask, create one. Stop without editing when `rum-application.md` says to stop, or when only its settings step can create the application.

## Put values in the application

For React Native, follow "Credentials" in `rum-react-native.md` instead of this section.

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
