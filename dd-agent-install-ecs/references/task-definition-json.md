# Task-definition JSON (AWS CLI)

Use this file for the `cli` method: the project keeps task definitions as JSON files and registers them with `aws ecs register-task-definition`. It is also the field-by-field list that [cloudformation.md](cloudformation.md) and the CDK EC2 path in [cdk.md](cdk.md) follow.

## Contents

- Values used below
- Fargate: the app's task definition
- EC2: the Agent daemon and the app's task definition
- IAM policy file

## Values used below

| Placeholder | Value |
|---|---|
| `<DD_SITE>` | The site from step 3 of SKILL.md |
| `<DD_API_KEY_SECRET_ARN>` | The ARN of the existing secret, when the repository has it. Otherwise keep this literal placeholder; the deploy steps print the ARN and tell the user to put it in. |
| `<SERVICE>`, `<ENV>`, `<VERSION>` | The unified service tags the repository shows. Leave out any tag it does not show. |
| `<SOURCE>` | The container's language or technology in lowercase, such as `python`, `nodejs`, `java`, `ruby`, `go`, `csharp`, `php` or `nginx` |

## Fargate: the app's task definition

On Fargate, the Agent and the log router run in the same task as the app. Edit the app's task definition file. Keep `"networkMode": "awsvpc"` and `"requiresCompatibilities": ["FARGATE"]`.

**Keep the project's registration script working.** Add the new containers after the existing ones, so each app container keeps its index. When a script fills in or patches the file before it registers it (for example `jq` on `.containerDefinitions[0]`, or `envsubst` on `${IMAGE}`), keep the placeholders it replaces, check that its patches still hit the app container, and do not add values the script supplies, such as the image or `executionRoleArn`, because its patch would then overwrite or clash with them.

1. **Add the Agent sidecar** to the end of `containerDefinitions`:

   ```json
   {
     "name": "datadog-agent",
     "image": "public.ecr.aws/datadog/agent:7",
     "essential": false,
     "environment": [
       { "name": "ECS_FARGATE", "value": "true" },
       { "name": "DD_SITE", "value": "<DD_SITE>" },
       { "name": "DD_APM_ENABLED", "value": "true" }
     ],
     "secrets": [
       { "name": "DD_API_KEY", "valueFrom": "<DD_API_KEY_SECRET_ARN>" }
     ],
     "portMappings": [
       { "containerPort": 8126, "protocol": "tcp" },
       { "containerPort": 8125, "protocol": "udp" }
     ],
     "mountPoints": [
       { "sourceVolume": "dd-sockets", "containerPath": "/var/run/datadog", "readOnly": false }
     ],
     "logConfiguration": {
       "logDriver": "awsfirelens",
       "options": {
         "Name": "datadog",
         "Host": "http-intake.logs.<DD_SITE>",
         "TLS": "on",
         "provider": "ecs",
         "dd_service": "datadog-agent",
         "dd_source": "datadog-agent"
       },
       "secretOptions": [
         { "name": "apikey", "valueFrom": "<DD_API_KEY_SECRET_ARN>" }
       ]
     }
   }
   ```

   Do not set `DD_LOGS_ENABLED` on this container. On Fargate, FireLens collects the logs, not the Agent.

2. **Add the log router** to `containerDefinitions`:

   ```json
   {
     "name": "datadog-log-router",
     "image": "public.ecr.aws/aws-observability/aws-for-fluent-bit:stable",
     "essential": false,
     "firelensConfiguration": {
       "type": "fluentbit",
       "options": { "enable-ecs-log-metadata": "true" }
     }
   }
   ```

   When an app container had an `awslogs` log configuration, give the router that configuration, with `awslogs-stream-prefix` set to `datadog-log-router`. The router's own errors then stay visible in CloudWatch, and no new log group is needed.

3. **Change every app container:**

   - Replace `logConfiguration` with the FireLens output. App logs then go to Datadog instead of CloudWatch; say so in the report.

     ```json
     "logConfiguration": {
       "logDriver": "awsfirelens",
       "options": {
         "Name": "datadog",
         "Host": "http-intake.logs.<DD_SITE>",
         "TLS": "on",
         "provider": "ecs",
         "dd_service": "<SERVICE>",
         "dd_source": "<SOURCE>",
         "dd_tags": "env:<ENV>"
       },
       "secretOptions": [
         { "name": "apikey", "valueFrom": "<DD_API_KEY_SECRET_ARN>" }
       ]
     }
     ```

   - Add to `environment`: `{ "name": "DD_TRACE_AGENT_URL", "value": "unix:///var/run/datadog/apm.socket" }`, plus `DD_SERVICE`, `DD_ENV` and `DD_VERSION` with the values the repository shows.
   - Add to `dockerLabels` the same tags: `"com.datadoghq.tags.service": "<SERVICE>"`, `"com.datadoghq.tags.env": "<ENV>"`, `"com.datadoghq.tags.version": "<VERSION>"`. The Agent reads tags for metrics from labels; the tracer reads the environment variables.
   - Add to `mountPoints`: `{ "sourceVolume": "dd-sockets", "containerPath": "/var/run/datadog", "readOnly": true }`.

4. **Change the task level:**

   - Add `{ "name": "dd-sockets" }` to `volumes`, next to any existing volumes. On Fargate, a volume with no `host` is an empty volume that the task's containers share.
   - Keep `executionRoleArn`. If there is none, add the account's `ecsTaskExecutionRole` ARN, using the account ID from another ARN in the file, and tell the user that role must exist with the `AmazonECSTaskExecutionRolePolicy` managed policy.
   - Size the task by the rule in step 3 of SKILL.md: raise a literal `"cpu": "256"` and `"memory": "512"` to `"512"` and `"1024"`. When a script fills the size in, keep its placeholder and tell the user the size their script passes.

**TCP fallback.** If an app cannot use the socket, leave out its socket mount and set `DD_TRACE_AGENT_URL` to `http://localhost:8126`. This works on Fargate only, because all containers in a Fargate task share one network namespace.

## EC2: the Agent daemon and the app's task definition

On EC2, the Agent runs in its own task definition as a daemon service, one task per instance.

1. **Create `datadog-agent-task-definition.json`** next to the app's task definition file. Use the app's execution role, so the Agent can read the same secret.

   ```json
   {
     "family": "datadog-agent",
     "networkMode": "bridge",
     "requiresCompatibilities": ["EC2"],
     "executionRoleArn": "<EXECUTION_ROLE_ARN>",
     "containerDefinitions": [
       {
         "name": "datadog-agent",
         "image": "public.ecr.aws/datadog/agent:7",
         "essential": true,
         "cpu": 256,
         "memory": 512,
         "environment": [
           { "name": "DD_SITE", "value": "<DD_SITE>" },
           { "name": "DD_APM_ENABLED", "value": "true" },
           { "name": "DD_APM_NON_LOCAL_TRAFFIC", "value": "true" },
           { "name": "DD_DOGSTATSD_NON_LOCAL_TRAFFIC", "value": "true" },
           { "name": "DD_LOGS_ENABLED", "value": "true" },
           { "name": "DD_LOGS_CONFIG_CONTAINER_COLLECT_ALL", "value": "true" }
         ],
         "secrets": [
           { "name": "DD_API_KEY", "valueFrom": "<DD_API_KEY_SECRET_ARN>" }
         ],
         "portMappings": [
           { "containerPort": 8126, "hostPort": 8126, "protocol": "tcp" },
           { "containerPort": 8125, "hostPort": 8125, "protocol": "udp" }
         ],
         "mountPoints": [
           { "sourceVolume": "docker_sock", "containerPath": "/var/run/docker.sock", "readOnly": true },
           { "sourceVolume": "proc", "containerPath": "/host/proc", "readOnly": true },
           { "sourceVolume": "cgroup", "containerPath": "/host/sys/fs/cgroup", "readOnly": true },
           { "sourceVolume": "pointdir", "containerPath": "/opt/datadog-agent/run", "readOnly": false },
           { "sourceVolume": "containers_root", "containerPath": "/var/lib/docker/containers", "readOnly": true },
           { "sourceVolume": "dd-sockets", "containerPath": "/var/run/datadog", "readOnly": false }
         ]
       }
     ],
     "volumes": [
       { "name": "docker_sock", "host": { "sourcePath": "/var/run/docker.sock" } },
       { "name": "proc", "host": { "sourcePath": "/proc/" } },
       { "name": "cgroup", "host": { "sourcePath": "/sys/fs/cgroup/" } },
       { "name": "pointdir", "host": { "sourcePath": "/opt/datadog-agent/run" } },
       { "name": "containers_root", "host": { "sourcePath": "/var/lib/docker/containers/" } },
       { "name": "dd-sockets", "host": { "sourcePath": "/var/run/datadog" } }
     ]
   }
   ```

   `pointdir` keeps the Agent's read position in each log, so logs are not sent twice after a restart. `containers_root` gives the Agent the containers' log files.

2. **Change every app container** in the app's task definition:

   - Add to `dockerLabels`: `"com.datadoghq.ad.logs": "[{\"source\": \"<SOURCE>\", \"service\": \"<SERVICE>\"}]"`. Without it, logs are tagged only with the image's short name. Also add the `com.datadoghq.tags.*` labels from the Fargate section.
   - Add to `environment`: `DD_TRACE_AGENT_URL` set to `unix:///var/run/datadog/apm.socket`, plus the unified service tags.
   - Add to `mountPoints`: `{ "sourceVolume": "dd-sockets", "containerPath": "/var/run/datadog", "readOnly": true }`.
   - Keep the container's `logConfiguration`. When it uses the `awslogs` driver, the Agent cannot read that container's logs; the report says they need the AWS log forwarder.

3. **Add the host volume** to the app's `volumes`: `{ "name": "dd-sockets", "host": { "sourcePath": "/var/run/datadog" } }`.

Never use `http://localhost:8126` on EC2. The Agent runs in another task, so `localhost` does not reach it, and the tracer drops the spans without an error. **TCP fallback:** if an app cannot use the socket, it must set `DD_AGENT_HOST` to the instance's private IP when it starts, which needs a change to its entrypoint. Read the IP with IMDSv2, which new instances require:

```bash
TOKEN=$(curl -sX PUT http://169.254.169.254/latest/api/token -H 'X-aws-ec2-metadata-token-ttl-seconds: 60')
export DD_AGENT_HOST=$(curl -s -H "X-aws-ec2-metadata-token: $TOKEN" http://169.254.169.254/latest/meta-data/local-ipv4)
```

## IAM policy file

Create `iam/datadog-secret-policy.json`, which the deploy steps attach to the task execution role:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": "secretsmanager:GetSecretValue",
      "Resource": "<DD_API_KEY_SECRET_ARN>"
    }
  ]
}
```

Grant only this action on this secret. Do not attach the broad `SecretsManagerReadWrite` managed policy, which grants every secret in the account. Add `kms:Decrypt` on the key when the secret uses a customer managed KMS key.
