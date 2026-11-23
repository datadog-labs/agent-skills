# Prepare the Agent install in the repository

In this mode you change project files and give the user the commands to run. Never run the
install script. Never run a command that installs, starts, stops or changes the Datadog Agent on
this machine, not even to test your change.

## Contents

1. Settings
2. Pick the target directory
3. Detect the IaC tool and an existing Agent
4. Build the install variables
5. Write the change: Terraform, Ansible, other IaC or no IaC
6. Validate offline
7. Reply

## 1. Settings

- **Site.** Use the site that the user or the run supplied. Otherwise use `DD_SITE` from the
  environment, `.env.local` or `.env`. Check it with the site check in Phase 0, and ask only when
  no site is found.
- **Products.** Two cases, which step 4 applies:
  - **The run settings carry a products setting**, as a calling tool's always do: use the products
    it names (`infra`, `apm`, `logs` or `otel`), or `infra`, `apm` and `logs` when it is empty.
    This is full coverage. Do not ask again for a value the run supplied.
  - **No run products setting:** follow install now's opt-ins from Phase 1. Infrastructure
    metrics, logs and the APM trace receiver are on. Host Single Step Instrumentation, process-log
    discovery and the OpenTelemetry Collector are added only when the user asks for them; ask about
    process-log discovery as Phase 1 does.
- **Environment.** Set `DD_ENV` only to a value that the user or the run supplied. Never infer
  `DD_ENV`, `DD_SERVICE` or `DD_VERSION` from file, repository or branch names.
- **API key.** You do not need the key to edit files. The user needs it when they run the
  commands. Never put a key in a committed file, in a printed command or in your reply. When the
  run offers a way to write the key to a git-ignored, untracked `.env`, use it. First add `.env` to
  `.gitignore` on its own line if it is missing. Never read that file after it holds a key. This
  mode needs no application key.

## 2. Pick the target directory

List the repository by name before you read any file. Look for IaC roots: `.tf` files,
`ansible.cfg`, `playbook.yml`, `roles/`, `Pulumi.yaml`, `cdk.json`, `*.pp`, `Berksfile`, or a
CloudFormation template (a YAML or JSON file that contains `AWSTemplateFormatVersion`).

- Several candidates, as in a monorepo: ask the user which directory to configure. This is a stop.
- One candidate, or IaC at the repository root: continue.

Keep every later step inside the chosen directory.

## 3. Detect the IaC tool and an existing Agent

| Evidence in the directory | Delivery |
|---|---|
| `.tf` files | Terraform |
| `ansible.cfg`, `playbook.yml` or `roles/` | Ansible |
| `Pulumi.yaml`, `cdk.json`, `*.pp`, `Berksfile` or a CloudFormation template | Other IaC |
| None of these | One shell command |

Then look for an Agent that the project already installs: `datadog.yaml` or other `datadog*`
config files, the install script host `install.datadoghq.com`, a `datadog-agent` package or
service, a Datadog Agent role, module or cookbook, or Agent install variables such as
`DD_API_KEY` in IaC files. Variables that only tag or configure applications, such as
`DD_SERVICE` or tracer settings, are not an Agent install.

- The project already installs the Agent with every requested product: say so and stop. Change
  nothing.
- The project installs the Agent but lacks a requested product: add only the missing variables to
  that install. Never add a second install. Hosts that already have the Agent skip the install
  because of its check, so the new settings reach only new hosts. Say so in the Notes.

## 4. Build the install variables

`<VARS>` below stands for `DD_SITE=<site>` followed by the variables of each requested product.
With a run products setting, every requested product's row applies in full. Without one, use only
the rows the user opted into: the `apm` row only for Single Step Instrumentation (without
profiling or Data Streams unless asked, and with library pins only when versions are given), the
`logs` row only for process-log discovery, and the `otel` row only on request.

| Product | Variables | What the install does with them |
|---|---|---|
| `infra` | none | Infrastructure metrics are always on. |
| `apm` | `DD_APM_INSTRUMENTATION_ENABLED=host`<br>`DD_DATA_STREAMS_ENABLED=true`<br>`DD_PROFILING_ENABLED=auto`<br>`DD_APM_INSTRUMENTATION_LIBRARIES=java:1,python:4,js:5,php:1,dotnet:3,ruby:2` | Host Single Step Instrumentation. The script runs the Datadog installer, which installs only the listed tracer libraries at those major versions and writes the Data Streams and profiling defaults to `/etc/datadog-agent/application_monitoring.yaml` for instrumented processes. |
| `logs` | `DD_LOGS_CONFIG_PROCESS_COLLECT_ALL=true` | Turns on the Logs Agent and automatic process-log discovery, with privileged access to process log files. Add `DD_PRIVILEGED_LOGS_ENABLED=false` to keep discovery without privileged access. |
| `otel` | `DD_OTELCOLLECTOR_ENABLED=true` | Installs the bundled OpenTelemetry Collector, which needs Agent 7.69.3 or later. |

Add `DD_ENV=<env>` when an environment was supplied. When the user or the run gives tracer
versions, use them in the same `language:version` form.

The install script reads no plain logs switch. When logs are on without the `logs` row, run
`<LOGS_ON>` right after the install, inside the same guarded command (`install && <LOGS_ON>`), so
it runs only where the Agent was just installed:

```bash
(sudo grep -q '^logs_enabled:' /etc/datadog-agent/datadog.yaml && sudo sed -i 's/^logs_enabled:.*/logs_enabled: true/' /etc/datadog-agent/datadog.yaml || echo 'logs_enabled: true' | sudo tee -a /etc/datadog-agent/datadog.yaml >/dev/null) && sudo systemctl restart datadog-agent
```

The one-line install command is:

```bash
<VARS> DD_API_KEY=$DD_API_KEY bash -c "$(curl -L https://install.datadoghq.com/scripts/install_script_agent7.sh)"
```

## 5. Write the change

### Terraform

Add a `terraform_data` resource. Terraform runs its provisioner once, when it creates the
resource. Use `local-exec` when `terraform apply` runs on the host that should get the Agent:

```hcl
variable "dd_api_key" {
  type      = string
  sensitive = true
}

resource "terraform_data" "datadog_agent" {
  provisioner "local-exec" {
    interpreter = ["bash", "-c"]
    command     = "command -v datadog-agent >/dev/null 2>&1 || (<VARS> sudo -E bash -c \"$(curl -L https://install.datadoghq.com/scripts/install_script_agent7.sh)\")"
    environment = {
      DD_API_KEY = var.dd_api_key
    }
  }
}
```

Use `remote-exec` when the project creates the VMs and Terraform reaches them over SSH. Use
`for_each` over the hosts, so that every host gets the Agent. Reuse the project's existing host
addresses, SSH user and key, and add the variables below only when they are missing:

```hcl
variable "host_ips" {
  type = list(string)
}

variable "ssh_user" {
  type = string
}

variable "ssh_key_path" {
  type = string
}

resource "terraform_data" "datadog_agent" {
  for_each = toset(var.host_ips)

  provisioner "remote-exec" {
    connection {
      type        = "ssh"
      host        = each.value
      user        = var.ssh_user
      private_key = file(var.ssh_key_path)
    }
    inline = [
      "command -v datadog-agent >/dev/null 2>&1 || (<VARS> DD_API_KEY=\"${var.dd_api_key}\" sudo -E bash -c \"$(curl -L https://install.datadoghq.com/scripts/install_script_agent7.sh)\")"
    ]
  }
}
```

This path also needs the `dd_api_key` variable. In both paths, add
`depends_on = [terraform_data.datadog_agent]` to the application resources, so that the Agent is
installed first.

### Ansible

Add two tasks to the playbook that provisions the hosts, before any application task. Put each
variable from `<VARS>` on its own line that ends in ` \`:

```yaml
- name: Check if Datadog Agent is already installed
  ansible.builtin.shell: command -v datadog-agent >/dev/null 2>&1
  register: dd_agent_status
  changed_when: false
  failed_when: false

- name: Install Datadog Agent
  ansible.builtin.shell: |
    DD_SITE=<site> \
    bash -c "$(curl -L https://install.datadoghq.com/scripts/install_script_agent7.sh)"
  become: true
  environment:
    DD_API_KEY: "{{ lookup('env', 'DD_API_KEY') }}"
  when: dd_agent_status.rc != 0
```

If the play tags its tasks for a lifecycle, such as `tags: [start]`, add the same tags to both
tasks.

### Other IaC

Inject the install only when you are confident of the mapping. Include the
`command -v datadog-agent >/dev/null 2>&1 ||` check, so that a re-run skips hosts that already
have the Agent. Otherwise, use the shell command and say in the summary that the user may need to
adapt the steps to their IaC.

### No IaC

Use the shell command from step 4. Change no other project file.

## 6. Validate offline

Check every file that you added or changed, without contacting a host or a cloud:

- Terraform: `terraform fmt -check` in the directory. Never run `terraform init`, `plan` or
  `apply`.
- Ansible: `ansible-playbook --syntax-check <playbook>`.
- YAML and JSON: parse them with any available parser, such as `python3`, `node` or `jq`. Skip
  templates such as `*.j2`.
- Shell scripts: `bash -n <script>`.

When a tool is missing, say which check did not run. When a check fails for any reason other than
template syntax, fix the file, or report that the setup is incomplete.

## 7. Reply

Use the caller's result format when it defines one. Otherwise, the reply has these parts and
nothing else:

1. One or two sentences on what you prepared. Nothing is installed or enabled until the user runs
   the steps, so never say that a product is enabled or configured. When you changed no file,
   start with "No project files were modified."
2. **Changes made:** one bullet per file, as `file — what changed`. Omit this part when no file
   changed.
3. **To run:** numbered steps. Each step has a label and then a fenced `bash` block. Use the
   literal `$DD_API_KEY` in commands. Put "(Replace $DD_API_KEY with your API key)" after the label
   of each step that uses it. Give one delivery path only, with no alternatives.
   - Step 1 is always "Get your Datadog API key" with the link
     `<APP_BASE>/organization-settings/api-keys`. For Ansible, step 1 also holds
     `export DD_API_KEY=$DD_API_KEY`, followed by this line: "If you're running this stack anywhere
     else — a remote server, CI/CD, etc. — make sure DD_API_KEY is set in that environment."
   - Terraform: step 2 is `export TF_VAR_dd_api_key=$DD_API_KEY`. The last step runs
     `terraform init` and `terraform apply` in one block.
   - Ansible: the `ansible-playbook` command only.
   - Shell: the install command with `DD_API_KEY=$DD_API_KEY` inline, and no `export` step.
   - Unless the caller shows its own verification, a last step checks `sudo datadog-agent status`
     on a host and the host list at `<APP_BASE>/infrastructure`.
4. **Notes:** one bullet for each note below that applies to the path you used. Omit this part
   when none applies.

Notes:

- Terraform `local-exec`: `sudo -E` keeps `DD_API_KEY` only when sudoers allows it. If the key
  arrives empty, the Agent installs but cannot authenticate. Then add
  `Defaults env_keep += "DD_API_KEY"` to `/etc/sudoers` on the machine that runs `terraform apply`.
- Terraform `remote-exec`: the key is part of the command that Terraform sends to each host.
  Treat Terraform state, plan output and logs as sensitive, with encrypted remote state and strict
  access, or use `local-exec` or the project's existing secret delivery instead. The `sudo -E` note
  applies on each remote host.
- Ansible: `become` runs the task as root, and most systems clear environment variables when they
  switch users, so `DD_API_KEY` can arrive empty. The Agent then installs but cannot authenticate.
  Try these fixes in order: add `become_flags: "-E"` to the install task (for the default `sudo`
  method); add `Defaults env_keep += "DD_API_KEY"` to `/etc/sudoers`; or keep the key in Ansible
  Vault and pass it as a task variable.
- APM: processes that were running before the install must restart before Single Step
  Instrumentation applies to them. Processes that start later are instrumented automatically.
- OpenTelemetry Collector: the collector only receives data. Making the application export OTLP to
  it is the job of the `dd-instrument-otel` skill.
- Every path: for a few seconds the install script puts the key on its own command lines, where
  other local users can see it with `ps`.
- Every path: if the Agent does not start because it cannot detect a hostname, add
  `hostname: <name>` to `/etc/datadog-agent/datadog.yaml`, using `hostname -f` or the name that
  the cloud provider assigns. Then run `sudo systemctl restart datadog-agent`.
