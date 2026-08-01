import asyncio

import pytest

from jenny.bus.events import InboundMessage
from jenny.bus.runtime_events import (
    RuntimeEventBus,
    RuntimeEventContext,
    RuntimeEventPublisher,
    RuntimeModelChanged,
    SessionTurnStarted,
    TurnCompleted,
    TurnRunStatusChanged,
)


@pytest.mark.asyncio
async def test_runtime_event_bus_filters_by_event_type() -> None:
    bus = RuntimeEventBus()
    seen: list[str] = []

    async def handle_run_status(event: TurnRunStatusChanged) -> None:
        seen.append(event.status)

    bus.subscribe(handle_run_status, TurnRunStatusChanged)

    await bus.publish(RuntimeModelChanged(model="m", model_preset=None))
    await bus.publish(
        TurnRunStatusChanged(
            context=RuntimeEventContext(
                channel="internal",
                chat_id="direct",
                session_key="internal:direct",
            ),
            status="running",
        )
    )

    assert seen == ["running"]


@pytest.mark.asyncio
async def test_runtime_event_bus_keeps_catch_all_subscription() -> None:
    bus = RuntimeEventBus()
    seen: list[str] = []

    def handle_any(event) -> None:
        seen.append(type(event).__name__)

    bus.subscribe(handle_any)

    await bus.publish(RuntimeModelChanged(model="m", model_preset=None))

    assert seen == ["RuntimeModelChanged"]


@pytest.mark.asyncio
async def test_publish_nowait_tracks_task_and_discards_on_completion() -> None:
    bus = RuntimeEventBus()
    started = asyncio.Event()
    release = asyncio.Event()

    async def handle_any(event) -> None:
        started.set()
        await release.wait()

    bus.subscribe(handle_any)

    assert bus._bg_tasks == set()
    bus.publish_nowait(RuntimeModelChanged(model="m", model_preset=None))

    # The background task holds a strong reference in the tracking set while running.
    await started.wait()
    assert len(bus._bg_tasks) == 1
    (task,) = tuple(bus._bg_tasks)

    # Once the handler completes, the done-callback removes it from the set.
    release.set()
    await task
    await asyncio.sleep(0)
    assert bus._bg_tasks == set()


@pytest.mark.asyncio
async def test_runtime_event_publisher_builds_context_from_inbound_message() -> None:
    bus = RuntimeEventBus()
    seen: list[object] = []
    publisher = RuntimeEventPublisher(bus)
    msg = InboundMessage(
        channel="websocket",
        sender_id="user",
        chat_id="chat-a",
        content="hello",
        metadata={"trace_id": "turn-1"},
    )

    bus.subscribe(seen.append)

    await publisher.session_turn_started(msg, "websocket:chat-a")
    await publisher.run_status_changed(
        msg,
        "websocket:chat-a",
        "running",
        started_at=12.5,
    )

    started = seen[0]
    running = seen[1]
    assert isinstance(started, SessionTurnStarted)
    assert started.context.channel == "websocket"
    assert started.context.chat_id == "chat-a"
    assert started.context.session_key == "websocket:chat-a"
    assert started.context.metadata == {"trace_id": "turn-1"}
    assert started.context.metadata is not msg.metadata
    assert isinstance(running, TurnRunStatusChanged)
    assert running.status == "running"
    assert running.started_at == 12.5


@pytest.mark.asyncio
async def test_runtime_event_publisher_consumes_turn_metadata_on_complete() -> None:
    bus = RuntimeEventBus()
    seen: list[object] = []
    publisher = RuntimeEventPublisher(bus)

    bus.subscribe(seen.append)
    publisher.record_turn_runtime("internal:direct", "runtime")
    publisher.record_turn_latency("internal:direct", 123)

    await publisher.turn_completed(
        channel="internal",
        chat_id="direct",
        session_key="internal:direct",
        metadata={"source": "test"},
    )
    await publisher.turn_completed(
        channel="internal",
        chat_id="direct",
        session_key="internal:direct",
        metadata=None,
    )

    first = seen[0]
    second = seen[1]
    assert isinstance(first, TurnCompleted)
    assert first.context.metadata == {"source": "test"}
    assert first.latency_ms == 123
    assert first.runtime == "runtime"
    assert isinstance(second, TurnCompleted)
    assert second.latency_ms is None
    assert second.runtime is None
