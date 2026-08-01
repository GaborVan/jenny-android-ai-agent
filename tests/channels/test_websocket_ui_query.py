"""Tests for the ui_query/ui_result wiring in the WebSocket channel.

Exercises the conn_id plumbing (ready event + message metadata), the new
``ui_result`` inbound branch, and disconnect cleanup.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from jenny.channels.websocket import WebSocketChannel, WebSocketConfig
from jenny.webui.gateway_services import build_gateway_services


def _make_channel(ui_query=None) -> WebSocketChannel:
    bus = MagicMock()
    bus.publish_inbound = AsyncMock()
    cfg = {"enabled": True, "allowFrom": ["*"], "websocketRequiresToken": False}
    parsed = WebSocketConfig.model_validate(cfg)
    gateway = build_gateway_services(
        config=parsed,
        bus=bus,
        session_manager=None,
        workspace_path=Path.cwd(),
        default_restrict_to_workspace=False,
        runtime_model_name=None,

    )
    channel = WebSocketChannel(cfg, bus, gateway=gateway, ui_query=ui_query)
    channel._handle_message = AsyncMock()  # type: ignore[method-assign]
    return channel


class _FakeConn:
    """Connessione WS finta: iterabile async che si chiude subito dopo ready."""

    def __init__(self, path: str = "/"):
        self.request = MagicMock(path=path)
        self.remote_address = ("127.0.0.1", 5000)
        self.sent: list[str] = []

    async def send(self, raw):
        self.sent.append(raw)

    def __aiter__(self):
        return self

    async def __anext__(self):
        raise StopAsyncIteration


async def test_ready_event_includes_conn_id():
    channel = _make_channel()
    conn = _FakeConn()
    await channel._connection_loop(conn)
    ready = json.loads(conn.sent[0])
    assert ready["event"] == "ready"
    assert isinstance(ready.get("conn_id"), str) and ready["conn_id"]
    # La connessione è pulita a fine loop: la mappa non deve trattenerla.
    assert conn not in channel._conn_ids


async def test_message_metadata_carries_conn_id():
    channel = _make_channel()
    conn = AsyncMock()
    channel._conn_ids[conn] = "conn-xyz"
    channel._conns_by_id["conn-xyz"] = conn

    await channel._dispatch_envelope(
        conn, "client-1", {"type": "message", "chat_id": "abc123", "content": "hi"}
    )

    channel._handle_message.assert_awaited_once()
    metadata = channel._handle_message.call_args.kwargs["metadata"]
    assert metadata["conn_id"] == "conn-xyz"


async def test_ui_result_routes_to_coordinator():
    coord = MagicMock()
    channel = _make_channel(ui_query=coord)
    conn = AsyncMock()
    channel._conn_ids[conn] = "conn-xyz"

    envelope = {"type": "ui_result", "correlation_id": "uiq-abc", "payload": {"view": "wiki"}}
    await channel._dispatch_envelope(conn, "client-1", envelope)

    coord.handle_ui_result.assert_called_once_with("conn-xyz", envelope)


async def test_ui_result_ignored_without_registered_conn():
    coord = MagicMock()
    channel = _make_channel(ui_query=coord)
    conn = AsyncMock()  # non registrata in _conn_ids

    await channel._dispatch_envelope(
        conn, "client-1", {"type": "ui_result", "correlation_id": "uiq-abc"}
    )
    coord.handle_ui_result.assert_not_called()


async def test_unknown_type_still_errors():
    channel = _make_channel()
    conn = AsyncMock()
    await channel._dispatch_envelope(conn, "client-1", {"type": "nope"})
    # Un tipo sconosciuto continua a produrre un evento di errore verso il client.
    sent = [json.loads(c.args[0]) for c in conn.send.call_args_list]
    assert any(m.get("event") == "error" for m in sent)


async def test_cleanup_cancels_pending_ui_queries():
    coord = MagicMock()
    channel = _make_channel(ui_query=coord)
    conn = AsyncMock()
    channel._conn_ids[conn] = "conn-xyz"
    channel._conns_by_id["conn-xyz"] = conn

    channel._cleanup_connection(conn)

    coord.cancel_for_conn.assert_called_once_with("conn-xyz")
    assert "conn-xyz" not in channel._conns_by_id
