"""Test diretti per jenny/bus/progress.py.

``build_bus_progress_callback`` produce una callback che pubblica un
``OutboundMessage`` di progress sul bus, con i flag ``_progress``/``_tool_hint``
e i campi opzionali (reasoning, tool_events, file_edit_events) fusi nei
metadata del messaggio inbound originale. Copriamo qui la costruzione dei
metadata e il forwarding di canale/chat_id — non c'è throttling/logica di
stato nel modulo, solo mapping puro.
"""

from __future__ import annotations

from jenny.bus.events import InboundMessage
from jenny.bus.progress import build_bus_progress_callback
from jenny.bus.queue import MessageBus


def _inbound(**overrides) -> InboundMessage:
    defaults = dict(channel="websocket", sender_id="u1", chat_id="c1", content="ciao")
    defaults.update(overrides)
    return InboundMessage(**defaults)


async def test_default_call_sets_progress_flag_and_no_tool_hint() -> None:
    bus = MessageBus()
    callback = build_bus_progress_callback(bus, _inbound())

    await callback("testo parziale")

    out = await bus.consume_outbound()
    assert out.content == "testo parziale"
    assert out.metadata["_progress"] is True
    assert out.metadata["_tool_hint"] is False
    assert "_reasoning_delta" not in out.metadata
    assert "_reasoning_end" not in out.metadata
    assert "_tool_events" not in out.metadata
    assert "_file_edit_events" not in out.metadata


async def test_forwards_channel_and_chat_id_from_inbound_message() -> None:
    bus = MessageBus()
    msg = _inbound(channel="websocket", chat_id="room-7")
    callback = build_bus_progress_callback(bus, msg)

    await callback("hey")

    out = await bus.consume_outbound()
    assert out.channel == "websocket"
    assert out.chat_id == "room-7"


async def test_tool_hint_true_is_propagated() -> None:
    bus = MessageBus()
    callback = build_bus_progress_callback(bus, _inbound())

    await callback("chiamo un tool", tool_hint=True)

    out = await bus.consume_outbound()
    assert out.metadata["_tool_hint"] is True


async def test_reasoning_flag_sets_reasoning_delta_marker() -> None:
    bus = MessageBus()
    callback = build_bus_progress_callback(bus, _inbound())

    await callback("sto pensando...", reasoning=True)

    out = await bus.consume_outbound()
    assert out.metadata["_reasoning_delta"] is True
    assert "_reasoning_end" not in out.metadata


async def test_reasoning_end_flag_sets_reasoning_end_marker() -> None:
    bus = MessageBus()
    callback = build_bus_progress_callback(bus, _inbound())

    await callback("", reasoning_end=True)

    out = await bus.consume_outbound()
    assert out.metadata["_reasoning_end"] is True


async def test_reasoning_false_does_not_add_marker_key() -> None:
    """reasoning=False (default) non deve aggiungere la chiave, non solo essere falsy."""
    bus = MessageBus()
    callback = build_bus_progress_callback(bus, _inbound())

    await callback("testo", reasoning=False, reasoning_end=False)

    out = await bus.consume_outbound()
    assert "_reasoning_delta" not in out.metadata
    assert "_reasoning_end" not in out.metadata


async def test_tool_events_included_when_non_empty() -> None:
    bus = MessageBus()
    callback = build_bus_progress_callback(bus, _inbound())
    events = [{"name": "read_file", "status": "started"}]

    await callback("uso un tool", tool_events=events)

    out = await bus.consume_outbound()
    assert out.metadata["_tool_events"] == events


async def test_tool_events_empty_list_is_not_included() -> None:
    """Una lista vuota è falsy: la chiave non deve comparire nei metadata."""
    bus = MessageBus()
    callback = build_bus_progress_callback(bus, _inbound())

    await callback("niente tool", tool_events=[])

    out = await bus.consume_outbound()
    assert "_tool_events" not in out.metadata


async def test_file_edit_events_included_when_non_empty() -> None:
    bus = MessageBus()
    callback = build_bus_progress_callback(bus, _inbound())
    events = [{"path": "a.py", "action": "edit"}]

    await callback("modifico un file", file_edit_events=events)

    out = await bus.consume_outbound()
    assert out.metadata["_file_edit_events"] == events


async def test_original_inbound_metadata_is_preserved_and_not_mutated() -> None:
    """meta = dict(msg.metadata or {}) deve copiare, non condividere il dict originale."""
    bus = MessageBus()
    msg = _inbound(metadata={"reply_to": "msg-1"})
    callback = build_bus_progress_callback(bus, msg)

    await callback("ciao")

    out = await bus.consume_outbound()
    assert out.metadata["reply_to"] == "msg-1"
    assert out.metadata["_progress"] is True
    # Il dict originale del messaggio inbound non viene alterato.
    assert msg.metadata == {"reply_to": "msg-1"}


async def test_inbound_metadata_defaulting_to_empty_dict_does_not_raise() -> None:
    """InboundMessage.metadata di default è {} (mai None), ma la callback usa
    ``msg.metadata or {}`` difensivamente: verifichiamo che regga comunque."""
    bus = MessageBus()
    msg = _inbound()
    msg.metadata = None  # type: ignore[assignment]
    callback = build_bus_progress_callback(bus, msg)

    await callback("ciao")

    out = await bus.consume_outbound()
    assert out.metadata["_progress"] is True


async def test_all_optional_flags_combined() -> None:
    bus = MessageBus()
    callback = build_bus_progress_callback(bus, _inbound())
    tool_events = [{"name": "x"}]
    file_edit_events = [{"path": "y"}]

    await callback(
        "combo",
        tool_hint=True,
        tool_events=tool_events,
        file_edit_events=file_edit_events,
        reasoning=True,
        reasoning_end=True,
    )

    out = await bus.consume_outbound()
    assert out.metadata["_tool_hint"] is True
    assert out.metadata["_tool_events"] == tool_events
    assert out.metadata["_file_edit_events"] == file_edit_events
    assert out.metadata["_reasoning_delta"] is True
    assert out.metadata["_reasoning_end"] is True
