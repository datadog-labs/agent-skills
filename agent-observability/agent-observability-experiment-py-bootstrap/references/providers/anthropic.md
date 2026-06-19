# Provider: Anthropic

Triggered by introspection (Workflow step 2.5) when the call-site function imports `anthropic` and calls one of:

- `anthropic.messages.create`
- `client.messages.create` (where `client = anthropic.Anthropic(...)`)
- `Anthropic(...).messages.create`

## `{{PROVIDER_ASSERTS}}` substitution

```python
assert os.getenv("ANTHROPIC_API_KEY"), "ANTHROPIC_API_KEY is required for the wired task_fn."
```

## Optional env vars (do NOT assert; document in `# TODO` comments)

- `ANTHROPIC_BASE_URL` — only needed when pointing at a proxy or self-hosted relay.

## Adapter notes

- `anthropic.Anthropic().messages.create(model=..., max_tokens=..., messages=[...])` returns a `Message` object. Extract text via `.content[0].text` (note: `content` is a list of blocks, not a single string).
- If the user's function returns the raw `Message`, wrap with a `.content[0].text` extractor in `task_fn`.
- `max_tokens` is **required** — unlike OpenAI, Anthropic raises if it's missing. If the user's function omits it, leave their signature alone; the call will fail at runtime and the user can fix.
- Async (`AsyncAnthropic`): wrap with `asyncio.run(...)` inside a sync `task_fn`.

## Common gotchas

- Tool use response format differs from OpenAI — `tool_use` blocks are interleaved with `text` blocks in `.content`. Don't assume `.content[0]` is text; loop and concatenate `text` blocks if needed.
- Anthropic models require `messages` to alternate user/assistant (no two consecutive same-role messages). If the user's function builds messages, trust it; don't second-guess.
