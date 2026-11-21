# Deploy steps and the check after the deploy

## Contents

- Deploy steps
- After the deploy
- If data does not arrive

## Deploy steps

The user runs these steps; you never run them. Number the steps in the report in this order, and leave out the ones that do not apply. Fill in names that the repository shows, such as the cluster, service, family, stack, role and template file, and keep `<…>` placeholders only for values it does not show. Fill `<APP_HOST>` from step 3 of SKILL.md.

1. **Get your Datadog API key.** Copy it from `<APP_HOST>/organization-settings/api-keys`, or use the `DD_API_KEY` that `dd-account-setup` saved in the project's git-ignored `.env`. Load it into the shell without printing it:

   ```bash
   export DD_API_KEY="$(grep -E '^DD_API_KEY=' .env | cut -d= -f2-)"
   ```

2. **Store the key in AWS Secrets Manager.** Skip this step when you reused an existing secret. The key goes through standard input, so it never appears on a command line:

   (Replace $DD_API_KEY with your API key)

   ```bash
   printf '%s' "$DD_API_KEY" | aws secretsmanager create-secret --name datadog/api-key --secret-string file:///dev/stdin
   aws secretsmanager describe-secret --secret-id datadog/api-key --query ARN --output text
   ```

   For `cli` and `cloudformation`, tell the user to put the printed ARN wherever the files show `<DD_API_KEY_SECRET_ARN>`, or to pass it as the `DatadogApiKeySecretArn` parameter.

3. **Grant the task execution role access to the secret.** Only for `cli`, or for `cloudformation` when the template imports the role:

   ```bash
   aws iam put-role-policy --role-name <EXECUTION_ROLE_NAME> --policy-name datadog-api-key-secret --policy-document file://iam/datadog-secret-policy.json
   ```

4. **Update the lockfile, then rebuild and push the app image,** when you changed a Dockerfile or a dependency file. Use the project's own commands from its CI, `Makefile` or README, for example `npm install` and then `docker build` and `docker push`. The new task definition must point at the new image.

5. **Deploy**, with the command for the method. When the project registers or deploys the task with its own script or `Makefile` target (for example a `create.sh` that patches the JSON and calls `aws ecs`), give that command here instead of the raw `aws ecs` commands, with any variables it needs:

   - `terraform`:

     ```bash
     terraform init
     terraform plan
     terraform apply
     ```

   - `cdk`, after installing the new dependency with the project's package manager:

     ```bash
     npx cdk diff
     npx cdk deploy
     ```

   - `cloudformation`. Keep any parameters the project already passes, and use `CAPABILITY_NAMED_IAM` when the template names its IAM resources:

     ```bash
     aws cloudformation deploy --stack-name <STACK_NAME> --template-file <TEMPLATE_FILE> --parameter-overrides DatadogApiKeySecretArn=<DD_API_KEY_SECRET_ARN> --capabilities CAPABILITY_IAM
     ```

   - `cli` on Fargate:

     ```bash
     aws ecs register-task-definition --cli-input-json file://task-definition.json
     aws ecs update-service --cluster <CLUSTER_NAME> --service <SERVICE_NAME> --task-definition <TASK_FAMILY> --force-new-deployment
     ```

   - `cli` on EC2. Register and start the Agent daemon first, then the app:

     ```bash
     aws ecs register-task-definition --cli-input-json file://datadog-agent-task-definition.json
     aws ecs create-service --cluster <CLUSTER_NAME> --service-name datadog-agent --task-definition datadog-agent --scheduling-strategy DAEMON --launch-type EC2
     aws ecs register-task-definition --cli-input-json file://task-definition.json
     aws ecs update-service --cluster <CLUSTER_NAME> --service <SERVICE_NAME> --task-definition <TASK_FAMILY> --force-new-deployment
     ```

## After the deploy

This check only reads from Datadog. Run it when the user says the service is deployed and has served some traffic. Data usually appears 2 to 5 minutes after the first request, so query once and offer to check again rather than looping.

`<FAMILY>` is the app's task definition family, and `<SERVICE>` is its `DD_SERVICE`, or the service name when the repository showed no tag. Scope every query to them: other hosts and services in the organization must not count as this service's data.

| Signal | Query, last 15 minutes |
|---|---|
| Infrastructure on Fargate | `avg:ecs.fargate.cpu.percent{task_family:<FAMILY>}` |
| Infrastructure on EC2 | `avg:container.cpu.usage{task_family:<FAMILY>}` |
| Logs | `service:<SERVICE>` |
| Traces | `service:<SERVICE>` |

Use the Datadog MCP server's metric, log and span search tools when they are connected. Otherwise use the API with the keys that `dd-account-setup` saved. The keys travel on standard input, so they never appear on a command line, and the commands never print them:

```bash
for f in .env.local .env; do [ -f "$f" ] || continue; for k in DD_SITE DD_API_KEY DD_APP_KEY; do eval "[ -n \"\${$k:-}\" ]" && continue; v=$(grep -E "^$k=" "$f" | head -1 | cut -d= -f2- | tr -d '\r' | sed 's/^["'\'']//;s/["'\'']$//'); [ -n "$v" ] && export "$k=$v"; done; done
DD_SITE='<DD_SITE>'; export DD_SITE
: "${DD_API_KEY:?not set - run dd-account-setup}"; : "${DD_APP_KEY:?not set - run dd-account-setup}"
hdr() { printf 'DD-API-KEY: %s\nDD-APPLICATION-KEY: %s\nContent-Type: application/json\n' "$DD_API_KEY" "$DD_APP_KEY"; }

# Metrics: https://docs.datadoghq.com/api/latest/metrics/#query-timeseries-points
hdr | curl -sS -G -H @- "https://api.${DD_SITE}/api/v1/query" \
  --data-urlencode "from=$(($(date +%s) - 900))" --data-urlencode "to=$(date +%s)" \
  --data-urlencode "query=avg:ecs.fargate.cpu.percent{task_family:<FAMILY>}"

# Logs: https://docs.datadoghq.com/api/latest/logs/#search-logs
hdr | curl -sS -H @- -X POST "https://api.${DD_SITE}/api/v2/logs/events/search" \
  -d '{"filter":{"query":"service:<SERVICE>","from":"now-15m","to":"now"},"page":{"limit":5}}'

# Spans: https://docs.datadoghq.com/api/latest/spans/#search-spans
hdr | curl -sS -H @- -X POST "https://api.${DD_SITE}/api/v2/spans/events/search" \
  -d '{"data":{"type":"search_request","attributes":{"filter":{"query":"service:<SERVICE>","from":"now-15m","to":"now"},"page":{"limit":5}}}}'
```

On EC2, use the EC2 metric from the table. A metric response with an empty `series`, or a search with an empty `data`, means nothing has arrived yet; any HTTP status other than 200 is an error to report, not a delay. Report each signal as arrived or not yet, with the number of points or events, and give the links:

- Containers: `<APP_HOST>/containers`
- Logs: `<APP_HOST>/logs?query=service%3A<SERVICE>`
- Traces: `<APP_HOST>/apm/traces?query=service%3A<SERVICE>`

## If data does not arrive

Check these in order, 10 minutes after traffic, and tell the user what you found. Each check is a read-only command for the user or a file to re-read.

- **The task does not start, with an error about pulling secrets.** The task execution role cannot read the secret, or the ARN is wrong. Check the role's policy and the ARN in the task definition.
- **No metrics.** On Fargate, the `datadog-agent` container is running in the task. On EC2, the `datadog-agent` daemon service runs one task per instance: `aws ecs describe-services --cluster <CLUSTER_NAME> --services datadog-agent`. Errors in the Agent's logs about the API key mean the key or `DD_SITE` is wrong.
- **No logs on Fargate.** Each app container's `awsfirelens` output has `Host` set to the site's intake, `TLS` set to `on`, and the `apikey` secret option. A container that kept its own `logConfiguration` does not send through FireLens.
- **No logs on EC2.** The Agent has `DD_LOGS_ENABLED=true` and the `containers_root` and `pointdir` mounts. The Agent reads only the local Docker log, so containers that use the `awslogs` driver send their logs to CloudWatch, where only the AWS log forwarder picks them up.
- **No traces.** The app's start command loads the tracer, the image with the tracer was built and deployed, and the app container mounts `/var/run/datadog` and has `DD_TRACE_AGENT_URL`.
