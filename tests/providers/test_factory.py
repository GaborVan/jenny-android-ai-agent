"""Test diretti di ``providers/factory.py`` (provider da config).

Copre la selezione del backend per ``format``, il travaso dei default di
generazione e i contratti d'errore (nessun provider, api_key mancante).
"""

from __future__ import annotations

import pytest

from jenny.config.schema import Config, ProviderConfig
from jenny.providers.anthropic_provider import AnthropicProvider
from jenny.providers.factory import make_provider
from jenny.providers.openai_compat_provider import OpenAICompatProvider


def _config(*providers: ProviderConfig, default: str | None = None, **agent_defaults) -> Config:
    config = Config()
    config.providers.providers = list(providers)
    if default is not None:
        config.providers.default = default
    for key, value in agent_defaults.items():
        setattr(config.agents.defaults, key, value)
    return config


def _anthropic(name: str = "claude") -> ProviderConfig:
    return ProviderConfig(name=name, format="anthropic", api_key="k-anthropic")


def _openai(name: str = "compat") -> ProviderConfig:
    return ProviderConfig(
        name=name,
        format="openai_compat",
        api_key="k-openai",
        api_base="http://localhost:8080/v1",
    )


def test_anthropic_format_builds_anthropic_provider() -> None:
    provider = make_provider(_config(_anthropic(), model="claude-x"))
    assert isinstance(provider, AnthropicProvider)
    assert provider.get_default_model() == "claude-x"


def test_openai_compat_format_builds_compat_provider() -> None:
    provider = make_provider(_config(_openai(), model="local-model"))
    assert isinstance(provider, OpenAICompatProvider)
    assert provider.get_default_model() == "local-model"


def test_generation_defaults_are_applied() -> None:
    config = _config(_anthropic(), temperature=0.3, max_tokens=1234)
    provider = make_provider(config)
    assert provider.generation.temperature == 0.3
    assert provider.generation.max_tokens == 1234


def test_provider_name_is_stamped_from_config() -> None:
    # La WebUI usa provider_name per il branding (runtime_model_updated).
    assert make_provider(_config(_anthropic("claude"))).provider_name == "claude"
    assert make_provider(_config(_openai("compat"))).provider_name == "compat"


def test_default_name_selects_provider() -> None:
    config = _config(_openai("primo"), _anthropic("secondo"), default="secondo")
    assert isinstance(make_provider(config), AnthropicProvider)


def test_unknown_default_falls_back_to_first() -> None:
    config = _config(_openai("primo"), _anthropic("secondo"), default="inesistente")
    assert isinstance(make_provider(config), OpenAICompatProvider)


def test_no_provider_raises_actionable_error() -> None:
    with pytest.raises(RuntimeError, match="No provider configured"):
        make_provider(_config())


def test_missing_api_key_raises() -> None:
    config = _config(ProviderConfig(name="senza-chiave", format="anthropic"))
    with pytest.raises(RuntimeError, match="api_key is required"):
        make_provider(config)
