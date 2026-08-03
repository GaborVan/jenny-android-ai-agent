"""Il reasoning si persiste una volta per segmento, non per chunk.

Misurato su un transcript reale: 111.660 record per rendere 160 messaggi, di cui
109.051 ``reasoning_delta`` su 77 segmenti — ~1.400 record dove ne basta uno. Il
primo caricamento della chat ci metteva 4,5 s perché ogni passata a valle
riattraversava tutti quei record.

Il test che conta è ``test_coalesced_and_chunked_replay_are_equivalent``: cambia
il formato su disco, quindi va dimostrato che l'utente vede la stessa cosa.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from jenny.webui.transcript_replay import replay_transcript_to_ui_messages


class _RecordingTranscripts:
    """Cattura ciò che finisce nel transcript, nell'ordine in cui ci finisce."""

    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []

    def prepare_and_append(self, chat_id, event, *, metadata=None, phase=None, **kw) -> None:
        self.rows.append(dict(event))

    def client_turn_metadata(self, value):  # pragma: no cover - non usato qui
        return {}


@pytest.fixture
def channel():
    from jenny.channels.websocket import WebSocketChannel

    bus = MagicMock()
    bus.publish_inbound = AsyncMock()
    ch = WebSocketChannel.__new__(WebSocketChannel)
    ch._subs = {}
    ch._transcripts = _RecordingTranscripts()
    ch._stream_text_buffers = {}
    ch._reasoning_text_buffers = {}
    return ch


def _events(channel) -> list[str]:
    return [r.get("event") for r in channel._transcripts.rows]


async def test_chunks_are_not_persisted_individually(channel):
    for i in range(50):
        await channel.send_reasoning_delta("c", f"chunk{i}", {"_stream_id": "r1"})

    assert channel._transcripts.rows == []


async def test_segment_becomes_one_record_on_reasoning_end(channel):
    for i in range(50):
        await channel.send_reasoning_delta("c", f"[{i}]", {"_stream_id": "r1"})
    await channel.send_reasoning_end("c", {"_stream_id": "r1"})

    assert _events(channel) == ["reasoning_delta", "reasoning_end"]
    joined = channel._transcripts.rows[0]
    assert joined["text"] == "".join(f"[{i}]" for i in range(50))
    assert joined["stream_id"] == "r1"


async def test_coalesced_and_chunked_replay_are_equivalent(channel):
    """Il fold deve produrre lo stesso messaggio dalle due forme.

    ``attach_reasoning_chunk`` accumula, quindi un record col testo intero e N
    record coi pezzi convergono. Se questo test cade, il cambio di formato è
    visibile all'utente e va rivisto.
    """
    chunks = [f"pensiero {i}. " for i in range(30)]
    for chunk in chunks:
        await channel.send_reasoning_delta("c", chunk, {"_stream_id": "r1"})
    await channel.send_reasoning_end("c", {"_stream_id": "r1"})
    coalesced_rows = list(channel._transcripts.rows)

    chunked_rows = [
        {"event": "reasoning_delta", "chat_id": "c", "text": chunk, "stream_id": "r1"}
        for chunk in chunks
    ] + [{"event": "reasoning_end", "chat_id": "c", "stream_id": "r1"}]

    def _reasoning(rows):
        msgs = replay_transcript_to_ui_messages(rows)
        return [(m.get("role"), m.get("reasoning"), m.get("content")) for m in msgs]

    assert _reasoning(coalesced_rows) == _reasoning(chunked_rows)


async def test_turn_end_flushes_a_segment_left_open(channel):
    """Un turno interrotto non deve perdere il pensiero.

    Senza il flush, un errore del provider fra l'ultimo chunk e ``reasoning_end``
    lascerebbe il segmento solo in memoria — mentre prima, un chunk per volta,
    veniva salvato comunque.
    """
    await channel.send_reasoning_delta("c", "pensavo che", {"_stream_id": "r1"})
    await channel.send_turn_end("c", 1234)

    assert _events(channel) == ["reasoning_delta", "turn_end"]
    assert channel._transcripts.rows[0]["text"] == "pensavo che"


async def test_flush_is_idempotent(channel):
    """Un ``reasoning_end`` doppio non duplica il record."""
    await channel.send_reasoning_delta("c", "x", {"_stream_id": "r1"})
    await channel.send_reasoning_end("c", {"_stream_id": "r1"})
    await channel.send_reasoning_end("c", {"_stream_id": "r1"})

    assert _events(channel) == ["reasoning_delta", "reasoning_end", "reasoning_end"]


async def test_segments_stay_separate_per_stream(channel):
    """Due segmenti nello stesso turno restano due record distinti."""
    await channel.send_reasoning_delta("c", "primo", {"_stream_id": "r1"})
    await channel.send_reasoning_end("c", {"_stream_id": "r1"})
    await channel.send_reasoning_delta("c", "secondo", {"_stream_id": "r2"})
    await channel.send_reasoning_end("c", {"_stream_id": "r2"})

    texts = [r["text"] for r in channel._transcripts.rows if r["event"] == "reasoning_delta"]
    assert texts == ["primo", "secondo"]


async def test_skip_persist_does_not_buffer(channel):
    """Un retry di consegna non deve ri-accumulare il testo.

    ``skip_persist`` marca la riconsegna alle connessioni mancate: se scrivesse
    di nuovo nel buffer, il segmento uscirebbe duplicato.
    """
    await channel.send_reasoning_delta("c", "una volta", {"_stream_id": "r1"})
    await channel.send_reasoning_delta("c", "una volta", {"_stream_id": "r1"}, skip_persist=True)
    await channel.send_reasoning_end("c", {"_stream_id": "r1"})

    assert channel._transcripts.rows[0]["text"] == "una volta"


async def test_live_delivery_is_still_per_chunk(channel):
    """La coalescenza è solo persistenza: lo stream ai client non cambia."""
    ws = AsyncMock()
    channel._subs = {"c": [ws]}
    for i in range(5):
        await channel.send_reasoning_delta("c", f"[{i}]", {"_stream_id": "r1"})

    assert ws.send.await_count == 5
