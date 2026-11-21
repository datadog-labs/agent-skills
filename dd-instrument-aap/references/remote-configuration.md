# Remote Configuration check

Open this in step 4 when the target is a tracer, APM is on, and the environment is not Cloud Run. Remote Configuration can turn AAP on for a running service only when its tracer is compatible with AAP threat detection and reports the `ASM_ACTIVATION` capability for that service and env.

## Decide the path

1. **Service and env known.** Run the lookup below from the project root and act on its `rc:` line:
   - `rc: available` means the Remote Configuration path.
   - `rc: not-available` means the manual path. Do not offer the toggle, because Datadog reports that it cannot turn AAP on for this service.
   - `rc: unknown` means the lookup could not run or failed. Go to case 2.
2. **Lookup not possible:** no service or env in the repository, or `rc: unknown`. When only the service or env is missing, ask the user for it and run the lookup with the answer. Never guess it, because a wrong name checks another service. For PHP on `aws-ecs` or `aws-fargate`, take the manual path without asking. Otherwise ask the user one question: open `https://APP_HOST/security/appsec/inventory/services`, find the service, and say whether its side panel offers to activate Threat Detection under Capabilities. A yes means the Remote Configuration path. A no, no answer, or a run where you cannot ask means the manual path.

## Look up the service

The [Get Application Security details for a service](https://docs.datadoghq.com/api/latest/application-security/#get-application-security-details-for-a-service) endpoint (`GET /api/v2/security/asm/services/{service}`) returns one row per service and env. It is in preview. The application key needs one of `apm_read`, `appsec_protect_read` or `apm_service_catalog_read`.

The block reads `DD_API_KEY` and `DD_APP_KEY` from the shell, then `.env.local`, then `.env`, the way `dd-account-setup` stores them, and never prints them. It maps the site to one API host first, so a lookalike domain receives no keys. It lowercases and normalizes the service and env the way Datadog stores tags, because `OrderService` is reported as `orderservice`, and it prints the values to use in the links. It runs in any POSIX shell:

```sh
SITE=<site from step 2> SERVICE=<service> ENV=<env> python3 -I - <<'PY'
import json, os, re, urllib.error, urllib.parse, urllib.request
HOSTS = {s: "api." + s for s in (
    "datadoghq.com", "datadoghq.eu", "us3.datadoghq.com", "us5.datadoghq.com",
    "ap1.datadoghq.com", "ap2.datadoghq.com", "uk1.datadoghq.com",
    "ddog-gov.com", "us2.ddog-gov.com")}
def setting(name):
    if os.environ.get(name):
        return os.environ[name]
    for path in (".env.local", ".env"):
        try:
            with open(path, encoding="utf-8") as f:
                for line in f:
                    if line.startswith(name + "="):
                        value = line.split("=", 1)[1].strip().strip("\"'")
                        if value:
                            return value
        except OSError:
            pass
    return ""
def stop(message):
    print(message)
    raise SystemExit(1)
def tag(value):
    value = re.sub(r"[^a-z0-9_.:/-]", "_", value.strip().lower())
    return re.sub(r"_+", "_", value).strip("_")
service, env = tag(os.environ["SERVICE"]), tag(os.environ["ENV"])
print("service=%s env=%s" % (service, env))
host = HOSTS.get(os.environ["SITE"].strip().lower())
api_key, app_key = setting("DD_API_KEY"), setting("DD_APP_KEY")
if not host:
    stop("rc: unknown (unsupported site, not sending keys)")
if not (api_key and app_key and service and env):
    stop("rc: unknown (missing keys, service or env)")
url = "https://%s/api/v2/security/asm/services/%s" % (host, urllib.parse.quote(service, safe=""))
try:
    req = urllib.request.Request(url, headers={
        "DD-API-KEY": api_key, "DD-APPLICATION-KEY": app_key, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        rows = [d.get("attributes", {}) for d in json.load(resp).get("data", [])]
except urllib.error.HTTPError as e:
    stop("rc: unknown (HTTP %d)" % e.code)
except Exception as e:
    stop("rc: unknown (%s)" % type(e).__name__)
match = [a for a in rows if a.get("service") == service and a.get("env") == env]
if any(a.get("asm_threat_compatible") and "ASM_ACTIVATION" in (a.get("rc_capabilities") or [])
       for a in match):
    print("rc: available")
elif match:
    print("rc: not-available (the service cannot be activated remotely)")
else:
    print("rc: not-available (no AAP data for this service and env yet)")
print("has_appsec_enabled: %s" % any(a.get("has_appsec_enabled") for a in match))
PY
```

Only a row whose `service` and `env` both match counts; the endpoint filters by service name only. HTTP 401 or 403 means a key is invalid or the application key lacks the permissions above. HTTP 404 can mean the endpoint is not available on that site.

When `has_appsec_enabled: True` appears before any change, AAP is already on for that service and env. Change nothing for activation and go to Verify and view.

## Remote Configuration path

Change no code, packages, environment variables, Dockerfiles or manifests, and do not set the marker, because the toggle alone turns AAP on. One exception: a Python app on Tornado also needs `DD_TRACE_TORNADO_ENABLED=true` where it runs, because Tornado is instrumented only with that setting; add it there when it is missing, and tell the user that the app must restart once for it. Then give the user this step, and wait:

1. Open `https://APP_HOST/security/appsec/inventory/services?env=<env>&service=<service>`, with the values the lookup printed, to reach the service's side panel.
2. Under Capabilities, turn Threat Detection on. The change reaches the running tracer within seconds, with no redeploy or restart.

Build `APP_HOST` as described in Verify and view. If the user reports that the toggle is missing or fails, take the manual path.

## Verify

Run the same lookup after test traffic. `has_appsec_enabled: True` for the service and env shows that Datadog sees AAP on. When APM was on before, also send normal traffic and confirm that new traces still arrive under the same service and env, because security signals alone do not prove that APM still works.
