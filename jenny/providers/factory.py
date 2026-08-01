"""Create LLM providers from config."""

from __future__ import annotations

from jenny.config.schema import Config
from jenny.providers.base import GenerationSettings, LLMProvider


def _make_provider_core(config: Config) -> LLMProvider:
    """Create a plain LLM provider from the active provider in config."""
    try:
        p = config.get_active_provider()
    except ValueError:
        raise RuntimeError(
            "No provider configured. Add a provider in Settings or edit "
            "workspace/config.json to set providers.providers[0]."
        ) from None

    backend = p.format
    defaults = config.agents.defaults
    model = defaults.model

    if not p.api_key:
        raise RuntimeError(f"Provider '{p.name}': api_key is required.")

    if backend == "anthropic":
        from jenny.providers.anthropic_provider import AnthropicProvider

        provider = AnthropicProvider(
            api_key=p.api_key,
            api_base=p.api_base,
            default_model=model,
            extra_headers=p.extra_headers,
        )
    else:  # "openai_compat"
        from jenny.providers.openai_compat_provider import OpenAICompatProvider

        provider = OpenAICompatProvider(
            api_key=p.api_key,
            api_base=p.api_base,
            default_model=model,
            extra_headers=p.extra_headers,
            extra_body=p.extra_body,
            api_type=p.api_type,
            extra_query=p.extra_query,
        )

    provider.generation = GenerationSettings(
        temperature=defaults.temperature,
        max_tokens=defaults.max_tokens,
        reasoning_effort=defaults.reasoning_effort,
    )
    # Nome config del provider attivo: la WebUI lo usa per il branding
    # (evento runtime_model_updated e popover Info sessione).
    provider.provider_name = p.name
    return provider


def make_provider(config: Config) -> LLMProvider:
    """Create the LLM provider from config."""
    return _make_provider_core(config)
