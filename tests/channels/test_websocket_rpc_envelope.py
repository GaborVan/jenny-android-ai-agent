"""Il frame ``rpc`` end-to-end nel canale: envelope → comando → ``rpc_result``.

Copre la giuntura che i test di ``ws_rpc`` e di ``commands`` non toccano: che il
canale instradi il tipo ``rpc``, passi il ``CommandContext`` del gateway, usi il
verdetto dell'handshake come autorizzazione e risponda sulla stessa connessione.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from jenny.channels.websocket import WebSocketChannel, WebSocketConfig
from jenny.config.loader import save_config
from jenny.config.schema import Config
from jenny.runtime.context import get_runtime_context
from jenny.webui.gateway_services import build_gateway_services


class _FakeConnection:
    """Raccoglie i frame inviati dal canale su questa connessione."""

    def __init__(self) -> None:
        self.frames: list[dict[str, Any]] = []
        self.remote_address = ("127.0.0.1", 1234)

    async def send(self, raw: str) -> None:
        self.frames.append(json.loads(raw))


@pytest.fixture()
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "workspace"
    root.mkdir()
    config_path = tmp_path / "config.json"
    save_config(Config(), config_path)
    monkeypatch.setattr(get_runtime_context(), "config_path", config_path)
    monkeypatch.setattr("jenny.config.paths.get_workspace_path", lambda: root)
    return root


def _channel(workspace: Path, *, secret: str = "") -> WebSocketChannel:
    bus = MagicMock()
    bus.publish_inbound = AsyncMock()
    cfg = {
        "enabled": True,
        "allowFrom": ["*"],
        "websocketRequiresToken": False,
        "tokenIssueSecret": secret,
    }
    parsed = WebSocketConfig.model_validate(cfg)
    gateway = build_gateway_services(
        config=parsed,
        bus=bus,
        session_manager=None,
        workspace_path=workspace,
        default_restrict_to_workspace=False,
        runtime_model_name=None,
    )
    return WebSocketChannel(cfg, bus, gateway=gateway)


async def test_rpc_writes_the_file_and_answers_ok(workspace: Path) -> None:
    channel = _channel(workspace)
    conn = _FakeConnection()
    # Contenuto che il vecchio trasporto (header HTTP) non poteva spedire.
    content = "riga con emoji 😏 e accenti: perché città\n" * 300

    await channel._dispatch_envelope(conn, "client-1", {
        "type": "rpc",
        "id": "rpc-1",
        "method": "workspace.write",
        "params": {"path": "SOUL.md", "content": content},
    })

    assert conn.frames == [{
        "event": "rpc_result",
        "id": "rpc-1",
        "ok": True,
        "result": {"path": "SOUL.md", "bytes": len(content.encode("utf-8"))},
    }]
    assert (workspace / "SOUL.md").read_text(encoding="utf-8") == content


async def test_rpc_error_comes_back_as_a_result_frame(workspace: Path) -> None:
    channel = _channel(workspace)
    conn = _FakeConnection()

    await channel._dispatch_envelope(conn, "client-1", {
        "type": "rpc",
        "id": "rpc-2",
        "method": "workspace.write",
        "params": {"path": "../fuori.txt", "content": "x"},
    })

    frame = conn.frames[0]
    assert frame["ok"] is False
    assert frame["error"]["code"] == "bad_request"
    assert not (workspace.parent / "fuori.txt").exists()


async def test_unknown_method_answers_instead_of_raising(workspace: Path) -> None:
    channel = _channel(workspace)
    conn = _FakeConnection()

    await channel._dispatch_envelope(conn, "client-1", {
        "type": "rpc", "id": "rpc-3", "method": "workspace.explode", "params": {},
    })

    assert conn.frames[0]["error"]["code"] == "bad_request"


async def test_frame_without_a_usable_id_is_dropped_silently(workspace: Path) -> None:
    """Nessun id valido = nessuna risposta possibile: solo un log, non un crash."""
    channel = _channel(workspace)
    conn = _FakeConnection()

    await channel._dispatch_envelope(conn, "client-1", {
        "type": "rpc", "method": "workspace.write", "params": {},
    })

    assert conn.frames == []


async def test_bad_method_still_gets_a_reply_when_the_id_is_valid(workspace: Path) -> None:
    channel = _channel(workspace)
    conn = _FakeConnection()

    await channel._dispatch_envelope(conn, "client-1", {
        "type": "rpc", "id": "rpc-4", "params": {},
    })

    assert conn.frames[0]["id"] == "rpc-4"
    assert conn.frames[0]["error"]["code"] == "bad_request"


async def test_unauthenticated_connection_cannot_write_when_a_secret_is_set(
    workspace: Path,
) -> None:
    """L'autorizzazione è il verdetto dell'handshake, non un campo del frame."""
    channel = _channel(workspace, secret="s3cr3t")
    conn = _FakeConnection()

    await channel._dispatch_envelope(conn, "client-1", {
        "type": "rpc",
        "id": "rpc-5",
        "method": "workspace.write",
        "params": {"path": "a.txt", "content": "x"},
    })

    assert conn.frames[0]["error"]["code"] == "forbidden"
    assert not (workspace / "a.txt").exists()

    # Con l'handshake superato (token corretto) lo stesso frame passa.
    channel._conn_authed[conn] = True
    conn.frames.clear()
    await channel._dispatch_envelope(conn, "client-1", {
        "type": "rpc",
        "id": "rpc-6",
        "method": "workspace.write",
        "params": {"path": "a.txt", "content": "x"},
    })
    assert conn.frames[0]["ok"] is True
    assert (workspace / "a.txt").read_text(encoding="utf-8") == "x"


def test_handshake_records_the_verdict_and_cleanup_forgets_it(workspace: Path) -> None:
    channel = _channel(workspace, secret="s3cr3t")
    conn = _FakeConnection()

    assert channel._authorize_websocket_handshake(conn, {"token": ["s3cr3t"]}) is None
    assert channel._conn_authed[conn] is True
    channel._cleanup_connection(conn)
    assert conn not in channel._conn_authed

    # Token sbagliato: handshake respinto e nessuna voce lasciata dietro.
    conn.respond = MagicMock(return_value="401")  # type: ignore[attr-defined]
    assert channel._authorize_websocket_handshake(conn, {"token": ["wrong"]}) == "401"
    assert conn not in channel._conn_authed
