# Terraform

Use the Datadog ECS module, `DataDog/ecs-datadog/aws`, with `version = "~> 1.2"`. Its `ecs_fargate` submodule builds the app's task definition with the Agent sidecar, and its `ecs_ec2` submodule creates the Agent daemon. Placeholders are the ones in [task-definition-json.md](task-definition-json.md).

The module needs Terraform 1.5 or later and the `hashicorp/aws` provider 5.85 or later. When the project pins older versions, say so in the report instead of changing the pins.

## Contents

- The API key secret
- Fargate: replace the task definition with `ecs_fargate`
- EC2: add `ecs_ec2` and wire the app's task definition
- Mistakes to avoid

## The API key secret

Reference the secret; never create its value in Terraform. A `secret_string` set from a variable ends up in plain text in the Terraform state. If the configuration already has a data source or resource for the Datadog secret, use it. Otherwise add:

```hcl
variable "datadog_api_key_secret_name" {
  description = "Name of the AWS Secrets Manager secret that holds the Datadog API key"
  type        = string
  default     = "datadog/api-key"
}

data "aws_secretsmanager_secret" "datadog_api_key" {
  name = var.datadog_api_key_secret_name
}
```

Both submodules grant the task execution role `secretsmanager:GetSecretValue` on this secret.

## Fargate: replace the task definition with `ecs_fargate`

Replace the app's `resource "aws_ecs_task_definition"` with the module, and carry its arguments over:

```hcl
module "datadog_ecs_fargate_task" {
  source  = "DataDog/ecs-datadog/aws//modules/ecs_fargate"
  version = "~> 1.2"

  dd_api_key_secret = { arn = data.aws_secretsmanager_secret.datadog_api_key.arn }
  dd_site           = "<DD_SITE>"
  dd_image_version  = "7"

  # Unified service tags: only the values the repository shows.
  dd_service = "<SERVICE>"
  dd_env     = "<ENV>"
  dd_version = "<VERSION>"
  dd_tags    = "env:<ENV>" # the module tags FireLens logs only from dd_tags

  dd_apm = { enabled = true }

  dd_log_collection = {
    enabled = true
    fluentbit_config = {
      log_driver_configuration = {
        host_endpoint = "http-intake.logs.<DD_SITE>"
        tls           = true
        service_name  = "<SERVICE>"
        source_name   = "<SOURCE>"
      }
    }
  }

  # Carried over from the old aws_ecs_task_definition:
  family                   = var.task_family
  cpu                      = tonumber(var.task_cpu)
  memory                   = tonumber(var.task_memory)
  requires_compatibilities = ["FARGATE"]
  execution_role           = { arn = aws_iam_role.task_execution.arn }
  task_role                = { arn = aws_iam_role.task.arn }
  container_definitions    = jsonencode([ /* the same app containers as before */ ])
}
```

- **Carry every argument over.** Pass `container_definitions`, `cpu` and `memory` with the same expressions as before (`jsonencode(...)`, `file(...)`, `templatefile(...)`, `var.task_cpu`). Never replace a size expression with a literal: `*.tfvars` files and workspaces set their own sizes through it. The module takes `cpu` and `memory` as numbers, so wrap string expressions in `tonumber()`. Apply the size rule from step 3 of SKILL.md to the variable's `default`, and tell the user to check the overrides. Carry over `runtime_platform`, `volumes`, `ephemeral_storage` and `tags` when they are set.
- **Pass the existing roles.** When the old resource set `execution_role_arn` or `task_role_arn`, pass them as `execution_role = { arn = … }` and `task_role = { arn = … }`. Without `task_role`, the module creates a new task role, and the app loses the AWS permissions of its old one.
- **Point the service at the module.** In `aws_ecs_service`, set `task_definition = module.datadog_ecs_fargate_task.arn`. Change every other reference to the removed resource, such as outputs, to the module's outputs (`arn`, `family`, `revision`).
- **Do not repeat what the module adds,** because a second copy conflicts with it. It adds the Agent and log router containers, the `dd-sockets` volume and its mounts, `DD_TRACE_AGENT_URL`, the unified service tags as environment variables and labels, and the FireLens log configuration on every container. It replaces each container's `logConfiguration`, so app logs go to Datadog instead of CloudWatch.
- **Log tags.** The module sets `dd_tags` on the FireLens output only from `dd_tags`; `dd_env` sets the environment variable and label, not a log tag. When the repository shows an env, pass `dd_tags = "env:<ENV>"`, appended with a comma to any `dd_tags` already set. It also becomes a global tag on the Agent's data.
- **IAM names.** The module names the IAM policies it creates after `family`, and IAM names are unique per account. If two environments in one account deploy the same family, tell the user that the second apply fails until the families differ.
- Leave the module's other defaults, such as `dd_essential` and `dd_is_datadog_dependency_enabled`, as they are.

## EC2: add `ecs_ec2` and wire the app's task definition

Add the Agent daemon:

```hcl
module "datadog_agent" {
  source  = "DataDog/ecs-datadog/aws//modules/ecs_ec2"
  version = "~> 1.2"

  dd_api_key_secret = { arn = data.aws_secretsmanager_secret.datadog_api_key.arn }
  dd_site           = "<DD_SITE>"
  dd_image_version  = "7"
  dd_apm            = { enabled = true }
  dd_log_collection = { enabled = true }

  family       = "${aws_ecs_cluster.main.name}-datadog-agent"
  cluster_arn  = aws_ecs_cluster.main.arn
  service_name = "datadog-agent"
}
```

Set `cluster_arn` from the cluster resource, variable or data source the configuration already uses. The module has no `dd_cluster_name` input; do not pass one. Build `family` from the cluster or environment name, because the module creates IAM roles and policies named after it, and IAM names are unique per account: a fixed `datadog-agent` makes the second cluster in an account fail with `EntityAlreadyExists`.

Then keep the app's `aws_ecs_task_definition` and add the socket volume and mount. The module's `app_dd_sockets_volume` and `app_dd_sockets_mount` outputs are lists, so use a `dynamic` block and `concat`:

```hcl
resource "aws_ecs_task_definition" "app" {
  # ...existing arguments...

  dynamic "volume" {
    for_each = module.datadog_agent.app_dd_sockets_volume
    content {
      name      = volume.value.name
      host_path = volume.value.host_path
    }
  }

  container_definitions = jsonencode([
    {
      # ...existing container settings...
      environment = concat(
        [ /* existing variables */ ],
        module.datadog_agent.apm_env_vars,
        [
          { name = "DD_SERVICE", value = "<SERVICE>" },
          { name = "DD_ENV", value = "<ENV>" },
          { name = "DD_VERSION", value = "<VERSION>" },
        ]
      )
      mountPoints = concat([ /* existing mounts */ ], module.datadog_agent.app_dd_sockets_mount)
      dockerLabels = {
        "com.datadoghq.ad.logs"      = jsonencode([{ source = "<SOURCE>", service = "<SERVICE>" }])
        "com.datadoghq.tags.service" = "<SERVICE>"
        "com.datadoghq.tags.env"     = "<ENV>"
        "com.datadoghq.tags.version" = "<VERSION>"
      }
    }
  ])
}
```

Merge with existing `environment`, `mountPoints` and `dockerLabels` rather than replacing them. Keep each container's `logConfiguration`; containers that use `awslogs` need the AWS log forwarder for their logs (step 3 of SKILL.md). When `container_definitions` reads a JSON file, edit that file as the EC2 section of [task-definition-json.md](task-definition-json.md) shows, using the volume name `dd-sockets`, and add only the `dynamic "volume"` block here.

## Mistakes to avoid

- `module.datadog_agent.app_dd_sockets_volume.name` and `mountPoints = [module.datadog_agent.app_dd_sockets_mount]` fail, because both outputs are lists.
- Do not set `DD_LOGS_ENABLED` in `dd_environment` on Fargate. The Agent does not collect logs there; FireLens does, and both would compete for the same logs.
- Do not add `local-exec` provisioners or `aws_secretsmanager_secret_version` resources for the key. They put it in shell history or in the Terraform state.
