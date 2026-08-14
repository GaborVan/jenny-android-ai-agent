"""Il conteggio token dello streaming Anthropic arriva in due eventi, non uno.

``message_start`` porta gli input token e le voci di cache, ``message_delta``
porta gli output. Il path streaming leggeva solo il secondo, quindi
``prompt_tokens`` restava a zero: il contatore in WebUI e il context governor
sottostimavano ogni turno, e il risparmio del prompt caching — sempre attivo su
questo provider — non si vedeva da nessuna parte.

Il gemello per il path non-streaming è ``test_cached_tokens.py``.
"""

from __future__ import annotations

import json
from typing import Any

import httpx

from jenny.providers.anthropic_provider import AnthropicProvider
from jenny.providers.anthropic_usage import merge_raw_usage


async def _stream_usage(*events: tuple[str, dict[str, Any]]) -> dict[str, int]:
    body = "".join(
        f"event: {name}\ndata: {json.dumps(payload)}\n\n" for name, payload in events
    ).encode()
    provider = AnthropicProvider(api_key="k", api_base="https://api.example.com")
    provider._http_client = httpx.AsyncClient(
        base_url="https://api.example.com",
        transport=httpx.MockTransport(lambda _req: httpx.Response(
            200, content=body, headers={"content-type": "text/event-stream"},
        )),
    )
    response = await provider.chat_stream(messages=[{"role": "user", "content": "ciao"}])
    return response.usage


def _start(**usage: int) -> tuple[str, dict[str, Any]]:
    return ("message_start", {"type": "message_start", "message": {"usage": usage}})


def _delta(**usage: int) -> tuple[str, dict[str, Any]]:
    return ("message_delta", {
        "type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": usage,
    })


TEXT = ("content_block_delta", {
    "type": "content_block_delta", "index": 0,
    "delta": {"type": "text_delta", "text": "ciao"},
})


async def test_input_tokens_from_message_start_are_counted() -> None:
    usage = await _stream_usage(_start(input_tokens=100), TEXT, _delta(output_tokens=7))

    assert usage["prompt_tokens"] == 100
    assert usage["completion_tokens"] == 7
    assert usage["total_tokens"] == 107


async def test_cache_figures_survive_the_stream() -> None:
    """Sono i soli numeri da cui si vede se il prompt caching sta funzionando."""
    usage = await _stream_usage(
        _start(input_tokens=100, cache_read_input_tokens=50, cache_creation_input_tokens=20),
        TEXT,
        _delta(output_tokens=7),
    )

    # I token di cache sono token di prompt: tenerli fuori dal totale farebbe
    # sembrare il prompt molto più piccolo di com'è.
    assert usage["prompt_tokens"] == 170
    assert usage["cached_tokens"] == 50
    assert usage["cache_read_input_tokens"] == 50
    assert usage["cache_creation_input_tokens"] == 20
    assert usage["total_tokens"] == 177


async def test_a_gateway_that_repeats_usage_in_the_delta_still_adds_up() -> None:
    """Alcuni gateway ripetono il blocco intero: l'ultimo valore non-zero vince."""
    usage = await _stream_usage(
        _start(input_tokens=100),
        TEXT,
        _delta(input_tokens=100, output_tokens=7),
    )

    assert usage["prompt_tokens"] == 100
    assert usage["completion_tokens"] == 7


async def test_output_tokens_advance_across_several_deltas() -> None:
    usage = await _stream_usage(
        _start(input_tokens=10), TEXT, _delta(output_tokens=3), _delta(output_tokens=9),
    )

    assert usage["completion_tokens"] == 9


async def test_no_usage_at_all_stays_empty() -> None:
    """Un gateway che non manda conteggi non deve produrre numeri inventati."""
    usage = await _stream_usage(TEXT, ("message_delta", {
        "type": "message_delta", "delta": {"stop_reason": "end_turn"},
    }))

    assert usage == {}


# ── merge_raw_usage: la regola sullo zero, in isolamento ──────────────────

def test_a_zero_does_not_erase_a_known_value() -> None:
    target: dict[str, Any] = {"input_tokens": 100}
    merge_raw_usage(target, {"input_tokens": 0, "output_tokens": 5})

    assert target == {"input_tokens": 100, "output_tokens": 5}


def test_a_zero_is_still_recorded_when_nothing_is_known_yet() -> None:
    target: dict[str, Any] = {}
    merge_raw_usage(target, {"output_tokens": 0})

    assert target == {"output_tokens": 0}


def test_non_numeric_and_boolean_values_are_ignored() -> None:
    target: dict[str, Any] = {}
    merge_raw_usage(target, {"input_tokens": "many", "cached": True, "nested": {"a": 1}})

    assert target == {}


def test_a_missing_usage_block_is_harmless() -> None:
    target: dict[str, Any] = {"input_tokens": 1}
    merge_raw_usage(target, None)

    assert target == {"input_tokens": 1}
