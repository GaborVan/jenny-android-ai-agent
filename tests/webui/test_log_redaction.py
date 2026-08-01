"""Regression tests for the log secret-redaction invariant (Intervento 4a).

Secrets travel in query strings (``?token=…``, ``?api_key=…``). Logging today is
already prudent, but nothing *guaranteed* it stayed that way. These tests lock in
the invariant: the redaction helper masks secret values, and a real onboarding
route call never emits the raw ``api_key`` into the logs.
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest
from loguru import logger as loguru_logger
from websockets.http11 import Headers
from websockets.http11 import Request as WsRequest

from jenny.channels.http_utils import (
    http_error,
    http_json_response,
    parse_query,
    redact_query_secrets,
)
from jenny.runtime.context import get_runtime_context
from jenny.session.manager import SessionManager
from jenny.webui.settings_routes import WebUISettingsRouter

# ---------------------------------------------------------------------------
# Unit: redact_query_secrets
# ---------------------------------------------------------------------------


def test_redact_masks_token_value():
    assert (
        redact_query_secrets("/api/thing?token=super-secret-123")
        == "/api/thing?token=REDACTED"
    )


def test_redact_masks_api_key_value():
    assert (
        redact_query_secrets("/webui/bootstrap?api_key=sk-abc999")
        == "/webui/bootstrap?api_key=REDACTED"
    )


def test_redact_keeps_non_sensitive_params_intact():
    out = redact_query_secrets("/api/x?model=gpt-x&token=zzz&format=openai_compat")
    assert out == "/api/x?model=gpt-x&token=REDACTED&format=openai_compat"


def test_redact_no_query_is_unchanged():
    assert redact_query_secrets("/api/settings") == "/api/settings"
    assert redact_query_secrets("") == ""


def test_redact_empty_query_after_qmark():
    assert redact_query_secrets("/api/x?") == "/api/x?"


def test_redact_handles_empty_secret_value():
    assert redact_query_secrets("/api/x?token=") == "/api/x?token=REDACTED"


def test_redact_is_case_insensitive_on_key_names():
    assert redact_query_secrets("/api/x?ApiKey=nope") == "/api/x?ApiKey=REDACTED"
    assert redact_query_secrets("/api/x?ACCESS_TOKEN=nope") == "/api/x?ACCESS_TOKEN=REDACTED"
    assert redact_query_secrets("/api/x?Secret=nope") == "/api/x?Secret=REDACTED"


def test_redact_masks_multiple_secrets():
    out = redact_query_secrets("/api/x?token=aaa&client_id=me&api_key=bbb")
    assert out == "/api/x?token=REDACTED&client_id=me&api_key=REDACTED"


# ---------------------------------------------------------------------------
# Regression: onboarding route must not leak the api_key value into logs
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_onboarding_route_does_not_log_api_key_value(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_path = tmp_path / "config.json"
    monkeypatch.setattr(get_runtime_context(), "config_path", config_path)

    secret = "sk-live-DO-NOT-LOG-me-4a2b7"
    path = (
        "/api/onboarding/save?provider_name=openai&format=openai_compat"
        f"&model=gpt-x&api_key={secret}"
    )
    request = WsRequest(path=path, headers=Headers())

    router = WebUISettingsRouter(
        bus=MagicMock(),
        logger=loguru_logger,
        check_api_token=lambda _req: True,
        parse_query=parse_query,
        json_response=http_json_response,
        error_response=http_error,
        session_manager=SessionManager(tmp_path),
        onboarding_event=asyncio.Event(),
    )

    captured: list[str] = []
    sink_id = loguru_logger.add(lambda m: captured.append(str(m)), level="DEBUG")
    try:
        await router._handle_onboarding_save(request)
    finally:
        loguru_logger.remove(sink_id)

    joined = "".join(captured)
    assert joined, "expected the onboarding route to emit at least one log line"
    assert secret not in joined
