#!/usr/bin/env bash
# dd-env.sh — resolve Datadog credentials for the agent fleet flow.
#
# Values already exported win; .env.local then .env fill only what is
# unset. DD_SITE defaults to datadoghq.com when nothing sets it. Prints
# the resolved state (set/unset only — never key values). Run from the
# directory that holds the .env files, or source it to export into the
# current shell.

for f in .env.local .env; do
  [ -f "$f" ] || continue
  for k in DD_SITE DD_API_KEY DD_APP_KEY; do
    [ -n "$(printenv "$k")" ] && continue
    v=$(grep -E "^$k=" "$f" | head -1 | cut -d= -f2- | sed 's/^["'\'']//;s/["'\'']$//')
    [ -n "$v" ] && export "$k=$v"
  done
done

: "${DD_SITE:=datadoghq.com}"
echo "DD_SITE=${DD_SITE} api_key=$([ -n "${DD_API_KEY:-}" ] && echo set || echo unset) app_key=$([ -n "${DD_APP_KEY:-}" ] && echo set || echo unset)"
