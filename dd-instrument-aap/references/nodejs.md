# Node.js

Open this in step 5 for a Node.js service outside AWS Lambda. The tracer is the `dd-trace` package. Supported frameworks: `express`, `fastify`, `nextjs`.

## Contents

- Install `dd-trace`
- Start the app
- Bundled apps
- Next.js
- In a container image

## Install `dd-trace`

When `dd-trace` is already a dependency, keep its major version. Update to the latest release of that major, such as `dd-trace@^5`, only when a later step needs a file that the installed release lacks: `dd-trace/register.js` for ES modules, or the `dd-trace/webpack` plugin, which needs 5.x or later. Otherwise install `dd-trace` with the package manager the project already uses (`npm install dd-trace`, `yarn add dd-trace` or `pnpm add dd-trace`), in the install step it already has. First read the Node.js version the app runs on (the Dockerfile `FROM node:<version>`, `engines.node`, `.nvmrc` or `.node-version`): dd-trace 6 needs Node.js 22 or later and does not start on older versions, so below Node.js 22 install `dd-trace@5`.

## Start the app

Preload the tracer before any app code, and set the AAP variables from step 5 in the place the deployment reference names:

```bash
node --require dd-trace/init app.js
```

ES modules (`import` syntax, `"type": "module"`, or a `.mjs` entry point) also need the loader, because `--require` alone instruments nothing:

- Node.js 20.6 or later: `node --import dd-trace/register.js --require dd-trace/init app.js`
- Node.js 18 to 20.5: `node --loader dd-trace/loader-hook.mjs --require dd-trace/init app.js`

Put the flags where the app starts: the `start` script, the service unit, the Dockerfile `CMD`, or `NODE_OPTIONS`.

## Bundled apps

For esbuild or Webpack (not Next.js):

- Add the `dd-trace/esbuild` plugin to the esbuild config, or the `dd-trace/webpack` plugin to the Webpack config.
- Keep the native AAP modules out of the bundle: `@datadog/native-appsec`, `@datadog/native-iast-taint-tracking` and `@datadog/wasm-js-rewriter` (`@datadog/native-iast-rewriter` before dd-trace 5.46).
- In the runtime stage, install `dd-trace` and those native modules, but not the dependencies already inside the bundle.
- Leave no build output, such as `dist/`, or a lockfile you created, in the project.

## Next.js

`next dev` and `next start` load modules before app code, so preload the tracer through `NODE_OPTIONS` in the scripts, with the AAP variables next to it:

```json
"start": "DD_APPSEC_ENABLED=true DD_APPSEC_AGENTIC_ONBOARDING=true DD_APM_TRACING_ENABLED=false NODE_OPTIONS='--require dd-trace/init' next start"
```

Drop `DD_APM_TRACING_ENABLED=false` when APM was on. Set the same variables wherever else Next.js starts, such as `.env.local`, the platform settings or the image.

- **Next.js below 15:** add a `webpack` hook to `next.config.*` that pushes `@datadog/native-appsec`, `@datadog/native-iast-taint-tracking` and `@datadog/wasm-js-rewriter` (plus `@datadog/native-metrics`, `@datadog/pprof` or `@datadog/libdatadog` when used) to `config.externals`, and merge `dd-trace` into `experimental.serverComponentsExternalPackages`. Merge with existing config. Next.js 15 and later need neither, because `dd-trace` is on their built-in server externals list.
- **`output: 'standalone'`:** file tracing copies only what it detects. Make it copy the tracer with both of these, because each one supplies files the other misses:
  - `import 'dd-trace'` at the top of the root `app/layout.tsx` or `pages/_document.tsx` (server code, never a client component). Never `import 'dd-trace/init'`, which starts the tracer inside `next build`.
  - `outputFileTracingIncludes: { '/*': ['./node_modules/dd-trace/**'] }` in `next.config.*` (under `experimental` below Next.js 15). In a monorepo with a hoisted `dd-trace`, point the glob at the workspace root, such as `'../../node_modules/dd-trace/**'`, and set `outputFileTracingRoot` to it.
- **After `next build` with standalone output**, check `test -d .next/standalone/node_modules/dd-trace` and that `@datadog/native-appsec` is under `.next/standalone/node_modules/@datadog/`. If either is missing, install the resolved `dd-trace` version into a temporary directory with `npm install --omit=dev --install-strategy=nested "dd-trace@<version>"` (npm 9 or later), and move that `dd-trace` folder into `.next/standalone/node_modules/`.
- **Start standalone output** as `node --require dd-trace/init server.js` with `.next/standalone` as the working directory, or with absolute paths to both. The generated `server.js` is CommonJS: never add `--import dd-trace/register.js`, `--import dd-trace/initialize.mjs` or `--loader`, even through `NODE_OPTIONS`, because the loader's dependencies are not in the standalone output and the server crashes at start. For the same reason, add no `instrumentation.ts` hook to standalone apps.
- When the app ships as an image, build it, run it, and request a working route before you report success. The file checks above do not prove that the server starts.

## In a container image

Copy the manifest and its lockfile before the install step, and install with the project's package manager: `npm ci` with `package-lock.json`, `yarn install --frozen-lockfile` (Yarn 1) or `yarn install --immutable` (Yarn 2 and later) with `yarn.lock`, `pnpm install --frozen-lockfile` with `pnpm-lock.yaml`. Copy `.yarnrc.yml` and `.yarn/` only when they exist, because `COPY` fails on a missing path. In a workspace, also copy the workspace and per-package manifests the install needs. Do not add a separate `RUN npm install dd-trace` when `package.json` declares it, and add no variables AAP does not need, such as `NODE_ENV`. Then start with `CMD ["node", "--require", "dd-trace/init", "app.js"]`, plus the ESM loader from above when the app uses ES modules.
