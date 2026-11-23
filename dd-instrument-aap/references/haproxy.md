# HAProxy

Open this in step 5 to protect traffic at HAProxy on Linux or Docker. HAProxy calls the Datadog Stream Processing Offload Agent (SPOA) through SPOE, and processes requests synchronously through it. Install no tracer in the upstream app.

## Contents

- Before you start
- Resolve the SPOA image
- Deploy the SPOA
- Download the configuration files
- Merge the configuration
- Validate and reload

## Before you start

- Run `haproxy -v` where HAProxy runs, when you can. Stop when it is below 3.1 or an end-of-life release, because the published `backend.cfg` uses `mode spop`, which HAProxy supports only from 3.1, so `haproxy -c` fails on older versions. When you cannot run it, tell the user that HAProxy 3.1 or later is required.
- Find every frontend that protects the upstream and the configuration file HAProxy loads.
- The SPOA needs a reachable Datadog Agent with a valid API key that accepts traces from containers (`apm_config.apm_non_local_traffic: true`, or `DD_APM_NON_LOCAL_TRAFFIC=true`), because by default the Agent receives traces on localhost only. Blocking needs Remote Configuration on that Agent. List each Agent setting you cannot check as a step left for the user.

## Resolve the SPOA image

Resolve the current release now, and write the concrete result, such as `ghcr.io/datadog/dd-trace-go/haproxy-spoa:v2.12.0`, into every deployment file. Leave no `latest`, variable, placeholder, or resolving command in them:

```bash
SPOA_TAG="$(curl -fsSLI -o /dev/null -w '%{url_effective}' https://github.com/DataDog/dd-trace-go/releases/latest)"
echo "ghcr.io/datadog/dd-trace-go/haproxy-spoa:${SPOA_TAG##*/tag/}"
```

SPOA defaults: it listens on `0.0.0.0` (`DD_HAPROXY_SPOA_HOST`), port `3000` for SPOE (`DD_HAPROXY_SPOA_PORT`) and `3080` for health (`DD_HAPROXY_SPOA_HEALTHCHECK_PORT`). It reaches the Agent at `localhost:8126` (`DD_AGENT_HOST`, `DD_TRACE_AGENT_PORT`). Never expose the SPOE or health ports publicly, because only HAProxy should reach them.

## Deploy the SPOA

Set `DD_SERVICE` and `DD_ENV` to verified tag values.

- **Docker:** add an `spoa` service with the resolved image on a private network shared with HAProxy, which reaches it as `spoa:3000`. Set `DD_AGENT_HOST: host.docker.internal` with `extra_hosts: [host.docker.internal:host-gateway]` for an Agent on the Docker host, or the Agent container's service name when it runs in Compose. Publish no ports, and set no `pull_policy`.
- **Linux:** run the container with both ports on loopback only, from a start script that first runs `docker rm -f spoa 2>/dev/null || true`:

  ```bash
  docker run -d --name spoa --restart unless-stopped --add-host host.docker.internal:host-gateway \
    -e DD_AGENT_HOST=host.docker.internal -e DD_TRACE_AGENT_PORT=8126 \
    -e DD_SERVICE=<service> -e DD_ENV=<env> \
    -p 127.0.0.1:3000:3000 -p 127.0.0.1:3080:3080 <resolved image>
  ```

  Do not run it from a unit with `Restart=always`, because `--restart unless-stopped` already restarts the container. A systemd unit uses `Type=oneshot`, `RemainAfterExit=yes`, `After=docker.service` and `Requires=docker.service`, and runs that start script.

## Download the configuration files

Download the five official files now, in your shell, into the directory of the configuration file HAProxy loads, overwriting earlier Datadog copies. Use a `datadog-aap/` subdirectory only when HAProxy loads a whole directory, an earlier setup used one, or a file with the same name there is not a Datadog copy. Stop and report it if a download fails. Never defer the downloads to a build or start step, or write the files from memory, because you must read and merge the current files now.

```bash
B=https://raw.githubusercontent.com/DataDog/dd-trace-go/main/contrib/haproxy/stream-processing-offload/cmd/spoa/haproxyconf
for f in spoe.cfg global-config.cfg frontend-config.cfg backend.cfg datadog_aap_blocking_response.lua; do curl -fSLO "$B/$f"; done
```

## Merge the configuration

Read the downloaded files first. Merge into the HAProxy configuration in place, not at start-up, so the reviewed file is the one HAProxy loads. On a reinstall, replace the Datadog directives an earlier setup merged instead of adding them again.

- Keep `spoe.cfg` and `datadog_aap_blocking_response.lua` whole and unchanged, because the merged directives reference them as published, and make them reach HAProxy at run time (copied into the image or mounted). Point `DD_SPOA_SPOA_CONF_FILE` and `lua-load` at their run-time paths.
- Merge `global-config.cfg` into the existing `global` section rather than adding a second one. Its paths and timeouts may change.
- Copy the Datadog block from `frontend-config.cfg`, unchanged, to the top of every protected frontend, before other filters and routing rules.
- Append `backend.cfg` near the end. Its server is `spoa1 spoa:3000 check` on Docker, or `spoa1 127.0.0.1:3000 check` on Linux. Several `server` lines give failover.
- Then delete `global-config.cfg`, `frontend-config.cfg` and `backend.cfg`, which are merge-only fragments.
- Docker images based on the official image run as the non-root `haproxy` user, so copy the Lua file with `COPY --chmod=0755 datadog_aap_blocking_response.lua /etc/haproxy/lua/datadog_aap_blocking_response.lua` instead of a `RUN mkdir`, and keep the image's non-root user, because HAProxy only needs to read the file.

Add no undocumented directives and no invented health paths.

## Validate and reload

Before any reload, run `haproxy -c -f <config file>` and keep its full output as evidence. Then reload with the deployment's own safe procedure. For later upgrades, resolve the image again and download all five files again. Signals appear under the SPOA's `DD_SERVICE`.
