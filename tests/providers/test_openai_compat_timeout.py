from unittest.mock import patch, sentinel

from jenny.providers.openai_compat_provider import OpenAICompatProvider


async def test_openai_compat_provider_defers_http_client_until_first_use() -> None:
    provider = OpenAICompatProvider(
        api_key="test-key", api_base="https://example.com/v1", default_model="test"
    )
    assert provider._http_client is None
    await provider._ensure_client()
    assert provider._http_client is not None


async def test_openai_compat_provider_sets_timeout_on_http_client() -> None:
    with patch(
        "httpx.AsyncClient",
        return_value=sentinel.http_client,
    ) as mock_http_client:
        provider = OpenAICompatProvider(
            api_key="test-key",
            api_base="http://127.0.0.1:11434/v1",
            default_model="test",
        )
        await provider._ensure_client()

    client_kwargs = mock_http_client.call_args.kwargs
    assert client_kwargs["timeout"] == 120.0
    assert client_kwargs["limits"].keepalive_expiry == 0
    assert provider._http_client is sentinel.http_client


async def test_openai_compat_provider_timeout_can_be_overridden_by_env(monkeypatch) -> None:
    monkeypatch.setenv("JENNY_OPENAI_COMPAT_TIMEOUT_S", "45")

    provider = OpenAICompatProvider(
        api_key="test-key", api_base="https://example.com/v1", default_model="test"
    )
    await provider._ensure_client()

    assert provider._http_client.timeout.read == 45.0
