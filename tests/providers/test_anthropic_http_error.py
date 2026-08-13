"""Un errore HTTP deve arrivare leggibile e classificabile, su entrambi i path.

Streaming: il body di una response aperta con ``client.stream()`` non è ancora
stato letto e il context manager la chiude prima che l'except giri, quindi
toccare ``.text`` lì sollevava ``ResponseNotRead`` — che sostituiva l'errore
vero con il proprio messaggio.

Non-streaming: l'errore veniva riavvolto in una ``RuntimeError``, che non porta
con sé ``.response``; status code, retry-after ed error_type sparivano e la
retry policy poteva solo tirare a indovinare sul testo.
"""

from __future__ import annotations

import httpx
import pytest

from jenny.providers.anthropic_provider import AnthropicProvider
from jenny.providers.retry_policy import is_transient_response

RATE_LIMIT_BODY = (
    '{"type":"error","error":{"type":"rate_limit_error",'
    '"message":"concurrency limit reached"}}'
)

# I due ingressi pubblici condividono la stessa promessa sugli errori.
ENTRYPOINTS = ["chat", "chat_stream"]


def _provider(handler) -> AnthropicProvider:
    provider = AnthropicProvider(api_key="k", api_base="https://api.example.com")
    provider._http_client = httpx.AsyncClient(
        base_url="https://api.example.com",
        transport=httpx.MockTransport(handler),
    )
    return provider


async def _call(entrypoint: str, handler):
    provider = _provider(handler)
    return await getattr(provider, entrypoint)(
        messages=[{"role": "user", "content": "hi"}]
    )


@pytest.mark.parametrize("entrypoint", ENTRYPOINTS)
async def test_http_error_reports_the_server_body(entrypoint: str) -> None:
    response = await _call(
        entrypoint, lambda _req: httpx.Response(429, text=RATE_LIMIT_BODY)
    )

    assert response.finish_reason == "error"
    assert "ResponseNotRead" not in (response.content or "")
    assert "without having called" not in (response.content or "")
    assert "concurrency limit reached" in (response.content or "")


@pytest.mark.parametrize("entrypoint", ENTRYPOINTS)
async def test_http_error_stays_retryable(entrypoint: str) -> None:
    response = await _call(
        entrypoint, lambda _req: httpx.Response(429, text=RATE_LIMIT_BODY)
    )

    assert response.error_status_code == 429
    assert response.error_type == "rate_limit_error"
    assert is_transient_response(response) is True


@pytest.mark.parametrize("entrypoint", ENTRYPOINTS)
async def test_retry_after_header_survives(entrypoint: str) -> None:
    response = await _call(
        entrypoint,
        lambda _req: httpx.Response(
            429, text=RATE_LIMIT_BODY, headers={"retry-after": "7"}
        ),
    )

    assert response.error_retry_after_s == 7.0


@pytest.mark.parametrize("entrypoint", ENTRYPOINTS)
async def test_empty_error_body_still_names_the_status(entrypoint: str) -> None:
    response = await _call(entrypoint, lambda _req: httpx.Response(503, text=""))

    assert response.error_status_code == 503
    assert "503" in (response.content or "")
    assert is_transient_response(response) is True


@pytest.mark.parametrize("entrypoint", ENTRYPOINTS)
async def test_quota_exhaustion_is_not_retried(entrypoint: str) -> None:
    """Un 429 da credito esaurito non è transient: ritentarlo non lo risolve."""
    response = await _call(
        entrypoint,
        lambda _req: httpx.Response(
            429,
            text='{"type":"error","error":{"type":"insufficient_quota",'
            '"message":"credit balance too low"}}',
        ),
    )

    assert response.error_type == "insufficient_quota"
    assert is_transient_response(response) is False
