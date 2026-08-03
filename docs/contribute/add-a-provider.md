# Adding an LLM Provider

Most new providers need zero code — this page covers when you actually need to write some, and what to write.

## The two existing wire formats

Jenny has no built-in provider catalog. A provider is a user-defined entry under `providers.providers` in `config.json` (`ProviderConfig` in `jenny/config/schema.py`):

```json
{
  "providers": {
    "providers": [
      {
        "name": "myprovider",
        "format": "openai_compat",
        "apiKey": "${MYPROVIDER_API_KEY}",
        "apiBase": "https://api.myprovider.com/v1"
      }
    ],
    "default": "myprovider"
  }
}
```

`ProviderConfig` fields:

| Field (camelCase in JSON) | Type | Notes |
|---|---|---|
| `name` | string | Your label for the entry; referenced by `providers.default`. |
| `format` | `"openai_compat"` \| `"anthropic"` | Selects which runtime provider class handles this entry — see below. No other value is accepted. |
| `apiKey` | string or null | Credential. Supports `${ENV_VAR}` interpolation, resolved at config load time. If the variable is missing, loading raises `ValueError` rather than silently leaving the key blank. |
| `apiBase` | string or null | Base URL of the endpoint. |
| `apiType` | `"auto"` \| `"chat_completions"` \| `"responses"` | OpenAI-compatible endpoints only; `"auto"` probes for Responses API support with a circuit breaker that falls back to Chat Completions after repeated failures. |
| `extraHeaders` / `extraBody` / `extraQuery` | dict or null | Extra request headers, JSON body fields, and query params merged into every request to this provider. |

If your target speaks either the Anthropic Messages API or an OpenAI-compatible Chat Completions/Responses API — which covers the overwhelming majority of hosted and self-hosted LLM servers, including most "OpenAI-compatible" gateways, OpenRouter, and local runners like Ollama/LM Studio — **you don't write any Python.** You add a `providers.providers[]` entry with `format: "openai_compat"` (or `"anthropic"`) and you're done. This page is for the genuinely new-wire-format case.

## When you actually need a new class

`jenny/providers/factory.py` is the only place that dispatches on `format`:

```python
if backend == "anthropic":
    provider = AnthropicProvider(api_key=p.api_key, api_base=p.api_base, ...)
else:  # "openai_compat"
    provider = OpenAICompatProvider(api_key=p.api_key, api_base=p.api_base, ...)
```

There are exactly two branches. If the endpoint you want to support doesn't speak either wire format — a genuinely different request/response shape, not just a slightly different Chat Completions dialect — you need a third `LLMProvider` subclass and a third branch here.

## Subclassing `LLMProvider`

`jenny/providers/base.py::LLMProvider` is the abstract base every provider (including the two built-in ones) implements. It has exactly two abstract methods:

```python
@abstractmethod
async def chat(
    self,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None = None,
    model: str | None = None,
    max_tokens: int = 4096,
    temperature: float = 0.7,
    reasoning_effort: str | None = None,
    tool_choice: str | dict[str, Any] | None = None,
) -> LLMResponse:
    ...

@abstractmethod
def get_default_model(self) -> str:
    ...
```

- **`chat()`** is the only method you must implement to have a working provider: translate `messages`/`tools` into your wire format, call the endpoint, translate the response back into an `LLMResponse` (content and/or tool calls).
- **`get_default_model()`** returns the model identifier to use when none is set explicitly.
- **`chat_stream()`** is *not* abstract — the base class provides a default that just calls `chat()` once and delivers the whole response through `on_content_delta` as a single chunk. Override it only if your endpoint has real token-by-token streaming and you want the UI to show it incrementally; otherwise the fallback is a correct (if non-streaming-looking) implementation for free.

`LLMProvider` also gives you, for free, without touching them: retry with backoff (`chat_with_retry`/`chat_stream_with_retry`), a streaming idle timeout (`JENNY_STREAM_IDLE_TIMEOUT_S`, default 90 s, capped at 3600 s) and a separate, longer budget for the wait before the model's first output (`resolve_first_output_timeout_s`, 300 s, or 600 s on a loopback endpoint) — a new provider that streams should apply the second one until it sees its first delta and the first one after that, message-list repair helpers (role alternation, empty-content sanitization, image stripping) in `jenny/providers/message_repair.py`, and transient/persistent error classification in `jenny/providers/retry_policy.py`. Look at `AnthropicProvider` (`jenny/providers/anthropic_provider.py`) for a from-scratch implementation, or `OpenAICompatProvider` (`jenny/providers/openai_compat_provider.py`) for one that also handles the OpenAI Responses API variant (`jenny/providers/openai_responses/`) behind the same class via `api_type`.

## Wiring it into the factory

Once your class exists:

1. Add a new literal value to `ProviderConfig.format` in `jenny/config/schema.py` (currently `Literal["openai_compat", "anthropic"]`).
2. Add a branch in `_make_provider_core()` (`jenny/providers/factory.py`) that constructs your class from the active `ProviderConfig` entry.
3. Apply the shared `GenerationSettings` (`temperature`, `max_tokens`, `reasoning_effort` from `config.agents.defaults`) the same way the existing branches do — `factory.py` sets `provider.generation` after construction, not inside the constructor.

## Testing

Provider tests live under `tests/providers/`, generally mocking the HTTP layer (`httpx.MockTransport` or a fake client) rather than hitting a real endpoint — follow the pattern in the existing Anthropic/OpenAI-compat test files for message conversion, tool-call round-tripping, and retry/error-classification behavior. As with any change, run the checks in [Code style](code-style.md) before opening a PR.

## See also

- [Providers and models](../reference/providers.md) — the user-facing reference (no-code-required setup, per-format config examples, troubleshooting).
- [Local models](../reference/local-models.md) — running against a self-hosted OpenAI-compatible endpoint, which needs no new provider class at all.
