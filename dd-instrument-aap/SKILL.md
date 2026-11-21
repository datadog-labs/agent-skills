---
name: dd-instrument-aap
description: Sets up Datadog App and API Protection (AAP, also called AppSec or ASM) in a service's Datadog tracer (Python, Node.js, Java, Go, Ruby, .NET, PHP) or at an Envoy, HAProxy or NGINX proxy, edits its deployment, uses Remote Configuration when no code change is needed, and checks it with test attack traffic. Use when asked to add, enable, set up or verify App and API Protection, AppSec, ASM or threat protection for an app, API or proxy. Not for APM tracing alone, Code Security or Cloud Security.
metadata:
  version: "0.1.0"
  author: datadog-labs
  repository: https://github.com/datadog-labs/agent-skills
  tags: datadog,app-and-api-protection,aap,appsec,asm,security,instrumentation,python,nodejs,java,go,ruby,dotnet,php,envoy,haproxy,nginx
  alwaysApply: "false"
---

# Datadog App and API Protection

App and API Protection (AAP) detects and blocks application-layer attacks, such as injections and security scanners, and reports through a Datadog Agent (on Lambda, the Lambda Extension).

## How it works

- **The switch is `DD_APPSEC_ENABLED=true` on the running process, or a Remote Configuration toggle.** A tracer target needs a Datadog tracer and `DD_APPSEC_ENABLED=true`. A running tracer that supports Remote Configuration for AAP can be switched on from the Datadog UI instead, with no code change or restart. Without either, the AAP service list shows no coverage, and test attacks raise no signals.
- **A proxy target needs no tracer in the upstream app.** Envoy, HAProxy and NGINX each call or load a Datadog security component.
- **APM is not a prerequisite, and this skill never turns APM on.** Reuse an installed tracer and keep its APM setting and service identity. Without one, install the tracer and run AAP alone with `DD_APM_TRACING_ENABLED=false`. Never use `DD_TRACE_ENABLED=false`, because it turns AAP off too.
- **The install marker.** For tracer targets, set `DD_APPSEC_AGENTIC_ONBOARDING=true` once, beside `DD_APPSEC_ENABLED` and with the same mechanism (Java `-D`: `-Ddd.appsec.agentic_onboarding=true`). It marks the service as onboarded by this setup and does not change AAP behavior. Skip it on the Remote Configuration path and for proxies.
- **The Agent.** Never install or reconfigure a host or externally managed Agent; add an Agent sidecar only where a reference says so. An Agent check you cannot run becomes a step left for the user.
- **Credentials.** `DD_API_KEY` and `DD_APP_KEY` come from `dd-account-setup`, in the shell or a gitignored `.env.local` or `.env`. They serve only the Remote Configuration check and verification, and never go into project files. A workload reads its API key from the platform's secret store.

## Set up

Copy these steps into your reply and tick them off as you go.

1. **Choose the target.** List the repository by file and directory names only. Each directory with a dependency manifest, such as `package.json`, `go.mod`, `pom.xml` or `*.csproj`, is a candidate. With several, ask which one and wait; with one, use it. Keep later reads and edits inside it. The target is a proxy when the user asks to protect traffic at Envoy, HAProxy or NGINX. When the user asks for Code Security (IAST), Software Composition Analysis or vulnerability scanning of code or libraries, stop and say this skill sets up threat protection only, because those are separate products.

2. **Detect the facts.** Read the manifest, any `datadog*`, `dd-trace*` or `ddtrace*` file, and the deployment files. Record:
   - **Language** (`python`, `nodejs`, `java`, `go`, `ruby`, `dotnet`, `php`) or **proxy** (`envoy`, `haproxy`, `nginx`).
   - **Deployment environment,** where the app runs: `aws-lambda` (`serverless.yml`, SAM or CDK functions), `aws-ecs` or `aws-fargate` (an ECS task; [deployments.md](references/deployments.md) tells them apart), `kubernetes`, `gcp-cloud-run`, `azure-app-service`, `docker` (only a Dockerfile or Compose file), or a `linux`, `macos` or `windows` host. Ask when unclear.
   - **Frameworks** in the manifest that the language reference lists.
   - **APM status:** on with a Datadog tracer that is not disabled: a tracer dependency or import (not only for tests or tools), tracer start-up code, a tracer in the image or start command (`ddtrace-run`, `--require dd-trace/init`, `-javaagent:…dd-java-agent.jar`), or an Orchestrion Go build. It is also on when an earlier step of this plan set up APM, or when Single Step Instrumentation covers the service: `admission.datadoghq.com/*` pod labels or annotations, `apm.instrumentation` enabled for its namespace in the cluster's DatadogAgent or Helm values, or the Datadog launcher in the host's `/etc/ld.so.preload`. `DD_TRACE_*` settings count only for an image built elsewhere. An Agent, `DD_AGENT_HOST` or `DD_API_KEY` alone does not count; `DD_TRACE_ENABLED=false` or `DD_APM_TRACING_ENABLED=false` means off. When unsure, ask; if nobody knows, treat APM as off and say so.
   - **Service and env,** from `DD_SERVICE`, `DD_ENV`, `tags.datadoghq.com/*` labels, `DD_TAGS` or `datadog.yaml`. Never guess them.
   - **AAP already on:** `DD_APPSEC_ENABLED=true`, or `c.appsec.enabled` in a Ruby initializer.
   - **Site:** the first of the shell's `DD_SITE`, the `DD_SITE` line of `.env.local` or `.env` (read only that line, as with `grep -E '^DD_SITE=' .env.local`), or a site the repository already sets (`custom.datadog.site`, a CDK `site`, a task definition, `datadog.yaml`). Otherwise ask, because a wrong site loses the data while the deploy still succeeds. Never replace a literal site in a file.

3. **Check the pair.**

   | Target | Environments |
   |---|---|
   | Python, Node.js, Java | All 10 |
   | Go, Ruby | All except `windows` |
   | .NET | All except `macos` |
   | PHP | `docker`, `kubernetes`, `aws-ecs`, `aws-fargate`, `gcp-cloud-run`, `azure-app-service`, `linux` |
   | Envoy | `linux`, `docker`, `kubernetes` |
   | HAProxy, NGINX | `linux`, `docker` |

   For another pair, stop before editing and give the [AAP setup docs](https://docs.datadoghq.com/security/application_security/setup/). When AAP is already on, go to Verify and view.

4. **Choose the activation path.** For a tracer target with APM on, outside Cloud Run, follow [references/remote-configuration.md](references/remote-configuration.md). It checks the public API or asks the user, and picks one path:
   - **Remote Configuration:** change no files, except the Tornado setting that reference names. Give the user its toggle step, wait, and go to Verify and view.
   - **Manual:** step 5. Proxies, services without APM, and Cloud Run always take it.

5. **Apply the manual path.** Open the references for the target:

   | Target | References |
   |---|---|
   | Tracer, except on Lambda | The language: [python.md](references/python.md), [nodejs.md](references/nodejs.md), [java.md](references/java.md), [go.md](references/go.md), [ruby.md](references/ruby.md), [dotnet.md](references/dotnet.md) or [php.md](references/php.md); and [deployments.md](references/deployments.md) |
   | Tracer on AWS Lambda | [lambda.md](references/lambda.md) only |
   | Proxy | [envoy.md](references/envoy.md), [haproxy.md](references/haproxy.md) or [nginx.md](references/nginx.md) only |

   On every tracer path:
   - Install a tracer only when the app has none, with the project's own package manager and lockfile, and only once.
   - Set these variables once, where the deployment reference says (Lambda lists its own):

     ```bash
     DD_APPSEC_ENABLED=true
     DD_APPSEC_AGENTIC_ONBOARDING=true
     DD_APM_TRACING_ENABLED=false   # only when APM was off
     DD_SERVICE=<service>           # only when nothing sets the service yet
     ```

   - Keep existing `DD_SERVICE`, `DD_ENV`, `DD_VERSION` and `tags.datadoghq.com/*` values. When nothing sets the service and APM is on, add no `DD_SERVICE` on Kubernetes or for PHP on Docker, and elsewhere add only the name the user confirms the service already reports. With APM off, add a name the user confirms, offering the repository name. When nothing sets it and you cannot ask, stop before editing and report that the service name is needed.
   - Replace an existing `DD_APPSEC_ENABLED=false` in place.
   - Instrument the recorded frameworks, and only those.
   - Never write an API key or invent a secret reference. Add no comments, documentation files or Agent-running checks in start scripts, because such a check fails wherever the Agent arrives later.

6. **Review and build.** Re-read every edit, then run the project's build or validation (its build script, `docker build`, `orchestrion go build`, `nginx -t`, `haproxy -c`). Fix errors your edits caused, and name each check you could not run.

## Verify and view

Record the highest level you actually reached:

1. **Configuration changed:** the edits are in place, or the user turned Threat Detection on.
2. **The app ran:** the service or proxy started with the change and answered a request. Restart only after manual changes.
3. **Test traffic sent** to an existing and a missing route, through the app's or the proxy's address:

   ```bash
   URL=http://localhost:8080   # where the app or proxy listens
   for i in $(seq 1 250); do
     curl -sS -o /dev/null -A dd-test-scanner-log "$URL/<existing-route>"
     curl -sS -o /dev/null -A dd-test-scanner-log "$URL/<missing-route>"
   done
   ```

4. **Datadog shows AAP:** the lookup in [references/remote-configuration.md](references/remote-configuration.md) reports `has_appsec_enabled: True`, or signals appear in the AAP traces explorer. Check once a minute for up to 10 minutes. When APM was on, also confirm that new traces still arrive under the same service and env.

Name each level you could not check and the step left, such as deploying.

The app host is `app.datadoghq.com` for `datadoghq.com`, `app.datadoghq.eu` for `datadoghq.eu`, `app.ddog-gov.com` for `ddog-gov.com`, and the site itself for the others, for example `us5.datadoghq.com`. Use the service and env as the lookup prints them, lowercased. End with this summary:

```text
App and API Protection: turned on | turned on through Remote Configuration | already on | waiting for your step (<the step>) | not supported (<target and environment>)
Target: <language or proxy> on <environment>; APM: kept on | off (AAP only)
Changed: <files, or none>
Evidence: <highest level reached>; not checked: <levels and the step left>
View: https://APP_HOST/security/appsec/inventory/services?env=<env>&service=<service>
Signals: https://APP_HOST/security/appsec/investigate/traces
```

## Undo

Revert this run's edits, and keep a tracer that was there before. After the Remote Configuration path, turn Threat Detection off in the same service panel.

## Gotchas

| Mistake | Symptom | Fix |
|---|---|---|
| Go built with `CGO_ENABLED=0` | Without `-tags appsec`, the binary has no AAP. With it, the binary needs glibc and does not start on `scratch`, distroless `static` or Alpine | `-tags appsec` and a glibc runtime, per [go.md](references/go.md) |
| PHP-FPM with the default `clear_env` | Workers never see `DD_APPSEC_ENABLED` | `env[]` lines from [php.md](references/php.md) |
