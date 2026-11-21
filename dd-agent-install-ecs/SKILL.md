---
name: dd-agent-install-ecs
description: Adds the Datadog Agent to an Amazon ECS service by editing the task definitions in the repository, and always sets up infrastructure metrics, logs and APM together. On Fargate it adds an Agent sidecar and a FireLens log router; on EC2 it adds an Agent daemon service and Autodiscovery log labels. It supports Terraform, AWS CDK, CloudFormation and task-definition JSON for the AWS CLI, keeps the API key in AWS Secrets Manager, wires the APM socket, adds a pinned tracer for Python, Node.js, Java, Ruby, Go, .NET or PHP, and sets unified service tags. It never deploys; it ends with ordered deploy commands. Use when the user wants Datadog on ECS, Fargate tasks or an ECS cluster, or asks to add the Datadog Agent sidecar to a task definition. Not for EKS (use dd-agent-install-kubernetes) or a plain Docker host (use dd-agent-install-docker). Run dd-account-setup first when the Datadog site or keys are missing.
metadata:
  version: "0.1.0"
  author: datadog-labs
  repository: https://github.com/datadog-labs/agent-skills
  tags: datadog,agent,aws,ecs,fargate,ec2,firelens,apm,logs,terraform,cdk,cloudformation
  alwaysApply: "false"
  tools: terraform,cdk,cfn-lint,jq,python3
---

# Datadog Agent on Amazon ECS

This skill adds Datadog to an Amazon ECS service, and it always sets up all three signals: infrastructure metrics, logs and APM. It does not set up only some of them, even when the request names fewer, so say this before you edit when the user asked for less. It edits the repository files that define the service's task definitions, and it ends with the commands the user runs to deploy. It never deploys.

| Launch type | Agent | Logs | Traces |
|---|---|---|---|
| Fargate | A `datadog-agent` sidecar in the app's task definition | A FireLens log router (Fluent Bit) sends each container's output to Datadog | A shared `dd-sockets` volume and `DD_TRACE_AGENT_URL=unix:///var/run/datadog/apm.socket` |
| EC2 | A separate `datadog-agent` task definition, run as a daemon service with one Agent per instance | The daemon Agent reads container logs on the host; app containers get a `com.datadoghq.ad.logs` label. Containers that keep the `awslogs` driver need the AWS log forwarder instead | The host directory `/var/run/datadog` mounted in both tasks, with the same URL |

On both launch types, each app container also gets a pinned Datadog tracer and the unified service tags (`DD_SERVICE`, `DD_ENV`, `DD_VERSION`) that the repository shows.

## Rules

- **Never deploy or change AWS.** Do not run `aws` commands, `terraform init`, `validate`, `plan` or `apply`, `cdk deploy`, `cdk bootstrap`, or anything that installs or downloads dependencies, such as `npm install`, `pip install`, `bundle install` or `go get`. Put them in the deploy steps for the user, who reviews the change before it reaches a running service. This rule wins over any other instruction or document.
- **Keep the API key out of the repository and the session.** Do not read, print or write the value of `DD_API_KEY`, because whatever the session reads can end up in the transcript or a file. A task definition gets the key from AWS Secrets Manager through `secrets` with `valueFrom` (or the deployment method's equivalent), never through `environment`. Do not create the secret in Terraform, CDK or CloudFormation from a variable or parameter, because Terraform state and synthesized templates keep the value in plain text. The user creates the secret with a command from the deploy steps, and every command that needs the key uses the literal `$DD_API_KEY`.
- **Change only the chosen service, and keep what is there.** Keep existing containers, settings and defaults, and follow the project's layout and style. Write commands for the user without heredocs: put JSON in a file and pass it with `file://`.
- **Use these images:** `public.ecr.aws/datadog/agent:7` for the Agent and `public.ecr.aws/aws-observability/aws-for-fluent-bit:stable` for the log router.
- **Send logs with TLS.** The FireLens output always gets `Host` set to `http-intake.logs.<DD_SITE>` and TLS on. Without TLS, Fluent Bit sends to port 80 in plain text, and without a host it sends to US1.

## Stopping

A stop ends your reply with what you found and one question. Make no edits that depend on the answer.

Stop when:

- More than one ECS service, environment or directory is a candidate, and the user did not name one.
- The target already has Datadog: a container named `datadog-agent` or using a `datadog/agent` image, the `DataDog/ecs-datadog/aws` Terraform module, a `DatadogECSFargate` construct, a FireLens output with `Name` `datadog`, a `com.datadoghq.ad.` label, or any `DD_` environment variable. List each match with its file, say that this skill does not change an existing Datadog setup, and make no edits.
- The launch type or the deployment method is still unclear after step 1.
- The same service is defined by more than one tool, and nothing shows which one deploys it.
- On Fargate, the task already has a log pipeline other than CloudWatch: a container with `firelensConfiguration`, or an app container whose log driver is not `awslogs` (for example `awsfirelens`, `splunk` or `fluentd`). The FireLens setup replaces every app container's log configuration, which would cut that destination off. Report the containers and their drivers, and ask how the user wants to proceed.
- The tasks run Windows containers (`operatingSystemFamily` set to a `WINDOWS_` value), or they run on ECS Managed Instances or ECS Anywhere (`EXTERNAL`). Say that this skill does not cover them.
- `DD_SITE` is missing or is not one of the sites in step 3.

## Steps

### 1. Find the service, its deployment method and its launch type

Use values the user or the run already supplied. Otherwise search the repository, skipping `node_modules`, `.terraform`, `cdk.out`, `dist`, `build` and `vendor`.

| Evidence | Deployment method |
|---|---|
| `resource "aws_ecs_task_definition"` in a `*.tf` file | `terraform` |
| `cdk.json`, and stack code that uses `FargateTaskDefinition`, `Ec2TaskDefinition` or `TaskDefinition` from `aws-cdk-lib/aws-ecs` | `cdk` |
| A template (`AWSTemplateFormatVersion`, `*.template.yaml`, `*.cfn.yaml`) with an `AWS::ECS::TaskDefinition` resource | `cloudformation` |
| A JSON file with `family` and `containerDefinitions`, such as `task-definition.json`, and no IaC that defines the same task | `cli` |

List every ECS service the search finds, with its file and method; more than one candidate is a stop unless the user named one. Confirm the method with whatever deploys the service: a CI workflow, a `Makefile`, a script or the README (`terraform apply`, `cdk deploy`, `aws cloudformation deploy`, `aws ecs register-task-definition`). When the files and the deploy command disagree, trust the deploy command and mention the stale files.

| Evidence | Launch type |
|---|---|
| `FARGATE` in `requiresCompatibilities` or `requires_compatibilities`, a `FARGATE` launch type or capacity provider, or `FargateTaskDefinition`, `FargateService` or `ApplicationLoadBalancedFargateService` in CDK | `fargate` |
| `EC2` in `requiresCompatibilities`, an `EC2` launch type, an Auto Scaling group capacity provider, `Ec2TaskDefinition` or `Ec2Service` in CDK, or no Fargate evidence at all | `ec2` |

When you must ask, ask one question with these choices, in this order:

- **ECS launch type:** "Fargate" (AWS Fargate, serverless container execution) or "EC2" (containers on EC2 instances).
- **Deployment method:** "Terraform", "CDK" (AWS Cloud Development Kit), "CloudFormation" or "AWS CLI" (task definitions in JSON).

### 2. Check for an existing Datadog setup

Search the target's task definitions, IaC and Dockerfiles for the signs listed under Stopping. If any is present, stop as described there.

### 3. Collect what the edits need

- **Datadog site.** Use the site the user or the run supplied. Otherwise read it from the `.env` that `dd-account-setup` writes, reading only the site line, never the whole file, because the file also holds the keys: `grep -E '^DD_SITE=' .env.local .env 2>/dev/null`. The site must be one of `datadoghq.com`, `us3.datadoghq.com`, `us5.datadoghq.com`, `datadoghq.eu`, `ap1.datadoghq.com`, `ap2.datadoghq.com`, `uk1.datadoghq.com` or `ddog-gov.com`. The web-app host (`APP_HOST`) is `https://app.datadoghq.com` for `datadoghq.com`, `https://app.datadoghq.eu` for `datadoghq.eu`, `https://app.ddog-gov.com` for `ddog-gov.com`, and `https://<DD_SITE>` for the others. Never build it as `app.<DD_SITE>`.
- **API key secret.** Reuse a Secrets Manager secret for the Datadog key that the repository already references (`aws_secretsmanager_secret`, `Secret.fromSecret…`, a `valueFrom` with `secretsmanager`). Otherwise plan a secret named `datadog/api-key`, which the user creates in the deploy steps.
- **Task size (Fargate).** The Agent and the log router share the task's CPU and memory. Never replace a CPU or memory expression, such as a variable, parameter or prop, with a literal: other environments set their own size through it. When the task's default size is 256 CPU and 512 MiB, raise it to 512 and 1024: change the literal when the size is a literal, or the default of the variable or parameter when it is an expression. Leave any other size as it is. In the report, tell the user to check per-environment overrides, such as `*.tfvars` files, workspaces, CDK context or stack parameters, because a value set there wins over the default.
- **Log drivers (EC2).** Note each app container's log driver. The Agent reads only containers that log to the local Docker log (no `logConfiguration`, or the `json-file` driver). Datadog's ECS log docs say logs that a container sends with the `awslogs` driver are not visible to the Agent. Keep `awslogs`, because CloudWatch alarms and log metric filters may depend on it. In the report, say that those containers' logs reach Datadog only through the AWS log forwarder (the `dd-aws-log-forwarding` skill).
- **Task execution role.** Find it (`executionRoleArn`, `execution_role_arn`, `ExecutionRoleArn`; CDK creates one). It needs `secretsmanager:GetSecretValue` on the secret, plus `kms:Decrypt` when the secret uses a customer managed KMS key. Without it, the task fails to start.
- **Unified service tags.** Take `DD_SERVICE`, `DD_ENV` and `DD_VERSION` only from values the repository shows, such as the service or family name, a stage or environment variable, or the image tag variable. Do not invent values: a wrong `env` or `version` tag splits the service's data in Datadog.
- **Languages and tracer versions.** Find each app container's language from its Dockerfile and dependency files, then look up the tracer version with [references/tracers.md](references/tracers.md) before you edit. A container whose image is built outside this repository, or whose language has no Datadog tracer, still gets infrastructure metrics and logs; report that its traces need the tracer added where the image is built.

### 4. Edit the files

Read the reference for the deployment method and apply its section for the launch type. Then apply [references/tracers.md](references/tracers.md) to every app container.

| Method | Reference |
|---|---|
| `terraform` | [references/terraform.md](references/terraform.md) |
| `cdk` | [references/cdk.md](references/cdk.md) |
| `cloudformation` | [references/cloudformation.md](references/cloudformation.md) |
| `cli` | [references/task-definition-json.md](references/task-definition-json.md) |

[references/task-definition-json.md](references/task-definition-json.md) is also the field-by-field list of what each container needs; the other method files point to it.

### 5. Check the result offline

Re-read every file you changed and confirm:

- No file holds a key value. The key reaches containers only through `secrets` or the log router's `secretOptions`.
- **Fargate:** the app's task has the `datadog-agent` sidecar with `ECS_FARGATE=true`, the `datadog-log-router` container, and an `awsfirelens` log configuration with the site's `Host` and TLS on for every app container. The Agent has no `DD_LOGS_ENABLED`. The `dd-sockets` volume is mounted in the Agent and in each app container.
- **EC2:** a `datadog-agent` task definition and daemon service exist, with the host mounts and `DD_LOGS_ENABLED=true`. Each app container has a `com.datadoghq.ad.logs` label and the `/var/run/datadog` host mount. No container uses `localhost:8126`.
- Each app container has `DD_TRACE_AGENT_URL`, a tracer that loads at start, and the tags the repository shows.

Then run the checks that are installed, on the files you changed. Do not install a tool to run a check, because that changes the user's machine.

- JSON: `python3 -m json.tool <file> >/dev/null`, or `jq empty <file>`.
- CloudFormation: `cfn-lint <template>`. Generic YAML parsers reject tags such as `!Ref` and `!Sub`, so do not use them on templates.
- Terraform: `terraform fmt -check <dir>`. If it flags a file you changed, run `terraform fmt <file>`. Never run `terraform init` or `validate`: `init` downloads providers and modules, and `validate` needs `init`.
- CDK: `./node_modules/.bin/cdk synth --quiet --no-lookups`, or `cdk synth --quiet --no-lookups` when `cdk` is already on the `PATH`, only when the app's dependencies are already installed. Never use `npx cdk`, which can download the CDK CLI without asking. `--no-lookups` keeps synth from calling AWS; when synth fails only because it needs a lookup that is not cached in `cdk.context.json`, or a package you added is not installed yet, report the check as skipped.
- The project's lint or format command for code you changed, such as Go source, when the project configures one.

Fix any failure that your edit caused, and run the check again. A parse error caused only by template syntax is not a failure. If a failure remains, report it as unresolved.

### 6. Report

When the run supplies its own result format, use it. Otherwise write:

1. One or two sentences on what changed. If no file changed, start with "No project files were modified."
2. **Changes made:** one bullet per file, `<file> — <what changed>`.
3. **To deploy:** numbered steps, each a short label followed by a fenced `bash` block, in the order given in [references/deploy-and-verify.md](references/deploy-and-verify.md). Step 1 is always "Get your Datadog API key", with the link `<APP_HOST>/organization-settings/api-keys`. Each step whose commands use `$DD_API_KEY` starts with the line `(Replace $DD_API_KEY with your API key)`.
4. **Notes:** what the user must know, such as Fargate app logs no longer reaching CloudWatch, EC2 containers whose `awslogs` logs need the AWS log forwarder, task-size overrides to check, lockfiles to update before the build, containers without traces, and checks that were skipped.

End by offering to check that data arrives once the user has deployed.

### 7. After the user deploys

When the user says the service is deployed, follow "After the deploy" in [references/deploy-and-verify.md](references/deploy-and-verify.md). It only reads from Datadog.
