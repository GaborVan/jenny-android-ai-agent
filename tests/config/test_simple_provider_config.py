"""Tests for the simplified provider config."""

import pytest

from jenny.config.schema import Config, ProviderConfig, ProvidersConfig


class TestProviderConfig:
    def test_valid_openai_compat(self):
        p = ProviderConfig.model_validate(
            {
                "name": "my-gpt",
                "format": "openai_compat",
                "apiKey": "sk-test",
            }
        )
        assert p.name == "my-gpt"
        assert p.format == "openai_compat"
        assert p.api_key == "sk-test"

    def test_valid_anthropic(self):
        p = ProviderConfig.model_validate(
            {
                "name": "claude",
                "format": "anthropic",
                "apiKey": "sk-ant-test",
                "apiBase": "https://api.anthropic.com",
            }
        )
        assert p.format == "anthropic"

    def test_invalid_format(self):
        with pytest.raises(Exception):
            ProviderConfig.model_validate({"name": "bad", "format": "invalid"})

    def test_serialization_camel_case(self):
        p = ProviderConfig(
            name="test",
            format="openai_compat",
            api_key="sk-test",
            api_base="https://example.com",
            extra_headers={"X-Custom": "value"},
            extra_body={"key": "value"},
            extra_query={"q": "v"},
        )
        dumped = p.model_dump(by_alias=True)
        assert dumped["name"] == "test"
        assert dumped["format"] == "openai_compat"
        assert dumped["apiKey"] == "sk-test"
        assert dumped["apiBase"] == "https://example.com"
        assert dumped["extraHeaders"] == {"X-Custom": "value"}
        assert dumped["extraBody"] == {"key": "value"}


class TestProvidersConfig:
    def test_empty_providers(self):
        cfg = ProvidersConfig.model_validate({})
        assert cfg.providers == []
        assert cfg.default is None

    def test_multiple_providers(self):
        cfg = ProvidersConfig.model_validate(
            {
                "providers": [
                    {"name": "gpt", "format": "openai_compat", "apiKey": "sk-a"},
                    {"name": "claude", "format": "anthropic", "apiKey": "sk-b"},
                ],
                "default": "claude",
            }
        )
        assert len(cfg.providers) == 2
        assert cfg.default == "claude"


class TestGetActiveProvider:
    def test_returns_default(self):
        config = Config.model_validate(
            {
                "providers": {
                    "providers": [
                        {"name": "a", "format": "openai_compat", "apiKey": "sk-a"},
                        {"name": "b", "format": "anthropic", "apiKey": "sk-b"},
                    ],
                    "default": "b",
                }
            }
        )
        assert config.get_active_provider().name == "b"

    def test_falls_back_to_first(self):
        config = Config.model_validate(
            {
                "providers": {
                    "providers": [{"name": "a", "format": "openai_compat", "apiKey": "sk-a"}]
                }
            }
        )
        assert config.get_active_provider().name == "a"

    def test_raises_when_empty(self):
        config = Config.model_validate({})
        with pytest.raises(ValueError, match="No provider configured"):
            config.get_active_provider()
