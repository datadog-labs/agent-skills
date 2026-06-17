# Build, Upload, And Publish

Use this when the user needs to build, upload, publish, configure the Datadog site, or interpret upload output.

## Build And Upload

Inspect the generated `package.json` scripts before choosing a command; scaffolder behavior can change. Use `typecheck` for a credential-free TypeScript check when available:

```bash
npm run typecheck
```

Export credentials and run build or upload commands:

```bash
export DD_API_KEY="<YOUR_API_KEY>"
export DD_APP_KEY="<YOUR_APPLICATION_KEY>"
npm run upload
```

`npm run upload` builds with asset upload enabled and publishes the new version live. A successful upload prints a Datadog URL for the uploaded app. In some scaffold versions, `npm run build` may also exercise Datadog upload behavior; if it fails with missing API or app keys, ensure credentials are exported or use `typecheck` for local compile validation.

## Upload Without Publishing

To upload a draft without immediately publishing it live, use the `upload-no-publish` script. This is useful for staging environments or CI pipelines where a separate approval step controls promotion.

Projects scaffolded with `npm create @datadog/apps` already include this script. Run it directly:

```bash
npm run upload-no-publish
```

If the project's `package.json` does not already have the script (older scaffolded projects), add it:

```json
{
    "scripts": {
        "upload": "DD_APPS_UPLOAD_ASSETS=1 vite build",
        "upload-no-publish": "DD_APPS_UPLOAD_ASSETS=1 DD_APPS_PUBLISH=false vite build"
    }
}
```

You can also pass the flag inline without a dedicated script:

```bash
DD_APPS_PUBLISH=false npm run upload
```

After an upload-no-publish, the new version appears in the App Builder version history but does not go live. Publish it from the App Builder UI when ready.

## Upload Environment

Relevant environment variables:

- `DD_API_KEY`: Datadog API key.
- `DD_APP_KEY`: Datadog application key.
- `DD_APPS_PUBLISH`: set to `false` to upload without publishing. Defaults to `true`.
- `DD_APPS_VERSION_NAME`: optional unique uploaded app version name.
- `DD_APPS_UPLOAD_ASSETS`: enables asset upload; upload scripts normally set this.

## Datadog Site

For non-US1 sites, set the Datadog site in `vite.config.ts` so local development and uploads target the right site:

```ts
datadogVitePlugin({
  auth: {
    site: '<YOUR_DATADOG_SITE>',
  },
});
```

## Publish And Manage

After upload, the app appears in the App Builder app list. From there users can publish the app, edit name and description, manage permissions, and embed it in supported Datadog surfaces.

Locally built Apps cannot be edited with low-code App Builder drag-and-drop UI. Update the local code and re-upload instead.
