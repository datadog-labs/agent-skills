# AWS Lambda

Open this in step 5 for a function on AWS Lambda, in Python, Node.js, Go, Java, .NET or Ruby (PHP is not supported). The Datadog Lambda Extension and the runtime's Datadog library run AAP, so the language reference's install and start steps do not apply here.

## Contents

- Pick one method
- Runtime notes
- Serverless Framework
- AWS CDK
- Datadog CLI
- Manual layers
- Resolve layer versions

## Pick one method

Use exactly one, because two methods attach duplicate layers and conflicting settings:

- `serverless.yml` or `serverless.ts`: Serverless Framework.
- `cdk.json`: AWS CDK.
- The project already runs `datadog-ci lambda instrument`, for example in CI: Datadog CLI.
- Otherwise: manual layers.

The API key goes in AWS Secrets Manager as a plain-text string (not JSON), and the execution role needs `secretsmanager:GetSecretValue`. Never write the key value into the project, because it would leak through git history. When you do not know how the user stores it, ask.

## Runtime notes

| Runtime | Library | Tracer layer | AAP variable (manual) |
|---|---|---|---|
| Python | layer `Datadog-<Runtime>` (or `-ARM`), such as `Datadog-Python311`; repo `datadog-lambda-python` | yes | `DD_APPSEC_ENABLED` |
| Node.js | layer `Datadog-<Runtime>`, such as `Datadog-Node20-x`; repo `datadog-lambda-js` | yes | `DD_SERVERLESS_APPSEC_ENABLED` |
| Java | layer `dd-trace-java`; repo `datadog-lambda-java` | yes | `DD_SERVERLESS_APPSEC_ENABLED` |
| .NET | layer `dd-trace-dotnet` (or `-ARM`); repo `dd-trace-dotnet-aws-lambda-layer` | yes | `DD_SERVERLESS_APPSEC_ENABLED` |
| Ruby | layer `Datadog-<Runtime>` (or `-ARM`), such as `Datadog-Ruby3-2`; repo `datadog-lambda-rb` | yes | `DD_SERVERLESS_APPSEC_ENABLED` |
| Go | `datadog-lambda-go`, compiled in | no | `DD_SERVERLESS_APPSEC_ENABLED` |

- **Go:** no Orchestrion and no `appsec` tag. Keep `github.com/DataDog/dd-trace-go/contrib/aws/datadog-lambda-go/v2` when `go.mod` already has it. Replace the legacy `github.com/DataDog/datadog-lambda-go` import with it. For a new install, run `go get github.com/DataDog/dd-trace-go/contrib/aws/datadog-lambda-go/v2`, wrap the handler as `lambda.Start(ddlambda.WrapFunction(handler, nil))`, run `go mod tidy`, check the toolchain rule in [go.md](go.md), and build. Let the Go toolchain edit `go.mod`, because it also has to update `go.sum`.
- **Ruby:** no method redirects the Ruby handler, so in the handler's file add `require 'datadog/lambda'`, call `Datadog::Lambda.configure_apm` once at load, and wrap the existing body in `Datadog::Lambda.wrap(event, context) do … end`, keeping the file, method, routes and responses unchanged. Keep an existing `configure_apm` block. Do not add the `datadog-lambda` gem, because the layer provides it.
- **Java:** use a supported runtime (`java8.al2`, `java11`, `java17`, `java21` or `java25`) with at least 1024 MB of memory, and set `memorySize` explicitly, per function when a function overrides it.

## Serverless Framework

1. Run `serverless plugin install --name serverless-plugin-datadog`. Without the `serverless` CLI, add the plugin as a dev dependency with the project's package manager (let the package manager pick the version) and list it under `plugins:`.
2. Turn AAP on in `serverless.yml`:

   ```yaml
   custom:
     datadog:
       apiKeySecretArn: "<secret ARN>"
       appSecMode: "on"
       site: "<site from step 2>"
       service: "<service>"             # only when APM was off and nothing sets it yet
   ```

   Use `apiKeySecretArn` rather than `apiKey`, because `apiKey` puts the key itself in a committed file. Set `site` for every runtime, because the plugin otherwise defaults to `datadoghq.com` and the data of any other site is lost. Keep an existing `site` value, literal or a variable reference such as `${env:DD_SITE}`. The plugin needs version 5.96.0 or later for `ap2.datadoghq.com`, 5.127.0 for `us2.ddog-gov.com` and 5.132.0 for `uk1.datadoghq.com`; tell the user to upgrade an older one, because the deploy otherwise fails with `Invalid site URL`.
3. Merge the marker into `provider.environment` (or the function's `environment`), keeping the variables already there. Do not put it under `custom.datadog`, which holds plugin parameters, not environment variables:

   ```yaml
   provider:
     environment:
       DD_APPSEC_AGENTIC_ONBOARDING: "true"
   ```

## AWS CDK

1. Install `datadog-cdk-constructs-v2` (`npm install --save-dev datadog-cdk-constructs-v2`, or `pip install datadog-cdk-constructs-v2` for a Python stack).
2. Add AAP to an existing `DatadogLambda` construct, or create one. The library exports `DatadogLambda`, not `Datadog`, so update an older `Datadog` construct to it:

   ```typescript
   import { DatadogLambda, DatadogAppSecMode } from "datadog-cdk-constructs-v2";
   const datadog = new DatadogLambda(this, "DatadogLambda", {
     nodeLayerVersion: <layer version>,      // pythonLayerVersion, javaLayerVersion, dotnetLayerVersion or rubyLayerVersion; none for Go
     extensionLayerVersion: <extension version>,
     site: "<site from step 2>",
     apiKeySecretArn: "<secret ARN>",
     datadogAppSecMode: DatadogAppSecMode.ON,
   });
   datadog.addLambdaFunctions([<functions>]);
   ```

   In Python stacks, import `DatadogLambda` and `DatadogAppSecMode` from `datadog_cdk_constructs_v2`, and the names are `python_layer_version`, `extension_layer_version`, `site`, `api_key_secret_arn`, `datadog_app_sec_mode=DatadogAppSecMode.ON` and `add_lambda_functions`. Keep an existing `site` value.
3. Add `DD_APPSEC_AGENTIC_ONBOARDING=true` to the function's environment, then run `cdk synth`.

## Datadog CLI

1. Install `npm install -g @datadog/datadog-ci @datadog/datadog-ci-plugin-lambda`, with AWS credentials as for the AWS CLI.
2. Export `DATADOG_SITE` (the site from step 2) and `DATADOG_API_KEY_SECRET_ARN`.
3. Run `datadog-ci lambda instrument --appsec -f <function> -r <region> -v <layer version> -e <extension version>`. Go takes no `-v`. Try a staging function first; `datadog-ci lambda uninstrument` with the same arguments reverts it.
4. Add `DD_APPSEC_AGENTIC_ONBOARDING=true` to the function's environment.

## Manual layers

1. Attach the tracer layer (none for Go) and the extension layer. The account is `464622532012`; in AWS GovCloud, use partition `aws-us-gov` and account `002406178527`:

   ```text
   arn:aws:lambda:<region>:464622532012:layer:<tracer layer>:<layer version>
   arn:aws:lambda:<region>:464622532012:layer:Datadog-Extension:<extension version>   # Datadog-Extension-ARM on arm64
   ```

2. Python: set the handler to `datadog_lambda.handler.handler` and `DD_LAMBDA_HANDLER` to the original handler. Node.js: set the handler to `/opt/nodejs/node_modules/datadog-lambda-js/handler.handler` and `DD_LAMBDA_HANDLER` to the original. Go and Ruby keep the wrapping from Runtime notes.
3. Set the function environment: the runtime's AAP variable from the table set to `true`, `DD_APPSEC_AGENTIC_ONBOARDING=true`, `DD_SERVICE`, `DD_SITE` (the site from step 2), and `DD_API_KEY_SECRET_ARN`. Every runtime except Python also needs `AWS_LAMBDA_EXEC_WRAPPER=/opt/datadog_wrapper`.
4. For a container-image Python function, install the `datadog-lambda` package instead of the layer, built for the function's architecture.

## Resolve layer versions

Read the latest release tags rather than a version from memory, because layer versions change with each release:

```sh
curl -fsSI https://github.com/DataDog/datadog-lambda-extension/releases/latest | grep -io '/tag/[^[:space:]]*' | cut -d/ -f3
curl -fsSI https://github.com/DataDog/<library repo>/releases/latest | grep -io '/tag/[^[:space:]]*' | cut -d/ -f3
```

The extension version is its tag without the `v` (`v99` gives `99`). For Python, Node.js and Ruby, the layer version is the minor part of the library tag (`v8.128.0` gives `128`). For Java and .NET, strip the `v`. The CLI and the CDK construct take bare integers and do not validate them.
