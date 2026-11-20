# Switch Error Tracking processing on through the API

Open this in step 3 when `DD_API_KEY` and `DD_APP_KEY` are both set. The application key needs `rum_apps_read` for the lookup and `rum_apps_write` for the update.

The block below makes both requests and decides between them itself:

1. It reads the application with the [Get a RUM application](https://docs.datadoghq.com/api/latest/rum/#get-a-rum-application) endpoint and prints `data.attributes.product_scales.rum_event_processing_scale.state`:
   - `ALL` or `ERROR_FOCUSED_MODE` means Error Tracking event processing is already on, so it sends no update. Sending `ERROR_FOCUSED_MODE` to an `ALL` application would downgrade full RUM session processing to error-only mode.
   - `NONE` means RUM and Error Tracking events are currently dropped (for example, when the application was created for Product Analytics only).
2. Only after a lookup that returned HTTP 200 and `NONE`, it sends `rum_event_processing_state: "ERROR_FOCUSED_MODE"` with the [Update a RUM application](https://docs.datadoghq.com/api/latest/rum/#update-a-rum-application) endpoint. It leaves out `product_analytics_retention_state`: sending `"NONE"` turns Product Analytics off on an application that already has it enabled.

Run the block as one unit, and keep the update inside its `if`. The keys reach `curl` through a pipe, so they never appear in the process list. The site resolves to one API host first; any other value, including a lookalike domain, leaves the host empty and sends no keys. The commands run in any POSIX shell:

```sh
SITE=<site from step 1> APP_ID=<application id from step 1>
case "$SITE" in
  datadoghq.com) API_HOST=api.datadoghq.com ;;
  datadoghq.eu) API_HOST=api.datadoghq.eu ;;
  us3.datadoghq.com) API_HOST=api.us3.datadoghq.com ;;
  us5.datadoghq.com) API_HOST=api.us5.datadoghq.com ;;
  ap1.datadoghq.com) API_HOST=api.ap1.datadoghq.com ;;
  ap2.datadoghq.com) API_HOST=api.ap2.datadoghq.com ;;
  uk1.datadoghq.com) API_HOST=api.uk1.datadoghq.com ;;
  ddog-gov.com) API_HOST=api.ddog-gov.com ;;
  *) API_HOST= ;;
esac
if [ -z "$API_HOST" ]; then
  echo "unsupported site, not sending keys" >&2
else
  URL="https://$API_HOST/api/v2/rum/applications/$APP_ID"
  keys() { echo "DD-API-KEY: $DD_API_KEY"; echo "DD-APPLICATION-KEY: $DD_APP_KEY"; }
  LOOKUP=$(keys | curl -sS -w '\n%{http_code}' -H @- "$URL")
  CODE=$(printf '%s\n' "$LOOKUP" | tail -n 1)
  STATE=$(printf '%s' "$LOOKUP" | tr -d ' \n' | sed -n 's/.*"rum_event_processing_scale":{[^}]*"state":"\([A-Z_]*\)".*/\1/p')
  echo "lookup: HTTP $CODE, rum_event_processing_state=${STATE:-unknown}"
  if [ "$CODE" = 200 ] && [ "$STATE" = NONE ]; then
    BODY=$(printf '{"data":{"id":"%s","type":"rum_application_update","attributes":{"rum_event_processing_state":"ERROR_FOCUSED_MODE"}}}' "$APP_ID")
    keys | curl -sS -w ' HTTP %{http_code}' -H @- -H 'Content-Type: application/json' -X PATCH -d "$BODY" "$URL"
  fi
fi
```

Read the result from the printed lines:

| Output | Meaning |
|---|---|
| `lookup: HTTP 200` with `ALL` or `ERROR_FOCUSED_MODE` | Already on; no update was sent |
| `lookup: HTTP 200` with `NONE`, then the update's body ending in ` HTTP 200` | Turned on: the body shows `ERROR_FOCUSED_MODE` in `rum_event_processing_scale.state` |
| HTTP 401 or 403 | A key is invalid, or the application key lacks `rum_apps_read` / `rum_apps_write` |
| HTTP 404 | The application belongs to another org, or the site is wrong |

On any failure, fall back to the settings step in `SKILL.md`.

**Undo.** If the lookup read `NONE` and the update turned it to `ERROR_FOCUSED_MODE`, send the same update with `"rum_event_processing_state": "NONE"` using the same `API_HOST`. If the state was already `ALL` or `ERROR_FOCUSED_MODE` before this run, do not change it.
