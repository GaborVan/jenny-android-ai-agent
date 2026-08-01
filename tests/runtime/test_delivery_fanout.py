"""Test del fan-out proattivo di ``ChannelDeliverer``: persistenza singola in
sessione, copia websocket sempre primaria, copie extra marcate ``_mirror``."""

from __future__ import annotations

import asyncio
from typing import Any

from jenny.bus.events import INTERNAL_CHANNEL, OutboundMessage
from jenny.bus.queue import MessageBus
from jenny.runtime.delivery import ChannelDeliverer
from jenny.session.keys import UNIFIED_SESSION_KEY


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
