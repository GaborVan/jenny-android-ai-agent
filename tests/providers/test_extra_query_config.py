"""Tests for provider extra_query config injection into client defaults."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx

from jenny.config.schema import ProviderConfig
from jenny.providers.openai_compat_provider import OpenAICompatProvider


class TestExtraQuerySchema:
    """Verify ProviderConfig accepts extra_query."""

    def test_default_is_none(self) -> None:
        config = ProviderConfig(name="test", format="openai_compat")
        assert config.extra_query is None

    def test_accepts_dict(self) -> None:
        config = ProviderConfig(name="test", format="openai_compat", extra_query={"api-version": "2024-02-01"})
        assert config.extra_query == {"api-version": "2024-02-01"}


class TestExtraQueryInit:
    """Verify the provider stores extra_query from config."""

    def test_default_is_empty(self) -> None:
        provider = OpenAICompatProvider(api_key="test", api_base="", default_model="")
        assert provider._extra_query == {}

    def test_none_becomes_empty(self) -> None:
        provider = OpenAICompatProvider(api_key="test", api_base="", default_model="", extra_query=None)
        assert provider._extra_query == {}

    def test_dict_stored(self) -> None:
        query = {"api-version": "v1"}
        provider = OpenAICompatProvider(api_key="test", api_base="", default_model="", extra_query=query)
        assert provider._extra_query == query


class TestExtraQueryRequest:
    """Verify extra_query flows into httpx request query params."""

    async def test_http_request_includes_extra_query(self) -> None:
        mock_client = MagicMock(spec=httpx.AsyncClient)
        mock_client.build_request.return_value = MagicMock()
        mock_client.send = AsyncMock(return_value=MagicMock())
        with patch(
            "httpx.AsyncClient",
            return_value=mock_client,
        ):
            provider = OpenAICompatProvider(
                api_key="test",
                api_base="https://example.com/v1",
                default_model="",
                extra_query={"api-version": "v1"},
            )
            await provider._http_request("/chat/completions", {})

        call_kwargs = mock_client.build_request.call_args.kwargs
        assert call_kwargs["params"] == {"api-version": "v1"}

    async def test_http_request_passes_no_query_when_empty(self) -> None:
        mock_client = MagicMock(spec=httpx.AsyncClient)
        mock_client.build_request.return_value = MagicMock()
        mock_client.send = AsyncMock(return_value=MagicMock())
        with patch(
            "httpx.AsyncClient",
            return_value=mock_client,
        ):
            provider = OpenAICompatProvider(
                api_key="test",
                api_base="https://example.com/v1",
                default_model="",
            )
            await provider._http_request("/chat/completions", {})

        call_kwargs = mock_client.build_request.call_args.kwargs
        assert call_kwargs.get("params") is None
