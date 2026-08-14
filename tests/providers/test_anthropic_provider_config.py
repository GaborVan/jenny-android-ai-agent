"""La config di un provider Anthropic deve arrivare dove serve.

``extraBody``, ``extraQuery`` e ``apiType`` sono nello schema per ogni provider,
ma la factory li passava solo al ramo OpenAI-compat: con ``format: anthropic``
venivano buttati senza un rigo di log. ``extraHeaders``, l'unico che arrivava,
finiva anche nel CORPO della richiesta — residuo dell'epoca SDK, dove era un
kwarg del client — e l'API rifiuta i campi di body che non conosce.

Copre anche ``tool_choice``, che veniva ignorato su due valori dei quattro che
lo schema di config ammette (``config/schema.py::AgentDefaults``).
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from loguru import logger as loguru_logger

from jenny.config.schema import Config
from jenny.providers.anthropic_provider import AnthropicProvider
from jenny.providers.endpoint_budget import (
    DEFAULT_REQUEST_TIMEOUT_S,
    LOCAL_REQUEST_TIMEOUT_S,
)
from jenny.providers.factory import make_provider

MESSAGES = [{"role": "user", "content": "ciao"}]
TOOLS = [{"function": {"name": "read_file", "parameters": {"type": "object"}}}]


def _provider(**kwargs: Any) -> AnthropicProvider:
    return AnthropicProvider(api_key="k", api_base="https://api.example.com", **kwargs)


def _kwargs(provider: AnthropicProvider, *, tools=None, tool_choice=None, effort=None):
    return provider._build_kwargs(
        MESSAGES, tools, "claude-sonnet-4-6", 1024, 0.7, effort, tool_choice,
    )


# ── extra_headers: negli header, non nel body ─────────────────────────────

def test_extra_headers_reach_the_headers() -> None:
    provider = _provider(extra_headers={"x-team": "jenny"})

    assert provider._http_client.headers["x-team"] == "jenny"


def test_extra_headers_stay_out_of_the_request_body() -> None:
    """Un campo di body sconosciuto è un 400, non un header."""
    provider = _provider(extra_headers={"x-team": "jenny"})

    assert "extra_headers" not in _kwargs(provider)


# ── extra_body / extra_query / api_type ───────────────────────────────────

def test_extra_body_is_merged_into_the_request() -> None:
    provider = _provider(extra_body={"metadata": {"user_id": "u1"}})

    assert _kwargs(provider)["metadata"] == {"user_id": "u1"}


def test_extra_body_merges_without_clobbering_siblings() -> None:
    """Merge ricorsivo: una chiave annidata non cancella le vicine."""
    provider = _provider(extra_body={"thinking": {"budget_tokens": 2048}})

    thinking = _kwargs(provider, effort="low")["thinking"]
    assert thinking == {"type": "enabled", "budget_tokens": 2048}


def test_extra_body_can_override_a_computed_default() -> None:
    provider = _provider(extra_body={"temperature": 0.1})

    assert _kwargs(provider)["temperature"] == 0.1


async def test_extra_query_is_sent_on_the_request() -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(dict(request.url.params))
        return httpx.Response(200, json={
            "content": [{"type": "text", "text": "ok"}], "stop_reason": "end_turn",
        })

    provider = _provider(extra_query={"api-version": "2024-01"})
    provider._http_client = httpx.AsyncClient(
        base_url="https://api.example.com", transport=httpx.MockTransport(handler),
    )
    await provider.chat(messages=MESSAGES)

    assert seen == {"api-version": "2024-01"}


def _warnings_while(build: Any) -> list[str]:
    records: list[str] = []
    handler_id = loguru_logger.add(lambda m: records.append(str(m)), level="WARNING")
    try:
        build()
    finally:
        loguru_logger.remove(handler_id)
    return records


def test_a_meaningless_api_type_is_reported_not_swallowed() -> None:
    """``apiType`` sceglie fra due dialetti OpenAI: qui non vuol dire niente."""
    records = _warnings_while(lambda: _provider(api_type="responses"))

    assert any("apiType" in line for line in records)


def test_the_default_api_type_says_nothing() -> None:
    records = _warnings_while(lambda: _provider(api_type="auto"))

    assert not any("apiType" in line for line in records)


# ── tool_choice ───────────────────────────────────────────────────────────

def test_tool_choice_none_removes_the_tools() -> None:
    """Lasciare i tool con tool_choice assente = default auto = non rispettarlo."""
    kwargs = _kwargs(_provider(), tools=TOOLS, tool_choice="none")

    assert "tools" not in kwargs
    assert "tool_choice" not in kwargs


def test_tool_choice_none_is_respected_even_with_thinking_on() -> None:
    kwargs = _kwargs(_provider(), tools=TOOLS, tool_choice="none", effort="high")

    assert "tools" not in kwargs


@pytest.mark.parametrize("value", ["any", "required"])
def test_tool_choice_any_and_required_both_force_a_call(value: str) -> None:
    kwargs = _kwargs(_provider(), tools=TOOLS, tool_choice=value)

    assert kwargs["tool_choice"] == {"type": "any"}


def test_tool_choice_auto_is_unchanged() -> None:
    kwargs = _kwargs(_provider(), tools=TOOLS, tool_choice="auto")

    assert kwargs["tool_choice"] == {"type": "auto"}
    assert kwargs["tools"]


# ── La factory deve consegnarli ───────────────────────────────────────────

def test_the_factory_hands_the_anthropic_provider_its_config() -> None:
    config = Config.model_validate({
        "providers": {
            "providers": [{
                "name": "zai",
                "format": "anthropic",
                "apiKey": "k",
                "apiBase": "https://api.z.ai/api/anthropic",
                "extraBody": {"metadata": {"user_id": "u1"}},
                "extraQuery": {"api-version": "2024-01"},
            }],
            "default": "zai",
        },
    })

    provider = make_provider(config)

    assert isinstance(provider, AnthropicProvider)
    assert provider._extra_body == {"metadata": {"user_id": "u1"}}
    assert provider._extra_query == {"api-version": "2024-01"}


# ── Budget HTTP: la regola è quella condivisa, non un 120 scritto a mano ──

def test_a_remote_endpoint_gets_the_tight_timeout() -> None:
    provider = _provider()

    assert provider._http_client.timeout.read == DEFAULT_REQUEST_TIMEOUT_S


def test_a_loopback_endpoint_gets_the_long_timeout() -> None:
    """Un model server locale macina il prompt in silenzio per minuti."""
    provider = AnthropicProvider(api_key="k", api_base="http://127.0.0.1:11434")

    assert provider._http_client.timeout.read == LOCAL_REQUEST_TIMEOUT_S


def test_the_http_timeout_can_be_raised_by_env(monkeypatch) -> None:
    """Senza questo, il budget lungo per il primo token era irraggiungibile."""
    monkeypatch.setenv("JENNY_LLM_HTTP_TIMEOUT_S", "450")
    provider = _provider()

    assert provider._http_client.timeout.read == 450.0


def test_the_historic_env_name_still_works_for_both(monkeypatch) -> None:
    monkeypatch.setenv("JENNY_OPENAI_COMPAT_TIMEOUT_S", "45")
    provider = _provider()

    assert provider._http_client.timeout.read == 45.0
