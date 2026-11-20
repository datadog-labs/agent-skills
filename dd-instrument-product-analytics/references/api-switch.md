# Switch Product Analytics on through the API

Open this in step 3 when `DD_API_KEY` and `DD_APP_KEY` are both set. The application key needs `rum_apps_read` for the initial lookup, plus `rum_apps_write` and `product_analytics_apps_write` for the update.

1. Read the application with the [Get a RUM application](https://docs.datadoghq.com/api/latest/rum/#get-a-rum-application) endpoint (the first `curl` below). Record two fields of `data.attributes`:
   - `product_scales.product_analytics_retention_scale.state`: `MAX` means Product Analytics was already on, so stop here.
   - `product_analytics_preview_disabled`: absent while the application is in preview. A new application starts in preview, and the API reports its state as `NONE`. Setting `MAX` ends the preview, and the public API documents no way back to it, so when this field is absent, say in the summary that turning Product Analytics on ended the preview.
2. Otherwise, send only `product_analytics_retention_state` with the [Update a RUM application](https://docs.datadoghq.com/api/latest/rum/#update-a-rum-application) endpoint (the PATCH below). Leave out `rum_event_processing_state`: sending a lower value, such as `NONE`, stops RUM and Error Tracking processing for the app.

The keys reach curl through a pipe, so they never appear in the process list. Resolve the site to one API host before either request. Any other value, including a lookalike domain, leaves the host empty and sends no keys. The commands run in any POSIX shell:

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
  *) API_HOST= ;;
esac
if [ -n "$API_HOST" ]; then
  URL="https://$API_HOST/api/v2/rum/applications/$APP_ID"
  keys() { echo "DD-API-KEY: $DD_API_KEY"; echo "DD-APPLICATION-KEY: $DD_APP_KEY"; }
  keys | curl -sS -w ' HTTP %{http_code}' -H @- "$URL"
  BODY=$(printf '{"data":{"id":"%s","type":"rum_application_update","attributes":{"product_analytics_retention_state":"MAX"}}}' "$APP_ID")
  keys | curl -sS -w ' HTTP %{http_code}' -H @- -H 'Content-Type: application/json' -X PATCH -d "$BODY" "$URL"
else
  echo "unsupported site, not sending keys" >&2
fi
```

Each call prints its HTTP status after the body:

| Status | Meaning |
|---|---|
| 200 | The update's response shows `MAX` in the same field |
| 401 or 403 | A key is invalid, or the application key lacks one of the three permissions |
| 404 | The application belongs to another org, or the site is wrong |

On any failure, fall back to the settings step in `SKILL.md`.

**Undo.** Use the same `API_HOST`. Send the update with `"NONE"` only when that host was set. When step 1 found the application in preview, also tell the user that its preview ended when Product Analytics was turned on, and that undoing does not bring the preview back.
