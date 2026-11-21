# Node.js (server side)

## Contents

- Pick the release line
- Packages
- Bootstrap
- Load it first
- Logs

Browser code never exports to Datadog directly; see step 1 of `SKILL.md`.

## Pick the release line

Read the project's Node.js version from `engines.node`, `.nvmrc`, `.node-version` or the Dockerfile `FROM node:` tag. Compare it with the range the current SDK supports: `npm view @opentelemetry/sdk-node engines.node`.

- **Inside the range:** install the `latest` packages.
- **Older Node.js** (18.19 or later, or 20.6 or later): install the previous line. Use the `latest-2` dist-tag for every `@opentelemetry/sdk-*` and `@opentelemetry/exporter-*` package, and the newest `@opentelemetry/auto-instrumentations-node` whose `@opentelemetry/sdk-node` dependency matches that line (`npm view @opentelemetry/auto-instrumentations-node@<version> dependencies`). For example, the 0.81 releases pair with `sdk-node` 0.223.
- **Older still:** record the service as skipped with the reason.

Install every `@opentelemetry/*` package from one line. Mixing lines installs duplicate SDK packages whose versions are not tested together.

## Packages

Use the project's package manager (the lockfile tells you: `package-lock.json`, `pnpm-lock.yaml`, `yarn.lock` or `bun.lock`).

- Traces and metrics: `@opentelemetry/sdk-node`, `@opentelemetry/auto-instrumentations-node`, `@opentelemetry/exporter-trace-otlp-proto`, `@opentelemetry/exporter-metrics-otlp-proto`, `@opentelemetry/exporter-metrics-otlp-http` (for the temporality setting), `@opentelemetry/sdk-metrics` and `@opentelemetry/resources`.
- ESM apps also need `@opentelemetry/instrumentation` as a direct dependency (see "Load it first").
- Logs, only when step 4 of `SKILL.md` included them: `@opentelemetry/exporter-logs-otlp-proto` and `@opentelemetry/sdk-logs`.

The `sdk-node` and exporter packages are 0.x, and their READMEs call them experimental. Record that in `OPENTELEMETRY.md`. The opt-in question still follows the status table, where Node.js traces and metrics are Stable.

## Bootstrap

Create `telemetry.js` next to the entry file (`telemetry.ts` in a TypeScript project, or `telemetry.mjs` for ESM, written with `import` statements). Replace `<DD_SITE>`, `<service-name>`, `<env>` (the environment name the project uses, or `local`) and `<version>` (the manifest's version). Pass all three signal options: an omitted one makes the SDK build a default exporter that sends to `localhost:4318`. Use `metricReaders`, an array: the README still documents a singular `metricReader`, which the current `sdk-node` ignores, so no metrics are sent.

```js
// OpenTelemetry bootstrap: exports to Datadog. Load it before the app.
const { NodeSDK } = require('@opentelemetry/sdk-node');
const { getNodeAutoInstrumentations } = require('@opentelemetry/auto-instrumentations-node');
const { OTLPTraceExporter } = require('@opentelemetry/exporter-trace-otlp-proto');
const { OTLPMetricExporter } = require('@opentelemetry/exporter-metrics-otlp-proto');
const { AggregationTemporalityPreference } = require('@opentelemetry/exporter-metrics-otlp-http');
const { PeriodicExportingMetricReader } = require('@opentelemetry/sdk-metrics');
const { defaultResource, resourceFromAttributes } = require('@opentelemetry/resources');

// OTEL_EXPORTER_OTLP_ENDPOINT is set only on the route through a Datadog Agent.
const agent = process.env.OTEL_EXPORTER_OTLP_ENDPOINT;
const apiKey = process.env.DD_API_KEY;
const on = Boolean(agent || apiKey);
const base = (agent || 'https://otlp.<DD_SITE>').replace(/\/$/, '');
const headers = agent ? {} : { 'dd-api-key': apiKey };
const traceHeaders = agent ? {} : { ...headers, compute_stats: 'true' };

const sdk = new NodeSDK({
  serviceName: process.env.OTEL_SERVICE_NAME || '<service-name>',
  // Defaults for a start without OTEL_RESOURCE_ATTRIBUTES, which overrides them.
  resource: defaultResource().merge(resourceFromAttributes({
    'deployment.environment.name': '<env>',
    'service.version': process.env.npm_package_version || '<version>',
  })),
  instrumentations: [getNodeAutoInstrumentations()],
  ...(on
    ? { traceExporter: new OTLPTraceExporter({ url: `${base}/v1/traces`, headers: traceHeaders }) }
    : { spanProcessors: [] }),
  metricReaders: on
    ? [
        new PeriodicExportingMetricReader({
          exporter: new OTLPMetricExporter({
            url: `${base}/v1/metrics`,
            headers,
            temporalityPreference: AggregationTemporalityPreference.DELTA,
          }),
          exportIntervalMillis: Number(process.env.OTEL_METRIC_EXPORT_INTERVAL) || 60000,
        }),
      ]
    : [],
  logRecordProcessors: [],
});

sdk.start();
process.once('SIGTERM', () => {
  sdk.shutdown().finally(() => process.exit(0));
});
```

- The SDK's default resource detectors add host, process and container attributes, and read `OTEL_RESOURCE_ATTRIBUTES`, which wins over the defaults in code.
- When the app already handles `SIGTERM`, call `sdk.shutdown()` from that handler instead of adding a second one, because the added handler exits the process.
- When the app loads `.env` with `dotenv`, call `require('dotenv').config()` at the top of the bootstrap too. The bootstrap runs before the app's own load, and `dotenv` does not override variables that are already set.

## Load it first

Instrumentation patches modules as they load, so anything loaded before the bootstrap produces no spans.

- **CommonJS** (no `"type": "module"`, or TypeScript compiled to CommonJS): make `require('./telemetry');` (or `import './telemetry';` in TypeScript) the first line of the entry file.
- **ESM** (`"type": "module"`, `.mjs`, or TypeScript compiled to ESM): imports are hoisted, so a first-line import is not enough. Add `--experimental-loader=@opentelemetry/instrumentation/hook.mjs --import ./telemetry.mjs` to `NODE_OPTIONS` in the existing start surface: the deployment layer's env, the runtime stage of the Dockerfile, or the existing package script. The user still runs the same command.
  - Add `@opentelemetry/instrumentation`, from the same release line, as a direct dependency. The loader resolves the hook from the app's own `node_modules`, and pnpm does not hoist transitive packages, so without it the app fails with `ERR_MODULE_NOT_FOUND`.
  - In a Dockerfile, set `NODE_OPTIONS` after the install and build steps. An earlier `ENV NODE_OPTIONS` makes `npm ci` itself load the hook before it is installed, and the build fails.
- **Next.js:** load the bootstrap from `register()` in `instrumentation.ts` when `process.env.NEXT_RUNTIME === 'nodejs'`, with `await import('./telemetry')`.

## Logs

Only when step 4 of `SKILL.md` included logs. Replace `logRecordProcessors: []` with the following; `base`, `headers` and `on` come from the bootstrap:

```js
const { OTLPLogExporter } = require('@opentelemetry/exporter-logs-otlp-proto');
const { BatchLogRecordProcessor } = require('@opentelemetry/sdk-logs');

// inside new NodeSDK({ ... })
logRecordProcessors: on
  ? [new BatchLogRecordProcessor({
      exporter: new OTLPLogExporter({ url: `${base}/v1/logs`, headers }),
    })]
  : [],
```

Both release lines take the options object shown here; older SDKs took the exporter as the first argument.

The auto-instrumentations forward records from `pino` and `bunyan`. `winston` also needs `@opentelemetry/winston-transport`. `console.log` output is not captured, so an app that logs only to the console sends no logs; record that in `OPENTELEMETRY.md`.
