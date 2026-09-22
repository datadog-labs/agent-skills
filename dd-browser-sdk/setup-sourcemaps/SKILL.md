---
name: setup-browser-sdk-sourcemaps
description: >
  Configure build-time JavaScript sourcemap upload to Datadog using the Datadog build plugin
  (@datadog/esbuild-plugin, @datadog/rollup-plugin, @datadog/rspack-plugin, @datadog/vite-plugin,
  @datadog/webpack-plugin), so RUM and Error Tracking show un-minified stack traces with git
  metadata attached. Use when errors in Datadog show minified stack traces, when asked to
  "upload sourcemaps", "set up sourcemaps", "unminify errors", or "configure the Datadog build
  plugin", or when a project has a bundler config and @datadog/browser-rum but no sourcemap upload.
metadata:
  version: "1.0.0"
  author: datadog-labs
  repository: https://github.com/datadog-labs/agent-skills
  tags: datadog,browser-sdk,rum,error-tracking,sourcemaps,build-plugin,webpack,vite,rollup,esbuild,rspack
---

# Set up Datadog sourcemap upload via the build plugin

Adds build-time sourcemap upload to a frontend project using the Datadog build plugin. Sourcemaps
are uploaded as part of the build, so every deploy ships un-minified stack traces to RUM and Error
Tracking. Git metadata is attached automatically, which links errors back to source files.

Follow steps 1-7 in order.

**Before starting any work:** Create exactly 7 todo items — one per step below. Do not begin
implementation until all 7 todos are created. Do not compress or merge steps.

**Never invent a value.** Three fields (`service`, `releaseVersion`, `minifiedPathPrefix`) must
match how the app is actually deployed. If you cannot read a value from the repo, stop and ask the
user. Do not write a placeholder, a `TODO`, or a guessed hostname.

## Scope

In scope: projects built by esbuild, Rollup, Rspack, Vite, or webpack — including meta-frameworks
that expose one of these directly in their own config file (e.g. Remix/React Router v7+ framework
mode, SvelteKit, Nuxt via `vite.config.ts`/`nuxt.config.ts`'s `vite:` key). If Step 1's config-file
search below finds a real bundler config, the meta-framework sitting on top of it does not change
anything — wire the plugin into that config file exactly as normal.

Out of scope: Next.js and Turbopack specifically. Unlike the meta-frameworks above, Next.js does
not expose a plain webpack/Turbopack config surface the plugin can hook into — it has no Next.js
or Turbopack integration at all. For those, use the `datadog-ci sourcemaps upload` CLI in a
`postbuild` script instead — do not attempt to thread the webpack plugin through `next.config.js`.

If a project uses some other meta-framework not listed here and Step 1 finds no plain bundler
config file to edit, stop and ask rather than guessing whether it's supported.

## Step 1: Detect the bundler

Search for a bundler config file:

```
find . -maxdepth 1 \( -name "vite.config.*" -o -name "rollup.config.*" -o -name "webpack.config.*" -o -name "rspack.config.*" -o -name "esbuild.config.*" \) 2>/dev/null
grep -l -E "\"(vite|rollup|webpack|@rspack/core|esbuild)\"" package.json
```

Use `find`, not `ls` with these glob patterns directly — in zsh, an `ls` invocation with multiple
glob patterns aborts with `no matches found` the moment any one pattern fails to match, even with
`2>/dev/null` appended, because the failure happens during shell word-expansion before `ls` ever
runs. `find` does its own pattern matching internally, so it works identically in bash and zsh and
correctly returns nothing (not an error) when no bundler config exists.

Map the result:

| Config file | Bundler | Package to install | Named export |
|---|---|---|---|
| `vite.config.{js,ts,mjs}` | Vite | `@datadog/vite-plugin` | `datadogVitePlugin` |
| `webpack.config.{js,ts,mjs}` | webpack | `@datadog/webpack-plugin` | `datadogWebpackPlugin` |
| `rollup.config.{js,ts,mjs}` | Rollup | `@datadog/rollup-plugin` | `datadogRollupPlugin` |
| `rspack.config.{js,ts,mjs}` | Rspack | `@datadog/rspack-plugin` | `datadogRspackPlugin` |
| esbuild build script | esbuild | `@datadog/esbuild-plugin` | `datadogEsbuildPlugin` |

Supported bundler versions:

| Bundler | Supported range |
|---|---|
| esbuild | any |
| Rollup | `>= 3 < 5` |
| Rspack | `1.x` or `2.x` |
| Vite | `>= 5 <= 8` |
| webpack | `>= 5 < 6` |

**Stop and ask the user if:** no bundler config is found, more than one is found (monorepo — ask
which app to instrument), Next.js or Turbopack is detected (out of scope, see above), or the
bundler version is outside the supported range.

## Step 2: Collect the three required values

The plugin will not build without these three. Read them from the repo; ask the user for anything
you cannot read.

**`service`** — must equal the `service` passed to `datadogRum.init()`. Datadog matches uploaded
sourcemaps to incoming errors on this value. A mismatch means the sourcemaps upload successfully
and are never used.

```
grep -rn "datadogRum.init" --include="*.ts" --include="*.tsx" --include="*.js" --include="*.jsx" -A 15
```

If the grep finds no `datadogRum.init()` call anywhere, stop: this skill only adds sourcemap
upload to an existing RUM setup, it does not instrument RUM itself. Tell the user Browser RUM
needs to be set up first, then re-run this skill.

If `datadogRum.init()` has no `service`, stop and tell the user: sourcemaps cannot be matched
without it, and it must be added to the RUM init first.

**`releaseVersion`** — must equal the `version` passed to `datadogRum.init()`. Typically a git
commit SHA, release tag, or CI build ID. Can be set here, or once at the top level as
`metadata.version`. If both are set and differ, the build fails with
`sourcemaps.releaseVersion must match metadata.version when both are configured`.

**`minifiedPathPrefix`** — the URL prefix the built JS is served from. Must be a full URL or start
with `/`, otherwise the build fails with
`sourcemaps.minifiedPathPrefix must be a valid URL or start with '/'`.

Worked example: if `dist/main.js` is served at `https://example.com/static/main.js`, then
`minifiedPathPrefix` is `https://example.com/static/` or `/static/`. If JS is served from the
server root, `/` is valid.

This is the field most often wrong, and it cannot be derived from the bundler config alone. Before
asking the user, check the repo for how it actually deploys:

1. Check the bundler's own `base`/`publicPath`/`baseURL` config first — if set, that already
   answers the question.
2. Otherwise check deploy/CI config for the actual deploy command and target directory:
   `.github/workflows/*.yml`, `.gitlab-ci.yml`, `vercel.json`, `netlify.toml`, `wrangler.toml`,
   `Dockerfile`. If the deploy step ships the bundler's output directory verbatim (e.g.
   `pages deploy ./dist`, `netlify deploy --dir=build`), the prefix is the bundler's default asset
   subdirectory relative to that root — Vite's default is `/assets/`, Create React App's is
   `/static/js/`, etc.
3. Check for path-rewrite config that could override the answer from step 2 above: Cloudflare
   `_routes.json` / `_redirects`, Netlify `_redirects`, Vercel `rewrites` in `vercel.json`. If any
   exist, re-derive the prefix from the rewrite rule instead of the raw build output path.
4. If none of the above resolves it — no committed deploy config, a custom domain/CDN configured
   only in a hosting dashboard — stop and ask the user. Give them a concrete way to check rather
   than just asking blind: open browser devtools on the live site, go to the Network tab, reload,
   and find the URL the main JS bundle actually loads from.

Do not write a placeholder, a `TODO`, or a guessed hostname even after checking all of the above.

## Step 3: Install the plugin

```
npm install --save-dev @datadog/<bundler>-plugin
```

Use the project's package manager: `yarn add -D`, `pnpm add -D`, or `bun add -d`.

## Step 4: Make the bundler emit sourcemaps

**This is the most common silent failure.** The plugin uploads whatever `.map` files the build
produces. If the bundler is not configured to emit them it finds nothing, logs only a
`No sourcemaps to upload` warning, and the build still succeeds. Nothing appears in Datadog and
there is no error to explain why.

These are bundler-native settings, not plugin options. The Datadog build plugin does not set them
and does not document them.

| Bundler | Setting |
|---|---|
| webpack | `devtool: 'source-map'` |
| Rspack | `devtool: 'source-map'` |
| Vite | `build.sourcemap: true` |
| Rollup | `output.sourcemap: true` |
| esbuild | `sourcemap: true` |

Add the setting if it is missing. If the project deliberately disables sourcemaps in production,
stop and raise it with the user — hidden sourcemaps served publicly is a real concern, and the
plugin uploads then deletes nothing, so the `.map` files still ship unless the deploy strips them.

## Step 5: Wire the plugin into the config

Register the plugin **first** in the `plugins` array — the plugin's own README recommends this
across all five bundlers for complete build reporting.

Do not put the API key in the config file. The plugin reads `DATADOG_API_KEY` (or `DD_API_KEY`)
from the environment, and the environment takes precedence over the config value. Leaving it out
keeps credentials out of version control.

**Vite** (`vite.config.js`):

```js
import { defineConfig } from 'vite';
import { datadogVitePlugin } from '@datadog/vite-plugin';

export default defineConfig({
  build: { sourcemap: true },
  plugins: [
    datadogVitePlugin({
      errorTracking: {
        sourcemaps: {
          service: 'my-service',
          releaseVersion: process.env.GIT_COMMIT_SHA,
          minifiedPathPrefix: '/static/',
        },
      },
    }),
  ],
});
```

**webpack** (`webpack.config.js`):

```js
const { datadogWebpackPlugin } = require('@datadog/webpack-plugin');

module.exports = {
  devtool: 'source-map',
  plugins: [
    datadogWebpackPlugin({
      errorTracking: {
        sourcemaps: {
          service: 'my-service',
          releaseVersion: process.env.GIT_COMMIT_SHA,
          minifiedPathPrefix: '/static/',
        },
      },
    }),
  ],
};
```

**Rspack** (`rspack.config.js`) — same as webpack, with `datadogRspackPlugin` from
`@datadog/rspack-plugin`.

**Rollup** (`rollup.config.js`):

```js
import { datadogRollupPlugin } from '@datadog/rollup-plugin';

export default {
  output: { sourcemap: true },
  plugins: [
    datadogRollupPlugin({
      errorTracking: {
        sourcemaps: {
          service: 'my-service',
          releaseVersion: process.env.GIT_COMMIT_SHA,
          minifiedPathPrefix: '/static/',
        },
      },
    }),
  ],
};
```

**esbuild** (build script):

```js
const { datadogEsbuildPlugin } = require('@datadog/esbuild-plugin');

require('esbuild').build({
  sourcemap: true,
  plugins: [
    datadogEsbuildPlugin({
      errorTracking: {
        sourcemaps: {
          service: 'my-service',
          releaseVersion: process.env.GIT_COMMIT_SHA,
          minifiedPathPrefix: '/static/',
        },
      },
    }),
  ],
});
```

Replace every value with the ones collected in step 2. Do not leave `'my-service'` or `'/static/'`
in place.

Optional sub-options, only if the user asks:

| Option | Default | Effect |
|---|---|---|
| `dryRun` | `false` | Documented to do everything except the upload — do not rely on this for safety, see step 7 |
| `bailOnError` | `false` | Fail the build on the first upload error |
| `maxConcurrency` | `20` | Concurrent uploads |

## Step 6: Set up credentials in the build environment

The upload needs an API key. No application key is required.

| Variable | Purpose |
|---|---|
| `DATADOG_API_KEY` or `DD_API_KEY` | Required. Without it the upload fails with `No authentication token provided`. |
| `DATADOG_SITE` or `DD_SITE` | Required for non-US1 orgs. Defaults to `datadoghq.com`. Use `datadoghq.eu`, `us3.datadoghq.com`, `us5.datadoghq.com`, `ap1.datadoghq.com`, `ap2.datadoghq.com`, `ddog-gov.com` as appropriate. |

Set these as CI/build secrets. Never write the key to a committed file.

Check the repo for what actually executes the build command (the `build` script) before asking —
that is where the secret needs to be injected, which is not always the same as the eventual
hosting target. A GitHub Actions workflow can run the build and then deploy the output to
Cloudflare Pages/Netlify/Vercel/S3 as a separate step; in that case the secret goes on the
workflow, not the host.

| Signal found in repo | Where the build actually runs | Where to add the secret |
|---|---|---|
| `.github/workflows/*.yml` with a build/install step | GitHub Actions | Repo Settings → Secrets and variables → Actions |
| `.gitlab-ci.yml` | GitLab CI | Settings → CI/CD → Variables |
| `vercel.json`, or no CI file and deployed via Vercel's own build | Vercel | Project Settings → Environment Variables |
| `netlify.toml`, or no CI file and deployed via Netlify's own build | Netlify | Site Settings → Environment variables |
| `Dockerfile`/`docker-compose.yml` with no CI workflow calling it | Docker, wherever the image is built | Depends on the deploy pipeline — ask |

**Confirm the detected provider with the user rather than assuming** — a repo can have leftover
config from a provider it no longer uses. If nothing matches, stop and ask where the build runs.

## Step 7: Verify

Git metadata is attached automatically. `enableGit` is a top-level option that defaults to `true`,
so there is nothing to switch on — only something to avoid breaking. Do not set it unless step 7
turns up the specific error below.

**Do not rely on `dryRun: true` to test safely.** It is documented to "do everything except the
upload," but this is not trustworthy — at least one real published plugin version ignores the flag
entirely and makes the live, authenticated upload anyway. Treat `dryRun` as unverified for safety
purposes; it may or may not actually skip the network call in whatever version is installed.

The only way to test discovery that is actually guaranteed not to upload anything: make sure
`DATADOG_API_KEY`/`DD_API_KEY` are **not set anywhere in the environment the build runs in** —
check for this explicitly, a key left over from unrelated local tooling (e.g. a personal
`datadog-ci` setup) counts and will be picked up silently. With no key reachable, the plugin cannot
authenticate no matter what `dryRun` does. Then run a normal production build (no `dryRun` needed):

- If it logs `No sourcemaps to upload`, step 4 is not correct — the plugin found no `.map` files.
  This check happens locally before any network call, regardless of credentials.
- If it does not log that — you may see git-metadata warnings, then the build fails with
  `No authentication token provided` — that confirms steps 4 and 5 are correct. The plugin found
  the sourcemaps; it only stopped because no key was reachable, which is the expected, safe
  outcome when none is set.

Only move on to testing the real upload path once that passes, and only deliberately — this is a
live action, not a verification step to run casually. Confirm with the user, explicitly, which key
will be used:
- If they want to verify the real upload path locally, they need their own valid
  `DATADOG_API_KEY` (and `DATADOG_SITE` if not US1) exported in the shell for this test.
- If a key is already set in their shell for unrelated tooling, that key gets used here too —
  confirm that's actually intended before proceeding, since it uploads to whatever org that key
  belongs to, not a sandboxed or CI-only destination, and it does so immediately once present.

**If the build fails with `Error: No git remotes available`:** the build environment has no git
remote, which is common in shallow CI clones and container builds. Two options, in order of
preference: make the git remote available in the build (better — keeps the error-to-source
linking), or set `enableGit: false` at the top level of the plugin config to disable git
collection entirely.

```js
datadogWebpackPlugin({
  enableGit: false,
  errorTracking: { sourcemaps: { /* ... */ } },
})
```

Confirm end to end: trigger an error in the deployed app and check that the stack trace in Datadog
Error Tracking is un-minified.

## Common mistakes

- Leaving `service` or `releaseVersion` different from the values in `datadogRum.init()`. The upload
  succeeds and the sourcemaps are never matched to any error. This is silent.
- Forgetting the bundler sourcemap setting in step 4. The build passes with only a warning.
- Guessing `minifiedPathPrefix` from the output directory instead of the served URL. `dist/` is not
  a URL prefix.
- Putting `apiKey` in the committed config file instead of the build environment.
- Setting `enableGit: true` because a ticket said to "enable git". It is already the default;
  setting it changes nothing.
- Setting both `releaseVersion` and `metadata.version` to different values, which fails the build.
- Trying to use the build plugin on Next.js. It has no Next.js integration.

## Verification checklist

- [ ] Bundler detected, version in the supported range
- [ ] Plugin package installed as a dev dependency
- [ ] Bundler configured to emit sourcemaps (step 4 table)
- [ ] Plugin registered first in the `plugins` array
- [ ] `service` matches `datadogRum.init()`'s `service`
- [ ] `releaseVersion` matches `datadogRum.init()`'s `version`
- [ ] `minifiedPathPrefix` is a full URL or starts with `/`, and matches where JS is served
- [ ] No API key in any committed file
- [ ] `DATADOG_API_KEY` (and `DATADOG_SITE` if not US1) set in the build environment
- [ ] Build with no API key reachable reports sourcemaps found (reaches
      `No authentication token provided`), not `No sourcemaps to upload` — do not use `dryRun` for
      this check, see step 7
- [ ] Real build completes and a deployed error shows an un-minified stack trace
