# Build and check export

## Contents

- Build
- Smoke test
- Export check
- Datadog check
- No-secrets check

## Build

Run the lightest check the project already has: a typecheck, a build or its tests, such as `npm run build`, `npx tsc --noEmit`, `python -m compileall <package>`, `mvn -q -DskipTests package`, `./gradlew assemble` or `go build ./...`. When you run a linter or formatter, pass only the files you changed. Fix errors your edits caused and rerun until it passes.

## Smoke test

For a web server, run one bounded request and stop the server; never leave it running.

1. Put the key into this shell only, without printing it. Read only the `DD_API_KEY` line, so `DD_APP_KEY` stays out of the app:

   ```sh
   [ -n "$DD_API_KEY" ] || DD_API_KEY=$(sed -n 's/^DD_API_KEY=//p' .env 2>/dev/null | tail -n 1)
   export DD_API_KEY
   ```

2. Start the server with its existing command in the background, with exporter logging turned up and a short metric interval, and write its output to a gitignored file: `OTEL_LOG_LEVEL=info OTEL_METRIC_EXPORT_INTERVAL=5000 <start command> > otel-smoke.log 2>&1 &`. Node.js prints export errors only when `OTEL_LOG_LEVEL` is set.
3. When the server is ready, request one local route with the runtime's own HTTP client, because `curl` may be unavailable:
   - Node.js: `node -e "fetch('http://localhost:<port>/').then(r => console.log(r.status))"`
   - Python: `python -c "import urllib.request as u; print(u.urlopen('http://localhost:<port>/').status)"`
   - Java or Go: `curl -s -o /dev/null -w '%{http_code}\n' http://localhost:<port>/`, or one of the lines above when Node.js or Python is installed.
4. Wait about 10 seconds, because the batch processors send every few seconds and a Python process stopped by `SIGTERM` skips its final flush. Then stop the server with `kill -TERM <pid>`. For a Compose project, use `docker compose up -d`, the same request and `docker compose down`.
5. Delete `otel-smoke.log` when you are done.

## Export check

Search the captured output for exporter failures, for example `grep -inE 'export|otlp' otel-smoke.log | grep -iE 'fail|error|40[0-9]|413'`. Every SDK in this skill reports a failed export: Node.js through its diagnostic logger (as `Forbidden` rather than `403`), Python as a logged error, Go on standard error, and the Java agent in its own log.

| Status | Meaning | Fix |
|---|---|---|
| 403 | The key does not belong to this site, or the endpoint is another site's | Match `DD_SITE` and the key |
| 413 | The request is larger than the intake accepts | Lower the batch size of the span, metric or log processor |
| An error on metrics only, while traces pass | Cumulative temporality | Set delta temporality |

No failure after the wait is level 3 evidence. On the Agent route, a connection error means the receiver is off or unreachable. Without a key or an Agent endpoint, no exporter starts, so stop at level 2 and name the step left for the user, such as passing `DD_API_KEY` with `docker run -e DD_API_KEY`.

## Datadog check

This step needs an application key with the `apm_read` scope. When the Datadog MCP server is connected, call `Datadog:search_datadog_spans` with the query `service:<service>` over the last 15 minutes. Otherwise, use the [Get a list of spans](https://docs.datadoghq.com/api/latest/spans/#get-a-list-of-spans) endpoint (`GET /api/v2/spans/events`). The keys reach `curl` through a pipe, so they never appear in the process list, and an unknown site sends no keys:

```sh
[ -n "$DD_APP_KEY" ] || DD_APP_KEY=$(sed -n 's/^DD_APP_KEY=//p' .env 2>/dev/null | tail -n 1)
case "$DD_SITE" in
  datadoghq.com|us3.datadoghq.com|us5.datadoghq.com|datadoghq.eu|ap1.datadoghq.com|ap2.datadoghq.com|uk1.datadoghq.com|ddog-gov.com|us2.ddog-gov.com) API_HOST="api.$DD_SITE" ;;
  *) API_HOST= ;;
esac
if [ -n "$API_HOST" ] && [ -n "$DD_APP_KEY" ]; then
  keys() { echo "DD-API-KEY: $DD_API_KEY"; echo "DD-APPLICATION-KEY: $DD_APP_KEY"; }
  keys | curl -sS -G -w ' HTTP %{http_code}\n' -H @- "https://$API_HOST/api/v2/spans/events" \
    --data-urlencode "filter[query]=service:<service>" \
    --data-urlencode 'filter[from]=now-15m' --data-urlencode 'page[limit]=1'
fi
```

A 200 with a non-empty `data` array is level 4. A new service can take a few minutes to appear, so query once a minute for up to 10 minutes after the smoke request; when nothing arrives, stop at level 3 and give the link. A 403 means the application key lacks `apm_read`.

## No-secrets check

Run these before you report. Each prints a file name or a warning, never a key:

```sh
git ls-files | grep -E '(^|/)\.env(\.local)?$'
[ -n "$DD_API_KEY" ] && printf '%s\n' "$DD_API_KEY" | git grep --untracked -l -F -f -
[ -n "$DD_APP_KEY" ] && printf '%s\n' "$DD_APP_KEY" | git grep --untracked -l -F -f -
git grep --untracked -l -E '(VITE|NEXT_PUBLIC|REACT_APP)_DD_API_KEY'
```

Each command must list nothing: the first lists tracked env files, the next two list files that hold a key, and the last lists public frontend key variables. Also confirm that every file that sets a `dd-api-key` header runs only on the server.
