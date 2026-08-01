"""Tests for cached token extraction from OpenAI-compatible providers."""

from __future__ import annotations

from jenny.providers.openai_compat_provider import OpenAICompatProvider


class _FakeSpec:
    supports_prompt_caching = False
    model_id_prefix = None
    strip_model_prefix = False
    max_completion_tokens = False
    reasoning_effort = None


def _provider():
    from unittest.mock import MagicMock
    p = OpenAICompatProvider.__new__(OpenAICompatProvider)
    p.client = MagicMock()
    p.spec = _FakeSpec()
    return p


# Minimal valid choice so _parse reaches _extract_usage.
_DICT_CHOICE = {"message": {"content": "Hello"}}


# --- dict-based response (raw JSON / mapping) ---

def test_extract_usage_openai_cached_tokens_dict():
    """prompt_tokens_details.cached_tokens from a dict response."""
    p = _provider()
    response = {
        "choices": [_DICT_CHOICE],
        "usage": {
            "prompt_tokens": 2000,
            "completion_tokens": 300,
            "total_tokens": 2300,
            "prompt_tokens_details": {"cached_tokens": 1200},
        }
    }
    result = p._parse(response)
    assert result.usage["cached_tokens"] == 1200
    assert result.usage["prompt_tokens"] == 2000


def test_extract_usage_deepseek_cached_tokens_dict():
    """prompt_cache_hit_tokens from a DeepSeek dict response."""
    p = _provider()
    response = {
        "choices": [_DICT_CHOICE],
        "usage": {
            "prompt_tokens": 1500,
            "completion_tokens": 200,
            "total_tokens": 1700,
            "prompt_cache_hit_tokens": 1200,
            "prompt_cache_miss_tokens": 300,
        }
    }
    result = p._parse(response)
    assert result.usage["cached_tokens"] == 1200


def test_extract_usage_no_cached_tokens_dict():
    """Response without any cache fields -> no cached_tokens key."""
    p = _provider()
    response = {
        "choices": [_DICT_CHOICE],
        "usage": {
            "prompt_tokens": 1000,
            "completion_tokens": 200,
            "total_tokens": 1200,
        }
    }
    result = p._parse(response)
    assert "cached_tokens" not in result.usage


def test_extract_usage_openai_cached_zero_dict():
    """cached_tokens=0 should NOT be included (same as existing fields)."""
    p = _provider()
    response = {
        "choices": [_DICT_CHOICE],
        "usage": {
            "prompt_tokens": 2000,
            "completion_tokens": 300,
            "total_tokens": 2300,
            "prompt_tokens_details": {"cached_tokens": 0},
        }
    }
    result = p._parse(response)
    assert "cached_tokens" not in result.usage


def test_extract_usage_stepfun_top_level_cached_tokens_dict():
    """StepFun/Moonshot: usage.cached_tokens at top level (not nested)."""
    p = _provider()
    response = {
        "choices": [_DICT_CHOICE],
        "usage": {
            "prompt_tokens": 591,
            "completion_tokens": 120,
            "total_tokens": 711,
            "cached_tokens": 512,
        }
    }
    result = p._parse(response)
    assert result.usage["cached_tokens"] == 512


def test_extract_usage_priority_nested_over_top_level_dict():
    """When both nested and top-level cached_tokens exist, nested wins."""
    p = _provider()
    response = {
        "choices": [_DICT_CHOICE],
        "usage": {
            "prompt_tokens": 2000,
            "completion_tokens": 300,
            "total_tokens": 2300,
            "prompt_tokens_details": {"cached_tokens": 100},
            "cached_tokens": 500,
        }
    }
    result = p._parse(response)
    assert result.usage["cached_tokens"] == 100


def test_anthropic_maps_cache_fields_to_cached_tokens():
    """Anthropic's cache_read_input_tokens should map to cached_tokens."""
    from jenny.providers.anthropic_provider import AnthropicProvider

    response = {
        "content": [{"type": "text", "text": "hello"}],
        "stop_reason": "end_turn",
        "usage": {
            "input_tokens": 800,
            "output_tokens": 200,
            "cache_creation_input_tokens": 300,
            "cache_read_input_tokens": 1200,
        },
    }
    result = AnthropicProvider._parse_response_dict(response)
    assert result.usage["cached_tokens"] == 1200
    assert result.usage["prompt_tokens"] == 2300
    assert result.usage["total_tokens"] == 2500
    assert result.usage["cache_creation_input_tokens"] == 300


def test_anthropic_no_cache_fields():
    """Anthropic response without cache fields should not have cached_tokens."""
    from jenny.providers.anthropic_provider import AnthropicProvider

    response = {
        "content": [{"type": "text", "text": "hello"}],
        "stop_reason": "end_turn",
        "usage": {"input_tokens": 800, "output_tokens": 200},
    }
    result = AnthropicProvider._parse_response_dict(response)
    assert "cached_tokens" not in result.usage
