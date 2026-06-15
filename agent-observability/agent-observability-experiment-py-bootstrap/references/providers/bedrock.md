# Provider: AWS Bedrock

Triggered by introspection (Workflow step 2.5) when the call-site function imports `boto3` and calls:

- `boto3.client("bedrock-runtime").invoke_model(...)`
- `boto3.client("bedrock-runtime").converse(...)` (newer API)
- `boto3.Session(...).client("bedrock-runtime")...`

## `{{PROVIDER_ASSERTS}}` substitution

```python
assert os.getenv("AWS_ACCESS_KEY_ID"), "AWS_ACCESS_KEY_ID is required for the wired task_fn (Bedrock)."
assert os.getenv("AWS_SECRET_ACCESS_KEY"), "AWS_SECRET_ACCESS_KEY is required for the wired task_fn (Bedrock)."
```

## Optional env vars (do NOT assert; document in `# TODO` comments)

- `AWS_SESSION_TOKEN` — required when using short-lived credentials (SSO, IAM Identity Center, etc.). If set, must be present at runtime.
- `AWS_REGION` (or `AWS_DEFAULT_REGION`) — Bedrock is region-scoped. Defaults to `us-east-1` if unset; emit a comment:

  ```python
  # AWS Bedrock is region-scoped. Defaults to us-east-1; set AWS_REGION if your
  # Bedrock-enabled region differs (us-west-2, eu-central-1, etc.).
  ```

- `AWS_PROFILE` — alternative to access-key/secret pair when using `~/.aws/credentials`. If the user's function uses `boto3.Session(profile_name=...)`, key-pair asserts may not apply — emit a comment instead.

## Adapter notes

- `client.invoke_model(modelId=..., body=...)` (older API) — body is a JSON string with provider-specific shape (Anthropic Claude, Amazon Titan, AI21, Cohere, Meta Llama — each has different body schema). Extract response via `json.loads(response["body"].read())`.
- `client.converse(modelId=..., messages=[...])` (newer Converse API) — standardized request/response across providers. Extract via `response["output"]["message"]["content"][0]["text"]`.
- If the user's function uses `invoke_model`, leave their body construction intact in `task_fn` — Anthropic-on-Bedrock vs Llama-on-Bedrock have different request shapes.

## Common gotchas

- Model IDs differ from upstream provider IDs (e.g., `anthropic.claude-3-5-sonnet-20240620-v1:0` rather than `claude-3-5-sonnet-20240620`). Don't rewrite the model ID.
- Bedrock charges per call; rate limits apply. Consider `--jobs 1` for the first experiment run to gauge cost.
- Cross-region inference profiles use a different model ID prefix (`us.anthropic...`, `eu.anthropic...`). Trust the user's setup.
