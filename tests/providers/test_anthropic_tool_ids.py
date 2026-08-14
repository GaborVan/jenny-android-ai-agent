"""Due tool call dello stesso turno non possono condividere un id.

GLM dietro un endpoint Anthropic-compatible riusa lo stesso ``call_…`` per le
chiamate parallele. Fino a qui il ramo Anthropic lo rimandava indietro verbatim:
la richiesta successiva veniva rifiutata (``duplicate tool_call id``) e il
messaggio, ormai persistito, murava la conversazione a ogni turno seguente.

L'invariante è la stessa che il ramo OpenAI-compat applica già, con la stessa
semantica: **la prima occorrenza tiene l'id del wire, i duplicati vengono
rinominati** (vedi ``test_custom_provider.py``). I test qui sono i suoi gemelli.

Un id duplicato non è solo un problema di wire, ed è il motivo per cui la
deduplica sta in parsing: a valle quell'id è una chiave, e due chiamate che la
condividono si sovrascrivono a vicenda (ultimo test del file).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx

from jenny.providers.anthropic_provider import AnthropicProvider
from jenny.utils.helpers import maybe_persist_tool_result


def _provider(handler=None) -> AnthropicProvider:
    provider = AnthropicProvider(api_key="k", api_base="https://api.example.com")
    if handler is not None:
        provider._http_client = httpx.AsyncClient(
            base_url="https://api.example.com",
            transport=httpx.MockTransport(handler),
        )
    return provider


def _sse(*events: tuple[str, dict[str, Any]]) -> bytes:
    return "".join(
        f"event: {name}\ndata: {json.dumps(payload)}\n\n" for name, payload in events
    ).encode()


def _tool_use_stream(call_id: str, other_id: str) -> bytes:
    """Due ``read_file`` in parallelo, con gli id che il gateway ci consegna."""
    return _sse(
        ("message_start", {"type": "message_start", "message": {"usage": {"input_tokens": 10}}}),
        ("content_block_start", {
            "type": "content_block_start", "index": 0,
            "content_block": {"type": "tool_use", "id": call_id, "name": "read_file"},
        }),
        ("content_block_delta", {
            "type": "content_block_delta", "index": 0,
            "delta": {"type": "input_json_delta", "partial_json": '{"path":"a.pdf"}'},
        }),
        ("content_block_stop", {"type": "content_block_stop", "index": 0}),
        ("content_block_start", {
            "type": "content_block_start", "index": 1,
            "content_block": {"type": "tool_use", "id": other_id, "name": "read_file"},
        }),
        ("content_block_delta", {
            "type": "content_block_delta", "index": 1,
            "delta": {"type": "input_json_delta", "partial_json": '{"path":"b.pdf"}'},
        }),
        ("content_block_stop", {"type": "content_block_stop", "index": 1}),
        ("message_delta", {"type": "message_delta", "delta": {"stop_reason": "tool_use"}}),
        ("message_stop", {"type": "message_stop"}),
    )


async def _stream_tool_calls(call_id: str, other_id: str):
    body = _tool_use_stream(call_id, other_id)
    provider = _provider(lambda _req: httpx.Response(
        200, content=body, headers={"content-type": "text/event-stream"},
    ))
    response = await provider.chat_stream(messages=[{"role": "user", "content": "leggi"}])
    return response.tool_calls


# ── Parsing: streaming ────────────────────────────────────────────────────

async def test_streaming_parallel_calls_do_not_share_an_id() -> None:
    tool_calls = await _stream_tool_calls("call_dup", "call_dup")
    ids = [tc.id for tc in tool_calls]

    assert len(ids) == 2
    assert len(set(ids)) == 2
    # La prima tiene l'id del wire: è quello che i consumatori dei delta hanno
    # già visto passare mentre lo stream correva.
    assert ids[0] == "call_dup"


async def test_streaming_keeps_the_arguments_with_their_own_call() -> None:
    """Rinominare un id non deve rimescolare gli argomenti fra le chiamate."""
    tool_calls = await _stream_tool_calls("call_dup", "call_dup")

    assert [tc.arguments["path"] for tc in tool_calls] == ["a.pdf", "b.pdf"]


async def test_streaming_leaves_distinct_ids_alone() -> None:
    tool_calls = await _stream_tool_calls("call_a", "call_b")

    assert [tc.id for tc in tool_calls] == ["call_a", "call_b"]


async def test_streaming_substitutes_are_deterministic() -> None:
    """Stesso stream, stessi id: un id casuale invaliderebbe il prompt cache."""
    first = await _stream_tool_calls("call_dup", "call_dup")
    second = await _stream_tool_calls("call_dup", "call_dup")

    assert [tc.id for tc in first] == [tc.id for tc in second]


# ── Parsing: non-streaming ────────────────────────────────────────────────

def test_non_streaming_parallel_calls_do_not_share_an_id() -> None:
    response = AnthropicProvider._parse_response_dict({
        "content": [
            {"type": "tool_use", "id": "call_dup", "name": "read_file", "input": {"path": "a.pdf"}},
            {"type": "tool_use", "id": "call_dup", "name": "read_file", "input": {"path": "b.pdf"}},
        ],
        "stop_reason": "tool_use",
    })
    ids = [tc.id for tc in response.tool_calls]

    assert ids[0] == "call_dup"
    assert len(set(ids)) == 2
    assert [tc.arguments["path"] for tc in response.tool_calls] == ["a.pdf", "b.pdf"]


def test_non_streaming_still_reads_text_and_tools_together() -> None:
    response = AnthropicProvider._parse_response_dict({
        "content": [
            {"type": "text", "text": "guardo i file"},
            {"type": "tool_use", "id": "u1", "name": "read_file", "input": {"path": "a"}},
        ],
        "stop_reason": "tool_use",
    })

    assert response.content == "guardo i file"
    assert [tc.id for tc in response.tool_calls] == ["u1"]
    assert response.finish_reason == "tool_calls"


# ── Invio: risanamento di una history già avvelenata ──────────────────────

POISONED_HISTORY: list[dict[str, Any]] = [
    {"role": "user", "content": "leggi i due pdf"},
    {"role": "assistant", "content": "", "tool_calls": [
        {"id": "call_dup", "type": "function",
         "function": {"name": "read_file", "arguments": '{"path":"a.pdf"}'}},
        {"id": "call_dup", "type": "function",
         "function": {"name": "read_file", "arguments": '{"path":"b.pdf"}'}},
    ]},
    {"role": "tool", "tool_call_id": "call_dup", "name": "read_file", "content": "contenuto A"},
    {"role": "tool", "tool_call_id": "call_dup", "name": "read_file", "content": "contenuto B"},
    {"role": "user", "content": "che è successo?"},
]


def _blocks(kwargs: dict[str, Any], block_type: str) -> list[dict[str, Any]]:
    return [
        block
        for message in kwargs["messages"]
        for block in message["content"]
        if isinstance(block, dict) and block.get("type") == block_type
    ]


def _build(history: list[dict[str, Any]]) -> dict[str, Any]:
    return _provider()._build_kwargs(
        history, None, "claude-sonnet-4-20250514", 1024, 0.7, None, None,
    )


def test_a_poisoned_history_is_repaired_on_the_way_out() -> None:
    """Senza questo, la conversazione resta murata: ogni turno rimanda il duplicato."""
    kwargs = _build(POISONED_HISTORY)
    tool_use_ids = [block["id"] for block in _blocks(kwargs, "tool_use")]

    assert len(tool_use_ids) == 2
    assert len(set(tool_use_ids)) == 2


def test_the_repaired_results_still_point_at_their_own_call() -> None:
    kwargs = _build(POISONED_HISTORY)
    tool_use_ids = [block["id"] for block in _blocks(kwargs, "tool_use")]
    results = _blocks(kwargs, "tool_result")

    # L'accoppiamento è in FIFO sull'id originale, nell'ordine in cui il runner
    # accoda i risultati: A appartiene alla prima chiamata, B alla seconda.
    assert [block["tool_use_id"] for block in results] == tool_use_ids
    assert [block["content"] for block in results] == ["contenuto A", "contenuto B"]


def test_repair_is_stable_across_requests() -> None:
    """Id diversi a ogni richiesta = prefisso in cache freddo a ogni richiesta."""
    first = [block["id"] for block in _blocks(_build(POISONED_HISTORY), "tool_use")]
    second = [block["id"] for block in _blocks(_build(POISONED_HISTORY), "tool_use")]

    assert first == second


def test_a_healthy_history_passes_through_untouched() -> None:
    healthy: list[dict[str, Any]] = [
        {"role": "user", "content": "leggi"},
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "toolu_one", "type": "function",
             "function": {"name": "read_file", "arguments": "{}"}},
            {"id": "toolu_two", "type": "function",
             "function": {"name": "list_dir", "arguments": "{}"}},
        ]},
        {"role": "tool", "tool_call_id": "toolu_one", "content": "A"},
        {"role": "tool", "tool_call_id": "toolu_two", "content": "B"},
        {"role": "user", "content": "ok"},
    ]
    kwargs = _build(healthy)

    assert [block["id"] for block in _blocks(kwargs, "tool_use")] == ["toolu_one", "toolu_two"]
    assert [
        block["tool_use_id"] for block in _blocks(kwargs, "tool_result")
    ] == ["toolu_one", "toolu_two"]


# ── Il danno a valle, che è il motivo per cui si deduplica in parsing ─────

async def test_two_large_results_no_longer_collide_on_disk(tmp_path: Path) -> None:
    """Il payload persistito è indicizzato per id: due id uguali, un file solo.

    ``maybe_persist_tool_result`` scrive in ``{tool_call_id}.txt`` e non
    sovrascrive un file esistente, quindi con l'id duplicato la seconda lettura
    si ritrovava il contenuto della prima — il modello riceveva il PDF sbagliato
    senza che niente segnalasse l'errore.
    """
    tool_calls = await _stream_tool_calls("call_dup", "call_dup")
    payloads = ["A" * 4000, "B" * 4000]

    for tool_call, payload in zip(tool_calls, payloads):
        maybe_persist_tool_result(
            tmp_path, "unified:default", tool_call.id, payload, max_chars=100,
        )

    stored = sorted(
        path.read_text() for path in (tmp_path / ".jenny" / "tool-results").rglob("*.txt")
    )
    assert stored == payloads
