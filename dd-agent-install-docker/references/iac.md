# Terraform and Ansible projects

When Terraform or Ansible manages the containers, the Agent becomes one more container resource in
that tool. The rules of the prepare mode apply: edit the IaC files, start nothing, and give the
user only the tool's own commands. When the IaC only runs `docker compose`, write the Compose
overlay instead.

## Contents

- Shared settings
- Terraform
- Ansible
- Validate and report

## Shared settings

- Name the container `dd-agent`. `DD_CONTAINER_EXCLUDE_LOGS=name:dd-agent` relies on that name.
- Use the environment variables and mounts of the Agent service in the prepare-mode overlay, in
  the tool's own syntax. On an APM opt-out, set `DD_APM_ENABLED=false`.
- Application containers reach the Agent by container name, which needs a user-defined network.
  Attach the Agent to the network the application containers already use; when they use none,
  create one and attach both.
- With application tracing on (as SKILL.md defines it), give each application container `DD_AGENT_HOST=dd-agent`, `DD_TRACE_AGENT_PORT=8126`,
  and `DD_ENV` when the environment is known, and make it start after the Agent. The tracer library
  or APM Instrumentation still has to be added; see their references.
- The API key comes from a variable that the user sets when they apply. Never write it in a file.

## Terraform

Reuse the project's `kreuzwerker/docker` provider. Add a sensitive variable for the key, the Agent
image and the container:

```hcl
variable "dd_api_key" {
  type      = string
  sensitive = true
}

resource "docker_image" "dd_agent" {
  name = "registry.datadoghq.com/agent:7"
}

resource "docker_container" "dd_agent" {
  name          = "dd-agent"
  image         = docker_image.dd_agent.image_id
  restart       = "unless-stopped"
  pid_mode      = "host"
  cgroupns_mode = "host"
  env = [
    "DD_API_KEY=${var.dd_api_key}",
    "DD_SITE=<site>",
    "DD_ENV=<env>",
    "DD_DOGSTATSD_NON_LOCAL_TRAFFIC=true",
    "DD_APM_ENABLED=true",
    "DD_APM_NON_LOCAL_TRAFFIC=true",
    "DD_LOGS_ENABLED=true",
    "DD_LOGS_CONFIG_AUTO_MULTI_LINE_DETECTION=true",
    "DD_LOGS_CONFIG_CONTAINER_COLLECT_ALL=true",
    "DD_CONTAINER_EXCLUDE_LOGS=name:dd-agent",
  ]
  volumes {
    host_path      = "/var/run/docker.sock"
    container_path = "/var/run/docker.sock"
    read_only      = true
  }
  # Add one volumes block each for /proc/ -> /host/proc/ (read-only),
  # /sys/fs/cgroup/ -> /host/sys/fs/cgroup (read-only),
  # /var/lib/docker/containers (read-only), /opt/datadog-agent/run (logs),
  # and /var/run/datadog when APM Instrumentation is used.
  networks_advanced {
    name = docker_network.<app_network>.name
  }
}
```

On each application `docker_container`, add the APM variables to `env` and
`depends_on = [docker_container.dd_agent]`; without it, Terraform starts both at once.
Terraform keeps container environment values, including the key, in its state, so the state must
stay private.

## Ansible

Prefer `community.docker.docker_container` when the collection is available. Add the task before
the application container tasks:

```yaml
- name: Run the Datadog Agent
  community.docker.docker_container:
    name: dd-agent
    image: registry.datadoghq.com/agent:7
    restart_policy: unless-stopped
    pid_mode: host
    cgroupns_mode: host
    state: started
    env:
      DD_API_KEY: "{{ lookup('ansible.builtin.env', 'DD_API_KEY') }}"
      DD_SITE: "<site>"
      DD_ENV: "<env>"
      DD_DOGSTATSD_NON_LOCAL_TRAFFIC: "true"
      DD_APM_ENABLED: "true"
      DD_APM_NON_LOCAL_TRAFFIC: "true"
      DD_LOGS_ENABLED: "true"
      DD_LOGS_CONFIG_AUTO_MULTI_LINE_DETECTION: "true"
      DD_LOGS_CONFIG_CONTAINER_COLLECT_ALL: "true"
      DD_CONTAINER_EXCLUDE_LOGS: "name:dd-agent"
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock:ro
      - /proc/:/host/proc/:ro
      - /sys/fs/cgroup/:/host/sys/fs/cgroup:ro
      - /var/lib/docker/containers:/var/lib/docker/containers:ro
      - /opt/datadog-agent/run:/opt/datadog-agent/run:rw
    networks:
      - name: <app_network>
  no_log: true
```

`env` values must be strings, so quote `"true"`. `no_log: true` keeps the key out of the task
output. Add `/var/run/datadog:/var/run/datadog` to `volumes` when APM Instrumentation is used. When
the collection is not installed, an `ansible.builtin.shell` task that runs the Agent `docker run`
command from the prepare mode is the fallback.

## Validate and report

When the tools are installed, check syntax without applying anything: `terraform fmt -check` for
Terraform, and `ansible-playbook --syntax-check <playbook>` for Ansible. Never run
`terraform apply` or the playbook yourself.

The next steps use only the tool, never `docker run` or `docker compose up`:

1. Get a Datadog API key at `<APP_BASE>/organization-settings/api-keys`.
2. Terraform: set the key as a Terraform variable, then run `terraform init` and `terraform apply`
   together:

   (Replace $DD_API_KEY with your API key)

   ```bash
   export TF_VAR_dd_api_key=$DD_API_KEY
   terraform init
   terraform apply
   ```

   Ansible: set the key and run the playbook in the same shell, with the project's usual inventory
   and options:

   (Replace $DD_API_KEY with your API key)

   ```bash
   export DD_API_KEY=$DD_API_KEY
   ansible-playbook <playbook>
   ```
