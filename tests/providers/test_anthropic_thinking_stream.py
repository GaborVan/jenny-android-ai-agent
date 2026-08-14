"""I blocchi thinking devono sopravvivere allo streaming, firma inclusa.

Con extended thinking attivo e tool use nello stesso turno, l'API pretende di
riavere indietro i blocchi thinking *firmati* di quel turno. Il parse
non-streaming li conservava, quello streaming no: non leggeva affatto
``signature_delta``, quindi ``thinking_blocks`` arrivava vuoto al runner e la
conversione non aveva niente da rimandare.

Nota sulla portata di questi test: la forma dell'onda qui è quella documentata
da Anthropic, scritta a mano. Non dimostrano che un dato gateway la emetta —
per questo il caso "nessuna firma" (in fondo) è quello che conta davvero: dice
che se quei delta non arrivano si torna esattamente al comportamento di prima,
non a una richiesta rifiutata.
"""

from __future__ import annotations

import json
from typing import Any

import httpx

from jenny.providers.anthropic_provider import AnthropicProvider
from jenny.utils.helpers import build_assistant_message, extract_reasoning


def _sse(*events: tuple[str, dict[str, Any]]) -> bytes:
    return "".join(
        f"event: {name}\ndata: {json.dumps(payload)}\n\n" for name, payload in events
    ).encode()


async def _stream(*events: tuple[str, dict[str, Any]]):
    provider = AnthropicProvider(api_key="k", api_base="https://api.example.com")
    provider._http_client = httpx.AsyncClient(
        base_url="https://api.example.com",
        transport=httpx.MockTransport(lambda _req: httpx.Response(
            200, content=_sse(*events), headers={"content-type": "text/event-stream"},
        )),
    )
    return await provider.chat_stream(messages=[{"role": "user", "content": "pensa"}])


THINKING_START = ("content_block_start", {
    "type": "content_block_start", "index": 0,
    "content_block": {"type": "thinking", "thinking": ""},
})
THINKING_DELTA = ("content_block_delta", {
    "type": "content_block_delta", "index": 0,
    "delta": {"type": "thinking_delta", "thinking": "ragiono un attimo"},
})
SIGNATURE_DELTA = ("content_block_delta", {
    "type": "content_block_delta", "index": 0,
    "delta": {"type": "signature_delta", "signature": "EqoBCkgIA=="},
})
BLOCK_STOP = ("content_block_stop", {"type": "content_block_stop", "index": 0})
TOOL_START = ("content_block_start", {
    "type": "content_block_start", "index": 1,
    "content_block": {"type": "tool_use", "id": "toolu_x", "name": "read_file"},
})
TOOL_DELTA = ("content_block_delta", {
    "type": "content_block_delta", "index": 1,
    "delta": {"type": "input_json_delta", "partial_json": '{"path":"a"}'},
})
STOP = ("message_delta", {"type": "message_delta", "delta": {"stop_reason": "tool_use"}})


async def test_a_signed_thinking_block_survives_the_stream() -> None:
    response = await _stream(
        THINKING_START, THINKING_DELTA, SIGNATURE_DELTA, BLOCK_STOP,
        TOOL_START, TOOL_DELTA, STOP,
    )

    assert response.thinking_blocks == [{
        "type": "thinking",
        "thinking": "ragiono un attimo",
        "signature": "EqoBCkgIA==",
    }]


async def test_the_signature_is_reassembled_from_several_deltas() -> None:
    response = await _stream(
        THINKING_START,
        THINKING_DELTA,
        ("content_block_delta", {
            "type": "content_block_delta", "index": 0,
            "delta": {"type": "signature_delta", "signature": "EqoB"},
        }),
        ("content_block_delta", {
            "type": "content_block_delta", "index": 0,
            "delta": {"type": "signature_delta", "signature": "CkgIA=="},
        }),
        BLOCK_STOP, STOP,
    )

    assert response.thinking_blocks[0]["signature"] == "EqoBCkgIA=="


async def test_thinking_still_reaches_the_user_as_before() -> None:
    """Il testo mostrato a schermo passa da ``reasoning_content``: non cambia."""
    response = await _stream(THINKING_START, THINKING_DELTA, SIGNATURE_DELTA, BLOCK_STOP, STOP)

    assert response.reasoning_content == "ragiono un attimo"
    reasoning_text, _ = extract_reasoning(
        response.reasoning_content, response.thinking_blocks, response.content,
    )
    assert reasoning_text == "ragiono un attimo"


async def test_a_redacted_block_is_carried_through_opaque() -> None:
    response = await _stream(
        ("content_block_start", {
            "type": "content_block_start", "index": 0,
            "content_block": {"type": "redacted_thinking", "data": "AbCdEf=="},
        }),
        BLOCK_STOP, STOP,
    )

    assert response.thinking_blocks == [{"type": "redacted_thinking", "data": "AbCdEf=="}]


async def test_an_unsigned_block_degrades_instead_of_being_rejected() -> None:
    """Il caso che rende sicuro il cambio senza vedere l'onda vera.

    Se un gateway manda il pensiero ma non la firma, un blocco non firmato
    rimandato indietro sarebbe un 400. Scartarlo riporta esattamente al
    comportamento precedente: reasoning visibile, nessun blocco da replayare.
    """
    response = await _stream(THINKING_START, THINKING_DELTA, BLOCK_STOP, STOP)

    assert response.thinking_blocks is None
    assert response.reasoning_content == "ragiono un attimo"


async def test_no_thinking_at_all_stays_empty() -> None:
    response = await _stream(
        ("content_block_start", {
            "type": "content_block_start", "index": 0,
            "content_block": {"type": "text", "text": ""},
        }),
        ("content_block_delta", {
            "type": "content_block_delta", "index": 0,
            "delta": {"type": "text_delta", "text": "ciao"},
        }),
        ("message_delta", {"type": "message_delta", "delta": {"stop_reason": "end_turn"}}),
    )

    assert response.thinking_blocks is None
    assert response.content == "ciao"


# ── Round-trip: dal blocco firmato al body della richiesta successiva ─────

def test_the_replayed_turn_puts_thinking_before_the_tool_use() -> None:
    """L'ordine dei blocchi è quello in cui il modello li ha prodotti."""
    provider = AnthropicProvider(api_key="k", api_base="https://api.example.com")
    history = [
        {"role": "user", "content": "leggi"},
        build_assistant_message(
            "",
            tool_calls=[{"id": "toolu_x", "type": "function",
                         "function": {"name": "read_file", "arguments": '{"path":"a"}'}}],
            thinking_blocks=[{"type": "thinking", "thinking": "ragiono", "signature": "sig"}],
        ),
        {"role": "tool", "tool_call_id": "toolu_x", "content": "contenuto"},
        {"role": "user", "content": "ok"},
    ]

    kwargs = provider._build_kwargs(history, None, "claude-sonnet-4-6", 1024, 0.7, "high", None)
    assistant = next(m for m in kwargs["messages"] if m["role"] == "assistant")
    types = [block["type"] for block in assistant["content"]]

    assert types == ["thinking", "tool_use"]
    assert assistant["content"][0]["signature"] == "sig"


def test_an_unsigned_block_in_history_is_dropped_on_the_way_out() -> None:
    """Una history vecchia può contenere blocchi senza firma: non vanno spediti."""
    provider = AnthropicProvider(api_key="k", api_base="https://api.example.com")
    history = [
        {"role": "user", "content": "leggi"},
        build_assistant_message(
            "risposta",
            thinking_blocks=[{"type": "thinking", "thinking": "ragiono", "signature": ""}],
        ),
        {"role": "user", "content": "ok"},
    ]

    kwargs = provider._build_kwargs(history, None, "claude-sonnet-4-6", 1024, 0.7, "high", None)
    assistant = next(m for m in kwargs["messages"] if m["role"] == "assistant")

    assert [block["type"] for block in assistant["content"]] == ["text"]
