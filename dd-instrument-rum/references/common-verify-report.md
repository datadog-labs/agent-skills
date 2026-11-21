# Verification and reporting

## Verify the edit

After instrumentation:

1. Search the edited source again and confirm there is exactly one effective RUM init path.
2. Confirm every added `@datadog/browser-rum*` or `@datadog/mobile-react-*` package is declared in the deployed frontend manifest and that the Datadog packages of one SDK use the same exact version. An import resolving from an existing `node_modules` directory is not proof.
3. Confirm the matching lockfile was generated or updated by the detected package manager; never hand-edit it. When practical, use the project's frozen/clean install command so stale local dependencies cannot hide a missing declaration or lock entry.
4. Run the project's configured formatter/fix command for changed files when one exists.
5. Run the normal terminating build, such as `npm run build`, `yarn build`, `pnpm build`, `bun run build`, `ng build`, `vite build`, `next build`, or `nuxt build`. Prefer the project's own script over an invented command.
6. For unmanaged static HTML with no build, inspect every HTML entry and validate that the loader URL, `onReady` wrapper, credentials, and site agree.

Do not use a successful dependency install as a substitute for the build. Do not leave a development server running.

If a real credentialed application can be run in a bounded local browser session, start it with the project's normal command, check for initialization errors and a RUM intake request, then stop it. Treat this as additional evidence, not a requirement when the environment cannot run a browser or reach Datadog. Do not claim that sessions are visible in Datadog based only on a successful build.

If formatting, install, or build fails:

- Capture the failing command and relevant error.
- Distinguish an instrumentation error from an external registry/network/permission failure.
- Fix errors caused by the instrumentation and rerun the check.
- Return `success: false` when the build remains broken or required verification could not run; do not conceal the failure.

## Report

Return a short JSON object:

```json
{
  "success": true,
  "rumApplicationId": "optional application ID",
  "dashboardUrl": "https://APP_HOST/rum/performance-monitoring?query=@application.id:<APPLICATION_ID>",
  "message": "Framework and router detected, RUM application outcome, files and packages changed, build command/result, telemetry evidence if any, and remaining placeholders or manual work"
}
```

The RUM application outcome is one of: created, RUM processing turned on, already on, or waiting for the user's settings step. Build `APP_HOST` with the table in `rum-application.md`; never use `app.` followed by the site.

For a no-op existing setup, say that RUM was already initialized, what plugin/router/error wiring was confirmed, and which build passed.

For a stopped run, set `success` to `false`, name the blocking credential, site, prerequisite, permission, conflict, or build failure, and state that no partial instrumentation was left behind.
