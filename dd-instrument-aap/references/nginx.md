# NGINX

Open this in step 5 to protect traffic at NGINX on Linux or Docker. NGINX loads `ngx_http_datadog_module` from [nginx-datadog](https://github.com/DataDog/nginx-datadog) in its AppSec build. Install no tracer in the upstream app; the module is the only component.

## Contents

- Find the NGINX version
- Pick the module artifact
- Install the module
- Configure nginx.conf
- Set the environment
- Validate

## Find the NGINX version

The module must match the NGINX binary version exactly, because NGINX refuses to start with a module built for another version. On Linux, run `nginx -v` on the host. In Docker, use the version a Dockerfile pins in full (`FROM nginx:1.25.3`, not a build argument that can change); for a minor-only or floating tag, run `nginx -v` inside the image. Keep only the number: `nginx/1.24.0 (Ubuntu)` gives `1.24.0`. NGINX must also be built `--with-threads` (`nginx -V`), because AAP needs a thread pool.

## Pick the module artifact

The file name is `ngx_http_datadog_module-appsec-<arch>-<nginx version>.so.tgz`, with `<arch>` `amd64` for `x86_64` or `arm64` for `aarch64`. Use only the `-appsec` variant; the plain artifact has no AAP.

- When both values are known now, list the releases newest first and use the first whose assets contain that exact file name. Prefer the latest release; when you fall back to an older one, say so in a comment. Never pick a release from memory or from its tag alone, because only the asset list proves that a release supports this NGINX version.
- When the architecture or the version is known only at build or deploy time, write install code that detects them there and finds the newest release with that exact asset.
- When no release has the file: for an NGINX newer than the latest release supports, stop and point the user to [nginx-datadog](https://github.com/DataDog/nginx-datadog/) to file an issue. For an older NGINX, recommend upgrading it, or, only if the user agrees, an older release.

## Install the module

Extract the `.so` into the modules directory, usually `/etc/nginx/modules` (`nginx -V 2>&1 | tr ' ' '\n' | grep modules-path`), and check that it exists. This Dockerfile step detects both values and finds the release at build time; drop the detection for a value you already know. It needs `curl` and `jq` (`apk add --no-cache curl jq`, or `apt-get update && apt-get install -y curl jq`):

```dockerfile
RUN set -eux; \
    case "$(uname -m)" in x86_64) DD_ARCH=amd64 ;; aarch64|arm64) DD_ARCH=arm64 ;; *) exit 1 ;; esac; \
    nginx -V 2>&1 | tr ' ' '\n' | grep -qx -- '--with-threads'; \
    NGINX_VERSION=$(nginx -v 2>&1 | sed -n 's#.*nginx/\([^[:space:]]*\).*#\1#p'); test -n "$NGINX_VERSION"; \
    ARTIFACT="ngx_http_datadog_module-appsec-${DD_ARCH}-${NGINX_VERSION}.so.tgz"; \
    TAG=$(curl -fsSLI -o /dev/null -w '%{url_effective}' https://github.com/DataDog/nginx-datadog/releases/latest | sed 's#.*/tag/##'); \
    if ! curl -fsLI -o /dev/null "https://github.com/DataDog/nginx-datadog/releases/download/${TAG}/${ARTIFACT}"; then \
      TAG=; PAGE=1; \
      while [ -z "$TAG" ]; do \
        curl -fsSL -o /tmp/releases.json -H 'Accept: application/vnd.github+json' "https://api.github.com/repos/DataDog/nginx-datadog/releases?per_page=30&page=${PAGE}" || exit 1; \
        OUT=$(jq -r --arg a "$ARTIFACT" '"\(length) \([.[] | select(any(.assets[]?; .name == $a))][0].tag_name // "")"' /tmp/releases.json) || exit 1; \
        [ -n "$OUT" ] || exit 1; \
        TAG=${OUT#* }; \
        if [ -z "$TAG" ] && [ "${OUT%% *}" -lt 30 ]; then echo "No nginx-datadog release has $ARTIFACT" >&2; exit 1; fi; \
        PAGE=$((PAGE + 1)); \
      done; \
    fi; \
    mkdir -p /etc/nginx/modules; \
    curl -fsSL -o /tmp/ddmodule.tgz "https://github.com/DataDog/nginx-datadog/releases/download/${TAG}/${ARTIFACT}"; \
    tar -xzf /tmp/ddmodule.tgz -C /etc/nginx/modules; rm -f /tmp/ddmodule.tgz /tmp/releases.json; \
    test -f /etc/nginx/modules/ngx_http_datadog_module.so
```

Keep `per_page` at 30, because each release lists hundreds of assets and larger pages can time out. The step fails instead of retrying when the GitHub API refuses a request, for example on a rate limit.

On a Linux host, run the same logic once on that host as a separate install command, with `sudo install -m 0644` into the modules directory. Keep it out of NGINX's start or deploy script. When the host is not reachable, prepare the command and tell the user to run it there.

## Configure nginx.conf

At the very top of `nginx.conf`, in the main context and before `events { }`:

```nginx
load_module /etc/nginx/modules/ngx_http_datadog_module.so;
thread_pool datadog_waf threads=4 max_queue=65536;
```

Inside the top-level `http { }` block:

```nginx
datadog_waf_thread_pool_name datadog_waf;
```

Add no `datadog_tracing off`, which also stops AppSec events. Add no `datadog_agent_url`, `datadog_appsec_enabled` or `datadog_service_name`, so the environment variables below stay in control.

## Set the environment

Set these on the NGINX container or service. Add no tracer packages or Admission Controller annotations, because they do not configure the module.

```bash
DD_APPSEC_ENABLED=true
DD_SERVICE=<service>
DD_ENV=<env>
DD_APM_TRACING_ENABLED=false   # only when APM was off
```

- **Docker:** `ENV` lines in the NGINX image, or `environment:` on its Compose service. When one image also starts the upstream app, give the app its own `DD_SERVICE` and `DD_ENV`, set right before each process starts. Set `DD_TRACE_AGENT_URL` (or `DD_AGENT_HOST`) when the Agent is not at the default `http://localhost:8126`. Rebuild the image and restart NGINX.
- **Linux:** a systemd drop-in at `/etc/systemd/system/nginx.service.d/datadog-aap.conf` with one `Environment="…"` line per variable, keeping the unit's `ExecStart`. Then run `sudo systemctl daemon-reload && sudo systemctl restart nginx`. The Agent must run on the host with a valid API key (`sudo datadog-agent status`).

## Validate

Run `nginx -t` (`sudo nginx -t` on a host). An unknown-directive error means the module did not load: check the `load_module` path and that the `.so` exists. Send the test traffic from Verify and view through NGINX.
