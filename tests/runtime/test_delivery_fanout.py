"""Test del fan-out proattivo di ``ChannelDeliverer``: persistenza singola in
sessione, copia websocket sempre primaria, copie extra marcate ``_mirror``."""

from __future__ import annotations

import asyncio
from typing import Any

from jenny.bus.events import INTERNAL_CHANNEL, OutboundMessage
from jenny.bus.queue import MessageBus
from jenny.runtime.delivery import ChannelDeliverer
from jenny.session.keys import UNIFIED_SESSION_KEY
from jenny.webui.metadata import WEBUI_TURN_METADATA_KEY


class StubSession:
    def __init__(self) -> None:
        self.messages: list[tuple[str, str, dict[str, Any]]] = []

    def add_message(self, role: str, content: str, **extra: Any) -> None:
        self.messages.append((role, content, extra))


class StubSessionManager:
    def __init__(self) -> None:
        self.session = StubSession()
        self.keys: list[str] = []
        self.saves = 0

    def get_or_create(self, key: str) -> StubSession:
        self.keys.append(key)
        return self.session

    def save(self, session: Any) -> None:
        self.saves += 1


def _drain(bus: MessageBus) -> list[OutboundMessage]:
    out: list[OutboundMessage] = []
    while True:
        try:
            out.append(bus.outbound.get_nowait())
        except asyncio.QueueEmpty:
            return out


async def test_fanout_to_telegram_when_paired_persists_once() -> None:
    bus = MessageBus()
    sm = StubSessionManager()
    deliverer = ChannelDeliverer(
        bus=bus, session_manager=sm, extra_targets=lambda: [("telegram", "42")]
    )

    await deliverer.deliver(
        OutboundMessage(channel="websocket", chat_id="default", content="promemoria"),
        record=True,
        proactive=True,
    )

    published = _drain(bus)
    assert len(published) == 2
    by_channel = {m.channel: m for m in published}
    assert not by_channel["websocket"].metadata.get("_mirror")
    assert by_channel["telegram"].metadata.get("_mirror") is True
    assert by_channel["telegram"].chat_id == "42"
    # Il primo pubblicato è il primario websocket (scrive lui il transcript).
    assert published[0].channel == "websocket"
    # Sessione unificata: una sola riga persistita.
    assert sm.keys == [UNIFIED_SESSION_KEY]
    assert len(sm.session.messages) == 1
    assert sm.saves == 1


async def test_no_mirror_when_unpaired() -> None:
    bus = MessageBus()
    deliverer = ChannelDeliverer(
        bus=bus, session_manager=StubSessionManager(), extra_targets=lambda: []
    )
    await deliverer.deliver(
        OutboundMessage(channel="websocket", chat_id="default", content="x")
    )
    published = _drain(bus)
    assert len(published) == 1
    assert published[0].channel == "websocket"
    assert not published[0].metadata.get("_mirror")


async def test_telegram_targeted_message_still_gets_websocket_primary() -> None:
    """Un invio PROATTIVO esplicito a telegram passa da websocket come primario:
    è la copia websocket a scrivere la riga nel transcript WebUI."""
    bus = MessageBus()
    deliverer = ChannelDeliverer(
        bus=bus, session_manager=StubSessionManager(), extra_targets=lambda: []
    )
    await deliverer.deliver(
        OutboundMessage(channel="telegram", chat_id="42", content="diretto"),
        proactive=True,
    )
    published = _drain(bus)
    assert [m.channel for m in published] == ["websocket", "telegram"]
    assert not published[0].metadata.get("_mirror")
    assert published[1].metadata.get("_mirror") is True
    assert published[1].chat_id == "42"


async def test_no_fanout_without_proactive_intent() -> None:
    """Regressione del leak: una consegna nella conversazione corrente (senza
    intento proattivo) NON si diffonde alla chat Telegram accoppiata, anche se
    ``extra_targets`` la espone. Resta sul solo canale d'origine."""
    bus = MessageBus()
    deliverer = ChannelDeliverer(
        bus=bus,
        session_manager=StubSessionManager(),
        extra_targets=lambda: [("telegram", "42")],
    )
    await deliverer.deliver(
        OutboundMessage(
            channel="websocket", chat_id="default", content="ecco il gatto",
            media=["gatto.svg"],
        ),
        record=True,
    )
    published = _drain(bus)
    assert [m.channel for m in published] == ["websocket"]
    assert published[0].media == ["gatto.svg"]
    assert not published[0].metadata.get("_mirror")


async def test_proactive_fanout_via_metadata_flag() -> None:
    """Il flag ``_proactive_fanout`` nei metadata (impostato dal tool ``message``
    per invii cross-canale) attiva il fan-out ed è consumato dal deliverer."""
    bus = MessageBus()
    deliverer = ChannelDeliverer(
        bus=bus,
        session_manager=StubSessionManager(),
        extra_targets=lambda: [("telegram", "42")],
    )
    await deliverer.deliver(
        OutboundMessage(
            channel="websocket", chat_id="default", content="promemoria",
            metadata={"_proactive_fanout": True},
        )
    )
    published = _drain(bus)
    assert {m.channel for m in published} == {"websocket", "telegram"}
    # Il flag non deve sopravvivere nei messaggi pubblicati.
    for m in published:
        assert "_proactive_fanout" not in m.metadata


async def test_internal_channel_never_fans_out() -> None:
    bus = MessageBus()
    deliverer = ChannelDeliverer(
        bus=bus,
        session_manager=StubSessionManager(),
        extra_targets=lambda: [("telegram", "42")],
    )
    await deliverer.deliver(
        OutboundMessage(channel=INTERNAL_CHANNEL, chat_id="x", content="interno")
    )
    published = _drain(bus)
    assert len(published) == 1
    assert published[0].channel == INTERNAL_CHANNEL


async def test_legacy_behavior_without_extra_targets() -> None:
    """Senza ``extra_targets`` (test/costruttori legacy) il comportamento
    resta identico a prima: un solo publish del messaggio originale."""
    bus = MessageBus()
    deliverer = ChannelDeliverer(bus=bus, session_manager=StubSessionManager())
    msg = OutboundMessage(channel="telegram", chat_id="42", content="x")
    await deliverer.deliver(msg)
    published = _drain(bus)
    assert len(published) == 1
    assert published[0].channel == "telegram"
    assert not published[0].metadata.get("_mirror")


async def test_proactive_delivery_stamps_a_webui_turn_id() -> None:
    """Ogni consegna proattiva porta la chiave del turno WebUI.

    Senza di essa ``TranscriptRecorder._annotate_turn`` esce senza stampare
    ``turn_id``/``turn_phase``/``turn_seq`` sul record, e nel replay
    ``_same_turn`` considera quel record "stesso turno" di qualunque altro.
    Misurato sul dispositivo il 2026-08-13: quattro avvisi heartbeat scritti
    consecutivi nel transcript (righe 17720-17723) tutti con ``turn_id: None``,
    fra un turno utente e un turno cron che invece il proprio id l'avevano —
    e in chat ne compariva solo l'ultimo.
    """
    bus = MessageBus()
    deliverer = ChannelDeliverer(
        bus=bus, session_manager=StubSessionManager(),
        extra_targets=lambda: [("telegram", "42")],
    )
    await deliverer.deliver(
        OutboundMessage(channel="websocket", chat_id="default", content="avviso"),
        proactive=True,
    )
    published = _drain(bus)
    assert len(published) == 2
    # Il primario websocket è quello che scrive il transcript: l'id serve a lui.
    ids = {m.metadata.get(WEBUI_TURN_METADATA_KEY) for m in published}
    assert len(ids) == 1
    turn_id = ids.pop()
    assert isinstance(turn_id, str) and turn_id.startswith("proactive:")


async def test_each_proactive_delivery_gets_its_own_turn_id() -> None:
    """Due avvisi distinti sono due turni distinti: è l'asimmetria fra i loro id
    che impedisce al replay di fonderli."""
    bus = MessageBus()
    deliverer = ChannelDeliverer(bus=bus, session_manager=StubSessionManager())
    for text in ("primo", "secondo"):
        await deliverer.deliver(
            OutboundMessage(channel="websocket", chat_id="default", content=text),
            proactive=True,
        )
    published = _drain(bus)
    assert len(published) == 2
    first, second = (m.metadata[WEBUI_TURN_METADATA_KEY] for m in published)
    assert first != second


async def test_non_proactive_delivery_gets_no_turn_id() -> None:
    """Una consegna dentro la conversazione corrente eredita già l'id del turno
    dal canale: qui non si conia niente."""
    bus = MessageBus()
    deliverer = ChannelDeliverer(bus=bus, session_manager=StubSessionManager())
    await deliverer.deliver(
        OutboundMessage(channel="websocket", chat_id="default", content="risposta")
    )
    published = _drain(bus)
    assert WEBUI_TURN_METADATA_KEY not in published[0].metadata


async def test_existing_turn_id_is_preserved() -> None:
    """``cron_proactive_delivery_metadata`` conia già il proprio id per il monitor
    cron: il deliverer riempie solo il buco, non sovrascrive."""
    bus = MessageBus()
    deliverer = ChannelDeliverer(bus=bus, session_manager=StubSessionManager())
    await deliverer.deliver(
        OutboundMessage(
            channel="websocket", chat_id="default", content="promemoria",
            metadata={WEBUI_TURN_METADATA_KEY: "cron:job-1:abc"},
        ),
        proactive=True,
    )
    published = _drain(bus)
    assert published[0].metadata[WEBUI_TURN_METADATA_KEY] == "cron:job-1:abc"
