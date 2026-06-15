# Provider: OpenAI

Triggered by introspection (Workflow step 2.5) when the call-site function imports `openai` and calls one of:

- `openai.ChatCompletion.create` / `openai.chat.completions.create`
- `client.chat.completions.create` (where `client = openai.OpenAI(...)`)
- `openai.completions.create` / `client.completions.create` (legacy text completions)

Also the fallback when introspection finds nothing and `--placeholder-task` is in effect — the placeholder makes an OpenAI chat call.

## `{{PROVIDER_ASSERTS}}` substitution

```python
assert os.getenv("OPENAI_API_KEY"), "OPENAI_API_KEY is required for the wired task_fn."
```

## Optional env vars (do NOT assert; document in `# TODO` comments)

- `OPENAI_ORG_ID` — only needed for orgs that scope keys per org.
- `OPENAI_BASE_URL` — only needed when pointing at a self-hosted / Azure-compatible endpoint. If the call-site uses `OpenAI(base_url=...)`, the wrapped function already handles this.

## Adapter notes

- Sync chat completion: `openai.OpenAI().chat.completions.create(model=..., messages=[...])` returns a `ChatCompletion` object. Extract the text via `.choices[0].message.content`.
- If the user's function returns the raw `ChatCompletion`, wrap with a `.choices[0].message.content` extractor in `task_fn` (the evaluator expects a string output, not the full SDK object).
- Async variants (`AsyncOpenAI`, `acreate`): wrap the async call with `asyncio.run(...)` inside a sync `task_fn`. See Workflow step 2.5d "Signature adaptation".

## Common gotchas

- Rate limits — pass `--jobs 1` or `--jobs 2` if the experiment hits 429s.
- `gpt-5.4-turbo` deprecations: the user's function may reference a model that has been retired. Don't silently rewrite the model name — surface as a `WARNING:` if `gpt-3.5-turbo` or similar is detected.
