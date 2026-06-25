# Getting Started Querying Data

Use this first when a Datadog App needs to read, transform, or mutate data and the right backend data access pattern is not yet clear.

Datadog Apps backend functions commonly use two data access paths:

- DDSQL: SQL for Datadog-visible data. Prefer this for read paths when the data is DDSQL-visible and the app needs projection, filtering, joins, grouping, ordering, limits, offsets, JSON/regex functions, window functions, or tag access.
- Action Catalog: typed actions for Datadog, cloud providers, SaaS tools, generic HTTP, and other integrations. Prefer this for mutations, product workflows, simple single-record reads, or sources that are not DDSQL-visible.
- Connections: reusable credentials and auth configuration for Action Catalog actions in App Builder and Workflow Automation. Use them when an action needs integration-specific credentials.

Public docs:

- [DDSQL Reference](https://docs.datadoghq.com/ddsql_reference/)
- [DDSQL Data Directory](https://docs.datadoghq.com/ddsql_reference/data_directory/)
- [Action Catalog](https://docs.datadoghq.com/actions/action_interface/)
- [`@datadog/action-catalog` npm package](https://www.npmjs.com/package/@datadog/action-catalog)
- [Connections](https://docs.datadoghq.com/actions/connections/)
- [App Builder Access and Authentication](https://docs.datadoghq.com/actions/app_builder/access_and_auth/)
- [Datastores](https://docs.datadoghq.com/actions/datastores/)
- [Use Datastores with Apps and Workflows](https://docs.datadoghq.com/actions/datastores/use/)

## Progressive Discovery

Use this root file only when the right data access path is not clear. Source-specific details for Action Catalog connections and Datadog App datastores are outside this overview and are routed directly from `SKILL.md`.

| Question | Next step |
| --- | --- |
| Is this a read from DDSQL-visible data? | Continue to the DDSQL section below. |
| Is this a mutation or integration workflow? | Continue to the Action Catalog section below. |

## DDSQL

DDSQL can read Datadog data exposed through documented datasets and table functions. Check the DDSQL Data Directory and schema tooling before writing queries.

For reads, check DDSQL first. If the data is exposed in the DDSQL Data Directory, through a DDSQL table function, or through a verified app datastore or Reference Table path, DDSQL usually gives the app more control and smaller responses than broad product-specific list actions.

Prefer DDSQL when the app needs:

- Projection of only needed columns.
- Filtering, sorting, limits, or offsets.
- Joins across DDSQL-visible sources.
- Counts, grouping, aggregation, or derived fields.
- JSON, regex, window, date/time, or tag operations.
- Bounded previews for schema and access validation.

DDSQL-visible Datadog product datasets include, but are not limited to:

- Logs, APM Spans, RUM Events, Events, Audit Trail, CI Pipelines, and CI Tests.
- Hosts, Containers, Services, Systems, Queues, Frontend Apps, Product Analytics, and LLM Observability.
- Database metadata such as Database Instances, MySQL, PostgreSQL, and SQL Server logical databases, schemas, and tables.
- Network Device Flows, Network Devices, Network Monitoring, Security Findings, and Security Inventory Libraries.
- Cloud and Kubernetes inventory datasets from AWS, Azure, GCP, Kubernetes, and OCI.
- Table functions for logs, metrics scalar/timeseries, cloud cost scalar/timeseries, and other documented DDSQL sources.
- App datastores and Reference Tables when confirmed in the target org/site.

Before writing DDSQL, identify:

- Data source name and ID or namespace.
- Confirmed table/path, such as a dataset name, table function, datastore ID, or `reference_tables.<table_name>`.
- Confirmed columns and rough DDSQL types.
- Key fields, row count, status, and access when tooling exposes them.
- One bounded preview query that proves the source is queryable.

If these are unknown, inspect more or ask the user instead of guessing schemas from memory.

### Inspect Tables And Schemas

Use discovery tooling before writing app code:

- Start with the public DDSQL Data Directory for documented datasets and table functions.
- Prefer Datadog MCP DDSQL, data directory, table search, schema, or product-specific exploration tools when they are available in the current session. Use MCP results to confirm table names, columns, supported filters, and access before generating SQL.
- For shell-based validation against the Datadog API directly, use `DD_API_KEY` and `DD_APP_KEY`. Normal scaffolded local app development uses OAuth by default.

Do not rely on guessed table names, guessed columns, or `information_schema`. Treat a bounded preview query as the final proof that the table path, schema, access, and syntax work in the target org/site.

Backend DDSQL should use fixed SQL templates, allowlisted frontend inputs, clamped limits, escaped literals or supported parameterization, and display-ready response rows. Verify the exact DDSQL execution API or action import against the generated app project's `AGENTS.md` and installed packages before writing app code.

## Action Catalog

Backend functions can call Action Catalog actions through `@datadog/action-catalog`. The catalog provides reusable, typed actions for Datadog APIs, infrastructure providers, SaaS tools, generic HTTP, and other integrations, so app code does not need to hand-roll API clients for supported workflows.

Use Action Catalog actions for:

- Mutations: create, update, delete, trigger, invoke, approve, assign, resolve, send, or start operations.
- Product workflows that map to an existing action.
- Simple single-record reads where the typed action response is exactly what the UI needs.
- External systems or integrations that are not DDSQL-visible.
- Workflows that require a connection or integration-specific auth behavior.

Prefer DDSQL for read-heavy paths when the same data is DDSQL-visible and the app needs projection, filtering, joins, aggregation, ordering, or pagination.

For exact Action Catalog imports, file layout, and app-code patterns, rely on the generated app scaffold's `AGENTS.md` and installed package APIs. This skill should explain the Datadog product model and decision tree, not own app implementation structure.

## Shared Safety

- Keep data access in backend functions, not frontend code.
- Do not accept raw frontend SQL, raw `ORDER BY`, or unbounded limits.
- Keep data discovery and backend calls server-side. Normal scaffolded local development uses OAuth by default; direct API shell commands still need API/application keys unless the tool provides its own auth.
