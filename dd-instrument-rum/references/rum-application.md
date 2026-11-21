# RUM application on the Datadog side

The SDK sends its events to a RUM application in Datadog. The application owns the application ID and the client token, and two settings decide what Datadog keeps:

- `rum_event_processing_state`: `NONE` drops RUM sessions and errors, `ERROR_FOCUSED_MODE` keeps the error data that Error Tracking needs, and `ALL` keeps every session. Datadog keeps no RUM data at `NONE`, even when the code is right.
- `product_analytics_retention_state`: `MAX` turns Product Analytics on, and `NONE` leaves it off.

Never lower either setting. Another product, such as Product Analytics or Error Tracking, may have set it.

## Contents

- Decide the goal
- Pick the method
- API
- Settings step

## Decide the goal

The goal is the set of products the user asked for. Under an orchestrator, read it from the user's request and the plan. When this skill runs on its own, the goal is the products the user named, or RUM when they named none.

| Goal | `rum_event_processing_state` on create | `product_analytics_retention_state` on create | Raise an existing application |
|---|---|---|---|
| RUM, alone or with other products | `ALL` | `MAX` when Product Analytics is also in the goal, otherwise `NONE` | Raise `rum_event_processing_state` to `ALL` |
| Product Analytics only | `NONE` | `MAX` | No change; `dd-instrument-product-analytics` turns it on |
| Error Tracking only | `ERROR_FOCUSED_MODE` | `NONE` | No change; `dd-instrument-error-tracking` turns it on |
| Product Analytics and Error Tracking | `ERROR_FOCUSED_MODE` | `MAX` | No change; each product skill turns its setting on |

On `ddog-gov.com` and `us2.ddog-gov.com`, always send `product_analytics_retention_state: NONE`, because Product Analytics is not offered there.

The application `type` is `react-native` for React Native and Expo apps, and `browser` for every web app.

Use a name from the deployed app's package, service or folder, with only letters, digits, spaces, `.`, `-` and `_`, because the name goes into a JSON body unescaped.

## Pick the method

Use the first method that is available:

1. **A local RUM tool from the run.** Some runs, such as a setup CLI, provide tools like `create_rum_application` and `update_rum_application`. They choose the settings from a `product` value and only raise settings. With an application ID, call the update tool once for each product in the goal; its result holds the client token. This may also raise a Product Analytics or Error Tracking setting, which is safe: the tool never lowers a setting, and the product skills would raise it next. Without one, call the create tool with the first goal product in the order `rum`, `error-tracking`, `product-analytics`, then the update tool for the others. When the create tool has no `react-native` type, do not create a React Native application with another type; use method 2 or 3. When a tool result says to stop, stop and report its reason.
2. **The Datadog API**, when `DD_API_KEY` and `DD_APP_KEY` are set in the shell or in the `.env.local` or `.env` that `dd-account-setup` wrote. The application key needs `rum_apps_read` and `rum_apps_write`, and also `product_analytics_apps_write` to create an application with `product_analytics_retention_state: MAX`. See "API".
3. **The settings step in Datadog**, for the user. See "Settings step".

## API

Run the block below as one unit from the directory that holds the `.env.local` or `.env` that `dd-account-setup` wrote. Fill the first three lines with the confirmed site (`DD_SITE`) and the values from the goal table, keeping the single quotes. Leave `APP_ID` empty to create an application.

- With `APP_ID`, the block reads the application with [Get a RUM application](https://docs.datadoghq.com/api/latest/rum/#get-a-rum-application). Only when `RUM_STATE` is `ALL` and the current state is lower, it sends `rum_event_processing_state: ALL` and nothing else with [Update a RUM application](https://docs.datadoghq.com/api/latest/rum/#update-a-rum-application). Sending `product_analytics_retention_state` could turn Product Analytics off.
- Without `APP_ID`, it first lists the org's applications and creates one with the goal's settings with [Create a new RUM application](https://docs.datadoghq.com/api/latest/rum/#create-a-new-rum-application) only when no application has that name. A rerun after an unclear result therefore cannot create a second application.

The keys reach `curl` through a pipe, so they never appear in the process list or the output. The site must match one known site exactly; any other value, including a lookalike domain, sends no keys. The block exits 0 and prints one line per request. It runs in any POSIX shell:

```sh
SITE='<site>' APP_ID='<application id, or empty to create one>'
NAME='<application name>' TYPE='<browser or react-native>'
RUM_STATE='<ALL, ERROR_FOCUSED_MODE or NONE>' PA_STATE='<MAX or NONE>'
envval() { [ -f "$2" ] && sed -n "s/^$1=//p" "$2" | tail -n 1 | sed "s/^[\"']//; s/[\"']\$//"; }
for f in .env.local .env; do
  [ -n "$DD_API_KEY" ] || DD_API_KEY=$(envval DD_API_KEY "$f")
  [ -n "$DD_APP_KEY" ] || DD_APP_KEY=$(envval DD_APP_KEY "$f")
done
case "$SITE" in
  datadoghq.com|datadoghq.eu|us3.datadoghq.com|us5.datadoghq.com|ap1.datadoghq.com|ap2.datadoghq.com|uk1.datadoghq.com|ddog-gov.com|us2.ddog-gov.com) API_HOST="api.$SITE" ;;
  *) API_HOST= ;;
esac
BAD=
case "$TYPE" in browser|react-native) ;; *) BAD=" TYPE" ;; esac
case "$NAME" in ''|*[!A-Za-z0-9' '._-]*) BAD="$BAD NAME" ;; esac
case "$RUM_STATE" in ALL|ERROR_FOCUSED_MODE|NONE) ;; *) BAD="$BAD RUM_STATE" ;; esac
case "$PA_STATE" in MAX|NONE) ;; *) BAD="$BAD PA_STATE" ;; esac
if [ -z "$API_HOST" ] || [ -z "$DD_API_KEY" ] || [ -z "$DD_APP_KEY" ]; then
  echo "skipped: unsupported site or missing keys"
elif [ -z "$APP_ID" ] && [ -n "$BAD" ]; then
  echo "skipped: invalid value for$BAD; nothing was sent"
else
  BASE="https://$API_HOST/api/v2/rum/applications"
  keys() { echo "DD-API-KEY: $DD_API_KEY"; echo "DD-APPLICATION-KEY: $DD_APP_KEY"; }
  call() { keys | curl -sS -w '\n%{http_code}' -H @- -H 'Content-Type: application/json' "$@"; }
  body() { printf '%s' "$OUT" | sed '$d' | tr -d '\n'; }
  pick() { body | tr -d ' ' | sed -n "s/.*\"$1\":\"\([^\"]*\)\".*/\1/p"; }
  state() { body | tr -d ' ' | sed -n "s/.*\"$1\":{[^}]*\"state\":\"\([A-Z_]*\)\".*/\1/p"; }
  code() { printf '%s\n' "$OUT" | tail -n 1; }
  report() { echo "$1: HTTP $(code) application_id=$(pick application_id) client_token=$(pick client_token) rum_event_processing_state=$(state rum_event_processing_scale) product_analytics_retention_state=$(state product_analytics_retention_scale) errors=$(body | sed -n 's/.*"errors"[[:space:]]*:[[:space:]]*\(\[.*\]\).*/\1/p')"; }
  if [ -n "$APP_ID" ]; then
    OUT=$(call "$BASE/$APP_ID"); report read
    if [ "$RUM_STATE" = ALL ] && [ "$(code)" = 200 ] && [ "$(state rum_event_processing_scale)" != ALL ]; then
      OUT=$(call -X PATCH -d "{\"data\":{\"id\":\"$APP_ID\",\"type\":\"rum_application_update\",\"attributes\":{\"rum_event_processing_state\":\"ALL\"}}}" "$BASE/$APP_ID"); report update
    fi
  else
    OUT=$(call "$BASE")
    if [ "$(code)" != 200 ]; then
      report list
    elif body | grep -qF -e "\"name\":\"$NAME\"" -e "\"name\": \"$NAME\""; then
      echo "exists: an application named '$NAME' already exists; nothing was created"
    else
      OUT=$(call -X POST -d "{\"data\":{\"type\":\"rum_application_create\",\"attributes\":{\"name\":\"$NAME\",\"type\":\"$TYPE\",\"rum_event_processing_state\":\"$RUM_STATE\",\"product_analytics_retention_state\":\"$PA_STATE\"}}}" "$BASE"); report create
    fi
  fi
  if body | grep -qi 'maximum amount of applications'; then echo "limit: the org has reached its RUM application limit"; fi
fi
```

Read the result from the printed lines:

| Output | Meaning | Next step |
|---|---|---|
| `read: HTTP 200` with no `update` line | The application exists and needs no change | Use the printed `client_token` |
| `update: HTTP 200` | RUM processing is now `ALL` | Use the printed `client_token`; report that this run turned RUM processing on |
| `create: HTTP 200` or `201` | The application exists now | Use the printed `application_id` and `client_token` |
| `exists:` | An application with this name is already in the org, for example from an earlier run | Do not create another one. Get its ID from the user or from `https://APP_HOST/rum/list`, then run the block again with `APP_ID`. When you cannot ask, stop and report it |
| `limit:` | The org has no room for another RUM application | Stop before editing. Tell the user to reuse an application from `https://APP_HOST/rum/list` by giving its ID, or to delete an unused one there, and then to run the setup again |
| `read: HTTP 400` or `HTTP 404` | The application belongs to another org, or the site is wrong; the `errors` text says which, for example "invalid application id" | Stop before editing. Ask the user to check the ID at `https://APP_HOST/rum/list` and the org they signed in to. Create a new application only when the user agrees |
| Other HTTP 401 or 403 | A key is invalid, or the application key lacks a permission: `rum_apps_read` and `rum_apps_write`, plus `product_analytics_apps_write` for a create with `PA_STATE='MAX'` | Use the settings step |
| HTTP `000`, `429` or `5xx` | A network failure, rate limit or Datadog error; a create may or may not have happened | Wait a minute and run the same block once more: its name check stops a second create. If it fails again, use the settings step |
| `skipped:` | No keys, a site outside the list, or an invalid template value; nothing was sent | Fix the value it names, or use the settings step |

The client token and the application ID are public identifiers that ship in the app, so printing them is safe. The `errors` text holds no keys. The API and application keys are secret: never print them or write them into the app.

**Undo.** When this run created the application, the user can delete it under Manage Applications. When this run raised RUM processing, send the same update with the earlier state, using the same `API_HOST`.

## Settings step

Build `APP_HOST` from the site:

| Site | `APP_HOST` |
|---|---|
| `datadoghq.com` | `app.datadoghq.com` |
| `datadoghq.eu` | `app.datadoghq.eu` |
| `ddog-gov.com` | `app.ddog-gov.com` |
| Any other site, such as `us3.datadoghq.com`, `us5.datadoghq.com`, `ap1.datadoghq.com`, `ap2.datadoghq.com` or `uk1.datadoghq.com` | The site itself |

Never build the host as `app.` followed by the site, because that host is wrong for most sites.

- **No application yet.** Stop before editing. Ask the user to open `https://APP_HOST/rum/application/create`, choose the application type (React Native, or JavaScript for a web app), create it, and send back the application ID and the client token. When RUM is the goal, they should also check that RUM event processing is on for all sessions in the application's settings.
- **An existing application below `ALL` when RUM is the goal.** Continue with the code. In the summary, ask the user to open Manage Applications at `https://APP_HOST/rum/list`, select the application, and set RUM event processing to all sessions in its settings.
