# CloudFormation

Edit the template that defines the app's `AWS::ECS::TaskDefinition`. The containers and volumes get the same fields as in [task-definition-json.md](task-definition-json.md), written with CloudFormation's property names. Edit only the source template, never a synthesized one under `cdk.out`, which the next synth overwrites.

## Contents

- Property names
- The API key secret
- Fargate
- EC2

## Property names

Task-definition JSON uses camelCase; CloudFormation uses PascalCase. The values stay the same, including option keys such as `Name`, `Host`, `TLS`, `dd_service` and `enable-ecs-log-metadata`, and label keys such as `com.datadoghq.ad.logs`.

| JSON | CloudFormation |
|---|---|
| `name`, `image`, `essential`, `cpu`, `memory` | `Name`, `Image`, `Essential`, `Cpu`, `Memory` |
| `environment` (`name`, `value`) | `Environment` (`Name`, `Value`) |
| `secrets` (`name`, `valueFrom`) | `Secrets` (`Name`, `ValueFrom`) |
| `portMappings` (`containerPort`, `hostPort`, `protocol`) | `PortMappings` (`ContainerPort`, `HostPort`, `Protocol`) |
| `mountPoints` (`sourceVolume`, `containerPath`, `readOnly`) | `MountPoints` (`SourceVolume`, `ContainerPath`, `ReadOnly`) |
| `logConfiguration` (`logDriver`, `options`, `secretOptions`) | `LogConfiguration` (`LogDriver`, `Options`, `SecretOptions`) |
| `firelensConfiguration` (`type`, `options`) | `FirelensConfiguration` (`Type`, `Options`) |
| `dockerLabels` | `DockerLabels` |
| `volumes` (`name`, `host.sourcePath`) | `Volumes` (`Name`, `Host.SourcePath`) |

Quote values that YAML would read as booleans or numbers, such as `'true'` and `'on'`.

## The API key secret

Pass the secret's ARN as a parameter. Do not add an `AWS::SecretsManager::Secret` with the key as a `NoEcho` parameter: the deploy command would carry the key on its command line.

```yaml
Parameters:
  DatadogApiKeySecretArn:
    Type: String
    Description: ARN of the AWS Secrets Manager secret that holds the Datadog API key
```

Use `!Ref DatadogApiKeySecretArn` wherever the JSON reference shows `<DD_API_KEY_SECRET_ARN>`. If the template already references a Datadog secret, use that instead.

Grant the task execution role access to the secret. When the role is a resource in this template, add a policy to it:

```yaml
      Policies:
        - PolicyName: DatadogApiKeySecret
          PolicyDocument:
            Version: '2012-10-17'
            Statement:
              - Effect: Allow
                Action: secretsmanager:GetSecretValue
                Resource: !Ref DatadogApiKeySecretArn
```

When the template imports the role by ARN, it cannot change it. Create `iam/datadog-secret-policy.json` as in [task-definition-json.md](task-definition-json.md), and put its `aws iam put-role-policy` command in the deploy steps.

## Fargate

In the app's existing `AWS::ECS::TaskDefinition`:

1. Add the `datadog-agent` and `datadog-log-router` containers to `ContainerDefinitions`, as in the Fargate section of [task-definition-json.md](task-definition-json.md).
2. Give every app container the FireLens `LogConfiguration`, `DD_TRACE_AGENT_URL`, the unified service tags, the `com.datadoghq.tags.*` labels and the read-only `dd-sockets` mount. Set `Host` to the site's intake, for example `Host: http-intake.logs.<DD_SITE>`, or `!Sub 'http-intake.logs.${DdSite}'` when the template already has a site parameter.
3. Add `- Name: dd-sockets` to `Volumes`.
4. Size the task by the rule in step 3 of SKILL.md. When `Cpu` or `Memory` is `!Ref` to a parameter, keep the `!Ref` and raise the parameter's `Default`, and tell the user to check the values each stack passes for it.

## EC2

1. Add a task definition and a daemon service for the Agent, with the containers, mounts and volumes from the EC2 section of [task-definition-json.md](task-definition-json.md):

   ```yaml
   DatadogAgentTaskDefinition:
     Type: AWS::ECS::TaskDefinition
     Properties:
       Family: datadog-agent
       NetworkMode: bridge
       RequiresCompatibilities: [EC2]
       ExecutionRoleArn: <the app's execution role, as the template already references it>
       ContainerDefinitions:
         - Name: datadog-agent
           Image: public.ecr.aws/datadog/agent:7
           # Essential, Cpu, Memory, Environment, Secrets, PortMappings and MountPoints
           # as in the EC2 section of task-definition-json.md
       Volumes:
         # docker_sock, proc, cgroup, pointdir, containers_root and dd-sockets

   DatadogAgentService:
     Type: AWS::ECS::Service
     Properties:
       Cluster: <the cluster, as the template already references it>
       TaskDefinition: !Ref DatadogAgentTaskDefinition
       SchedulingStrategy: DAEMON
       LaunchType: EC2
   ```

2. In the app's task definition, add the `dd-sockets` host volume (`Host: { SourcePath: /var/run/datadog }`), and give every app container the `com.datadoghq.ad.logs` label, the `com.datadoghq.tags.*` labels, `DD_TRACE_AGENT_URL`, the unified service tags and the read-only `dd-sockets` mount. Keep each container's `LogConfiguration`; containers that use `awslogs` need the AWS log forwarder for their logs (step 3 of SKILL.md).

CloudFormation waits until every instance runs one Agent task before it finishes the update, so instances with no spare CPU or memory make the update roll back. Say so in the report.
