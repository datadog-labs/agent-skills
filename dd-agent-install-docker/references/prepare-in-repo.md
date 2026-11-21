# Prepare in the repository

In this mode you change files in the project and give the user the commands to run. Nothing
starts on this machine.

## Contents

- Rules
- Pick the target directory
- Choose the path
- Compose: the overlay file
- Standalone containers: commands only
- Validate
- Report

## Rules

- Do not run `docker run`, `docker compose up` or `down`, `docker start` or `stop`, or anything else
  that creates, stops or changes containers. Save those commands for the report.
- Leave the base Compose file byte-for-byte unchanged, including `depends_on`. Write a new
  `docker-compose.datadog.yaml` next to it instead.
- Leave Dockerfiles, dependency manifests and application code alone. A missing tracer becomes a
  next step.
- Never write an API key into a file. Committed files reference `DD_API_KEY` only as a variable.
  The user supplies the key when they start the containers, from their shell or from a git-ignored
  `.env` beside the Compose file, which Compose reads for `${...}` values.
- Use the Datadog site from the run settings or Phase 0. You do not need the keys in this mode, so
  skip the key checks in Phase 0.

## Pick the target directory

List the top-level directories by name. Look for Compose files (`compose.yaml`, `compose.yml`,
`docker-compose.yaml`, `docker-compose.yml`), Dockerfiles, IaC files and dependency manifests.
With one candidate, use it. With several, ask the user which one to set up. Keep all later work in
that directory.

## Choose the path

1. **Existing Agent.** Run the existing-Agent check from Phase 1 first. It can end the run, or it can
   reduce the work to fixing `DD_SITE` where the Agent is defined: in a script or IaC file, edit that
   one value; for a Compose service, write an overlay that sets only `DD_SITE`:

   ```yaml
   services:
     <existing-agent-service>:
       environment:
         - DD_SITE=<site>
   ```

2. **Terraform or Ansible** (`*.tf` files; `ansible.cfg`, a playbook or a `roles/` directory) that
   manages the containers: follow the IaC reference, even when a Compose file also exists. When the
   IaC only runs `docker compose`, use the Compose path instead.
3. **Other IaC** (Pulumi, CDK, CloudFormation, Puppet, Chef): express the Agent container below as a
   container resource in that tool, with the same environment variables and mounts.
4. **Compose file present:** write the overlay file.
5. **No Compose file:** give commands only.

## Compose: the overlay file

Write `docker-compose.datadog.yaml` in the directory of the base Compose file. It has three parts.

**The Agent service.** All three products are on unless the user opts out or an explicit product
list leaves one out. Without APM, replace the two APM lines with `DD_APM_ENABLED=false`, because the
Agent turns APM on by default. Without logs, drop the logs lines and the `/opt/datadog-agent/run`
mount. Drop the `DD_ENV` line when no environment is known.

```yaml
services:
  datadog-agent:
    image: registry.datadoghq.com/agent:7
    container_name: dd-agent
    restart: unless-stopped
    pid: host
    environment:
      - DD_API_KEY=${DD_API_KEY}
      - DD_SITE=<site>
      - DD_ENV=<env>
      - DD_DOGSTATSD_NON_LOCAL_TRAFFIC=true
      # APM
      - DD_APM_ENABLED=true
      - DD_APM_NON_LOCAL_TRAFFIC=true
      # Logs
      - DD_LOGS_ENABLED=true
      - DD_LOGS_CONFIG_AUTO_MULTI_LINE_DETECTION=true
      - DD_LOGS_CONFIG_CONTAINER_COLLECT_ALL=true
      - DD_CONTAINER_EXCLUDE_LOGS=name:dd-agent
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock:ro
      - /proc/:/host/proc/:ro
      - /sys/fs/cgroup/:/host/sys/fs/cgroup:ro
      - /var/lib/docker/containers:/var/lib/docker/containers:ro
      - /opt/datadog-agent/run:/opt/datadog-agent/run:rw   # logs only
```

The application services reach the Agent over the Compose network, so the Agent needs no published
ports and no socket settings.

**One stanza per application service.** With application tracing on (as SKILL.md defines it), add
a stanza for every service that runs the user's own code: it builds from a local context
(`build:`) or runs the project's own image. Skip
stock images such as databases, caches, queues and proxies, because a service without a tracer does
nothing with these settings. Use the same `environment` syntax (list or map) as the base service,
and add nothing else:

```yaml
  <app-service>:
    environment:
      - DD_AGENT_HOST=datadog-agent
      - DD_TRACE_AGENT_PORT=8126
      - DD_ENV=<env>
```

With application tracing off, add only `DD_ENV` to each application service, and only when the
environment is known.
A service with `network_mode: host` cannot resolve `datadog-agent`: publish
`127.0.0.1:8126:8126/tcp` on the Agent and use `DD_AGENT_HOST=localhost` for that service.

**Networks.** When the application services attach to user-defined networks (a top-level
`networks:` block in the base file), list those networks under `datadog-agent`, spelled exactly as
in the base file. If any of those services has no `networks:` key, it is on the project's default
network, so also list `default`; a service-level `networks:` list replaces the default network
instead of adding to it. Do not add a top-level `networks:` block to the overlay, and never mark a
base network `external: true`: the base file already creates it. When the services use only the
default network, leave networks out.

```yaml
  datadog-agent:
    networks:
      - <network>
      - default   # only when a service uses the default network
```

## Standalone containers: commands only

No project file changes. With application tracing on, the first command is the APM Instrumentation
install from the APM Instrumentation reference. Then the Agent, with the key exported in the same
block:

(Replace $DD_API_KEY with your API key)

```bash
export DD_API_KEY=$DD_API_KEY
docker run -d --name dd-agent --restart unless-stopped --cgroupns host --pid host \
  -e DD_API_KEY \
  -e DD_SITE=<site> -e DD_ENV=<env> \
  -e DD_DOGSTATSD_NON_LOCAL_TRAFFIC=true \
  -e DD_APM_ENABLED=true -e DD_APM_NON_LOCAL_TRAFFIC=true \
  -e DD_LOGS_ENABLED=true -e DD_LOGS_CONFIG_AUTO_MULTI_LINE_DETECTION=true \
  -e DD_LOGS_CONFIG_CONTAINER_COLLECT_ALL=true -e DD_CONTAINER_EXCLUDE_LOGS=name:dd-agent \
  -v /var/run/docker.sock:/var/run/docker.sock:ro \
  -v /proc/:/host/proc/:ro -v /sys/fs/cgroup/:/host/sys/fs/cgroup:ro \
  -v /var/lib/docker/containers:/var/lib/docker/containers:ro \
  -v /opt/datadog-agent/run:/opt/datadog-agent/run:rw \
  -v /var/run/datadog:/var/run/datadog \
  registry.datadoghq.com/agent:7
```

`-e DD_API_KEY` without a value copies the exported variable from the shell, so the key stays off
the `docker run` command line. Without the export, the Agent container exits for lack of a key.
Tell the user to add `-e DD_ENV=<env>` to each application container's own `docker run` command
and to recreate those containers after the install.

## Validate

Run `docker compose -f <base> -f docker-compose.datadog.yaml config --quiet` when `docker` is
available. It only checks the merged files. Always pass `--quiet`, because without it the command
prints the merged file with every `${...}` value filled in, including `DD_API_KEY`. A warning that
`DD_API_KEY` is not set is expected. Also check that every other file you added still parses. If a
check fails, fix the file or report the failure.

## Report

Say what changed: each new or edited file and what it does. When no file changed, say so first.
Then give numbered next steps, each with a short label and a code block:

1. Get a Datadog API key at `<APP_BASE>/organization-settings/api-keys`.
2. Add the Datadog tracing library to your application, when a service still lacks one.
3. Start the stack with the key in the same shell. List the base Compose files the project already
   uses, in order (an auto-loaded `compose.override.yaml` must be named explicitly), then the overlay:

   (Replace $DD_API_KEY with your API key)

   ```bash
   export DD_API_KEY=$DD_API_KEY
   docker compose -f <base> -f docker-compose.datadog.yaml up -d
   ```

   If the stack runs somewhere else, such as a remote server or CI/CD, `DD_API_KEY` must be set
   there.

For standalone containers, the run step is the APM Instrumentation install (when application
tracing is on) and the Agent command above, starting with its `export` line. For Terraform and
Ansible, use the next steps from the IaC reference.

Do not say that a product is enabled or that traces flow: nothing runs until the user starts it.
Call the injection feature "APM Instrumentation".
