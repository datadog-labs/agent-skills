# Provider: LiteLLM

Triggered by introspection (Workflow step 2.5) when the call-site function imports `litellm` and calls:

- `litellm.completion(...)`
- `litellm.acompletion(...)`

## `{{PROVIDER_ASSERTS}}` substitution

LiteLLM auto-routes to whatever provider the underlying model identifier resolves to (`gpt-5.4-mini` → OpenAI, `claude-sonnet-4-5` → Anthropic, `gemini-pro` → Vertex, etc.). The skill cannot statically determine which provider's key is needed — the routing decision happens at runtime based on the model arg.

**Emit a comment instead of an assert:**

```python
# LiteLLM auto-routes to the underlying provider at runtime. Make sure the keys
# for your chosen model's provider are set in .env or shell:
#   - OpenAI models  → OPENAI_API_KEY
#   - Anthropic models → ANTHROPIC_API_KEY
#   - Gemini models → GEMINI_API_KEY or GOOGLE_API_KEY
#   - Bedrock models → AWS_ACCESS_KEY_ID + AWS_SECRET_ACCESS_KEY (+ AWS_REGION)
#   - Azure OpenAI  → AZURE_OPENAI_API_KEY + AZURE_OPENAI_ENDPOINT
```

## Adapter notes

- `litellm.completion(model=..., messages=[...])` returns a response with the same `.choices[0].message.content` shape as OpenAI, regardless of underlying provider. LiteLLM normalizes for you.
- Async variant: `litellm.acompletion(...)` — wrap with `asyncio.run(...)` inside a sync `task_fn`.
- LiteLLM has its own retry / fallback config (`litellm.set_verbose`, `litellm.api_base`, etc.). If the user's function configures these, leave their setup intact in `task_fn`.

## Common gotchas

- Detecting *which* underlying provider needs which key at runtime is on the user — they know which model they're calling. The TODO comment is the most we can do statically.
- If the user is using LiteLLM's proxy mode (`litellm.api_base` pointing at their own proxy), they may not need any provider keys at all in this process — surface that possibility in the comment block.
