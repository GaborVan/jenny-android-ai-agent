"""Test diretti per jenny/providers/retry_policy.py.

Copre i classificatori puri (transient/arrearage/429) e l'estrazione di
type/code dal payload d'errore. Il motore di retry vero e proprio
(``chat_with_retry``) e il parsing di ``retry-after`` restano in ``base.py``
e sono già coperti da ``tests/providers/test_provider_retry.py`` e
``tests/providers/test_provider_retry_after_hints.py``: qui testiamo solo le
funzioni di classificazione esportate da questo modulo.
"""

from __future__ import annotations

from jenny.providers.base import LLMResponse
from jenny.providers.retry_policy import (
    extract_error_type_code,
    is_arrearage_response,
    is_retryable_429_response,
    is_transient_error,
    is_transient_response,
    normalize_error_token,
)


def _response(**kwargs) -> LLMResponse:
    kwargs.setdefault("content", "boom")
    kwargs.setdefault("finish_reason", "error")
    return LLMResponse(**kwargs)


# ---------------------------------------------------------------------------
# normalize_error_token
# ---------------------------------------------------------------------------


def test_normalize_error_token_none_returns_none() -> None:
    assert normalize_error_token(None) is None


def test_normalize_error_token_empty_string_returns_none() -> None:
    assert normalize_error_token("") is None


def test_normalize_error_token_whitespace_only_returns_none() -> None:
    assert normalize_error_token("   ") is None


def test_normalize_error_token_lowercases_and_strips() -> None:
    assert normalize_error_token("  Rate_Limit_Exceeded  ") == "rate_limit_exceeded"


def test_normalize_error_token_coerces_non_string() -> None:
    assert normalize_error_token(429) == "429"


# ---------------------------------------------------------------------------
# is_transient_error (classificazione testuale pura)
# ---------------------------------------------------------------------------


def test_is_transient_error_none_content_is_false() -> None:
    assert is_transient_error(None) is False


def test_is_transient_error_no_marker_is_false() -> None:
    assert is_transient_error("401 unauthorized") is False


def test_is_transient_error_detects_marker_case_insensitive() -> None:
    assert is_transient_error("Rate Limit exceeded, please slow down") is True


def test_is_transient_error_detects_status_code_marker() -> None:
    assert is_transient_error("upstream returned 503") is True


def test_is_transient_error_detects_chinese_rate_limit_marker() -> None:
    assert is_transient_error("错误：速率限制，请重试") is True


# ---------------------------------------------------------------------------
# is_retryable_429_response
# ---------------------------------------------------------------------------


def test_is_retryable_429_response_defaults_true_when_unknown() -> None:
    """Un 429 senza metadati riconoscibili né marker testuali resta WAIT+retry."""
    response = _response(content="something odd happened")
    assert is_retryable_429_response(response) is True


def test_is_retryable_429_response_false_for_quota_token() -> None:
    response = _response(error_type="insufficient_quota")
    assert is_retryable_429_response(response) is False


def test_is_retryable_429_response_false_for_quota_code_token() -> None:
    response = _response(error_code="quota_exceeded")
    assert is_retryable_429_response(response) is False


def test_is_retryable_429_response_false_for_quota_text_marker() -> None:
    response = _response(content="429: you exceeded your current quota")
    assert is_retryable_429_response(response) is False


def test_is_retryable_429_response_true_for_rate_limit_token() -> None:
    response = _response(error_type="rate_limit_exceeded")
    assert is_retryable_429_response(response) is True


def test_is_retryable_429_response_true_for_rate_limit_text_marker() -> None:
    response = _response(content="rate limit hit, try again in 2s")
    assert is_retryable_429_response(response) is True


def test_is_retryable_429_response_quota_token_wins_over_retryable_text() -> None:
    """Il token di quota non-retryable ha priorità sui marker testuali retryable."""
    response = _response(error_type="insufficient_quota", content="rate limit exceeded")
    assert is_retryable_429_response(response) is False


# ---------------------------------------------------------------------------
# is_transient_response
# ---------------------------------------------------------------------------


def test_is_transient_response_prefers_structured_should_retry_true() -> None:
    response = _response(error_should_retry=True, content="401 unauthorized")
    assert is_transient_response(response) is True


def test_is_transient_response_prefers_structured_should_retry_false() -> None:
    """error_should_retry=False vince anche se il testo sembrerebbe transient."""
    response = _response(error_should_retry=False, content="503 server error")
    assert is_transient_response(response) is False


def test_is_transient_response_429_status_delegates_to_429_classifier() -> None:
    response = _response(error_status_code=429, error_type="insufficient_quota")
    assert is_transient_response(response) is False


def test_is_transient_response_5xx_status_is_transient() -> None:
    response = _response(error_status_code=503)
    assert is_transient_response(response) is True


def test_is_transient_response_retryable_status_code_408() -> None:
    response = _response(error_status_code=408)
    assert is_transient_response(response) is True


def test_is_transient_response_401_status_not_transient() -> None:
    response = _response(error_status_code=401, content="unauthorized")
    assert is_transient_response(response) is False


def test_is_transient_response_transient_kind_timeout() -> None:
    response = _response(error_kind="timeout", content="401 unauthorized")
    assert is_transient_response(response) is True


def test_is_transient_response_transient_kind_connection() -> None:
    response = _response(error_kind="connection", content="something else")
    assert is_transient_response(response) is True


def test_is_transient_response_falls_back_to_text_marker() -> None:
    response = _response(content="connection reset by peer")
    assert is_transient_response(response) is True


def test_is_transient_response_no_metadata_no_marker_is_false() -> None:
    response = _response(content="invalid api key")
    assert is_transient_response(response) is False


# ---------------------------------------------------------------------------
# is_arrearage_response
# ---------------------------------------------------------------------------


def test_is_arrearage_response_true_for_402() -> None:
    response = _response(error_status_code=402)
    assert is_arrearage_response(response) is True


def test_is_arrearage_response_true_for_billing_token() -> None:
    response = _response(error_type="billing_not_active")
    assert is_arrearage_response(response) is True


def test_is_arrearage_response_true_for_text_marker() -> None:
    response = _response(content="Error: out of credits, please top up")
    assert is_arrearage_response(response) is True


def test_is_arrearage_response_false_for_plain_rate_limit() -> None:
    response = _response(error_status_code=429, error_type="rate_limit_exceeded")
    assert is_arrearage_response(response) is False


def test_is_arrearage_response_false_when_content_none() -> None:
    response = _response(content=None)
    assert is_arrearage_response(response) is False


# ---------------------------------------------------------------------------
# extract_error_type_code
# ---------------------------------------------------------------------------


def test_extract_error_type_code_none_payload() -> None:
    assert extract_error_type_code(None) == (None, None)


def test_extract_error_type_code_non_dict_non_str_payload() -> None:
    assert extract_error_type_code(1234) == (None, None)


def test_extract_error_type_code_empty_string_payload() -> None:
    assert extract_error_type_code("") == (None, None)


def test_extract_error_type_code_malformed_json_string() -> None:
    assert extract_error_type_code("not json{{") == (None, None)


def test_extract_error_type_code_json_string_that_is_not_a_dict() -> None:
    assert extract_error_type_code("[1, 2, 3]") == (None, None)


def test_extract_error_type_code_flat_dict() -> None:
    payload = {"type": "invalid_request_error", "code": "bad_input"}
    assert extract_error_type_code(payload) == ("invalid_request_error", "bad_input")


def test_extract_error_type_code_nested_error_object_wins() -> None:
    payload = {
        "type": "outer_type",
        "code": "outer_code",
        "error": {"type": "rate_limit_error", "code": "rate_limit_exceeded"},
    }
    assert extract_error_type_code(payload) == ("rate_limit_error", "rate_limit_exceeded")


def test_extract_error_type_code_nested_error_partial_falls_back_to_outer() -> None:
    """Se il campo interno manca, si ripiega sul campo esterno (mismatch consentito)."""
    payload = {"type": "outer_type", "error": {"code": "inner_code"}}
    assert extract_error_type_code(payload) == ("outer_type", "inner_code")


def test_extract_error_type_code_json_string_payload_parsed() -> None:
    payload = '{"error": {"type": "server_error", "code": "internal"}}'
    assert extract_error_type_code(payload) == ("server_error", "internal")


def test_extract_error_type_code_normalizes_case_and_whitespace() -> None:
    payload = {"type": "  Rate_Limit_Error  ", "code": None}
    assert extract_error_type_code(payload) == ("rate_limit_error", None)
