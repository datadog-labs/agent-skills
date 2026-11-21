# AWS CDK

On Fargate, use the `DatadogECSFargate` construct from the `datadog-cdk-constructs-v2` package. On EC2 there is no Datadog construct, so add the Agent daemon with `aws-cdk-lib` constructs. The snippets are TypeScript; write the same constructs in the stack's own language. Placeholders are the ones in [task-definition-json.md](task-definition-json.md).

## Contents

- The API key secret
- Fargate: the `DatadogECSFargate` construct
- EC2: the Agent daemon with `aws-cdk-lib`

## The API key secret

Reference the secret by name; never pass the key to CDK. `SecretValue.unsafePlainText(...)` or a `Secret` created from an environment variable puts the key in plain text in the synthesized template under `cdk.out`.

```typescript
import * as secretsmanager from "aws-cdk-lib/aws-secretsmanager";

const ddApiKeySecret = secretsmanager.Secret.fromSecretNameV2(this, "DatadogApiKeySecret", "datadog/api-key");
```

Reuse a secret the stack already references instead. A container secret built with `ecs.Secret.fromSecretsManager(...)` grants the task execution role read access to it.

## Fargate: the `DatadogECSFargate` construct

1. **Add the dependency, pinned exactly**, in the manifest the CDK app uses: `"datadog-cdk-constructs-v2": "<VERSION>"` in `package.json`, or `datadog-cdk-constructs-v2==<VERSION>` in `requirements.txt` or `pyproject.toml`. Do not install it; installing downloads packages, so the user does it in the deploy steps. Pick the newest version whose `aws-cdk-lib` peer range accepts the project's `aws-cdk-lib` version; the Python package uses the same version numbers. This lists the most recent versions with their ranges:

   ```bash
   curl -fsS https://registry.npmjs.org/datadog-cdk-constructs-v2 | python3 -c 'import json,sys; d=json.load(sys.stdin); [print(v, d["versions"][v].get("peerDependencies",{}).get("aws-cdk-lib")) for v in list(d["versions"])[-10:]]'
   ```

   A Go CDK app uses the module `github.com/DataDog/datadog-cdk-constructs-go/ddcdkconstruct/v<MAJOR>`, where `<MAJOR>` is the npm version's major number, at the same version (for example `ddcdkconstruct/v5` at `v5.4.0`). Check it with `curl -fsS 'https://proxy.golang.org/github.com/!data!dog/datadog-cdk-constructs-go/ddcdkconstruct/v<MAJOR>/@latest'`. If a lookup fails, ask the user for the version. Never guess one: a wrong version fails the build or pulls a release that does not fit the project.

2. **Create the construct** next to the task definition:

   ```typescript
   import { DatadogECSFargate } from "datadog-cdk-constructs-v2";

   const ecsDatadog = new DatadogECSFargate({
     apiKeySecret: ddApiKeySecret,
     site: "<DD_SITE>",
     imageVersion: "7",
     service: "<SERVICE>",   // only the tags the repository shows
     env: "<ENV>",
     version: "<VERSION>",
     globalTags: "env:<ENV>", // the construct tags FireLens logs only from globalTags
     apm: { isEnabled: true },
     logCollection: {
       isEnabled: true,
       fluentbitConfig: {
         logDriverConfig: {
           hostEndpoint: "http-intake.logs.<DD_SITE>",
           tls: "on",
           serviceName: "<SERVICE>",
           sourceName: "<SOURCE>",
         },
       },
     },
   });
   ```

3. **Build the task definition from the construct.** Replace `new ecs.FargateTaskDefinition(this, "<Id>", props)` with `ecsDatadog.fargateTaskDefinition(this, "<Id>", props)`, with the same construct ID and the same props (`cpu`, `memoryLimitMiB`, `executionRole`, `taskRole`, `runtimePlatform`). The construct ID keeps the resource's logical ID. Keep each prop's expression: when `cpu` or `memoryLimitMiB` comes from a variable, stack prop or context value, never replace it with a literal. Apply the size rule from step 3 of SKILL.md to its default, and tell the user to check the values each environment passes. In Python the method is `fargate_task_definition`.

   `env` sets the environment variable and label, not a log tag, so `globalTags` carries `env` to the logs. Set it only when the repository shows an env, appended with a comma to any `globalTags` already set; it also becomes a global tag on the Agent's data.

4. **Add the app containers to that task definition** with `addContainer`, as before, and remove their `logging` property. The construct adds the FireLens log driver only to containers without `logging`; a container that keeps `ecs.LogDrivers.awsLogs(...)` sends its logs to CloudWatch only. Containers added this way also get the `dd-sockets` mount, `DD_TRACE_AGENT_URL` and the unified service tags.

5. **Patterns.** `ecs_patterns.ApplicationLoadBalancedFargateService` (or the network load balancer variant) builds its own task definition from `taskImageOptions`. Pass `taskDefinition: <the construct's task definition>` instead, and move the image, port mapping and environment from `taskImageOptions` into `addContainer`. The first container must keep its port mapping, because the load balancer targets it.

## EC2: the Agent daemon with `aws-cdk-lib`

Reuse the stack's cluster and task execution role. Create an execution role only when none exists.

```typescript
import * as ecs from "aws-cdk-lib/aws-ecs";

const agentTaskDef = new ecs.Ec2TaskDefinition(this, "DatadogAgentTaskDef", {
  family: "datadog-agent",
  networkMode: ecs.NetworkMode.BRIDGE,
  executionRole: taskExecutionRole,
  volumes: [
    { name: "docker_sock", host: { sourcePath: "/var/run/docker.sock" } },
    { name: "proc", host: { sourcePath: "/proc/" } },
    { name: "cgroup", host: { sourcePath: "/sys/fs/cgroup/" } },
    { name: "pointdir", host: { sourcePath: "/opt/datadog-agent/run" } },
    { name: "containers_root", host: { sourcePath: "/var/lib/docker/containers/" } },
    { name: "dd-sockets", host: { sourcePath: "/var/run/datadog" } },
  ],
});

const agentContainer = agentTaskDef.addContainer("datadog-agent", {
  containerName: "datadog-agent",
  image: ecs.ContainerImage.fromRegistry("public.ecr.aws/datadog/agent:7"),
  cpu: 256,
  memoryLimitMiB: 512,
  essential: true,
  environment: {
    DD_SITE: "<DD_SITE>",
    DD_APM_ENABLED: "true",
    DD_APM_NON_LOCAL_TRAFFIC: "true",
    DD_DOGSTATSD_NON_LOCAL_TRAFFIC: "true",
    DD_LOGS_ENABLED: "true",
    DD_LOGS_CONFIG_CONTAINER_COLLECT_ALL: "true",
  },
  secrets: { DD_API_KEY: ecs.Secret.fromSecretsManager(ddApiKeySecret) },
  portMappings: [
    { containerPort: 8126, hostPort: 8126, protocol: ecs.Protocol.TCP },
    { containerPort: 8125, hostPort: 8125, protocol: ecs.Protocol.UDP },
  ],
});

agentContainer.addMountPoints(
  { sourceVolume: "docker_sock", containerPath: "/var/run/docker.sock", readOnly: true },
  { sourceVolume: "proc", containerPath: "/host/proc", readOnly: true },
  { sourceVolume: "cgroup", containerPath: "/host/sys/fs/cgroup", readOnly: true },
  { sourceVolume: "pointdir", containerPath: "/opt/datadog-agent/run", readOnly: false },
  { sourceVolume: "containers_root", containerPath: "/var/lib/docker/containers", readOnly: true },
  { sourceVolume: "dd-sockets", containerPath: "/var/run/datadog", readOnly: false },
);

new ecs.Ec2Service(this, "DatadogAgentService", {
  cluster,
  taskDefinition: agentTaskDef,
  serviceName: "datadog-agent",
  daemon: true,
});
```

Then change the app's `Ec2TaskDefinition` and each app container:

```typescript
appTaskDef.addVolume({ name: "dd-sockets", host: { sourcePath: "/var/run/datadog" } });
appContainer.addMountPoints({ sourceVolume: "dd-sockets", containerPath: "/var/run/datadog", readOnly: true });
appContainer.addEnvironment("DD_TRACE_AGENT_URL", "unix:///var/run/datadog/apm.socket");
appContainer.addEnvironment("DD_SERVICE", "<SERVICE>");   // and DD_ENV, DD_VERSION when shown
appContainer.addDockerLabel("com.datadoghq.ad.logs", '[{"source": "<SOURCE>", "service": "<SERVICE>"}]');
appContainer.addDockerLabel("com.datadoghq.tags.service", "<SERVICE>");   // and env, version
```

Keep the app containers' `logging`. Containers that use `ecs.LogDrivers.awsLogs(...)` need the AWS log forwarder for their logs (step 3 of SKILL.md). Never point an EC2 app at `localhost:8126`, because the Agent runs in another task; see the EC2 fallback in [task-definition-json.md](task-definition-json.md).
