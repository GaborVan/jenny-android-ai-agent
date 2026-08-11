"""Il ramo heartbeat del ``CronDispatcher``: silenzioso, senza giudici.

Questo ramo non aveva copertura, ed è il motivo per cui il difetto è arrivato sul
dispositivo. Il vecchio disegno diceva al modello di produrre un riempitivo
("If nothing needs reporting, respond with just 'All clear.'"), sopprimeva il tool
``message`` per costringere tutto dentro quel testo, e poi pagava una seconda
chiamata LLM (``evaluate_response``) per indovinare se nasconderlo. Sul telefono
quel giudice finiva in ``finish_reason='length'`` e non decideva mai.

Ora il contratto è strutturale: il turno è silenzioso, il testo finale non è la
consegna, e l'unico modo di raggiungere l'utente è il tool ``message``.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from jenny.runtime.cron_dispatch import _HEARTBEAT_PREAMBLE, CronDispatcher
from jenny.session.keys import HEARTBEAT_SESSION_KEY
from jenny.session.turn_visibility import TurnVisibility

_HEARTBEAT_JOB = SimpleNamespace(name="heartbeat", id="job-heartbeat")

_HEARTBEAT_MD = """# Heartbeat

## Active Tasks

- Ogni ciclo, controlla l'umidità del suolo e avvertimi solo sotto il 15%.
"""


class _FakeSession:
    def __init__(self) -> None:
        self.retained: list[int] = []

    def retain_recent_legal_suffix(self, keep: int) -> None:
        self.retained.append(keep)


class _FakeSessions:
    def __init__(self) -> None:
        self.session = _FakeSession()
        self.saved = 0

    def get_or_create(self, _key: str) -> _FakeSession:
        return self.session

    def save(self, _session: _FakeSession) -> None:
        self.saved += 1


class _FakeAgent:
    def __init__(self) -> None:
        self.sessions = _FakeSessions()
        self.calls: list[dict] = []

    async def process_direct(self, prompt: str, **kwargs):
        self.calls.append({"prompt": prompt, **kwargs})
        # Un turno silenzioso non restituisce payload: è ciò che fa la FSM.
        return None

    def evict_pruned_sessions(self, keys) -> None:  # pragma: no cover - non usato qui
        pass


@pytest.fixture
def dispatcher(tmp_path: Path) -> tuple[CronDispatcher, _FakeAgent]:
    (tmp_path / "HEARTBEAT.md").write_text(_HEARTBEAT_MD, encoding="utf-8")
    agent = _FakeAgent()
    return (
        CronDispatcher(
            get_agent=lambda: agent,
            # ``workspace_path`` è una property su ``Config``: il ramo heartbeat
            # legge solo quello, quindi un doppio esplicito è più onesto di un
            # Config vero con un monkeypatch della radice del workspace.
            config=SimpleNamespace(workspace_path=tmp_path),
            cron=MagicMock(),
            heartbeat_cfg=SimpleNamespace(keep_recent_messages=8),
        ),
        agent,
    )


class TestTheHeartbeatTurnIsSilent:
    async def test_the_turn_declares_itself_silent(self, dispatcher) -> None:
        disp, agent = dispatcher

        await disp.dispatch(_HEARTBEAT_JOB)

        assert agent.calls[0]["visibility"] is TurnVisibility.SILENT

    async def test_it_keeps_the_user_chat_as_its_delivery_target(self, dispatcher) -> None:
        """Silenzioso non vuol dire senza indirizzo: il tool ``message`` deve avere
        dove consegnare quando la condizione scatta."""
        disp, agent = dispatcher

        await disp.dispatch(_HEARTBEAT_JOB)

        assert agent.calls[0]["channel"] == "websocket"
        assert agent.calls[0]["chat_id"] == "default"
        assert agent.calls[0]["session_key"] == HEARTBEAT_SESSION_KEY

    async def test_nothing_is_delivered_by_the_dispatcher_itself(self, dispatcher) -> None:
        """Il dispatcher non consegna più niente da fuori il turno.

        Prima consegnava il testo del modello con ``proactive=True`` se un giudice
        LLM diceva sì; ora non ha nemmeno il callback per farlo.
        """
        disp, _agent = dispatcher

        assert await disp.dispatch(_HEARTBEAT_JOB) is None
        assert not hasattr(disp, "_deliver_to_channel")
        assert not hasattr(disp, "_get_message_tool")

    async def test_the_session_tail_is_still_pruned(self, dispatcher) -> None:
        disp, agent = dispatcher

        await disp.dispatch(_HEARTBEAT_JOB)

        assert agent.sessions.session.retained == [8]
        assert agent.sessions.saved == 1

    async def test_a_file_without_active_tasks_runs_no_turn(self, tmp_path: Path) -> None:
        (tmp_path / "HEARTBEAT.md").write_text("# Heartbeat\n\nnothing here\n", "utf-8")
        agent = _FakeAgent()
        disp = CronDispatcher(
            get_agent=lambda: agent,
            config=SimpleNamespace(workspace_path=tmp_path),
            cron=MagicMock(),
            heartbeat_cfg=SimpleNamespace(keep_recent_messages=8),
        )

        assert await disp.dispatch(_HEARTBEAT_JOB) is None
        assert agent.calls == []


class TestThePreambleContract:
    """Il preambolo è il contratto che il modello legge: non può tornare a chiedere
    un riempitivo, perché è quel riempitivo che l'utente vedeva in chat."""

    def test_it_never_asks_for_filler(self) -> None:
        for filler in ("All clear.", "All done.", "nothing to report"):
            assert f"respond with just '{filler}'" not in _HEARTBEAT_PREAMBLE
        # Le stesse frasi compaiono solo come divieto esplicito.
        assert "never send filler" in _HEARTBEAT_PREAMBLE.lower()

    def test_it_names_the_message_tool_as_the_only_way_out(self) -> None:
        assert "`message` tool" in _HEARTBEAT_PREAMBLE
        assert "SILENT by default" in _HEARTBEAT_PREAMBLE

    def test_it_says_that_saying_nothing_is_correct(self) -> None:
        assert "do NOT call `message`" in _HEARTBEAT_PREAMBLE
        assert "correct, expected outcome" in _HEARTBEAT_PREAMBLE

    def test_it_still_forbids_leaking_internal_file_names(self) -> None:
        assert "HEARTBEAT.md" in _HEARTBEAT_PREAMBLE
        assert "never mention internal files" in _HEARTBEAT_PREAMBLE.lower()

    def test_it_tells_the_turn_not_to_speak_before_a_subagent_answers(self) -> None:
        """Misurato sul Titan 2, ciclo 19:08: il turno ha chiamato ``spawn`` e due
        secondi dopo ha mandato in chat le letture del ciclo PRECEDENTE come se
        fossero appena misurate, poi un messaggio di correzione. ``spawn`` ritorna
        subito: in quel turno il dato non esiste ancora."""
        text = _HEARTBEAT_PREAMBLE
        assert "`spawn` returns immediately" in text
        assert "Send NOTHING now" in text
        assert "comes back to you" in text

    def test_it_says_that_retained_history_is_not_the_current_state(self) -> None:
        text = _HEARTBEAT_PREAMBLE
        assert "history, not the current state" in text
        assert "never report a past value" in text

    def test_it_forbids_continuing_its_own_earlier_conversation(self) -> None:
        """Il ciclo 19:38 sul Titan 2: cinque ``message`` di fila, il primo dei
        quali si scusava per l'errore del ciclo precedente che aveva trovato nella
        storia conservata. Il rumore si autoalimentava di ciclo in ciclo."""
        text = _HEARTBEAT_PREAMBLE
        assert "mistakes, corrections or apologies" in text
        assert "do NOT continue that" in text
        assert "the user is not talking to you" in text


def test_the_llm_notification_judge_is_gone() -> None:
    """Un gate che con un modello reasoning finiva sempre in ``finish_reason='length'``
    non era una cintura di sicurezza: restituiva sempre il default."""
    with pytest.raises(ModuleNotFoundError):
        __import__("jenny.utils.evaluator")
