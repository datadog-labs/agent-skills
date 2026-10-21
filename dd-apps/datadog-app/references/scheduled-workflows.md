# Scheduled And API-Triggered Workflows

Use this when a Datadog App needs a backend function to run on a schedule, refresh data periodically, or be callable through the Workflow Automation API.

Datadog Apps have no built-in scheduler. Backend functions run in response to a frontend call. To run one on a schedule, publish it as a Workflow Automation workflow and let the workflow scheduler invoke it. The workflow holds the schedule; the app holds the code.

This is the inverse of `references/workflow-http-trigger.md`. Read that one when an app needs to *call* an existing workflow. Read this one when a backend function needs to *become* a workflow.

## Requirements

- The app must have at least one backend function export to publish.
- `DD_API_KEY` and `DD_APP_KEY` must be set. These scripts call the Datadog API directly, which requires key-based auth even though normal local development and upload use OAuth by default.
- Scheduled and API-triggered workflows must be published, so the app must be uploaded, not only built.

## How It Works

Uploading an app registers each backend function export as an App Builder action query. The creator script reads the uploaded app definition, lifts one of those action queries into a standalone workflow step, and rewires its inputs so the workflow scheduler can supply them.

The script at `<skill-dir>/scripts/create-highcode-workflow.mjs`:

1. Requires `DD_API_KEY` and `DD_APP_KEY` in the environment.
2. Resolves the Datadog domain from `--domain`, manifest `domain`, `vite.config.*` Datadog `site`/`domain`, then defaults to `app.datadoghq.com`.
3. Runs `DD_APPS_UPLOAD_ASSETS=1 DATADOG_APPS_UPLOAD_ASSETS=1 npm run build`.
4. Parses the App Builder app ID from the upload output.
5. Calls `GET /api/v2/app-builder/apps/{app_id}`.
6. Finds the action query whose backend function export name matches `--function`.
7. Rewrites the generated backend-function wrapper from `$.backendFunctionArgs` to one workflow input object at `$.Trigger.params`.
8. Copies `allowedConnectionIds` into the workflow step and workflow `connectionEnvs`.
9. Wires the JavaScript action return value into a workflow output named `output`.
10. Creates the workflow with `POST /api/v2/workflows`, or updates it with `PATCH /api/v2/workflows/{workflow_id}` when a workflow ID is provided.

## One-Off Workflow

Run the script from the app repository. Validate with `--dry-run --print-spec` before creating anything:

```bash
node <skill-dir>/scripts/create-highcode-workflow.mjs \
  --function refreshDatastore \
  --workflow-name "Refresh Jira datastore" \
  --schedule-rrule $'DTSTART;TZID=America/New_York:20260520T090000\nFREQ=HOURLY;INTERVAL=1' \
  --params-json '{}' \
  --dry-run \
  --print-spec
```

Then create it:

```bash
node <skill-dir>/scripts/create-highcode-workflow.mjs \
  --function refreshDatastore \
  --workflow-name "Refresh Jira datastore" \
  --schedule-rrule $'DTSTART;TZID=America/New_York:20260520T090000\nFREQ=HOURLY;INTERVAL=1' \
  --params-file params.json
```

The schedule is an RRULE. `FREQ=HOURLY;INTERVAL=1` refreshes every hour; `FREQ=MINUTELY;INTERVAL=15` every fifteen minutes. `DTSTART;TZID=<zone>` anchors the series. Workflow Automation enforces its own minimum interval, so sub-minute "every N seconds" refreshes are not available through the scheduler. Poll from the frontend for those.

## Project Upload Setup

To make repeated uploads deterministic, run `<skill-dir>/scripts/setup-app-upload.mjs`. It writes into the app project:

- `package.json` script: `"upload": "bash .datadog/create-highcode-workflows.sh"`
- `.datadog/create-highcode-workflows.sh`
- `.datadog/highcode-workflows.json`
- `.datadog/create-highcode-workflow.mjs`

```bash
node <skill-dir>/scripts/setup-app-upload.mjs \
  --project-dir /path/to/app \
  --workflow refreshDatastore:"Refresh Jira datastore" \
  --schedule-rrule $'DTSTART;TZID=America/New_York:20260520T090000\nFREQ=HOURLY;INTERVAL=1' \
  --api-trigger
```

Then, with `DD_API_KEY` and `DD_APP_KEY` set:

```bash
npm run upload
```

The bash wrapper exports the Vite plugin site (`DD_SITE`/`DATADOG_SITE`), runs `DD_APPS_UPLOAD_ASSETS=1 DATADOG_APPS_UPLOAD_ASSETS=1 npm run build`, extracts the App Builder URL from build output, then runs the local creator script with `--config .datadog/highcode-workflows.json --app-url <uploaded app url>`.

The setup script also ensures `datadogVitePlugin({ logLevel: 'info', ... })` is present. The Datadog Vite plugin logs the App Builder URL at `info`, and the upload wrapper needs that URL to discover the uploaded app ID.

## Workflow ID Tracking

The manifest tracks create-then-update state:

```json
{
  "domain": "app.datadoghq.com",
  "workflows": [
    {
      "function": "refreshDatastore",
      "workflowName": "Refresh Jira datastore",
      "workflowId": "created-workflow-id-goes-here",
      "scheduleRrule": "DTSTART;TZID=America/New_York:20260520T090000\nFREQ=HOURLY;INTERVAL=1",
      "apiTrigger": true,
      "params": {},
      "outputName": "output",
      "outputType": "STRING"
    }
  ]
}
```

On the first successful run, the script creates missing workflows and writes their `workflowId` values back to `.datadog/highcode-workflows.json`. On later runs, entries with `workflowId` are updated with `PATCH /api/v2/workflows/{workflow_id}`. Use `--no-write-config` only for dry validation, otherwise created workflow IDs will not be persisted and re-running will create duplicate workflows.

## Important Details

- `--function` is the backend function export name, not the full hashed App Builder query name.
- The current bridge supports a single `params` object. Backend functions with multiple positional arguments need an adapter or script changes.
- Scheduled and API-triggered workflows must be published; the script rejects `--unpublished` with those triggers.
- Use `--app-id` or `--app-url` to skip the upload build and create a workflow from an already uploaded app definition.
- Use `--workflow-id <id>` for one-off updates, or set `workflowId` in the manifest for deterministic project updates.
- By default, workflow output `output` is a `STRING` whose value is `{{ Steps.JavaScript_Script.data }}`. Workflow Automation coerces object and array values to JSON strings for string outputs. Use `--output-type OBJECT`, `--output-name <name>`, or `--no-output` when needed.
- Domain precedence is explicit `--domain`, manifest `domain`, `vite.config.*`, then `app.datadoghq.com`. The setup script writes the Vite-detected domain into `.datadog/highcode-workflows.json`; site-style values such as `datadoghq.com` are accepted and mapped to the right UI/API domains.
- Keep the manifest domain and Vite `auth.site` aligned. The manifest domain uses UI domains such as `app.datadoghq.com`; Vite `auth.site` uses site names such as `datadoghq.com`. Generated upload scripts derive and export the matching `DD_SITE`/`DATADOG_SITE` from the manifest domain and fail early if the current environment points at a different site.
- Non-US1 sites need `--domain` set to that site's app domain so API calls and printed workflow links resolve correctly.

## Verification

After creating a workflow, trigger an API run to confirm the step executes:

```bash
curl -sS --fail-with-body -X POST \
  "https://api.datadoghq.com/api/v2/workflows/${WORKFLOW_ID}/instances" \
  -H "DD-API-KEY: ${DD_API_KEY}" \
  -H "DD-APPLICATION-KEY: ${DD_APP_KEY}" \
  -H "Content-Type: application/json" \
  -H "Accept: application/json" \
  -d '{"meta":{"payload":{"params":{}}}}'
```

Scheduled runs appear in the workflow's run history in the Datadog UI.
