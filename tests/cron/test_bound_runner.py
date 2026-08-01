"""Test per jenny/cron/bound_runner.py.

Copre ``run_bound_cron_job`` (validazione, esito ok/errore, gestione del
contesto ``CronTool``, metadata webui/trigger) e ``is_bound_cron_job``
(``jenny/cron/session_turns.py``): quest'ultima è il gate booleano usato da
``jenny/runtime/cron_dispatch.py`` subito prima di invocare
``run_bound_cron_job`` e non ha un file di test proprio, quindi la copriamo
qui insieme al runner che la consuma concettualmente.
"""

from __future__ import annotations

import re
from typing import Any

import pytest

from jenny.agent.tools.cron import CronTool
from jenny.agent.tools.registry import ToolRegistry
from jenny.bus.events import InboundMessage, OutboundMessage
from jenny.cron.bound_runner import run_bound_cron_job
from jenny.cron.session_turns import (
    CRON_DEFER_UNTIL_IDLE_META,
    CRON_TRIGGER_META,
    is_bound_cron_job,
)
from jenny.cron.types import CronJob, CronPayload
from jenny.utils.prompt_templates import render_template
from jenny.webui.metadata import WEBUI_MESSAGE_SOURCE_METADATA_KEY, WEBUI_TURN_METADATA_KEY


def _bound_job(
    *,
    job_id: str = "job-1",
    name: str = "Promemoria",
    message: str = "annaffia le piante",
    origin_channel: str | None = "websocket",
    origin_chat_id: str | None = "chat-1",
    origin_metadata: dict[str, Any] | None = None,
    session_key: str | None = "unified:default",
) -> CronJob:
    return CronJob(
        id=job_id,
        name=name,
        payload=CronPayload(
            kind="agent_turn",
            message=message,
            session_key=session_key,
            origin_channel=origin_channel,
            origin_chat_id=origin_chat_id,
            origin_metadata=dict(origin_metadata or {}),
        ),
    )


class _FakeAgent:
    """Agente finto: registra i messaggi ricevuti e restituisce (o solleva) una risposta."""

    def __init__(
        self,
        *,
        tools: ToolRegistry | None = None,
        response: str | None = "fatto",
        error: Exception | None = None,
        events: list[str] | None = None,
    ) -> None:
        self.tools = tools if tools is not None else ToolRegistry()
        self._response = response
        self._error = error
        self.events = events if events is not None else []
        self.received: list[InboundMessage] = []

    async def submit_cron_turn(self, msg: InboundMessage) -> OutboundMessage | None:
        self.events.append("submit_cron_turn")
        self.received.append(msg)
        if self._error is not None:
            raise self._error
        if self._response is None:
            return None
        return OutboundMessage(channel=msg.channel, chat_id=msg.chat_id, content=self._response)


class _FakeCronRecorder:
    """Registra le run_record scritte, senza persistere nulla su disco."""

    def __init__(self) -> None:
        self.records: list[tuple[str, dict[str, Any]]] = []

    def write_run_record(self, run_id: str, record: dict[str, Any]) -> None:
        self.records.append((run_id, dict(record)))


class _SpyCronTool(CronTool):
    """CronTool reale (serve per l'isinstance) che registra set/reset del contesto."""

    def __init__(self, events: list[str]) -> None:
        super().__init__(cron_service=object())  # cron_service non è usato da questi metodi
        self._events = events

    def set_cron_context(self, active: bool):  # type: ignore[override]
        self._events.append(f"set_cron_context:{active}")
        return super().set_cron_context(active)

    def reset_cron_context(self, token) -> None:  # type: ignore[override]
        self._events.append("reset_cron_context")
        super().reset_cron_context(token)


class TestIsBoundCronJob:
    def test_true_for_complete_agent_turn_payload(self) -> None:
        assert is_bound_cron_job(_bound_job()) is True

    def test_false_for_system_event_kind(self) -> None:
        job = _bound_job()
        job.payload.kind = "system_event"
        assert is_bound_cron_job(job) is False

    def test_false_without_session_key(self) -> None:
        job = _bound_job(session_key=None)
        assert is_bound_cron_job(job) is False

    def test_false_without_origin_channel(self) -> None:
        job = _bound_job(origin_channel=None)
        assert is_bound_cron_job(job) is False

    def test_false_without_origin_chat_id(self) -> None:
        job = _bound_job(origin_chat_id=None)
        assert is_bound_cron_job(job) is False


class TestRunBoundCronJobValidation:
    async def test_missing_session_key_raises_value_error(self) -> None:
        job = _bound_job(session_key=None)
        agent = _FakeAgent()
        cron = _FakeCronRecorder()

        with pytest.raises(ValueError, match="missing payload.session_key"):
            await run_bound_cron_job(job, agent=agent, cron=cron)


class TestRunBoundCronJobSuccess:
    async def test_records_queued_then_ok_and_returns_response(self) -> None:
        job = _bound_job(job_id="job-42", name="Annaffia", message="annaffia le piante")
        agent = _FakeAgent(response="Fatto, annaffiato.")
        cron = _FakeCronRecorder()

        result = await run_bound_cron_job(job, agent=agent, cron=cron)

        assert result == "Fatto, annaffiato."
        statuses = [record["status"] for _run_id, record in cron.records]
        assert statuses == ["queued", "ok"]
        run_id_queued, _record_queued = cron.records[0]
        run_id_ok, record_ok = cron.records[1]
        assert run_id_queued == run_id_ok
        assert re.match(r"^job-42:\d+:[0-9a-f]{8}$", run_id_queued)
        assert record_ok["response"] == "Fatto, annaffiato."
        assert record_ok["job_id"] == "job-42"
        assert record_ok["job_name"] == "Annaffia"
        assert record_ok["session_key"] == "unified:default"
        expected_prompt = render_template(
            "agent/cron_reminder.md", strip=True, message="annaffia le piante"
        )
        assert record_ok["rendered_prompt"] == expected_prompt

    async def test_none_response_records_ok_with_empty_string(self) -> None:
        job = _bound_job()
        agent = _FakeAgent(response=None)
        cron = _FakeCronRecorder()

        result = await run_bound_cron_job(job, agent=agent, cron=cron)

        assert result == ""
        assert cron.records[-1][1]["response"] == ""
        assert cron.records[-1][1]["status"] == "ok"

    async def test_inbound_message_uses_session_key_override_and_origin(self) -> None:
        job = _bound_job(
            origin_channel="websocket", origin_chat_id="chat-9", session_key="unified:default"
        )
        agent = _FakeAgent()
        cron = _FakeCronRecorder()

        await run_bound_cron_job(job, agent=agent, cron=cron)

        received = agent.received[0]
        assert received.session_key_override == "unified:default"
        assert received.channel == "websocket"
        assert received.chat_id == "chat-9"
        assert received.sender_id == "cron"

    async def test_metadata_carries_cron_trigger_and_defer_flag(self) -> None:
        job = _bound_job(job_id="job-7", name="Sveglia", message="sveglia")
        agent = _FakeAgent()
        cron = _FakeCronRecorder()

        await run_bound_cron_job(job, agent=agent, cron=cron)

        metadata = agent.received[0].metadata
        trigger = metadata[CRON_TRIGGER_META]
        assert trigger["job_id"] == "job-7"
        assert trigger["job_name"] == "Sveglia"
        assert trigger["persist_content"] == "Scheduled cron job triggered: Sveglia\n\nsveglia"
        assert trigger["prompt_ref"]["id"] == "cron.agent_turn.reminder"
        assert trigger["prompt_ref"]["version"] == 1
        assert metadata[CRON_DEFER_UNTIL_IDLE_META] is True

    async def test_websocket_channel_adds_webui_metadata(self) -> None:
        job = _bound_job(origin_channel="websocket")
        agent = _FakeAgent()
        cron = _FakeCronRecorder()

        await run_bound_cron_job(job, agent=agent, cron=cron)

        metadata = agent.received[0].metadata
        assert metadata["webui"] is True
        assert metadata[WEBUI_TURN_METADATA_KEY].startswith(f"cron:{job.id}:")
        assert metadata[WEBUI_MESSAGE_SOURCE_METADATA_KEY] == {
            "kind": "cron",
            "label": job.name,
        }

    async def test_non_websocket_channel_has_no_webui_metadata(self) -> None:
        job = _bound_job(origin_channel="other-channel")
        agent = _FakeAgent()
        cron = _FakeCronRecorder()

        await run_bound_cron_job(job, agent=agent, cron=cron)

        metadata = agent.received[0].metadata
        assert "webui" not in metadata
        assert WEBUI_TURN_METADATA_KEY not in metadata
        assert WEBUI_MESSAGE_SOURCE_METADATA_KEY not in metadata


class TestRunBoundCronJobCronContext:
    async def test_cron_tool_context_set_before_and_reset_after_turn(self) -> None:
        events: list[str] = []
        registry = ToolRegistry()
        registry.register(_SpyCronTool(events))
        job = _bound_job()
        agent = _FakeAgent(tools=registry, events=events)
        cron = _FakeCronRecorder()

        await run_bound_cron_job(job, agent=agent, cron=cron)

        assert events == ["set_cron_context:True", "submit_cron_turn", "reset_cron_context"]

    async def test_missing_cron_tool_does_not_crash(self) -> None:
        job = _bound_job()
        agent = _FakeAgent(tools=ToolRegistry())
        cron = _FakeCronRecorder()

        result = await run_bound_cron_job(job, agent=agent, cron=cron)

        assert result == "fatto"

    async def test_non_cron_tool_object_under_cron_name_is_ignored(self) -> None:
        registry = ToolRegistry()

        class _NotACronTool:
            name = "cron"

        # Iniezione diretta nel dict interno: `register()` richiede un'istanza
        # di `Tool`, qui invece serve un oggetto qualunque per esercitare il
        # ramo isinstance-negativo (nessuna gestione del contesto cron).
        registry._tools["cron"] = _NotACronTool()  # type: ignore[assignment]
        job = _bound_job()
        agent = _FakeAgent(tools=registry)
        cron = _FakeCronRecorder()

        result = await run_bound_cron_job(job, agent=agent, cron=cron)

        assert result == "fatto"


class TestRunBoundCronJobError:
    async def test_error_writes_error_record_and_reraises(self) -> None:
        job = _bound_job(job_id="job-err")
        agent = _FakeAgent(error=RuntimeError("provider down"))
        cron = _FakeCronRecorder()

        with pytest.raises(RuntimeError, match="provider down"):
            await run_bound_cron_job(job, agent=agent, cron=cron)

        statuses = [record["status"] for _run_id, record in cron.records]
        assert statuses == ["queued", "error"]
        assert cron.records[-1][1]["error"] == "provider down"

    async def test_error_without_message_uses_exception_class_name(self) -> None:
        job = _bound_job()
        agent = _FakeAgent(error=RuntimeError())
        cron = _FakeCronRecorder()

        with pytest.raises(RuntimeError):
            await run_bound_cron_job(job, agent=agent, cron=cron)

        assert cron.records[-1][1]["error"] == "RuntimeError"

    async def test_cron_context_reset_even_on_error(self) -> None:
        events: list[str] = []
        registry = ToolRegistry()
        registry.register(_SpyCronTool(events))
        job = _bound_job()
        agent = _FakeAgent(tools=registry, events=events, error=RuntimeError("boom"))
        cron = _FakeCronRecorder()

        with pytest.raises(RuntimeError):
            await run_bound_cron_job(job, agent=agent, cron=cron)

        assert events == ["set_cron_context:True", "submit_cron_turn", "reset_cron_context"]
