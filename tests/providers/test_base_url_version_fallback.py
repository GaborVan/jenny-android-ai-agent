"""Recupero automatico da una base URL senza segmento di versione.

Diversi gateway OpenAI-compatibili servono ``/models`` sulla radice ma le
completions solo sotto ``/v1``: la lista modelli si popola, la base sembra
valida, e poi ogni turno torna 404 (issue #18, Pollinations). Il provider
ritenta una volta su ``<base>/v1`` e, se quello risponde, lo adotta.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from jenny.providers.openai_compat_helpers import _versioned_base_candidate
from jenny.providers.openai_compat_provider import OpenAICompatProvider


def _response(url: str, status: int, body: str = "") -> httpx.Response:
    return httpx.Response(status, text=body, request=httpx.Request("POST", url))


def _provider_with_replies(api_base: str, replies: dict[str, httpx.Response]):
    """Provider il cui client finto risponde per URL; annota gli URL chiamati."""
    calls: list[str] = []

    def _build_request(method, url, **kwargs):
        calls.append(url)
        request = MagicMock()
        request.url = url
        return request

    async def _send(request, **kwargs):
        return replies[request.url]

    client = MagicMock(spec=httpx.AsyncClient)
    client.build_request = MagicMock(side_effect=_build_request)
    client.send = AsyncMock(side_effect=_send)

    provider = OpenAICompatProvider(api_key="k", api_base=api_base, default_model="m")
    provider._http_client = client
    return provider, calls


NOT_FOUND = '{"success":false,"error":{"message":"Oh no, there is nothing here."}}'


class TestVersionedBaseCandidate:
    """La regola pura: quando ha senso aggiungere /v1 e quando no."""

    def test_bare_host_gets_v1(self) -> None:
        assert _versioned_base_candidate("https://gen.pollinations.ai") == (
            "https://gen.pollinations.ai/v1"
        )

    def test_trailing_slash_is_normalized(self) -> None:
        assert _versioned_base_candidate("https://gen.pollinations.ai/") == (
            "https://gen.pollinations.ai/v1"
        )

    def test_path_without_version_gets_v1(self) -> None:
        assert _versioned_base_candidate("https://api.groq.com/openai") == (
            "https://api.groq.com/openai/v1"
        )

    def test_existing_version_segment_is_left_alone(self) -> None:
        assert _versioned_base_candidate("https://api.openai.com/v1") is None
        assert _versioned_base_candidate("https://example.com/api/v3/") is None

    def test_empty_base_has_no_candidate(self) -> None:
        assert _versioned_base_candidate("") is None
        assert _versioned_base_candidate(None) is None


class TestRetryOnVersionedBase:
    """Il comportamento end-to-end di _http_request."""

    async def test_404_retries_on_v1_and_adopts_it(self) -> None:
        provider, calls = _provider_with_replies(
            "https://gen.pollinations.ai",
            {
                "https://gen.pollinations.ai/chat/completions": _response(
                    "https://gen.pollinations.ai/chat/completions", 404, NOT_FOUND,
                ),
                "https://gen.pollinations.ai/v1/chat/completions": _response(
                    "https://gen.pollinations.ai/v1/chat/completions", 200, "{}",
                ),
            },
        )

        response = await provider._http_request("/chat/completions", {})

        assert response.status_code == 200
        assert calls == [
            "https://gen.pollinations.ai/chat/completions",
            "https://gen.pollinations.ai/v1/chat/completions",
        ]
        assert provider._effective_base == "https://gen.pollinations.ai/v1"

    async def test_correction_is_remembered_for_later_calls(self) -> None:
        provider, calls = _provider_with_replies(
            "https://gen.pollinations.ai",
            {
                "https://gen.pollinations.ai/chat/completions": _response(
                    "https://gen.pollinations.ai/chat/completions", 404, NOT_FOUND,
                ),
                "https://gen.pollinations.ai/v1/chat/completions": _response(
                    "https://gen.pollinations.ai/v1/chat/completions", 200, "{}",
                ),
            },
        )

        await provider._http_request("/chat/completions", {})
        await provider._http_request("/chat/completions", {})

        # Il secondo turno non ripaga il tentativo a vuoto.
        assert calls[-1] == "https://gen.pollinations.ai/v1/chat/completions"
        assert calls.count("https://gen.pollinations.ai/chat/completions") == 1

    async def test_both_404_names_both_urls(self) -> None:
        provider, calls = _provider_with_replies(
            "https://wrong.example.com",
            {
                "https://wrong.example.com/chat/completions": _response(
                    "https://wrong.example.com/chat/completions", 404, NOT_FOUND,
                ),
                "https://wrong.example.com/v1/chat/completions": _response(
                    "https://wrong.example.com/v1/chat/completions", 404, NOT_FOUND,
                ),
            },
        )

        with pytest.raises(RuntimeError) as excinfo:
            await provider._http_request("/chat/completions", {})

        message = str(excinfo.value)
        assert "https://wrong.example.com/chat/completions" in message
        assert "https://wrong.example.com/v1/chat/completions" in message
        assert "base URL" in message
        # La base sbagliata non viene adottata.
        assert provider._effective_base == "https://wrong.example.com"
        assert len(calls) == 2

    async def test_versioned_base_does_not_retry(self) -> None:
        provider, calls = _provider_with_replies(
            "https://api.openai.com/v1",
            {
                "https://api.openai.com/v1/chat/completions": _response(
                    "https://api.openai.com/v1/chat/completions", 404, NOT_FOUND,
                ),
            },
        )

        with pytest.raises(RuntimeError) as excinfo:
            await provider._http_request("/chat/completions", {})

        assert len(calls) == 1
        assert "https://api.openai.com/v1/chat/completions" in str(excinfo.value)

    async def test_non_404_is_not_retried(self) -> None:
        provider, calls = _provider_with_replies(
            "https://gen.pollinations.ai",
            {
                "https://gen.pollinations.ai/chat/completions": _response(
                    "https://gen.pollinations.ai/chat/completions",
                    401,
                    '{"error":"invalid api key"}',
                ),
            },
        )

        with pytest.raises(RuntimeError) as excinfo:
            await provider._http_request("/chat/completions", {})

        message = str(excinfo.value)
        assert "HTTP 401" in message
        assert "invalid api key" in message
        assert len(calls) == 1
        assert provider._effective_base == "https://gen.pollinations.ai"

    async def test_streaming_404_retries_too(self) -> None:
        provider, calls = _provider_with_replies(
            "https://gen.pollinations.ai",
            {
                "https://gen.pollinations.ai/chat/completions": _response(
                    "https://gen.pollinations.ai/chat/completions", 404, NOT_FOUND,
                ),
                "https://gen.pollinations.ai/v1/chat/completions": _response(
                    "https://gen.pollinations.ai/v1/chat/completions", 200, "{}",
                ),
            },
        )

        response = await provider._http_request("/chat/completions", {}, stream=True)

        assert response.status_code == 200
        assert len(calls) == 2

    async def test_non_404_on_retry_adopts_the_versioned_base(self) -> None:
        """401 sul /v1 significa che l'endpoint c'è: la base è giusta, la chiave no."""
        provider, calls = _provider_with_replies(
            "https://gen.pollinations.ai",
            {
                "https://gen.pollinations.ai/chat/completions": _response(
                    "https://gen.pollinations.ai/chat/completions", 404, NOT_FOUND,
                ),
                "https://gen.pollinations.ai/v1/chat/completions": _response(
                    "https://gen.pollinations.ai/v1/chat/completions",
                    401,
                    '{"error":"API key required"}',
                ),
            },
        )

        with pytest.raises(RuntimeError) as excinfo:
            await provider._http_request("/chat/completions", {})

        assert "HTTP 401" in str(excinfo.value)
        assert "API key required" in str(excinfo.value)
        assert provider._effective_base == "https://gen.pollinations.ai/v1"
        assert len(calls) == 2
