"""Test per ``jenny.agent.cron_turns.CronTurnCoordinator``.

Copre la costruzione/gestione dei turni cron: submit (publish vs dispatch a
seconda dello stato del loop), completamento della future in attesa, il
deferral quando la sessione target è già attiva, e il tracciamento dei job id
pendenti/in coda usati per evitare sovrapposizioni.
"""

from __future__ import annotations

import asyncio

import pytest

from jenny.agent.cron_turns import CronTurnCoordinator
from jenny.agent.turn_types import TurnOutcome
from jenny.bus.events import InboundMessage, OutboundMessage
from jenny.cron.session_turns import CRON_DEFER_UNTIL_IDLE_META, CRON_TRIGGER_META


def _cron_msg(
    *,
    run_id: str | None = "run-1",
    job_id: str | None = "job-1",
    defer: bool = False,
    session_key_override: str | None = None,
    channel: str = "internal",
    chat_id: str = "chat-1",
    content: str = "cron prompt",
) -> InboundMessage:
    """Costruisce un InboundMessage con metadata di trigger cron."""
    trigger: dict = {}
    if run_id is not None:
        trigger["run_id"] = run_id
    if job_id is not None:
        trigger["job_id"] = job_id
    metadata = {CRON_TRIGGER_META: trigger}
    if defer:
        metadata[CRON_DEFER_UNTIL_IDLE_META] = True
    return InboundMessage(
        channel=channel,
        sender_id="cron",
        chat_id=chat_id,
        content=content,
        metadata=metadata,
        session_key_override=session_key_override,
    )


def _coordinator(*, is_running: bool = False):
    """Costruisce un coordinator con mock di publish/dispatch che registrano le chiamate."""
    published: list[InboundMessage] = []
    dispatched: list[InboundMessage] = []

    async def publish_inbound(msg: InboundMessage) -> None:
        published.append(msg)

    async def dispatch(msg: InboundMessage) -> object:
        dispatched.append(msg)
        return None

    coordinator = CronTurnCoordinator(
        publish_inbound=publish_inbound,
        dispatch=dispatch,
        is_running=lambda: is_running,
    )
    return coordinator, published, dispatched


class TestSubmit:
    """``submit`` instrada verso publish o dispatch e attende la risposta."""

    async def test_submit_missing_run_id_raises_value_error(self):
        coordinator, _, _ = _coordinator()
        msg = _cron_msg(run_id=None)
        with pytest.raises(ValueError, match="run_id"):
            await coordinator.submit(msg)

    async def test_submit_while_running_publishes_inbound_not_dispatch(self):
        coordinator, published, dispatched = _coordinator(is_running=True)
        msg = _cron_msg(run_id="r1")
        task = asyncio.create_task(coordinator.submit(msg))
        await asyncio.sleep(0)
        assert published == [msg]
        assert dispatched == []
        response = OutboundMessage(channel="internal", chat_id="chat-1", content="done")
        coordinator.complete(msg, outcome=TurnOutcome.delivered(response))
        result = await task
        assert result.message is response

    async def test_submit_while_idle_dispatches_directly(self):
        coordinator, published, dispatched = _coordinator(is_running=False)
        msg = _cron_msg(run_id="r2")
        task = asyncio.create_task(coordinator.submit(msg))
        await asyncio.sleep(0)
        assert dispatched == [msg]
        assert published == []
        coordinator.complete(msg, outcome=TurnOutcome.silent())
        result = await task
        assert result.message is None

    async def test_submit_duplicate_run_id_while_pending_raises_runtime_error(self):
        coordinator, _, _ = _coordinator(is_running=True)
        msg = _cron_msg(run_id="dup")
        task = asyncio.create_task(coordinator.submit(msg))
        await asyncio.sleep(0)
        with pytest.raises(RuntimeError, match="dup"):
            await coordinator.submit(_cron_msg(run_id="dup"))
        # Pulizia: completa il primo per non lasciare task pendenti.
        coordinator.complete(msg, outcome=TurnOutcome.silent())
        await task

    async def test_submit_error_propagates_as_exception(self):
        coordinator, _, _ = _coordinator(is_running=True)
        msg = _cron_msg(run_id="err")
        task = asyncio.create_task(coordinator.submit(msg))
        await asyncio.sleep(0)
        coordinator.complete(msg, error=RuntimeError("boom"))
        with pytest.raises(RuntimeError, match="boom"):
            await task

    async def test_waiters_and_pending_cleared_after_completion(self):
        coordinator, _, _ = _coordinator(is_running=True)
        msg = _cron_msg(run_id="cleanup")
        task = asyncio.create_task(coordinator.submit(msg))
        await asyncio.sleep(0)
        assert "cleanup" in coordinator._waiters
        coordinator.complete(msg, outcome=TurnOutcome.silent())
        await task
        assert "cleanup" not in coordinator._waiters
        assert "cleanup" not in coordinator._pending_messages_by_run_id


class TestComplete:
    """``complete`` è tollerante verso run_id sconosciuti o già risolti."""

    def test_complete_unknown_run_id_is_noop(self):
        coordinator, _, _ = _coordinator()
        msg = _cron_msg(run_id="ghost")
        coordinator.complete(msg, outcome=TurnOutcome.silent())  # non deve sollevare

    def test_complete_missing_run_id_is_noop(self):
        coordinator, _, _ = _coordinator()
        msg = _cron_msg(run_id=None)
        coordinator.complete(msg, outcome=TurnOutcome.silent())  # non deve sollevare

    async def test_complete_twice_second_call_is_noop(self):
        coordinator, _, _ = _coordinator(is_running=True)
        msg = _cron_msg(run_id="twice")
        task = asyncio.create_task(coordinator.submit(msg))
        await asyncio.sleep(0)
        response = OutboundMessage(channel="internal", chat_id="chat-1", content="first")
        coordinator.complete(msg, outcome=TurnOutcome.delivered(response))
        # La seconda complete (stesso run_id, future già done) non deve alzare eccezioni.
        coordinator.complete(msg, outcome=TurnOutcome.delivered(OutboundMessage(
            channel="internal", chat_id="chat-1", content="second"
        )))
        result = await task
        assert result.message is response


class TestShouldDeferAndDeferIfActive:
    """Deferral quando la sessione target è già attiva."""

    def test_should_defer_false_without_defer_flag(self):
        coordinator, _, _ = _coordinator()
        msg = _cron_msg(defer=False)
        assert coordinator.should_defer(
            msg, session_key=msg.session_key, active_session_keys=[msg.session_key]
        ) is False

    def test_should_defer_false_when_session_not_active(self):
        coordinator, _, _ = _coordinator()
        msg = _cron_msg(defer=True)
        assert coordinator.should_defer(
            msg, session_key=msg.session_key, active_session_keys=["other:session"]
        ) is False

    def test_should_defer_true_when_flagged_and_active(self):
        coordinator, _, _ = _coordinator()
        msg = _cron_msg(defer=True)
        assert coordinator.should_defer(
            msg, session_key=msg.session_key, active_session_keys=[msg.session_key]
        ) is True

    def test_defer_if_active_queues_message_and_returns_true(self):
        coordinator, _, _ = _coordinator()
        msg = _cron_msg(defer=True, channel="internal", chat_id="chat-1")
        deferred = coordinator.defer_if_active(
            msg, session_key=msg.session_key, active_session_keys=[msg.session_key]
        )
        assert deferred is True
        assert coordinator.deferred_queues[msg.session_key] == [msg]

    def test_defer_if_active_returns_false_and_does_not_queue_when_not_deferring(self):
        coordinator, _, _ = _coordinator()
        msg = _cron_msg(defer=False)
        deferred = coordinator.defer_if_active(
            msg, session_key=msg.session_key, active_session_keys=[msg.session_key]
        )
        assert deferred is False
        assert coordinator.deferred_queues == {}

    def test_defer_if_active_overrides_session_key_when_different(self):
        """Se la sessione target differisce da quella nativa del msg, il
        messaggio in coda deve portare il session_key_override giusto."""
        coordinator, _, _ = _coordinator()
        msg = _cron_msg(defer=True, channel="internal", chat_id="chat-1")
        target_session = "websocket:other-thread"
        assert target_session != msg.session_key

        deferred = coordinator.defer_if_active(
            msg, session_key=target_session, active_session_keys=[target_session]
        )

        assert deferred is True
        queued = coordinator.deferred_queues[target_session][0]
        assert queued.session_key == target_session
        assert queued is not msg


class TestPendingJobIdsForSession:
    """Traccia i job id in coda o in volo per una data sessione."""

    def test_includes_job_ids_from_deferred_queue(self):
        coordinator, _, _ = _coordinator()
        msg = _cron_msg(job_id="job-a", session_key_override="s1")
        coordinator.defer("s1", msg)
        assert coordinator.pending_job_ids_for_session("s1") == {"job-a"}

    def test_excludes_job_ids_from_other_sessions(self):
        coordinator, _, _ = _coordinator()
        coordinator.defer("s1", _cron_msg(job_id="job-a", session_key_override="s1"))
        coordinator.defer("s2", _cron_msg(job_id="job-b", session_key_override="s2"))
        assert coordinator.pending_job_ids_for_session("s1") == {"job-a"}

    async def test_includes_job_ids_from_in_flight_submissions(self):
        coordinator, _, _ = _coordinator(is_running=True)
        msg = _cron_msg(run_id="inflight", job_id="job-c", session_key_override="s3")
        task = asyncio.create_task(coordinator.submit(msg))
        await asyncio.sleep(0)
        assert coordinator.pending_job_ids_for_session("s3") == {"job-c"}
        coordinator.complete(msg, outcome=TurnOutcome.silent())
        await task
        # Dopo il completamento, il job non è più "in volo".
        assert coordinator.pending_job_ids_for_session("s3") == set()

    def test_no_job_id_in_trigger_is_ignored(self):
        coordinator, _, _ = _coordinator()
        msg = _cron_msg(job_id=None, session_key_override="s1")
        coordinator.defer("s1", msg)
        assert coordinator.pending_job_ids_for_session("s1") == set()

    def test_unknown_session_returns_empty_set(self):
        coordinator, _, _ = _coordinator()
        assert coordinator.pending_job_ids_for_session("nope") == set()


class TestPublishNextDeferred:
    """FIFO: un solo elemento in coda viene pubblicato per chiamata."""

    async def test_publishes_first_queued_message(self):
        coordinator, published, _ = _coordinator()
        first = _cron_msg(run_id="d1", session_key_override="s1")
        second = _cron_msg(run_id="d2", session_key_override="s1")
        coordinator.defer("s1", first)
        coordinator.defer("s1", second)

        await coordinator.publish_next_deferred("s1")

        assert published == [first]
        assert coordinator.deferred_queues["s1"] == [second]

    async def test_removes_session_key_when_queue_empties(self):
        coordinator, published, _ = _coordinator()
        only = _cron_msg(run_id="d3", session_key_override="s1")
        coordinator.defer("s1", only)

        await coordinator.publish_next_deferred("s1")

        assert published == [only]
        assert "s1" not in coordinator.deferred_queues

    async def test_empty_or_missing_queue_is_noop(self):
        coordinator, published, _ = _coordinator()
        await coordinator.publish_next_deferred("no-such-session")
        assert published == []
