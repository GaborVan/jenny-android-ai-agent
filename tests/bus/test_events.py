"""Test diretti per jenny/bus/events.py: dataclass e costanti degli eventi.

InboundMessage/OutboundMessage sono ampiamente usati (e implicitamente
esercitati) in molti altri test, ma nessuno testa qui il *contratto* del
dataclass in sé (default, mutabilità, session_key override). Questo file
copre solo quello.
"""

from __future__ import annotations

from datetime import datetime

from jenny.bus.events import (
    INTERNAL_CHANNEL,
    OUTBOUND_META_AGENT_UI,
    InboundMessage,
    OutboundMessage,
)

# ---------------------------------------------------------------------------
# Costanti
# ---------------------------------------------------------------------------


def test_internal_channel_constant_value() -> None:
    assert INTERNAL_CHANNEL == "internal"


def test_outbound_meta_agent_ui_constant_value() -> None:
    assert OUTBOUND_META_AGENT_UI == "_agent_ui"


# ---------------------------------------------------------------------------
# InboundMessage
# ---------------------------------------------------------------------------


def test_inbound_message_required_fields() -> None:
    msg = InboundMessage(channel="websocket", sender_id="u1", chat_id="c1", content="ciao")
    assert msg.channel == "websocket"
    assert msg.sender_id == "u1"
    assert msg.chat_id == "c1"
    assert msg.content == "ciao"


def test_inbound_message_default_media_and_metadata_are_empty() -> None:
    msg = InboundMessage(channel="websocket", sender_id="u1", chat_id="c1", content="ciao")
    assert msg.media == []
    assert msg.metadata == {}


def test_inbound_message_default_factories_are_independent_per_instance() -> None:
    """I default_factory di dataclass devono creare liste/dict distinti per istanza."""
    a = InboundMessage(channel="websocket", sender_id="u1", chat_id="c1", content="a")
    b = InboundMessage(channel="websocket", sender_id="u1", chat_id="c1", content="b")
    a.media.append("img.png")
    a.metadata["k"] = "v"
    assert b.media == []
    assert b.metadata == {}


def test_inbound_message_default_timestamp_is_datetime() -> None:
    msg = InboundMessage(channel="websocket", sender_id="u1", chat_id="c1", content="ciao")
    assert isinstance(msg.timestamp, datetime)


def test_inbound_message_session_key_default_combines_channel_and_chat_id() -> None:
    msg = InboundMessage(channel="websocket", sender_id="u1", chat_id="room-42", content="ciao")
    assert msg.session_key == "websocket:room-42"


def test_inbound_message_session_key_override_wins() -> None:
    msg = InboundMessage(
        channel="websocket",
        sender_id="u1",
        chat_id="room-42",
        content="ciao",
        session_key_override="unified:default",
    )
    assert msg.session_key == "unified:default"


def test_inbound_message_session_key_override_none_falls_back() -> None:
    msg = InboundMessage(
        channel="cron",
        sender_id="system",
        chat_id="job-1",
        content="tick",
        session_key_override=None,
    )
    assert msg.session_key == "cron:job-1"


def test_inbound_message_internal_channel_used_for_non_delivered_messages() -> None:
    """Documenta l'uso previsto di INTERNAL_CHANNEL come canale per cron/Dream/subagent."""
    msg = InboundMessage(
        channel=INTERNAL_CHANNEL, sender_id="cron", chat_id="job-1", content="tick"
    )
    assert msg.session_key == "internal:job-1"


# ---------------------------------------------------------------------------
# OutboundMessage
# ---------------------------------------------------------------------------


def test_outbound_message_required_fields_and_defaults() -> None:
    msg = OutboundMessage(channel="websocket", chat_id="c1", content="risposta")
    assert msg.media == []
    assert msg.metadata == {}
    assert msg.buttons == []


def test_outbound_message_default_factories_are_independent_per_instance() -> None:
    a = OutboundMessage(channel="websocket", chat_id="c1", content="a")
    b = OutboundMessage(channel="websocket", chat_id="c1", content="b")
    a.metadata["k"] = "v"
    a.buttons.append(["btn"])
    assert b.metadata == {}
    assert b.buttons == []


def test_outbound_message_metadata_carries_agent_ui_payload() -> None:
    msg = OutboundMessage(
        channel="websocket",
        chat_id="c1",
        content="",
        metadata={OUTBOUND_META_AGENT_UI: {"kind": "card", "title": "x"}},
    )
    assert msg.metadata[OUTBOUND_META_AGENT_UI] == {"kind": "card", "title": "x"}
