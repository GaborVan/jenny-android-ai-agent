from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from jenny.providers.anthropic_provider import AnthropicProvider
from jenny.providers.base import LLMProvider, LLMResponse
from jenny.providers.openai_compat_provider import OpenAICompatProvider


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
