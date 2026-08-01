"""Proiezione di un turno Telegram sulla vista WebUI.

Il canale WebSocket è la vista canonica della conversazione unificata: il
runtime gli consegna l'eco del messaggio utente (``_user_echo``), il finale
con ``origin_channel`` e il ``turn_end``. Qui si verifica che quella sequenza
produca un transcript coerente (righe user/message/turn_end con lo stesso
turn id e la provenienza) e una storia servibile alla WebUI.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from jenny.bus.events import OutboundMessage
from jenny.bus.queue import MessageBus
from jenny.channels.websocket import WebSocketChannel, WebSocketConfig
from jenny.webui.gateway_services import build_gateway_services
from jenny.webui.transcript import build_webui_thread_response
from jenny.webui.transcript_store import read_transcript_lines


def _ws_channel(bus: MessageBus) -> WebSocketChannel:
    cfg: dict[str, Any] = {
        "enabled": True,
        "allowFrom": ["*"],
        "host": "127.0.0.1",
        "port": 29877,
        "path": "/ws",
        "websocketRequiresToken": False,
    }
    parsed = WebSocketConfig.model_validate(cfg)
    gateway = build_gateway_services(
        config=parsed,
        bus=bus,
        session_manager=None,
        workspace_path=Path.cwd(),
        default_restrict_to_workspace=False,
        runtime_model_name=None,
    )
    return WebSocketChannel(cfg, bus, gateway=gateway)


async def test_telegram_turn_projected_into_webui_thread(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("jenny.config.paths.get_data_dir", lambda: tmp_path)
    bus = MessageBus()
    ws = _ws_channel(bus)
    meta = {"webui_turn_id": "turn-tg-1"}

    # 1) Eco del messaggio utente (pubblicato dal WebuiTurnCoordinator).
    await ws.send(
        OutboundMessage(
            channel="websocket", chat_id="default", content="che ore sono?",
            metadata={**meta, "_user_echo": True, "origin_channel": "telegram"},
        )
    )
    # 2) Finale del turno proiettato dal dispatcher con la provenienza.
    await ws.send(
        OutboundMessage(
            channel="websocket", chat_id="default", content="Sono le 10",
            metadata={**meta, "origin_channel": "telegram", "latency_ms": 1200},
        )
    )
    # 3) turn_end pubblicato dal coordinator sulla vista.
    await ws.send(
        OutboundMessage(
            channel="websocket", chat_id="default", content="",
            metadata={**meta, "_turn_end": True},
        )
    )

    lines = read_transcript_lines("websocket:default")
    events = [row.get("event") for row in lines]
    assert events == ["user", "message", "turn_end"]
    assert lines[0]["origin"] == "telegram"
    assert lines[1]["origin"] == "telegram"
    # Le righe condividono lo stesso turn id (raggruppamento del turno in UI).
    assert lines[0]["turn_id"] == lines[1]["turn_id"] == lines[2]["turn_id"]
    assert [row["turn_seq"] for row in lines] == [1, 2, 3]

    # La storia servita alla WebUI contiene domanda e risposta con provenienza.
    payload = build_webui_thread_response("websocket:default")
    assert payload is not None
    blob = json.dumps(payload, ensure_ascii=False)
    assert "che ore sono?" in blob
    assert "Sono le 10" in blob
    messages = payload["messages"]
    user_rows = [m for m in messages if m.get("role") == "user"]
    assert user_rows and user_rows[0].get("origin") == "telegram"


async def test_user_echo_without_content_is_dropped(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("jenny.config.paths.get_data_dir", lambda: tmp_path)
    bus = MessageBus()
    ws = _ws_channel(bus)
    await ws.send(
        OutboundMessage(
            channel="websocket", chat_id="default", content="   ",
            metadata={"_user_echo": True, "origin_channel": "telegram"},
        )
    )
    assert read_transcript_lines("websocket:default") == []
