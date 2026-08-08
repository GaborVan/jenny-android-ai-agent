"""Il turno soppresso della FSM: la meccanica su cui poggia il monitor cron.

Prima di questa feature il silenzio da un cron job era **impossibile**: un
turno che finiva senza contenuto veniva sostituito con
``EMPTY_FINAL_RESPONSE_MESSAGE`` (``jenny/agent/runner.py``, ``_finish_on_blank``)
e quel placeholder veniva consegnato in chat. ``suppress_response`` disattiva
quella sostituzione (``jenny/agent/turn_states.py``, ``_state_save``) e azzera
l'outbound (``_state_respond``).

Sono i due punti che, se regredissero, romperebbero la modalità monitor in
silenzio: il job continuerebbe a girare, i test del runner cron resterebbero
verdi, e l'utente si ritroverebbe "Non ho prodotto una risposta" ogni cinque
minuti. Da qui il test dedicato.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from jenny.agent.loop import AgentLoop, TurnContext, TurnState
from jenny.agent.tools.message import MessageTool
from jenny.bus.events import InboundMessage
from jenny.bus.queue import MessageBus
from jenny.cron.session_turns import CRON_MONITOR_META, CRON_SPOKE_META, CRON_TRIGGER_META
from jenny.providers.base import LLMResponse
from jenny.utils.runtime import EMPTY_FINAL_RESPONSE_MESSAGE


def _make_loop(tmp_path: Path) -> AgentLoop:
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    provider.chat_with_retry = AsyncMock(return_value=LLMResponse(content="ok"))
    return AgentLoop(
        bus=MessageBus(),
        provider=provider,
        workspace=tmp_path,
        model="test-model",
    )


def _cron_metadata(*, monitor: bool) -> dict:
    metadata: dict = {CRON_TRIGGER_META: {"job_id": "job-m", "run_id": "run-1"}}
    if monitor:
        metadata[CRON_MONITOR_META] = True
    return metadata


def _ctx(
    loop: AgentLoop,
    *,
    metadata: dict,
    suppress: bool,
    final_content: str | None,
) -> TurnContext:
    ctx = TurnContext(
        msg=InboundMessage(
            channel="websocket",
            sender_id="cron",
            chat_id="chat-1",
            content="controlla la posta",
            metadata=metadata,
        ),
        session_key="cron:job-m",
        state=TurnState.SAVE,
        turn_id="turn-1",
    )
    ctx.session = loop.sessions.get_or_create("cron:job-m")
    ctx.suppress_response = suppress
    ctx.final_content = final_content
    ctx.turn_latency_ms = 0
    return ctx


@pytest.fixture
def loop(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AgentLoop:
    agent_loop = _make_loop(tmp_path)
    # La persistenza di fine turno non è ciò che stiamo testando: la neutralizziamo
    # per isolare l'unica riga in gioco (la sostituzione del contenuto vuoto).
    monkeypatch.setattr(agent_loop, "_finalize_turn_save", lambda *a, **k: None)
    return agent_loop


class _StopTurnError(Exception):
    """Interrompe la FSM appena il TurnContext è stato costruito."""


class TestSuppressionIsWiredFromTheInboundMetadata:
    """``_process_message`` decide la soppressione dai soli metadata del messaggio."""

    async def _captured_ctx(
        self, loop: AgentLoop, monkeypatch: pytest.MonkeyPatch, metadata: dict
    ) -> TurnContext:
        captured: list[TurnContext] = []

        async def capture(ctx: TurnContext) -> str:
            captured.append(ctx)
            raise _StopTurnError

        monkeypatch.setattr(loop, "_state_restore", capture)
        msg = InboundMessage(
            channel="websocket",
            sender_id="cron",
            chat_id="chat-1",
            content="controlla la posta",
            metadata=metadata,
        )
        with pytest.raises(_StopTurnError):
            await loop._process_message(msg, session_key="cron:job-m")
        return captured[0]

    async def test_a_monitor_cron_message_starts_a_suppressed_turn(
        self, loop: AgentLoop, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        ctx = await self._captured_ctx(loop, monkeypatch, _cron_metadata(monitor=True))

        assert ctx.suppress_response is True

    async def test_a_plain_cron_message_starts_a_normal_turn(
        self, loop: AgentLoop, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        ctx = await self._captured_ctx(loop, monkeypatch, _cron_metadata(monitor=False))

        assert ctx.suppress_response is False

    async def test_a_user_message_starts_a_normal_turn(
        self, loop: AgentLoop, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        ctx = await self._captured_ctx(loop, monkeypatch, {})

        assert ctx.suppress_response is False


class TestBlankAnswerPlaceholder:
    """``_state_save`` sostituisce il vuoto con un placeholder — tranne se soppresso."""

    @pytest.mark.parametrize("blank", ["", "   ", "\n\t "])
    async def test_a_normal_turn_gets_the_placeholder_when_it_says_nothing(
        self, loop: AgentLoop, blank: str
    ) -> None:
        """Il comportamento storico, che il monitor deve poter disattivare."""
        ctx = _ctx(loop, metadata={}, suppress=False, final_content=blank)

        assert await loop._state_save(ctx) == "ok"

        assert ctx.final_content == EMPTY_FINAL_RESPONSE_MESSAGE

    async def test_a_normal_turn_with_no_content_at_all_gets_the_placeholder(
        self, loop: AgentLoop
    ) -> None:
        ctx = _ctx(loop, metadata={}, suppress=False, final_content=None)

        await loop._state_save(ctx)

        assert ctx.final_content == EMPTY_FINAL_RESPONSE_MESSAGE

    @pytest.mark.parametrize("blank", ["", "   ", "\n\t "])
    async def test_a_suppressed_turn_keeps_its_silence_instead_of_the_placeholder(
        self, loop: AgentLoop, blank: str
    ) -> None:
        """La riga che rende possibile il silenzio di un monitor.

        Se questa sostituzione tornasse ad applicarsi, il monitor consegnerebbe
        "Non ho prodotto una risposta" a ogni ciclo silenzioso.
        """
        ctx = _ctx(
            loop,
            metadata=_cron_metadata(monitor=True),
            suppress=True,
            final_content=blank,
        )

        assert await loop._state_save(ctx) == "ok"

        assert ctx.final_content == blank
        assert ctx.final_content != EMPTY_FINAL_RESPONSE_MESSAGE

    async def test_a_suppressed_turn_with_no_content_at_all_stays_empty(
        self, loop: AgentLoop
    ) -> None:
        ctx = _ctx(
            loop,
            metadata=_cron_metadata(monitor=True),
            suppress=True,
            final_content=None,
        )

        await loop._state_save(ctx)

        assert ctx.final_content is None

    async def test_a_suppressed_turn_that_did_produce_text_keeps_it(
        self, loop: AgentLoop
    ) -> None:
        """La soppressione non riscrive il contenuto: si limita a non consegnarlo."""
        ctx = _ctx(
            loop,
            metadata=_cron_metadata(monitor=True),
            suppress=True,
            final_content="ho trovato qualcosa",
        )

        await loop._state_save(ctx)

        assert ctx.final_content == "ho trovato qualcosa"


class TestSuppressedTurnProducesNoOutbound:
    """``_state_respond``: da un turno soppresso non esce nessun messaggio."""

    async def test_a_silent_monitor_turn_ends_with_no_outbound(self, loop: AgentLoop) -> None:
        """Il percorso completo save → respond su un turno che non ha detto nulla."""
        ctx = _ctx(
            loop,
            metadata=_cron_metadata(monitor=True),
            suppress=True,
            final_content="",
        )

        await loop._state_save(ctx)
        assert await loop._state_respond(ctx) == "ok"

        # Né il placeholder, né un outbound: è esattamente ciò che l'utente
        # non deve vedere arrivare in chat.
        assert ctx.final_content == ""
        assert ctx.outbound is None

    async def test_a_suppressed_turn_with_content_still_delivers_nothing(
        self, loop: AgentLoop
    ) -> None:
        ctx = _ctx(
            loop,
            metadata=_cron_metadata(monitor=True),
            suppress=True,
            final_content="testo che nessuno deve ricevere",
        )

        await loop._state_respond(ctx)

        assert ctx.outbound is None

    async def test_a_plain_cron_turn_still_produces_its_outbound(self, loop: AgentLoop) -> None:
        """Controprova: senza soppressione l'outbound c'è, e porta il testo."""
        ctx = _ctx(
            loop,
            metadata=_cron_metadata(monitor=False),
            suppress=False,
            final_content="promemoria consegnato",
        )

        await loop._state_respond(ctx)

        assert ctx.outbound is not None
        assert ctx.outbound.content == "promemoria consegnato"
        assert ctx.outbound.channel == "websocket"
        assert ctx.outbound.chat_id == "chat-1"


class TestMonitorSpokeSignal:
    """L'unico canale di ritorno di un turno monitor è il dict ``metadata``."""

    def _message_tool(self, loop: AgentLoop, *, sent: bool) -> MessageTool:
        tool = loop.tools.get("message")
        assert isinstance(tool, MessageTool)
        tool.start_turn()
        tool._sent_in_turn = sent
        return tool

    async def test_a_delivery_through_the_message_tool_is_reported_as_spoken(
        self, loop: AgentLoop
    ) -> None:
        self._message_tool(loop, sent=True)
        metadata = _cron_metadata(monitor=True)
        ctx = _ctx(loop, metadata=metadata, suppress=True, final_content="")

        await loop._state_respond(ctx)

        assert metadata[CRON_SPOKE_META] is True
        assert ctx.outbound is None

    async def test_a_turn_without_deliveries_is_reported_as_silent(
        self, loop: AgentLoop
    ) -> None:
        self._message_tool(loop, sent=False)
        metadata = _cron_metadata(monitor=True)
        ctx = _ctx(loop, metadata=metadata, suppress=True, final_content="")

        await loop._state_respond(ctx)

        assert metadata[CRON_SPOKE_META] is False

    async def test_a_delivery_through_a_per_turn_registry_is_reported_as_spoken(
        self, loop: AgentLoop
    ) -> None:
        """``_sent_in_turn`` vive in una ContextVar *per istanza*: leggere il
        MessageTool di default mentre il turno ne usa un altro darebbe sempre
        ``False``, e il monitor risulterebbe muto a ogni ciclo senza che nulla
        lo segnali. Il registry del turno deve avere la precedenza."""
        from jenny.agent.tools.registry import ToolRegistry

        # Il MessageTool di default resta a mani vuote: se la FSM leggesse
        # quello, il turno passerebbe per silenzioso.
        self._message_tool(loop, sent=False)

        turn_tool = MessageTool(send_callback=AsyncMock(), workspace=loop.workspace)
        turn_tool.start_turn()
        turn_tool._sent_in_turn = True
        turn_registry = ToolRegistry()
        turn_registry.register(turn_tool)

        metadata = _cron_metadata(monitor=True)
        ctx = _ctx(loop, metadata=metadata, suppress=True, final_content="")
        ctx.tools = turn_registry

        await loop._state_respond(ctx)

        assert metadata[CRON_SPOKE_META] is True

    async def test_the_flag_lands_in_the_very_dict_the_caller_handed_over(
        self, loop: AgentLoop
    ) -> None:
        """``run_bound_cron_job`` rilegge *quel* dict dopo l'await: se la FSM ne
        copiasse uno nuovo, ogni monitor risulterebbe muto per sempre."""
        self._message_tool(loop, sent=True)
        metadata = _cron_metadata(monitor=True)
        ctx = _ctx(loop, metadata=metadata, suppress=True, final_content="")

        await loop._state_respond(ctx)

        assert ctx.msg.metadata is metadata
        assert metadata.get(CRON_SPOKE_META) is True

    async def test_a_suppressed_turn_that_is_not_a_monitor_writes_no_flag(
        self, loop: AgentLoop
    ) -> None:
        """Le goal continuation condividono ``suppress_response`` ma non sono monitor.

        Scrivere comunque il flag sporcherebbe metadata che non lo aspettano.
        """
        self._message_tool(loop, sent=True)
        metadata: dict = {}
        ctx = _ctx(loop, metadata=metadata, suppress=True, final_content="")

        await loop._state_respond(ctx)

        assert CRON_SPOKE_META not in metadata
        assert ctx.outbound is None

    async def test_a_monitor_flag_without_a_cron_trigger_is_ignored(
        self, loop: AgentLoop
    ) -> None:
        """Il flag monitor da solo non basta: senza trigger cron non è un turno cron."""
        self._message_tool(loop, sent=True)
        metadata: dict = {CRON_MONITOR_META: True}
        ctx = _ctx(loop, metadata=metadata, suppress=True, final_content="")

        await loop._state_respond(ctx)

        assert CRON_SPOKE_META not in metadata
