# Provider: Google Gemini / Vertex AI

Triggered by introspection (Workflow step 2.5) when the call-site function imports `google.generativeai` and calls:

- `google.generativeai.GenerativeModel(...).generate_content(...)`
- `genai.GenerativeModel(...).generate_content(...)` (aliased import)

## `{{PROVIDER_ASSERTS}}` substitution

Either `GEMINI_API_KEY` or `GOOGLE_API_KEY` works — both are valid env names per `google-generativeai`'s SDK conventions. Emit a single combined assert:

```python
assert os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY"), (
    "GEMINI_API_KEY or GOOGLE_API_KEY is required for the wired task_fn."
)
```

## Vertex AI variant (separate authentication path)

If the call-site uses `vertexai.generative_models.GenerativeModel` (not `google.generativeai`), the auth path is Google Cloud Application Default Credentials, not an API key. Emit:

```python
# Vertex AI uses Google Cloud ADC, not an API key.
# Run `gcloud auth application-default login` before running this file, or set
# GOOGLE_APPLICATION_CREDENTIALS to point at a service account JSON.
assert os.getenv("GOOGLE_APPLICATION_CREDENTIALS"), (
    "GOOGLE_APPLICATION_CREDENTIALS path is required for the wired task_fn (Vertex AI), "
    "or run `gcloud auth application-default login` before invoking."
)
```

## Adapter notes

- `GenerativeModel("gemini-pro").generate_content("prompt")` returns a `GenerateContentResponse`. Extract via `.text` (single-candidate) or `.candidates[0].content.parts[0].text`.
- For chat: `model.start_chat(history=[]).send_message("prompt")` returns the same response shape.
- Async: `generate_content_async(...)` — wrap with `asyncio.run(...)`.

## Common gotchas

- Safety filters: Gemini may return an empty response if safety thresholds block it. `.text` raises `ValueError` in that case. If the user's function doesn't handle this, surface a `WARNING:`.
- Quota lives at the project level for Vertex AI, per-key for `google.generativeai`. Be aware of which path the user's function uses.
