# Troubleshooting

Use this when the user is diagnosing auth, upload, Node, site, or backend function failures.

## Authentication Errors

Symptoms include 401s, `Missing authentication token`, backend function call failures, OAuth browser flow failures, cached token failures, or the Vite plugin logging `Auth credentials not configured`.

- For the default OAuth flow, rerun the command and complete the browser authorization prompt.
- Confirm the Datadog site configured in `vite.config.ts` matches the site used for OAuth.
- If the cached OAuth token is invalid or belongs to the wrong site, rerun the command and complete authorization again.
- If secure token storage is unavailable, install the optional keyring package requested by the warning, such as `@napi-rs/keyring`, or expect to reauthorize more often.
- For key-based auth, verify both `DD_API_KEY` and `DD_APP_KEY` are set. The application key needs Actions API Access for backend function execution and Apps for uploading.

**Optional key-based `.env.local` not being picked up:** The generated config should read local env files before deciding whether to use API/application keys. If an older app reads credentials via `process.env` at config evaluation time, switch to Vite's `loadEnv`:

```ts
import { defineConfig, loadEnv } from 'vite';

export default defineConfig(({ mode }) => {
    const env = loadEnv(mode, process.cwd(), '');
    return {
        plugins: [
            datadogVitePlugin({
                auth: {
                    apiKey: env.DD_API_KEY,
                    appKey: env.DD_APP_KEY,
                },
            }),
        ],
    };
});
```

After updating `vite.config.ts`, restart the dev server. Credentials will be read from `.env.local` without shell exports, and OAuth remains the default when either key is absent.

## Upload Fails With 403 "you do not have access to this app"

The build and source map upload succeed but asset upload to App Builder fails with `HTTP 403 Forbidden: you do not have access to this app`.

For key-based auth, this is usually an application key permissions issue. The app key needs **two** scopes enabled:

1. **Actions API Access** — required for backend function execution during local dev.
2. **Apps** (or **App Builder**) — required for uploading and publishing app assets.

Fix: go to `https://app.datadoghq.com/organization-settings/application-keys`, find your key, and confirm both scopes are enabled. If the Apps scope is missing, create a new key with both scopes.

After updating the key, re-run `npm run upload`. For OAuth, confirm the authorized user has access to upload the app.

## Build Succeeds But Nothing Uploads

- When the intent is to upload, use `npm run upload`; do not rely on `npm run build` as the upload path.
- Confirm `dryRun` in `vite.config.ts` is not set to `true`.
- Check whether the upload output printed a Datadog app URL.
- Confirm `DD_APPS_UPLOAD_ASSETS` is enabled by the upload path.

## Build Fails With Missing Credentials

- Current scaffold versions may make `npm run build` exercise Datadog upload behavior.
- Complete the OAuth browser flow before running build commands that touch Datadog.
- If using key-based auth, ensure both `DD_API_KEY` and `DD_APP_KEY` are set.
- For credential-free validation, prefer `npm run typecheck` when available.

## Lint Fails Before Checking Code

- Some scaffold versions install ESLint 9 while generating legacy `.eslintrc.cjs`.
- If lint fails with an ESLint config-file error before checking source code, do not treat it as an app logic failure.
- Run `npm run typecheck` and build/upload validation, and report the scaffold lint mismatch to the Datadog Apps maintainers.

## Node Or Scaffolding Errors

- The generated app guidance expects Node.js 20.19+ on the Node 20 release line, or Node.js 22.12+ on the Node 22 release line.
- If errors persist on a supported version, use a current Node 22 release.
- Use Volta, nvm, fnm, or the Node installer to switch versions.

## Datadog Site Mismatch

- Inspect `vite.config.ts` for the configured Datadog site.
- If using OAuth, complete authorization against the same site.
- Ensure CI uses the same site configuration as local development.

## Backend Function Issues

- Confirm backend files match `*.backend.ts` or `*.backend.js`.
- For local development, run `npm run dev` and complete OAuth authorization when prompted.
- If using key-based auth, confirm both `DD_API_KEY` and `DD_APP_KEY` are set and the application key has Actions API Access.
- Prefer `@datadog/action-catalog` typed actions when available.
- Check frontend imports reference the backend module path exactly.

## Browser Debugging

If Playwright is available, headed mode can be useful for troubleshooting local app behavior because it shows the browser session while preserving automation and console/network inspection:

```bash
npx playwright test --headed
```

Adapt the command to the app's configured test scripts when they exist.

## Getting Help

If the user is stuck or has additional Datadog Apps questions, direct them to open an issue at https://github.com/DataDog/datadog-apps-claude-plugin/issues or visit the [Datadog developer documentation](https://docs.datadoghq.com/developers/apps/).
