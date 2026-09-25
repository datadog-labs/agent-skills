---
name: enable-dsm
description: Enable Data Streams Monitoring (DSM) on services already instrumented with APM, for end-to-end latency, throughput, and consumer lag across Kafka, RabbitMQ, SQS, SNS, Kinesis, Pub/Sub, IBM MQ, Azure Service Bus, and BullMQ pipelines. Use when the user asks for data streams, queue lag, pipeline latency, or Kafka monitoring, or when an APM install finds an event-driven, async microservice, or Lambda-based system.
metadata:
  version: "1.0.0"
  author: datadog-labs
  repository: https://github.com/datadog-labs/agent-skills
  tags: datadog,apm,dsm,data-streams,kafka,rabbitmq,sqs,sns,kinesis,pubsub,queues,event-driven,ssi
  alwaysApply: "false"
  tools: kubectl,pup
---

# Enable Data Streams Monitoring

Data Streams Monitoring runs inside the same Datadog SDK that APM already injected. It adds a small context header to each message so Datadog can stitch producers, queues, and consumers into pipelines and measure end-to-end latency and lag. There is no new agent, no new package, and no code change for supported libraries. It is one tracer setting per service.

> **Before doing anything else:** Fully resolve all variables in `## Context to resolve before acting`, then get the user's explicit yes in Step 1. Do not change any configuration before that yes.

---

## Triggers

Invoke this skill when:
- The user asks to enable Data Streams Monitoring, monitor queue or consumer lag, see pipeline latency, or monitor message flow through Kafka / RabbitMQ / SQS / SNS / Kinesis / Pub/Sub
- `enable-ssi` (Kubernetes or Linux) reached its event-driven check and found a fit
- The user's system is event-driven: microservices that hand work to each other asynchronously, services that turn a synchronous request into async background work, or Lambda functions triggered by queues or streams

Do NOT invoke this skill if:
- APM is not yet set up on the target services. Run the `dd-apm` install flow first; DSM needs the Datadog SDK running in the process
- The user only wants Kafka broker or cluster health (brokers, topics, partitions, configs). That is [Kafka Console](https://docs.datadoghq.com/data_streams/kafka/setup/), an Agent check, and out of scope for this skill
- The user already declined DSM in this session. Do not ask again

---

## Plan and cost: tell the user before enabling

DSM is **included with APM Pro and APM Enterprise**. On the base APM tier it is **billed separately**.

- Never state or estimate prices. Link to the [pricing page](https://www.datadoghq.com/pricing/?product=data-streams-monitoring#products) instead.
- Never guess the user's plan. If they don't know it, say it can be checked with their Datadog account team or under **Plan & Usage**.
- If the user says they have APM Pro or Enterprise, DSM is part of what they already pay for. Recommend enabling it on every service that produces or consumes messages.
- If the user is on base APM or unsure, recommend enabling it only on the producer and consumer services.
- **.NET:** tracer 3.22.0+ already sends DSM data in a default-enabled mode, which is processed only for orgs with APM Pro, APM Enterprise, or DSM in their contract. On base APM, setting `DD_DATA_STREAMS_ENABLED=true` on a .NET service is the step that turns DSM into billed usage. Say so when offering it.

---

## Is DSM a fit? Detect from the workspace

> **Discover from the code and cluster. Do not ask the user for information you can find yourself.**

This is the canonical detection command. `enable-ssi` runs it from here.

### Claude runs

```bash
grep -rliE "kafka|confluent|sarama|karafka|waterdrop|amqp|rabbitmq|kombu|rhea|sqs|sns|kinesis|pubsub|ibm\.mq|ibmmq|servicebus|bullmq|boto3|botocore|@aws-sdk/client-" \
  --exclude-dir=node_modules --exclude-dir=vendor --exclude-dir=.git \
  --include=requirements.txt --include=pyproject.toml --include=Pipfile \
  --include=package.json --include=pom.xml --include=build.gradle --include=build.gradle.kts \
  --include=go.mod --include=Gemfile --include='*.csproj' --include='*.fsproj' \
  --include=Directory.Packages.props --include=packages.config \
  . 2>/dev/null || echo "No messaging client dependency found"
```

On Kubernetes, also check what's deployed:

```bash
kubectl get pods -A -o jsonpath='{range .items[*]}{.metadata.namespace}{"\t"}{.metadata.name}{"\t"}{.spec.containers[*].image}{"\n"}{end}' \
  | grep -iE "kafka|rabbit|zookeeper|redpanda|strimzi|activemq|bullmq" || echo "No broker pods found"
```

| Signal | Fit |
|---|---|
| A messaging client dependency in any service manifest | **Strong**: offer DSM for those services |
| Broker pods in the cluster, a managed broker in config (MSK, Confluent Cloud, Amazon MQ), or `KAFKA_*` / `*_QUEUE_URL` / `*_TOPIC` env vars | **Strong** |
| Lambda functions with SQS, SNS, or Kinesis event sources | **Strong**: see Step 2c |
| Several microservices where one accepts a request and another finishes the work later (job queues, outbox, event bus, fan-out workers) | **Offer**: explain that DSM follows work across the async hand-off, which request traces alone don't connect end to end |
| A single synchronous service with no messaging | Skip. Do not offer DSM |

What DSM cannot see, so do not promise it:
- Redis-backed job queues (Sidekiq, Celery on Redis, RQ, Bull classic). If these are the only async mechanism, tell the user DSM won't cover them
- Unsupported clients that the grep still matches: `kafka-python` (Python), `node-rdkafka` (Node.js), `franz-go` (Go)
- Go and PHP services under SSI (see the table below)

---

## Language support

| Language | DSM with SSI (no code change) | Notes |
|---|---|---|
| Java | Yes | Kafka, RabbitMQ, SQS, SNS, Kinesis, Pub/Sub, IBM MQ. Kafka lag is not generated for `kafka-clients` 3.7.x (Spring Boot 3.3 / spring-kafka 3.2); upgrade to 3.8+ |
| Python | Yes | Kafka (`confluent-kafka`, `aiokafka`), RabbitMQ (Kombu), SQS/SNS/Kinesis (botocore), Pub/Sub. Not `kafka-python` |
| Node.js | Yes | Kafka (`kafkajs`, Confluent), RabbitMQ (`amqplib`, `rhea`), SQS, SNS, Kinesis, Pub/Sub, BullMQ. Not `node-rdkafka` |
| .NET | Yes | Tracer 3.22.0+ runs a default-enabled mode (see `## Plan and cost`). `DD_DATA_STREAMS_ENABLED=true` adds full mode: all messages, message sizes, schema tracking, and serverless |
| Ruby | Yes | Kafka only (`ruby-kafka`, `karafka`, `waterdrop`) |
| Go | No, SSI does not inject Go | Build with [Orchestrion](https://datadoghq.dev/orchestrion/docs/getting-started/) or wrap the client manually, then set `DD_DATA_STREAMS_ENABLED=true`. Follow the [Go DSM setup](https://docs.datadoghq.com/data_streams/setup/language/go/) |
| PHP | Not supported | Tell the user; do not enable |

Minimum tracer versions per library are in the [DSM setup docs](https://docs.datadoghq.com/data_streams/setup/). Datadog Agent v7.34.0 or later is required.

---

## Context to resolve before acting

| Variable | How to resolve |
|---|---|
| `PLATFORM` | `kubernetes`, `linux`, or `lambda`. Reuse what the APM install used |
| `DSM_SERVICES` | Services whose code produces or consumes messages, from the detection step above. Exclude services with no messaging client |
| `LANGUAGES` | From the manifests found in detection |
| `AGENT_MANAGER` | Kubernetes only. `kubectl get datadogagent -A` returns a resource → `operator`. Otherwise `helm list -A \| grep datadog` → `helm` |
| `DSM_APP_LABELS` | Kubernetes only. The `app` label of each Deployment in `DSM_SERVICES` |
| `APP_NAMESPACE`, `AGENT_NAMESPACE` | Kubernetes only. Reuse the values from `enable-ssi` |
| `SYSTEMD_SERVICE_NAME`, `SSH_*` | Linux only. Reuse the values from `enable-ssi` |
| `ENV`, `DD_SITE` | Reuse from the APM install |

---

## Step 1: Offer DSM and get an explicit yes

Tell the user, in one short message:
1. Which services look event-driven and why (name the dependency or signal you found)
2. What DSM adds: end-to-end latency across queues, consumer lag, throughput per topic/queue, and which service is the bottleneck
3. The plan rule from `## Plan and cost` above
4. What will change: one environment variable on `<DSM_SERVICES>`, plus a restart. For .NET 3.22+ on Pro/Enterprise, say DSM views may already show data and this adds full mode

Wait for an explicit yes. If the user says no, stop and continue with the calling skill's next step.

> **Called from `enable-ssi` before its restart step?** Make the Step 2a/2b config change, skip the application restart below, and return to `enable-ssi`. Its restart applies SSI and DSM together, and `onboarding-summary` verifies DSM data.

---

## Step 2a: Kubernetes (SSI targets)

Configure DSM in the SSI config (`DatadogAgent` or Helm values) with `ddTraceConfigs`. Do not add environment variables to application Deployments; `enable-ssi` keeps all SDK config in the SSI config.

> **How targets work. Get this wrong and APM silently disappears from other workloads.**
> - `ddTraceConfigs` is only valid inside a `targets[]` entry. `targets` requires Cluster Agent 7.64+.
> - As soon as `targets` exists, SSI instruments **only** pods that match a target. The first matching target wins.
> - A target with `ddTraceVersions` injects only the listed languages and turns off language detection for its pods.

Adjust the existing instrumentation config based on how `enable-ssi` set it up:

| Existing setup | Change |
|---|---|
| Option A: cluster-wide, no `targets` | Add the DSM target, then a catch-all `default` target last so every other pod stays instrumented |
| Option B: `enabledNamespaces` | Remove `enabledNamespaces` (it cannot be combined with `targets`; the Cluster Agent rejects the config). Put those namespaces in the `default` target's `namespaceSelector.matchNames` |
| Option C: `disabledNamespaces` | Keep it. Add the DSM target and the `default` target |
| Option D: existing `targets` | Insert the DSM target before any target that matches the same pods, and copy that target's `ddTraceVersions` / `ddTraceConfigs` into it |

Operator (`DatadogAgent`), Option A:

```yaml
features:
  apm:
    instrumentation:
      enabled: true
      targets:
        - name: data-streams
          namespaceSelector:
            matchNames:
              - <APP_NAMESPACE>
          podSelector:                # omit to cover the whole namespace
            matchExpressions:
              - key: app
                operator: In
                values: [<DSM_APP_LABELS>]
          ddTraceConfigs:
            - name: DD_DATA_STREAMS_ENABLED
              value: "true"
        - name: default               # keeps all other pods instrumented as before
```

Do not add `ddTraceVersions` to the DSM target unless the pods' previous target had one; then copy it verbatim.

The DSM docs also set `DD_TRACE_REMOVE_INTEGRATION_SERVICE_NAMES_ENABLED=true`. It renames integration spans in APM and is not required for DSM data. Do not set it unless the user asks; to consolidate service names, use `service-remapping`.

Helm: the same `targets` block goes under `datadog.apm.instrumentation.targets` in the values file, applied with `helm upgrade`.

### Claude runs

```bash
kubectl apply -f datadog-agent.yaml
sleep 15   # the Operator takes a few seconds to start rolling the Cluster Agent
kubectl rollout status deployment/datadog-cluster-agent -n <AGENT_NAMESPACE> --timeout=180s
kubectl get pods -n <AGENT_NAMESPACE> -l agent.datadoghq.com/component=cluster-agent --no-headers
```

Wait until only one Cluster Agent pod is listed and it is `Running`, then wait another 30 seconds before restarting applications. The injection webhook uses `failurePolicy: Ignore`, so while the old Cluster Agent pod is still answering, a restarted pod is admitted with the old config or with no injection at all, and nothing reports an error.

> **Confirm with the user before restarting.** Tell the user: "I need to restart `<DSM_SERVICES>` in `<APP_NAMESPACE>` for the DSM setting to reach the pods. This will cause a brief outage. Ready to proceed?" Wait for confirmation.

For each Deployment in `DSM_SERVICES`:

### Claude runs

```bash
kubectl rollout restart deployment/<DEPLOYMENT_NAME> -n <APP_NAMESPACE>
kubectl rollout status deployment/<DEPLOYMENT_NAME> -n <APP_NAMESPACE> --timeout=180s
kubectl get pod -l app=<APP_LABEL> -n <APP_NAMESPACE> \
  -o jsonpath='{range .items[0].spec.containers[*]}{.name}{"="}{.env[?(@.name=="DD_DATA_STREAMS_ENABLED")].value}{"\n"}{end}'
```

If the application container shows `=true`, DSM is configured for that service.

ERROR: Empty. Check the pod's init containers (`kubectl get pod <POD> -n <APP_NAMESPACE> -o jsonpath='{.spec.initContainers[*].name}'`):
- No `datadog-lib-*-init` at all → the pod was created during the Cluster Agent rollout. Wait 30 seconds and restart that Deployment again.
- Init containers present but no DSM variable → the pod doesn't match the DSM target. Compare the target's `namespaceSelector` / `podSelector` with the pod's namespace and labels.

Then confirm a workload **outside** `DSM_SERVICES` is still instrumented after its next restart:

```bash
kubectl get pod <OTHER_POD> -n <OTHER_NAMESPACE> -o jsonpath='{.spec.initContainers[*].name}'
```

ERROR: No `datadog-lib-*-init`. First restart that workload once more, in case it raced the Cluster Agent rollout. If it is still missing, the `default` target is missing or listed before the DSM target. Fix the order and re-apply.

**Java alternative without a restart:** on the APM Service Page, **Enable DSM** turns it on through Remote Configuration.

---

## Step 2b: Linux (SSI on a host)

Add the variable to the same systemd drop-in `enable-ssi` created for Unified Service Tags.

### What you need to do in a terminal

```bash
ssh -o StrictHostKeyChecking=no -i <SSH_KEY> <SSH_USER>@<SSH_HOST>
sudo systemctl edit <SYSTEMD_SERVICE_NAME>
```

Add below the existing `DD_SERVICE` / `DD_ENV` / `DD_VERSION` lines:

```ini
Environment="DD_DATA_STREAMS_ENABLED=true"
```

For supervisord or pm2, add `DD_DATA_STREAMS_ENABLED="true"` to the same `environment` / `env` block that holds the UST vars, then reload it again.

> **Confirm with the user before restarting.** Tell the user: "I need to restart `<SYSTEMD_SERVICE_NAME>` for DSM to take effect. This will cause a brief outage. Ready to proceed?" Wait for confirmation.

### Claude runs

```bash
ssh -o StrictHostKeyChecking=no -i <SSH_KEY> <SSH_USER>@<SSH_HOST> \
  "sudo systemctl daemon-reload && sudo systemctl restart <SYSTEMD_SERVICE_NAME> && sleep 3 && \
   sudo cat /proc/\$(systemctl show -p MainPID <SYSTEMD_SERVICE_NAME> | cut -d= -f2)/environ | tr '\0' '\n' | grep DD_DATA_STREAMS"
```

If `DD_DATA_STREAMS_ENABLED=true` is printed, continue to Step 3.

---

## Step 2c: AWS Lambda

SSI does not apply to Lambda. The function must already use the Datadog Lambda library or extension. Set `DD_DATA_STREAMS_ENABLED=true` in the function's environment in its IaC (`serverless.yml`, SAM, CDK, or Terraform), not on the live function. Check the runtime's minimum Lambda library version on its page in the [DSM setup docs](https://docs.datadoghq.com/data_streams/setup/) first. Ask the user before deploying.

---

## Step 3: Verify DSM data arrives

DSM data appears only after a DSM-enabled service produces or consumes a message. Allow a few minutes after the first message.

### Claude runs

```bash
DD_SITE=<DD_SITE> pup metrics query \
  --query "avg:data_streams.latency{service:<SERVICE_NAME>,env:<ENV>} by {pathway_type}" \
  --from 15m --to now
```

If series are returned, DSM is working for that service. If no message has flowed yet, tell the user DSM will appear after the first one.

ERROR: No series after traffic has flowed for 5+ minutes:
- Confirm the service actually sent or received messages in that window (check its traces for a produce/consume span)
- Confirm the client library and tracer version are in the language table above
- If `DD_DATA_STREAMS_ENABLED` did not reach a .NET 3.22+ process, it is still in default mode, which skips Kafka/Kinesis messages under 34 bytes and RabbitMQ messages over 128 KB
- Java on `kafka-clients` 3.7.x shows latency but no lag. Upgrade the client
- SQS: DSM needs one free message attribute (SQS allows 10 per message)
- SNS to SQS: enable SNS raw message delivery (Java SQS v1: set `DD_TRACE_SQS_BODY_PROPAGATION_ENABLED=true`)

---

## Done

Exit when ALL of the following are true:
- [ ] The user explicitly agreed to enable DSM, after hearing the plan rule
- [ ] `DD_DATA_STREAMS_ENABLED=true` is set on the scope the user agreed to, and nowhere else
- [ ] Kubernetes: a workload outside `DSM_SERVICES` still has its SSI init container
- [ ] Data verified in Step 3 (or in `onboarding-summary` when called from `enable-ssi`), or the user was told DSM will appear after the first message flows
- [ ] Any service that could not be enabled (PHP, Go without Orchestrion, unsupported client, Redis-backed queue) was named to the user, with the reason

Then give the user the link: `https://app.<DD_SITE>/data-streams`

---

## Security constraints

- Never write a raw API key into any file or chat message
- Never enable DSM without the user's explicit yes
- Always confirm with the user before restarting services or deploying functions
- Do not modify application source code. DSM for supported libraries is configuration only
