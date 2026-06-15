# Provider: LangChain

Triggered by introspection (Workflow step 2.5) when the call-site function imports from `langchain` / `langchain_openai` / `langchain_anthropic` / etc. and uses one of:

- `langchain.*.invoke(...)`
- `ChatOpenAI(...)`, `ChatAnthropic(...)`, `ChatVertexAI(...)`, `ChatBedrock(...)`, etc.
- `LLMChain(...)`

## `{{PROVIDER_ASSERTS}}` substitution

LangChain is a meta-framework — it wraps a specific provider. **Walk one level deeper**: the chat-client class names the provider. Read the user's function (and immediate imports) to identify which `Chat*` class is instantiated, then emit the assert for THAT provider per the table below:

| LangChain class | Underlying provider | Reference file |
|---|---|---|
| `ChatOpenAI`, `OpenAI`, `AzureChatOpenAI` (with `azure_endpoint=`) | OpenAI / Azure OpenAI | `providers/openai.md` or `providers/openai.md` (Azure: also `AZURE_OPENAI_ENDPOINT`) |
| `ChatAnthropic`, `AnthropicLLM` | Anthropic | `providers/anthropic.md` |
| `ChatVertexAI`, `ChatGoogleGenerativeAI` | Vertex / Gemini | `providers/gemini.md` |
| `ChatBedrock`, `BedrockLLM` | AWS Bedrock | `providers/bedrock.md` |
| `ChatLiteLLM` | LiteLLM (auto-routes) | `providers/litellm.md` |

**Emit the assert for the underlying provider**, not LangChain itself. Example: if the user's function has `from langchain_anthropic import ChatAnthropic`, emit:

```python
assert os.getenv("ANTHROPIC_API_KEY"), "ANTHROPIC_API_KEY is required for the wired task_fn (LangChain ChatAnthropic)."
```

If the call-site uses multiple chat clients (rare), emit asserts for each.

## Adapter notes

- `chain.invoke({...})` returns either a string (for simple chains) or an `AIMessage` (for chat-based chains). Extract `.content` if it's the latter.
- LangChain LCEL chains (`prompt | llm | parser`) return the parser's output type directly — usually a string. Trust the user's function signature.
- Async: `chain.ainvoke(...)` — wrap with `asyncio.run(...)`.

## Common gotchas

- LangChain configures provider via env vars by default but ALSO supports per-instance kwargs (`ChatOpenAI(api_key="sk-...")`). If the user's function passes a key explicitly, the env var is irrelevant — emit a `# Note:` comment instead of an assert.
- LangChain `prompts/` directory conventions vary widely; the wrapped function should encapsulate prompt loading, so `task_fn` just calls `function(input_data)` and trusts the chain.
