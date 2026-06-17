# Querying App Datastores With DDSQL

Use this when a Datadog App needs to query a Datadog App datastore from backend code.

Prefer DDSQL for datastore reads that need projection, filters, sorting, pagination, counts, grouping, or derived fields. Use datastore actions directly for writes, deletes, simple single-item reads, or workflows that need action-specific behavior rather than SQL-shaped data retrieval.

Public docs:

- [Datastores](https://docs.datadoghq.com/actions/datastores/)
- [Use Datastores with Apps and Workflows](https://docs.datadoghq.com/actions/datastores/use/)
- [Datastore Access and Authentication](https://docs.datadoghq.com/actions/datastores/auth/)
- [Actions Datastores API](https://docs.datadoghq.com/api/latest/actions-datastores/)
- [List datastore items API](https://docs.datadoghq.com/api/latest/actions-datastores/list-datastore-items/)

## Discover The Datastore

Before writing view queries:

- Locate the datastore ID from app configuration, existing backend code, generated app metadata, or the Datadog UI.
- Inspect the datastore write path to identify canonical field names and rough types.
- Confirm columns with small DDSQL probes that project only a few fields and use `LIMIT`.
- Keep the discovered column list close to backend query templates so future edits do not rediscover schema from frontend code.

Stop and inspect more if the datastore ID, columns, or key fields are not known.

## Query Shape

This is an observed DDSQL query shape, not a substitute for schema/spec discovery. Before generating app code, confirm that the current environment exposes `dd.actions_datastores`, inspect its required arguments and column typing, and validate a minimal bounded query.

```sql
SELECT key, summary, status
FROM dd.actions_datastores(
  id => '<datastore-id>',
  columns => ARRAY ['key', 'summary', 'status']
) AS (
  key VARCHAR,
  summary VARCHAR,
  status VARCHAR
)
WHERE status = 'Open'
ORDER BY key
LIMIT 100;
```

Use the pattern this way:

- Project only columns needed by the active view.
- Declare the returned column schema after the datastore function call.
- Push `WHERE`, `ORDER BY`, `LIMIT`, and aggregates into DDSQL where supported.
- Validate a minimal query before wiring the backend function; if the table function is not exposed, use Datastore actions/API instead.

## Performance Pattern

Use DDSQL to avoid treating a datastore as a full export source:

- Load startup metadata first.
- Load filter options separately.
- Load only the active tab or route.
- Bound list responses with `LIMIT`, pagination, or virtualization.
- Fetch heavy fields such as descriptions, blobs, or relationship payloads only for detail views.

This replaces the slow pattern of loading the full datastore into the browser, building a local database, and querying it client-side.

## Backend Safety

- Use fixed query templates.
- Map frontend filters to allowlisted datastore columns.
- Clamp numeric inputs.
- Do not accept raw frontend SQL.
- Do not accept raw frontend `ORDER BY` fields.
- Avoid selecting heavy fields in list queries.
- Return only display fields needed for the active screen.

For exact backend execution imports, prefer the generated app project's `AGENTS.md` and installed package APIs over examples from memory.
