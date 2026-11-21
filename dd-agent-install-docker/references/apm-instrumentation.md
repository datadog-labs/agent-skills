# Docker APM Instrumentation

APM Instrumentation (Single Step Instrumentation) loads the Datadog tracer into containers at the
Docker-daemon level, so the application code and images stay unchanged. Use it for containers that
run with `docker run`. Call it "APM Instrumentation" when you talk to the user.

## Contents

- How it works
- The install command
- Install now
- Prepare in the repository

## How it works

- It runs on the Linux host (amd64 or arm64) where `dockerd` runs natively. The installer exits on
  macOS and Windows, and it cannot instrument a remote daemon from a developer machine.
- It installs the tracer libraries, makes `dd-shim` the default runtime in `/etc/docker/daemon.json`,
  and reloads the Docker daemon. It does not install a host Agent: the Agent stays a container.
- A container is instrumented when it is created. Containers that already run stay untraced until
  they are removed and run again; `docker stop` and `docker start` are not enough.
- Injected tracers send traces through the sockets in `/var/run/datadog/`. The Agent listens on
  `apm.socket` and `dsd.socket` there by default on Linux, so the Agent container needs only the
  `-v /var/run/datadog:/var/run/datadog` mount, not extra socket settings.
- Run it once per host, on every host that should send traces. Run only one Agent per host.

## The install command

```bash
DD_APM_INSTRUMENTATION_ENABLED=docker DD_NO_AGENT_INSTALL=true \
  bash -c "$(curl -fsSL https://install.datadoghq.com/scripts/install_script_agent7.sh)"
```

The script calls `sudo` itself when it does not run as root. The output must list the installed
libraries without errors.

**Library versions.** Add `DD_APM_INSTRUMENTATION_LIBRARIES` only when the run settings or the user
give versions, for example `java:v1,python:v4,js:v5`. Strip each `v` and keep
`language:major` pairs: `DD_APM_INSTRUMENTATION_LIBRARIES=java:1,python:4,js:5`. The names are
`java`, `python`, `js` (Node.js; map `node` or `nodejs` to `js`), `php`, `dotnet` and `ruby`. Without
the variable, the installer installs every supported library at its default major version. Go and
C++ are not injected: use their tracer library steps instead.

**Check the result.** `docker system info --format '{{.DefaultRuntime}}'` prints `dd-shim` when the
install worked. After an application container is recreated,
`docker inspect <container> --format '{{.HostConfig.Runtime}}'` also prints `dd-shim`.

**macOS note.** When the user's machine is a Mac, give this note with the command: "APM
instrumentation (tracing injection) only works on Linux hosts where the Docker daemon runs natively.
On macOS, the agent and logs collection will work, but trace injection will not — this is expected
behavior."

## Install now

Explain that the command modifies the Docker host, and run it only after the user agrees. Run it
before you start the Agent container, in this form so a failed download or install stops the run:

```bash
if ! installer=$(curl -fsSL https://install.datadoghq.com/scripts/install_script_agent7.sh); then
  echo "Installer download failed - stop here." >&2
elif ! DD_APM_INSTRUMENTATION_ENABLED=docker \
  DD_NO_AGENT_INSTALL=true \
  bash -c "$installer"; then
  echo "Instrumentation setup failed - stop here and read the output above." >&2
fi
```

Stop if either message prints; do not start the Agent until the install succeeds. Put any library
pins next to `DD_NO_AGENT_INSTALL=true`.

After the Agent is healthy, list the application containers that should be traced, show the list,
and ask before you recreate them (`docker compose up -d --force-recreate <service>`, or the user's own
`docker run` command after `docker rm -f <name>`).

## Prepare in the repository

Do not run the command. Deliver it in the project's own provisioning path:

- **No IaC:** give the command as the first next step, before the Agent `docker run` command, and
  tell the user to recreate the application containers afterward.
- **Ansible:** add these two tasks to the play that provisions the Docker host, before any container
  task. The check skips hosts that are already instrumented, and the inventory covers every host.

  ```yaml
  - name: Read the Docker daemon info
    ansible.builtin.command: docker info
    register: dd_docker_info
    changed_when: false
    become: true

  - name: Install Datadog APM Instrumentation
    ansible.builtin.shell: |
      DD_APM_INSTRUMENTATION_ENABLED=docker DD_NO_AGENT_INSTALL=true \
        bash -c "$(curl -fsSL https://install.datadoghq.com/scripts/install_script_agent7.sh)"
    become: true
    when: "'Default Runtime: dd-shim' not in dd_docker_info.stdout"
  ```

- **Terraform:** add a `null_resource` (from the `hashicorp/null` provider) whose provisioner runs
  the command once, and add `depends_on = [null_resource.datadog_apm_instrumentation]` to every
  application container resource. Use `local-exec` when `terraform apply` runs on the Docker host:

  ```hcl
  resource "null_resource" "datadog_apm_instrumentation" {
    provisioner "local-exec" {
      command = "[ \"$(docker system info --format '{{.DefaultRuntime}}')\" = dd-shim ] || (DD_APM_INSTRUMENTATION_ENABLED=docker DD_NO_AGENT_INSTALL=true bash -c \"$(curl -fsSL https://install.datadoghq.com/scripts/install_script_agent7.sh)\")"
    }
  }
  ```

  When Terraform manages remote hosts (a `provider "docker"` with `host = "ssh://..."` or
  `"tcp://..."`, or VM resources), use a `remote-exec` provisioner with an SSH `connection` block
  and the same command in `inline`, and add `for_each` over the hosts so every host gets it.
- **Other IaC tools:** add the command as a host provisioning step that runs before the containers
  are created.

Put any library pins in front of the command in every variant.
