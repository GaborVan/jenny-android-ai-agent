"""Tests for reasoning_content extraction in OpenAICompatProvider.

Covers non-streaming (_parse) and streaming (_parse_chunks) paths for
providers that return a reasoning_content field (e.g. MiMo, DeepSeek-R1).
"""

from jenny.providers.openai_compat_provider import OpenAICompatProvider
from jenny.utils.helpers import build_assistant_message

# ── _parse: non-streaming ─────────────────────────────────────────────────


def test_parse_dict_extracts_reasoning_content() -> None:
    """reasoning_content at message level is surfaced in LLMResponse."""
    provider = OpenAICompatProvider(api_key="", api_base="", default_model="")

    response = {
        "choices": [{
            "message": {
                "content": "42",
                "reasoning_content": "Let me think step by step…",
            },
            "finish_reason": "stop",
        }],
        "usage": {"prompt_tokens": 5, "completion_tokens": 10, "total_tokens": 15},
    }

    result = provider._parse(response)

    assert result.content == "42"
    assert result.reasoning_content == "Let me think step by step…"


def test_parse_dict_reasoning_content_none_when_absent() -> None:
    """reasoning_content is None when the response doesn't include it."""
    provider = OpenAICompatProvider(api_key="", api_base="", default_model="")

    response = {
        "choices": [{
            "message": {"content": "hello"},
            "finish_reason": "stop",
        }],
    }

    result = provider._parse(response)

    assert result.reasoning_content is None


def test_parse_dict_reasoning_content_empty_string_preserved() -> None:
    """reasoning_content=\"\" is preserved, not coerced to None.

    Some providers (e.g. DeepSeek) require the reasoning_content key to
    be present in subsequent requests even when empty.  Coercing "" to
    None drops the key downstream and causes API errors.
    """
    provider = OpenAICompatProvider(api_key="", api_base="", default_model="")

    response = {
        "choices": [{
            "message": {
                "content": "answer",
                "reasoning_content": "",
            },
            "finish_reason": "stop",
        }],
        "usage": {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8},
    }

    result = provider._parse(response)

    assert result.reasoning_content == ""


# ── _parse_chunks: streaming ──────────────────────────────────────────────


def test_parse_chunks_extracts_reasoning_content() -> None:
    provider = OpenAICompatProvider(api_key="", api_base="", default_model="")

    chunks = [
        {"choices": [{"delta": {"content": "hello", "reasoning_content": "think"}}]},
        {"choices": [{"delta": {"content": " world", "reasoning_content": "ing"}}]},
    ]

    result = provider._parse_chunks(chunks)

    assert result.content == "hello world"
    assert result.reasoning_content == "thinking"


def test_parse_chunks_reasoning_content_none_when_absent() -> None:
    provider = OpenAICompatProvider(api_key="", api_base="", default_model="")

    chunks = [{"choices": [{"delta": {"content": "hi"}}]}]

    result = provider._parse_chunks(chunks)

    assert result.reasoning_content is None


# ── Round-trip into assistant message ─────────────────────────────────────


def test_assistant_message_preserves_reasoning_content() -> None:
    msg = build_assistant_message("answer", reasoning_content="step 1; step 2")

    assert msg["role"] == "assistant"
    assert msg["content"] == "answer"
    assert msg["reasoning_content"] == "step 1; step 2"


def test_assistant_message_drops_none_reasoning_content() -> None:
    msg = build_assistant_message("answer", reasoning_content=None)

    assert "reasoning_content" not in msg


# ── Inline think tags ─────────────────────────────────────────────────────


def test_extract_reasoning_from_inline_think_tags() -> None:
    from jenny.utils.helpers import extract_reasoning

    reasoning, cleaned = extract_reasoning(None, None, "<think>secret plan</think>hi")

    assert reasoning == "secret plan"
    assert cleaned == "hi"
