"""Shared tool-argument parsing policy tests."""

import json

import httpx

from jenny.providers.anthropic_provider import AnthropicProvider
from jenny.providers.base import (
    parse_tool_arguments,
    tool_arguments_json_for_replay,
    tool_arguments_object_for_replay,
)


def test_parse_tool_arguments_preserves_malformed_executable_arguments() -> None:
    assert parse_tool_arguments('{path:"foo.txt"}') == '{path:"foo.txt"}'


def test_parse_tool_arguments_preserves_non_object_executable_arguments() -> None:
    assert parse_tool_arguments('["foo.txt"]') == ["foo.txt"]
    assert parse_tool_arguments("false") is False
    assert parse_tool_arguments("null") == "null"


def test_tool_arguments_object_for_replay_repairs_object_like_history_arguments() -> None:
    assert tool_arguments_object_for_replay('{path:"foo.txt"}') == {"path": "foo.txt"}


def test_tool_arguments_object_for_replay_keeps_history_object_shaped() -> None:
    for arguments in ['["foo.txt"]', "false", "null", "0", ["foo.txt"], False, None, 0]:
        assert tool_arguments_object_for_replay(arguments) == {}


def test_tool_arguments_json_for_replay_returns_object_string() -> None:
    assert tool_arguments_json_for_replay('{path:"foo.txt"}') == '{"path": "foo.txt"}'


async def test_anthropic_stream_does_not_repair_arguments_it_is_about_to_run() -> None:
    """Uno stream troncato non deve produrre argomenti inventati.

    La variante di replay ``tool_arguments_object_for_replay`` ripara il JSON
    malformato, ed è giusto per la history; su una tool call che sta per essere
    eseguita significherebbe eseguire parametri indovinati. Qui lo stream si
    interrompe a metà dell'``input_json_delta``: gli argomenti devono restare la
    stringa grezza, che il registry rifiuta.
    """
    events = [
        ("content_block_start", {
            "type": "content_block_start", "index": 0,
            "content_block": {"type": "tool_use", "id": "toolu_1", "name": "write_file"},
        }),
        ("content_block_delta", {
            "type": "content_block_delta", "index": 0,
            "delta": {"type": "input_json_delta", "partial_json": '{"path":"a.txt","content":"tron'},
        }),
    ]
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

    response = await provider.chat_stream(messages=[{"role": "user", "content": "scrivi"}])

    assert response.tool_calls[0].arguments == '{"path":"a.txt","content":"tron'
