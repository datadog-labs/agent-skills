# Workflow HTTP Trigger

Use this when a Datadog App backend function needs to trigger a Datadog Workflow Automation workflow and poll the workflow instance result.

## Requirements

- Use a backend function, not frontend code. Backend functions can call `@datadog/action-catalog`.
- Use the generic HTTP action from `@datadog/action-catalog/http/http`.
- The HTTP action requires a connection ID for an HTTP connection configured in your Datadog org. Create or find a generic HTTP connection at `https://app.datadoghq.com/actions/connections` and copy its ID.
- Keep the app's configured Datadog site aligned with the Workflow API host. US1 apps use `https://api.datadoghq.com`.
- The target workflow must be published and must include an API trigger. In the workflow spec, look for a trigger object containing `apiTrigger`.
- Workflow inputs must match the workflow input schema exactly.

## Inspect The Workflow

Before wiring an app to a workflow, inspect the workflow's published shape using curl with your Datadog API credentials:

```bash
workflow_id="<WORKFLOW_ID>"
curl -sS --fail-with-body \
  "https://api.datadoghq.com/api/v2/workflows/${workflow_id}" \
  -H "DD-API-KEY: ${DD_API_KEY}" \
  -H "DD-APPLICATION-KEY: ${DD_APP_KEY}" \
  -H "Accept: application/json" \
| jq "{
    id: .data.id,
    name: .data.attributes.name,
    published: .data.attributes.published,
    inputSchema: .data.attributes.spec.inputSchema,
    outputSchema: .data.attributes.spec.outputSchema,
    hasApiTrigger: any(.data.attributes.spec.triggers[]?; has(\"apiTrigger\"))
  }"
```

Use this output to confirm:

- `published` is `true`.
- `inputSchema.parameters` contains the input names and types the app will send.
- `outputSchema.parameters` describes expected outputs.
- `hasApiTrigger` is `true`.

Do not copy browser UI curl cookies, CSRF tokens, or `_authentication_token` values into app code. Those belong to browser session-authenticated UI calls, not Datadog Apps backend functions.

## Trigger And Poll From A Backend Function

Inputs go under `meta.payload`; do not send the workflow inputs as the raw JSON body.

Replace `YOUR_HTTP_CONNECTION_ID` with the ID of an HTTP connection from your Datadog org (`https://app.datadoghq.com/actions/connections`).

```ts
import { request } from "@datadog/action-catalog/http/http";

const DATADOG_HTTP_CONNECTION_ID = "<YOUR_HTTP_CONNECTION_ID>";
const DATADOG_API_BASE_URL = "https://api.datadoghq.com";
const DEFAULT_POLL_TIMEOUT_MS = 120_000;
const INITIAL_POLL_DELAY_MS = 250;
const POLL_DELAY_MULTIPLIER = 1.05;

const jsonHeaders = [
  { key: "Accept", value: ["application/json"] },
  { key: "Content-Type", value: ["application/json"] },
];

type WorkflowInstanceResponse = {
  data?: {
    id?: string;
    attributes?: {
      endTimestamp?: string | null;
      outputs?: unknown;
      instanceStatus?: {
        detailsKind?: string;
        displayName?: string;
      };
    };
  };
};

type WorkflowRunResult = {
  instanceId?: string;
  statusKind?: string;
  displayStatus?: string;
  outputs?: unknown;
  body: unknown;
};

type PollWorkflowOptions = {
  timeoutMs?: number;
};

function workflowInstancesUrl(workflowId: string) {
  return `${DATADOG_API_BASE_URL}/api/v2/workflows/${workflowId}/instances`;
}

function getStatusKind(body: unknown): string | undefined {
  return (body as WorkflowInstanceResponse | undefined)?.data?.attributes
    ?.instanceStatus?.detailsKind;
}

function isRunningStatus(statusKind?: string) {
  return !statusKind || statusKind === "IN_PROGRESS";
}

function createSleepWithExponentialBackoff(
  initialDelayMs: number,
  multiplier: number,
) {
  let delay = initialDelayMs / multiplier;

  return () => {
    delay *= multiplier;

    // Randomize by plus or minus 20% to avoid synchronized polling.
    const jitterRange = delay * 0.4;
    const jitter = Math.random() * jitterRange - jitterRange / 2;
    const randomizedDelay = delay + jitter;

    return new Promise<void>((resolve) =>
      setTimeout(resolve, randomizedDelay),
    );
  };
}

function toRunResult(body: unknown): WorkflowRunResult {
  const response = body as WorkflowInstanceResponse | undefined;

  return {
    instanceId: response?.data?.id,
    statusKind: response?.data?.attributes?.instanceStatus?.detailsKind,
    displayStatus: response?.data?.attributes?.instanceStatus?.displayName,
    outputs: response?.data?.attributes?.outputs,
    body,
  };
}

export async function triggerWorkflow(
  workflowId: string,
  payload: Record<string, unknown>,
): Promise<WorkflowRunResult> {
  const response = await request({
    connectionId: DATADOG_HTTP_CONNECTION_ID,
    inputs: {
      verb: "POST",
      url: workflowInstancesUrl(workflowId),
      requestHeaders: jsonHeaders,
      responseParsing: "json",
      errorOnStatus: ["400-599"],
      body: JSON.stringify({
        meta: {
          payload,
        },
      }),
    },
  });

  return toRunResult(response.body);
}

export async function pollWorkflowInstance(
  workflowId: string,
  instanceId: string,
  { timeoutMs = DEFAULT_POLL_TIMEOUT_MS }: PollWorkflowOptions = {},
): Promise<WorkflowRunResult> {
  const expiresAt = Date.now() + timeoutMs;
  const sleepWithBackoff = createSleepWithExponentialBackoff(
    INITIAL_POLL_DELAY_MS,
    POLL_DELAY_MULTIPLIER,
  );

  while (Date.now() < expiresAt) {
    await sleepWithBackoff();

    const response = await request({
      connectionId: DATADOG_HTTP_CONNECTION_ID,
      inputs: {
        verb: "GET",
        url: `${workflowInstancesUrl(workflowId)}/${instanceId}`,
        requestHeaders: jsonHeaders,
        responseParsing: "json",
        errorOnStatus: ["400-599"],
      },
    });

    const result = toRunResult(response.body);

    if (!isRunningStatus(result.statusKind)) {
      return result;
    }

  }

  throw new Error(`Workflow instance ${instanceId} did not finish in time`);
}
```

Treat `statusKind === "SUCCEEDED"` as success. Treat `INSTANCE_ERROR`, `STEP_ERROR`, `CANCELED`, and other non-running statuses as terminal failures, and return or log the full response body so the caller can inspect `errorDetail`, `stepStateAssociations`, and any partial outputs.

The polling loop mirrors App Builder's async query polling pattern: it waits `250ms` before the first poll, grows by multiplier `1.05`, and applies random plus or minus 20% jitter to avoid synchronized polling. Waiting before the first poll also avoids transient `INSTANCE_NOT_FOUND` responses immediately after a successful trigger. The default `120s` timeout follows App Builder's default frontend timeout pattern.

If a polling request fails, surface the error or response body to the caller. Do not replace it with a generic polling failure because the workflow instance body often contains the useful `errorDetail`.

`data.attributes.outputs` may be present after a successful run, but the full response body is the source of truth.

## Troubleshooting

- `input parameter "<name>" not in workflow input schema`: update `meta.payload` keys to match `attributes.spec.inputSchema.parameters`.
- `Expected trigger type TRIGGER_TYPE_API not found`: add and publish an API trigger on the workflow.
- 401, 403, or missing authentication errors: confirm `DD_API_KEY` and `DD_APP_KEY` are exported, the app key has Actions API Access, and the app's Datadog site matches the API host and connection.
- Empty or missing outputs: inspect the full workflow instance response and the workflow's `outputSchema`.
