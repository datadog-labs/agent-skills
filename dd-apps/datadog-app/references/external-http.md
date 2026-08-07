# External HTTP Requests

Use this when a Datadog App needs to call an external URL or API.

Make every external network call from a backend function through the generic HTTP action exported by `@datadog/action-catalog/http/http`. Do not use `fetch`, Node.js networking APIs, or third-party HTTP clients to call the network directly from app code.

## Default: No Connection

The HTTP action does not require a connection. Omit `connectionId` by default:

```ts
import { request } from "@datadog/action-catalog/http/http";

export async function getExample() {
  const response = await request({
    inputs: {
      verb: "GET",
      url: "https://api.example.com/data",
      responseParsing: "json",
      errorOnStatus: ["400-599"],
    },
  });

  return response.body;
}
```

## Optional Authenticated Connection

Include `connectionId` only when the user already has a Datadog HTTP connection whose stored authentication the request needs:

```ts
const response = await request({
  connectionId: "<EXISTING_HTTP_CONNECTION_ID>",
  inputs: {
    verb: "GET",
    url: "https://api.example.com/private-data",
    responseParsing: "json",
    errorOnStatus: ["400-599"],
  },
});
```

Do not assume a connection exists. Most users will not have one because a human must create it in the Datadog UI and copy its ID into the app. If the request requires connection-managed authentication and no suitable connection exists, direct the user to create one at `https://app.datadoghq.com/actions/connections`; never ask them to paste credentials into app code or the agent conversation.
