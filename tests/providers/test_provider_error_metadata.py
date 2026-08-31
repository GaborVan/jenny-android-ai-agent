from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from jenny.providers.anthropic_provider import AnthropicProvider
from jenny.providers.base import LLMProvider, LLMResponse
from jenny.providers.openai_compat_provider import OpenAICompatProvider
from jenny.providers.retry_policy import is_transient_response


def _fake_response(
    *,
    status_code: int,
    headers: dict[str, str] | None = None,
    text: str = "",
) -> SimpleNamespace:
    return SimpleNamespace(
        status_code=status_code,
        headers=headers or {},
        text=text,
    )


def test_openai_handle_error_extracts_structured_metadata() -> None:
    class FakeStatusError(Exception):
        pass

    err = FakeStatusError("boom")
    err.status_code = 409
    err.response = _fake_response(
        status_code=409,
        headers={"retry-after-ms": "250", "x-should-retry": "false"},
        text='{"error":{"type":"rate_limit_exceeded","code":"rate_limit_exceeded"}}',
    )
    err.body = {"error": {"type": "rate_limit_exceeded", "code": "rate_limit_exceeded"}}

    response = OpenAICompatProvider._handle_error(err)

    assert response.finish_reason == "error"
    assert response.error_status_code == 409
    assert response.error_type == "rate_limit_exceeded"
    assert response.error_code == "rate_limit_exceeded"
    assert response.error_retry_after_s == 0.25
    assert response.error_should_retry is False


def test_openai_handle_error_marks_timeout_kind() -> None:
    class FakeTimeoutError(Exception):
        pass

    response = OpenAICompatProvider._handle_error(FakeTimeoutError("timeout"))

    assert response.finish_reason == "error"
    assert response.error_kind == "timeout"


def test_anthropic_handle_error_extracts_structured_metadata() -> None:
    class FakeStatusError(Exception):
        pass

    err = FakeStatusError("boom")
    err.status_code = 408
    err.response = _fake_response(
        status_code=408,
        headers={"retry-after": "1.5", "x-should-retry": "true"},
    )
    err.body = {"type": "error", "error": {"type": "rate_limit_error"}}

    response = AnthropicProvider._handle_error(err)

    assert response.finish_reason == "error"
    assert response.error_status_code == 408
    assert response.error_type == "rate_limit_error"
    assert response.error_retry_after_s == 1.5
    assert response.error_should_retry is True


def test_anthropic_handle_error_marks_connection_kind() -> None:
    class FakeConnectionError(Exception):
        pass

    response = AnthropicProvider._handle_error(FakeConnectionError("connection"))

    assert response.finish_reason == "error"
    assert response.error_kind == "connection"


@pytest.mark.parametrize("expected, kwargs", [
    (True, {"error_status_code": 402}),  # HTTP 402
    (True, {"error_type": "insufficient_quota"}),  # billing token
    (True, {"content": "429 You exceeded your current quota"}),  # text marker
    (False, {"error_status_code": 429, "error_type": "rate_limit_exceeded"}),  # plain rate limit
])
def test_is_arrearage_response(expected: bool, kwargs: dict) -> None:
    response = LLMResponse(finish_reason="error", **{"content": "boom", **kwargs})
    assert LLMProvider.is_arrearage_response(response) is expected


# ---------------------------------------------------------------------------
# Mid-stream exception partial content preservation (#audit mid-stream-
# exception loss). A stall/timeout is retried elsewhere (base.py
# chat_stream_with_retry) and is NOT covered here; these tests cover a
# genuine mid-stream exception (e.g. a dropped connection) that aborts the
# stream outright, verifying that text already delivered via
# on_content_delta is not silently discarded from persisted history.
# ---------------------------------------------------------------------------


class _FakeAnthropicStreamResponse:
    """Stand-in for the httpx streaming response consumed by
    AnthropicProvider._http_chat_stream via response.aiter_lines()."""

    def __init__(self, lines: list[str], status_code: int = 200):
        self._lines = lines
        self.status_code = status_code

    def raise_for_status(self) -> None:
        pass

    async def aiter_lines(self):
        for line in self._lines:
            yield line
        raise ConnectionError("connection reset mid-stream")


class _FakeAnthropicStreamCtx:
    def __init__(self, response: _FakeAnthropicStreamResponse):
        self._response = response

    async def __aenter__(self) -> _FakeAnthropicStreamResponse:
        return self._response

    async def __aexit__(self, *exc_info: object) -> bool:
        return False


async def test_anthropic_stream_preserves_partial_content_on_mid_stream_exception() -> None:
    provider = AnthropicProvider(api_key="test-key", api_base="https://example.com")
    lines = [
        "event: content_block_delta",
        'data: {"type": "content_block_delta", "index": 0, '
        '"delta": {"type": "text_delta", "text": "Hello world"}}',
        "",
    ]
    provider._http_client.stream = lambda *a, **kw: _FakeAnthropicStreamCtx(
        _FakeAnthropicStreamResponse(lines)
    )

    response = await provider._http_chat_stream(
        messages=[{"role": "user", "content": "hi"}],
        tools=None, model="claude-x", max_tokens=100, temperature=0.7,
        reasoning_effort=None, tool_choice=None,
    )

    assert response.finish_reason == "error"
    assert response.partial_content == "Hello world"
    assert "connection reset mid-stream" in response.content


async def test_anthropic_stream_no_partial_content_when_nothing_streamed() -> None:
    """Regression guard: when the exception happens before any text streamed,
    behavior is unchanged (no partial_content, generic error message)."""
    provider = AnthropicProvider(api_key="test-key", api_base="https://example.com")
    provider._http_client.stream = lambda *a, **kw: _FakeAnthropicStreamCtx(
        _FakeAnthropicStreamResponse([])
    )

    response = await provider._http_chat_stream(
        messages=[{"role": "user", "content": "hi"}],
        tools=None, model="claude-x", max_tokens=100, temperature=0.7,
        reasoning_effort=None, tool_choice=None,
    )

    assert response.finish_reason == "error"
    assert response.partial_content is None
    assert "connection reset mid-stream" in response.content


class _FakeOpenAICompatSSEResponse:
    """Stand-in for the httpx streaming response consumed by
    OpenAICompatProvider._http_chat_stream via response.aiter_lines()."""

    def __init__(self, lines: list[str]):
        self._lines = lines

    async def aiter_lines(self):
        for line in self._lines:
            yield line
        raise ConnectionError("connection reset mid-stream")


async def test_openai_compat_stream_preserves_partial_content_on_mid_stream_exception() -> None:
    provider = OpenAICompatProvider(
        api_key="test-key", api_base="https://example.com/v1", default_model="test",
    )
    lines = [
        'data: {"choices": [{"delta": {"content": "Hello "}}]}',
        "",
        'data: {"choices": [{"delta": {"content": "world"}}]}',
        "",
    ]
    provider._http_request = AsyncMock(return_value=_FakeOpenAICompatSSEResponse(lines))

    response = await provider.chat_stream(
        messages=[{"role": "user", "content": "hi"}],
        tools=None, model="test", max_tokens=100, temperature=0.7,
        reasoning_effort=None, tool_choice=None,
    )

    assert response.finish_reason == "error"
    assert response.partial_content == "Hello world"
    assert "connection reset mid-stream" in response.content


async def test_openai_compat_stream_no_partial_content_when_nothing_streamed() -> None:
    """Regression guard: when the exception happens before any text streamed,
    behavior is unchanged (no partial_content, generic error message)."""
    provider = OpenAICompatProvider(
        api_key="test-key", api_base="https://example.com/v1", default_model="test",
    )
    provider._http_request = AsyncMock(return_value=_FakeOpenAICompatSSEResponse([]))

    response = await provider.chat_stream(
        messages=[{"role": "user", "content": "hi"}],
        tools=None, model="test", max_tokens=100, temperature=0.7,
        reasoning_effort=None, tool_choice=None,
    )

    assert response.finish_reason == "error"
    assert response.partial_content is None
    assert "connection reset mid-stream" in response.content


class TestHTTPErrorsKeepTheirMetadata:
    """Il percorso vero: da un errore HTTP di ``httpx`` fino alla decisione di ritentare.

    I test qui sopra costruiscono l'eccezione a mano, con ``.response`` già
    attaccata — cioè misurano il ramo dell'SDK, non quello che percorre
    ``_http_request``. Quel ramo sollevava un ``RuntimeError`` nudo, che perdeva
    status e header: la classificazione ripiegava sul testo, dove ``"429"`` vale
    come marker di transitorio, e una quota esaurita veniva ritentata a vuoto.
    """

    @staticmethod
    def _provider_answering(status: int, body: str, headers: dict[str, str] | None = None):
        url = "https://api.example.com/v1/chat/completions"

        def _build_request(method, request_url, **kwargs):
            request = MagicMock()
            request.url = request_url
            return request

        async def _send(request, **kwargs):
            return httpx.Response(
                status,
                request=httpx.Request("POST", url),
                text=body,
                headers=headers or {},
            )

        client = MagicMock(spec=httpx.AsyncClient)
        client.build_request = MagicMock(side_effect=_build_request)
        client.send = AsyncMock(side_effect=_send)

        provider = OpenAICompatProvider(
            api_key="k", api_base="https://api.example.com/v1", default_model="m",
        )
        provider._http_client = client
        return provider

    async def _response_for(self, status: int, body: str, headers=None) -> LLMResponse:
        provider = self._provider_answering(status, body, headers)
        try:
            await provider._http_request("/chat/completions", {"model": "m"})
        except Exception as e:  # noqa: BLE001 — è esattamente ciò che il provider cattura
            return OpenAICompatProvider._handle_error(e)
        raise AssertionError("la richiesta doveva fallire")

    async def test_exhausted_quota_is_not_retried(self) -> None:
        """Un 429 da quota esaurita è terminale: ritentarlo non lo farà passare."""
        response = await self._response_for(
            429,
            '{"error":{"type":"insufficient_quota","code":"insufficient_quota",'
            '"message":"You exceeded your current quota"}}',
        )

        assert response.error_status_code == 429
        assert response.error_type == "insufficient_quota"
        assert is_transient_response(response) is False

    async def test_real_rate_limit_is_retried_after_the_header_says(self) -> None:
        """Un 429 di rate limit invece si ritenta, e ``Retry-After`` va rispettato."""
        response = await self._response_for(
            429,
            '{"error":{"type":"rate_limit_error","message":"Rate limit reached"}}',
            {"retry-after": "12"},
        )

        assert response.error_status_code == 429
        assert is_transient_response(response) is True
        assert response.retry_after == 12.0

    async def test_server_error_is_retried_and_bad_key_is_not(self) -> None:
        server = await self._response_for(500, '{"error":{"message":"internal"}}')
        unauthorized = await self._response_for(
            401, '{"error":{"type":"invalid_request_error","code":"invalid_api_key"}}',
        )

        assert is_transient_response(server) is True
        assert is_transient_response(unauthorized) is False

    async def test_the_message_still_names_status_and_url(self) -> None:
        """Il corpo da solo non basta a capire *dove* ha fallito: il testo li tiene entrambi."""
        response = await self._response_for(429, '{"error":{"code":"insufficient_quota"}}')

        assert "429" in response.content
        assert "api.example.com" in response.content
        assert "insufficient_quota" in response.content

    async def test_responses_incompatibility_can_still_fall_back(self) -> None:
        """Il fallback Responses→Chat-Completions ha bisogno dello status per scattare.

        Senza status l'endpoint Responses restava scelto anche di fronte a un 400
        che dice "parametro sconosciuto", cioè il caso per cui il fallback esiste.
        """
        provider = self._provider_answering(
            400,
            '{"error":{"message":"Unknown parameter: max_output_tokens",'
            '"type":"invalid_request_error"}}',
        )
        try:
            await provider._http_request("/responses", {"model": "m"})
        except Exception as e:  # noqa: BLE001
            assert OpenAICompatProvider._should_fallback_from_responses_error(e) is True
        else:
            raise AssertionError("la richiesta doveva fallire")

    async def test_a_quota_error_does_not_trigger_the_responses_fallback(self) -> None:
        """Riprovare su un altro endpoint non ricarica il credito: il 429 non è incompatibilità."""
        provider = self._provider_answering(429, '{"error":{"type":"insufficient_quota"}}')
        try:
            await provider._http_request("/responses", {"model": "m"})
        except Exception as e:  # noqa: BLE001
            assert OpenAICompatProvider._should_fallback_from_responses_error(e) is False
        else:
            raise AssertionError("la richiesta doveva fallire")
