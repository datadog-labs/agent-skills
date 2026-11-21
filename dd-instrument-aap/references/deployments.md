# Deployment environments

Open this in step 5 for a tracer target outside AWS Lambda. Each section says where the AAP variables from step 5 go and what else the environment needs. Set the variables in one place only. Check the Agent when you can reach the deployment, and otherwise list the check as a step left for the user. Never install or reconfigure a host Agent, because its owner manages it, and never add an Agent-running check to a start or deploy script, because the check stops the app wherever the Agent arrives later.

## Contents

- Docker and Compose
- Kubernetes
- Amazon ECS on EC2 and on Fargate
- Google Cloud Run
- Azure App Service
- Linux host
- macOS host
- Windows host

## Docker and Compose

1. Put the variables on the app's service in `docker-compose.yml` (`environment:`), or as `ENV` lines in the Dockerfile's runtime stage when there is no Compose file.
2. Install the tracer in the image, following the language reference's container section.
3. The Agent container must run with a valid API key on a network the app container can reach, for example as a Compose service named in `DD_AGENT_HOST`, and accept traces from other containers (`DD_APM_NON_LOCAL_TRAFFIC=true`). List what you cannot check as a step left for the user.

## Kubernetes

Add the variables to the app container's `env` in the Deployment (or the Helm values or Kustomize patch that renders it). Go builds an instrumented image first, as in [go.md](go.md).

- **APM off:** also add the unified service tags, read the service and env from them, and, for Java, Python, Node.js, .NET and Ruby, let the Admission Controller inject the tracer. The `admission.datadoghq.com/enabled` label is required, because the Admission Controller skips unlabeled pods by default (`mutateUnlabelled: false` in the Helm chart and the Operator):

  ```yaml
  spec:
    template:
      metadata:
        labels:
          admission.datadoghq.com/enabled: "true"
          tags.datadoghq.com/service: "<service>"
          tags.datadoghq.com/env: "<env>"
        annotations:
          admission.datadoghq.com/python-lib.version: latest   # java-lib, js-lib, dotnet-lib or ruby-lib
      spec:
        containers:
        - name: <app>
          env:
          - name: DD_APPSEC_ENABLED
            value: "true"
          - name: DD_APPSEC_AGENTIC_ONBOARDING
            value: "true"
          - name: DD_APM_TRACING_ENABLED
            value: "false"
          - name: DD_SERVICE
            valueFrom: {fieldRef: {fieldPath: "metadata.labels['tags.datadoghq.com/service']"}}
          - name: DD_ENV
            valueFrom: {fieldRef: {fieldPath: "metadata.labels['tags.datadoghq.com/env']"}}
  ```

  The `tags.datadoghq.com/*` labels and the `fieldRef` lookups go together: when you keep existing literal `DD_SERVICE` and `DD_ENV` values, add no `tags.datadoghq.com/*` labels. Without a known env, use the literal `value: "replace-me-with-environment-name"` and tell the user. Library injection works only when the cluster's Datadog Admission Controller is on.
- **APM on**, including Single Step Instrumentation that already covers the namespace: add only `DD_APPSEC_ENABLED` and the marker, and no injection label or annotation. Keep the Deployment name, `DD_SERVICE`, `DD_ENV` and `tags.datadoghq.com/*` labels. Add `DD_ENV` only when nothing sets the env yet (no `DD_ENV`, label, `envFrom` source or `env:` in `DD_TAGS`), and add no `DD_SERVICE` unless nothing sets the service and you have a verified name.
- Adjust `DD_TRACE_AGENT_URL` only when the cluster's networking needs it. When you can reach the cluster, check the Agent with `kubectl get daemonset -l app=datadog-agent -A` or `kubectl get deployment -l app.kubernetes.io/name=datadog-operator -A`.

## Amazon ECS on EC2 and on Fargate

Find the task definition first: `task-definition.json`, or a CloudFormation, Terraform or CDK definition. It is `aws-fargate` when it runs on Fargate (a `FARGATE` launch type or capacity provider, or `requiresCompatibilities` of only `FARGATE`), and `aws-ecs` otherwise. The variables go in the app container as JSON objects, such as `{"name": "DD_APPSEC_ENABLED", "value": "true"}`. Omit `DD_SERVICE` when the container or its image already sets it, and for PHP with APM on.

**When the task definition is in this repository:**

- Add the variables to the app container's `environment`, and not also as image `ENV` lines.
- Make sure exactly one Agent serves the task, with `DD_APM_ENABLED=true`. Reuse an Agent sidecar in the task, or on EC2 an Agent daemon service. Otherwise add an Agent sidecar only when a container in this task already reads a `DD_API_KEY` secret through `secrets`: give the sidecar `DD_API_KEY` from that secret, `DD_APM_ENABLED=true`, `DD_SITE` set to the site from step 2 (ask when it is unknown, and never write a guess), and on Fargate `ECS_FARGATE=true`.
- On EC2, check `networkMode` first. With `host`, add no sidecar; reuse a daemon on the host, or tell the user. With `bridge`, or unset on Linux, a sidecar is reached through `links` on the app container, with `DD_AGENT_HOST` set to the Agent container's name; update a `DD_TRACE_AGENT_URL` that points at `localhost`. Keep a working daemon or Unix-socket endpoint.
- Never write an API key, because it would leak through git history, and never invent a secret ARN, because the deploy fails on it. Leave the task and execution roles as they are, because their owner manages their permissions.

**When it is not in this repository:** create no task definition. Set the variables as `ENV` lines in the image's runtime stage.

**In both cases**, install the tracer in the image as in the language reference. In the final message, tell the user what is left: a running Agent (sidecar with `DD_API_KEY` from a secret the execution role can read, `DD_APM_ENABLED=true`, `DD_SITE`, and `ECS_FARGATE=true` on Fargate, or a reachable daemon on EC2), and a task role for the Agent with `ecs:ListClusters`, `ecs:ListTasks` and `ecs:DescribeTasks`. AAP setup is not complete until a running Agent is confirmed.

## Google Cloud Run

AAP on Cloud Run is in preview, and Remote Configuration cannot turn it on there.

1. Run the Datadog Agent as a sidecar container, or build with the Datadog buildpack. When the service has neither yet, add the sidecar the way the sidecar path of the `dd-instrument-cloud-run` skill does, then come back for the AAP variables. With a sidecar and your own image, install the tracer in the image as in the language reference. The buildpack installs the tracer itself, so leave the Dockerfile and what the buildpack sets alone.
2. Do not add `datadog-init` to the entrypoint or `CMD`. It belongs to the in-container `serverless-init` setup, which neither option uses.
3. Set the variables on the app container in the service definition only, such as the `service.yaml` env block, `gcloud run deploy --update-env-vars`, or Terraform `google_cloud_run_v2_service`, and not also as Dockerfile `ENV`. When the service definition is not in the repository, set them at deploy time with `--update-env-vars` (buildpack), or as `ENV` in your own image. Keep an existing `DD_SERVICE`.
4. The Agent sidecar needs `DD_API_KEY` and APM enabled. Tell the user to confirm it at deploy time when you cannot.

## Azure App Service

The Datadog collector depends on the operating system:

- **Linux** (code or container; the only option for Python and PHP, and for Ruby and Go as a container): the `serverless-init` sidecar, plus the language's tracer in the app.
- **Windows** (code, for .NET, Java and Node.js): the Datadog site extension, which brings the tracer.

1. When the app has no Datadog collector yet, set it up with the `dd-instrument-azure-serverless` skill, which covers both and the deployment tools (Terraform, Bicep, the Datadog CLI), then come back. If the skill is not available, follow the [Azure App Service docs](https://docs.datadoghq.com/serverless/azure_app_service/).
2. Add the AAP variables as application settings, in the same place that sets the other Datadog settings (the Terraform module, Bicep, or Configuration > Application settings). The API key comes from a secure source, such as a Key Vault reference, and never as a literal value, because a literal key leaks through git history.
3. Restart the App Service.

## Linux host

1. The Agent must run on the deployment host with a valid API key, listening on `127.0.0.1:8126`. When you can reach the host, check `sudo datadog-agent status`.
2. For a systemd service, put the variables in a drop-in at `/etc/systemd/system/<service>.service.d/datadog-aap.conf`, or edit the existing unit. Create a new unit only when no service manages the app.

   ```ini
   [Service]
   Environment="DD_APPSEC_ENABLED=true"
   Environment="DD_APPSEC_AGENTIC_ONBOARDING=true"
   Environment="DD_SERVICE=<service>"
   Environment="DD_APM_TRACING_ENABLED=false"
   ExecStart=
   ExecStart=/usr/bin/ddtrace-run python /path/to/app.py
   ```

   Drop the `DD_APM_TRACING_ENABLED` line when APM was on. Override `ExecStart` only when the language needs a new start command, such as `node --require dd-trace/init`, `java -javaagent:/path/to/dd-java-agent.jar` or `ddtrace-run`.
3. Restart with `sudo systemctl daemon-reload && sudo systemctl restart <service>`. Without systemd, set the variables in the start script or the `.env` file the app already loads.

## macOS host

Set the variables where the app starts: its launch script, its `launchd` plist (`EnvironmentVariables`), or the `.env` file it loads. The Agent must run on the host with a valid API key. Restart the app.

## Windows host

1. The Agent must run with a valid API key. When you can reach the host, check the `datadogagent` service.
2. Set the variables in the PowerShell script that launches the app, right before the launch command, so they apply only to that process:

   ```powershell
   $Env:DD_APPSEC_ENABLED = "true"
   $Env:DD_APPSEC_AGENTIC_ONBOARDING = "true"
   $Env:DD_SERVICE = "<service>"
   $Env:DD_APM_TRACING_ENABLED = "false"   # only when APM was off
   ```

3. Never set Datadog variables machine-wide with `[Environment]::SetEnvironmentVariable(..., "Machine")`, because every other app on the host would inherit the service name and AAP. A scheduled task runs the same launcher script. A native Windows service keeps its executable, and gets the variables through its wrapper's settings (WinSW or NSSM) or the `Environment` value under `HKLM\SYSTEM\CurrentControlSet\Services\<ServiceName>`, because a PowerShell script registered as a service fails with error 1053. .NET uses the scoping rules in [dotnet.md](dotnet.md).
4. Restart the app. Java sets AAP through these variables only, without `-D` properties.
