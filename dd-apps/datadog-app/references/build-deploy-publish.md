# Build, Deploy, And Publish

Use this when the user needs to build, deploy, publish, configure the Datadog site, or interpret deploy output.

## Build And Deploy

Inspect the generated `package.json` scripts before choosing a command; scaffolder behavior can change. Use `typecheck` for a credential-free TypeScript check when available:

```bash
npm run typecheck
```

**Using OAuth (default for local dev):**

If OAuth credentials are cached from local dev, no environment variables are needed:

```bash
npm run deploy
```

**Using API keys (CI and environments without OAuth):**

```bash
export DD_API_KEY="<YOUR_API_KEY>"
export DD_APP_KEY="<YOUR_APPLICATION_KEY>"
npm run deploy
```

`npm run deploy` builds the app, uploads it, and publishes the new version live. A successful deploy prints a Datadog URL for the app.

### Deploy without publishing

To upload a new version as a draft without making it live — useful for staging environments or CI pipelines with a separate approval step:

```bash
npm run deploy -- --no-publish
```

After a deploy without publish, the new version appears in the App Builder version history but does not go live.

## Publish

To publish an already-uploaded version without rebuilding:

```bash
npm run publish
```

This publishes the most recently uploaded version (read from `.datadog-app-version.json` in the project root, written automatically after each `deploy`).

To publish a specific version by ID:

```bash
npm run publish -- --version <version-id>
```

When using `--version` without a cache file, set `DD_APPS_IDENTIFIER` to the app's identifier so the CLI knows which app to publish.

## Older scaffolded projects

Projects scaffolded before `@datadog/vite-plugin` 3.2.0 may not have the `deploy` and `publish` scripts. Add them to `package.json`:

```json
{
    "scripts": {
        "deploy": "datadog-apps deploy",
        "publish": "datadog-apps publish"
    }
}
```

Requires `@datadog/vite-plugin` >= 3.2.0.

## Environment variables

- `DD_API_KEY`: Datadog API key.
- `DD_APP_KEY`: Datadog application key.
- `DD_APPS_IDENTIFIER`: override the app identifier (useful for `publish --version` without a cache file).
- `DD_APPS_PUBLISH`: set to `false` to deploy without publishing. The `--no-publish` flag does this automatically.
- `DD_APPS_VERSION_NAME`: optional unique uploaded version name.

## Datadog Site

For non-US1 sites, set the Datadog site in `vite.config.ts` so local development and deploys target the right site:

```ts
datadogVitePlugin({
  auth: {
    site: '<YOUR_DATADOG_SITE>',
  },
});
```

## Manage

After deploy, the app appears in the App Builder app list. From there users can also manage name, description, permissions, and embed the app in Datadog surfaces.

Locally built Apps cannot be edited with the low-code App Builder drag-and-drop UI. Update local code and re-deploy instead.
