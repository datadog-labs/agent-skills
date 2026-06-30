# Connections

Use this when a Datadog App or Workflow Automation action needs credentials for a Datadog, cloud, SaaS, generic HTTP, or third-party integration.

Connections are part of the Action Catalog ecosystem. They provide reusable authentication configuration for actions that need credentials. Some actions can use credentials from a Datadog integration tile; other actions need explicit connection credentials.

Public docs:

- [Connections](https://docs.datadoghq.com/actions/connections/)
- [App Builder Access and Authentication](https://docs.datadoghq.com/actions/app_builder/access_and_auth/)
- [Action Connection API](https://docs.datadoghq.com/api/latest/action-connection/)
- [Action Catalog](https://docs.datadoghq.com/actions/action_interface/)

## When Connections Matter

Use Connections guidance when:

- An Action Catalog action has a `connectionId` or Connection field.
- The app calls a generic HTTP action with custom authentication.
- The app needs credentials for an integration that does not inherit auth from a Datadog integration tile.
- Different apps or workflows should use different credential scopes.
- The workflow needs connection groups or identifier tags to resolve the right account or environment.

Some integrations, such as GitHub, Jira, Microsoft Teams, Opsgenie, PagerDuty, Slack, and Statuspage, may inherit credentials from their integration tile. For other integrations or custom actions, set up a connection.

## Discovery

Before telling an agent to wire an action:

- Identify which Action Catalog action will be used.
- Check whether the action requires a connection, supports integration tile auth, or can run without credentials.
- Find or create the relevant connection in Datadog Connections.
- Confirm the connection is scoped with only the permissions needed for the app or workflow.
- Copy the Connection ID from the connection details when the app implementation needs a stable ID.

Connections are managed in Datadog at `https://app.datadoghq.com/actions/connections`.

## Security Guidance

- Do not ask users to paste API keys, passwords, OAuth secrets, private keys, or other credentials into an AI conversation.
- Do not create connections directly from an AI agent when doing so would require secrets to enter the agent context, shell history, logs, or generated files.
- Have a human create or update connections in the Datadog UI, or use an approved secret-handling workflow that keeps credential material out of AI-visible context.
- Prefer granular connections for different apps, workflows, environments, or permission scopes.
- Do not reuse a high-privilege connection for unrelated actions.
- Restrict who can edit, resolve, or use a connection.
- Treat connection IDs as configuration, not secrets; the credentials remain stored in Datadog.
- Leave exact app-code organization for connection IDs to the generated app scaffold's `AGENTS.md`.
