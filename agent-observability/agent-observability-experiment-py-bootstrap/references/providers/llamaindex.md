# Provider: LlamaIndex

Triggered by introspection (Workflow step 2.5) when the call-site function imports `llama_index.*` and uses one of:

- `index.as_query_engine(...).query(...)`
- `index.as_chat_engine(...).chat(...)`
- `VectorStoreIndex.from_documents(...)`
- LlamaIndex agent classes (`AgentRunner`, `ReActAgent`, etc.)

## `{{PROVIDER_ASSERTS}}` substitution

Like LangChain, LlamaIndex is a meta-framework. **Walk one level deeper**: find the underlying `LLM` / `embedder` class the index / chat engine is configured with. The provider table:

| LlamaIndex class | Underlying provider | Reference file |
|---|---|---|
| `OpenAI`, `OpenAILike` (from `llama_index.llms.openai`) | OpenAI | `providers/openai.md` |
| `Anthropic` (from `llama_index.llms.anthropic`) | Anthropic | `providers/anthropic.md` |
| `Gemini` (from `llama_index.llms.gemini`) | Gemini | `providers/gemini.md` |
| `Bedrock` (from `llama_index.llms.bedrock`) | AWS Bedrock | `providers/bedrock.md` |
| `LiteLLM` (from `llama_index.llms.litellm`) | LiteLLM | `providers/litellm.md` |

**Emit the assert for the underlying provider**, not LlamaIndex itself. Embedders (`OpenAIEmbedding`, `HuggingFaceEmbedding`, etc.) may need separate keys if they're hosted; surface in a comment.

Example: if the user's function uses `Settings.llm = OpenAI(model="gpt-5.4o-mini")`, emit:

```python
assert os.getenv("OPENAI_API_KEY"), "OPENAI_API_KEY is required for the wired task_fn (LlamaIndex OpenAI LLM)."
```

## Adapter notes

- `query_engine.query("...")` returns a `Response` object — extract `.response` for the text.
- `chat_engine.chat("...")` returns a string directly.
- LlamaIndex `Settings` is global state — once configured, all index operations use the same LLM. Trust the user's function not to re-configure mid-call.
- Async: `aquery(...)` / `achat(...)` — wrap with `asyncio.run(...)`.

## Common gotchas

- If the user's function constructs the index inside `task_fn` (rebuilding it per record), the experiment will be very slow. Surface as a `WARNING:` in the next-steps output: "task_fn appears to rebuild the LlamaIndex on every call — consider caching the index at module scope for faster experiment runs."
- Embedding API calls also count against the LLM provider's quota — `OPENAI_API_KEY` may be used by both the embedder and the LLM. One assert covers both.
