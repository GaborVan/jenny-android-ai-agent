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

from jenny.agent.turn_types import TurnOutcome
from jenny.cron.heartbeat_tasks import parse_heartbeat_tasks
from jenny.cron.types import CronJob, CronPayload
from jenny.runtime.cron_dispatch import _HEARTBEAT_PREAMBLE, CronDispatcher
from jenny.session.keys import HEARTBEAT_SESSION_KEY
from jenny.session.turn_visibility import TurnVisibility


def _heartbeat_job() -> CronJob:
    """Il job vero e non un doppio: da B13 il ramo heartbeat legge e scrive
    ``job.state`` (lo stato per-task), quindi un ``SimpleNamespace`` senza stato
    nasconderebbe proprio la parte nuova."""
    return CronJob(
        id="heartbeat", name="heartbeat", payload=CronPayload(kind="system_event")
    )

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

    async def process_direct_outcome(self, prompt: str, **kwargs) -> TurnOutcome:
        self.calls.append({"prompt": prompt, **kwargs})
        # Un turno silenzioso non restituisce payload: è ciò che fa la FSM.
        # Il testo finale c'è comunque, ed è dove il modello dichiara i task
        # che non ha potuto eseguire (qui: nessuno).
        return TurnOutcome.silent(final_text="")

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

        await disp.dispatch(_heartbeat_job())

        assert agent.calls[0]["visibility"] is TurnVisibility.SILENT

    async def test_it_keeps_the_user_chat_as_its_delivery_target(self, dispatcher) -> None:
        """Silenzioso non vuol dire senza indirizzo: il tool ``message`` deve avere
        dove consegnare quando la condizione scatta."""
        disp, agent = dispatcher

        await disp.dispatch(_heartbeat_job())

        assert agent.calls[0]["channel"] == "websocket"
        assert agent.calls[0]["chat_id"] == "default"
        assert agent.calls[0]["session_key"] == HEARTBEAT_SESSION_KEY

    async def test_nothing_is_delivered_by_the_dispatcher_itself(self, dispatcher) -> None:
        """Il dispatcher non consegna più niente da fuori il turno.

        Prima consegnava il testo del modello con ``proactive=True`` se un giudice
        LLM diceva sì; ora non ha nemmeno il callback per farlo.
        """
        disp, _agent = dispatcher

        assert await disp.dispatch(_heartbeat_job()) is None
        assert not hasattr(disp, "_deliver_to_channel")
        assert not hasattr(disp, "_get_message_tool")

    async def test_the_session_tail_is_still_pruned(self, dispatcher) -> None:
        disp, agent = dispatcher

        await disp.dispatch(_heartbeat_job())

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

        assert await disp.dispatch(_heartbeat_job()) is None
        assert agent.calls == []


async def _prompt_for(workspace: Path, content: str) -> str:
    """Il prompt che un run dell'heartbeat manderebbe, dato quel ``HEARTBEAT.md``."""
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "HEARTBEAT.md").write_text(content, encoding="utf-8")
    agent = _FakeAgent()
    disp = CronDispatcher(
        get_agent=lambda: agent,
        config=SimpleNamespace(workspace_path=workspace),
        cron=MagicMock(),
        heartbeat_cfg=SimpleNamespace(keep_recent_messages=8),
    )
    await disp.dispatch(_heartbeat_job())
    return agent.calls[0]["prompt"]


# Gli stessi due task, in un file spoglio e in uno con addosso l'arredo che è
# **nostro**: i commenti HTML del template (uno su una riga, uno su più righe) e
# quel che sta fuori dalla sezione dei task.
_BARE = """## Active Tasks

- Ogni ciclo, controlla l'umidità del suolo e avvertimi solo sotto il 15%.
- Alle 8:00 dimmi se ci sono scadenze oggi.
"""

_FURNISHED = """# Heartbeat Tasks

<!--
This file is checked periodically by your Jenny agent. When the gateway starts
with `gateway.heartbeat.enabled=true`, it automatically registers a protected
heartbeat cron job that reads this file.

If this file has no tasks (only headers and comments), the agent will skip it.
-->

## Notes

Qualcosa che sta fuori dalla sezione dei task.

## Active Tasks

<!-- Add your periodic tasks below this line -->

<!--
Completed tasks should be deleted, not kept.
Un commento su più righe: è il caso che una regex ingenua si perde.
-->

- Ogni ciclo, controlla l'umidità del suolo e avvertimi solo sotto il 15%.
- Alle 8:00 dimmi se ci sono scadenze oggi.
"""

_TITAN2 = (Path(__file__).parent / "fixtures" / "heartbeat_titan2_2026-08-16.md").read_text(
    encoding="utf-8"
)

# Gli id che ``parse_heartbeat_tasks`` produce oggi sul file del Titan 2,
# calcolati **prima** di questo lavoro. V. il test che li usa.
_TITAN2_TASK_IDS = ["903dabc442ff", "8aa2cef88085", "113dc0426e58", "ff28e76dc65c"]


class TestThePromptCarriesTheTasksAndNotTheFile:
    """Nel prompt entra la sezione dei task, non il file.

    I commenti HTML sono spiegazioni nostre, non istruzioni dell'utente, e finché
    il file ci finiva grezzo il modello se li rileggeva a ogni run, per sempre, su
    ogni installazione. La macchina a stati che li salta esiste già ed è già usata
    da questo stesso ramo: qui guadagna un secondo chiamante.
    """

    async def test_heartbeat_prompt_ignores_comments_and_headings(
        self, tmp_path: Path
    ) -> None:
        """Commenti HTML e struttura fuori sezione non arrivano al modello.

        "Intestazioni" qui vuol dire quelle **fuori** da ``## Active Tasks``: il
        titolo del file, una sezione di appunti. Quelle dentro sono dell'utente e
        restano — v. il test sulla fixture del Titan 2.
        """
        bare = await _prompt_for(tmp_path / "bare", _BARE)
        furnished = await _prompt_for(tmp_path / "furnished", _FURNISHED)

        assert bare == furnished
        # Detto anche in positivo: è questo che un device già installato smette
        # di pagare, senza che nessuno gli tocchi il file.
        assert "gateway.heartbeat.enabled" not in furnished
        assert "Completed tasks should be deleted" not in furnished

    async def test_heartbeat_prompt_is_stable_across_healthy_runs(
        self, dispatcher
    ) -> None:
        """Due run sani, stesso file, prompt byte-identico.

        Non è una novità di questo cambiamento: ``521372f`` e ``ebafa02`` ci si
        appoggiano entrambi — è ciò su cui si regge la cache di prefisso del
        provider — e nessuno dei due lo fissava qui. Stesso ``job``, quindi lo
        stato per-task del primo run è quello che il secondo legge.
        """
        disp, agent = dispatcher
        job = _heartbeat_job()

        await disp.dispatch(job)
        await disp.dispatch(job)

        assert agent.calls[0]["prompt"] == agent.calls[1]["prompt"]

    async def test_a_user_heading_survives_but_our_comments_do_not(
        self, tmp_path: Path
    ) -> None:
        """Il file vero del Titan 2, che è il solo input che conta davvero.

        I quattro bullet non si reggono da soli: "notifica una sola volta per
        pianta" non dice di quali piante senza il titolo che ha scritto l'utente,
        e togliere quel titolo insieme ai nostri commenti sarebbe stata una
        regressione sull'unico dispositivo installato.
        """
        prompt = await _prompt_for(tmp_path / "titan2", _TITAN2)

        assert "### WaterBot: monitoraggio umidità piante" in prompt
        assert "gateway.heartbeat.enabled=true" not in prompt
        assert "Add your periodic tasks below this line" not in prompt
        for bullet in (
            "segui la skill `waterbot`",
            "Avverti l'utente SOLO se almeno una pianta",
            "Anti-spam: notifica una sola volta per pianta",
            "Se hps/Tailscale è irraggiungibile",
        ):
            assert bullet in prompt

    def test_task_ids_are_unchanged_by_this_work(self) -> None:
        """L'identità di un task non si tocca, e questa è la rete di sicurezza.

        ``_task_id`` è l'hash del testo del task, e ``state.task_checks`` sul
        telefono dell'utente è indicizzato con quegli hash. Cambiarli lascerebbe
        orfano lo stato dell'escalation e farebbe ripartire da zero le sequenze
        di guasto — cioè riaprirebbe il difetto che ``521372f`` e ``ebafa02``
        hanno appena chiuso. Le costanti qui sotto sono calcolate sul codice
        precedente a questo lavoro.
        """
        assert [t.id for t in parse_heartbeat_tasks(_TITAN2)] == _TITAN2_TASK_IDS


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

    def test_it_teaches_the_third_outcome_and_its_exact_form(self) -> None:
        """Silenzio e parola non bastano: un task che non è partito deve poterlo
        dire, e lo dice in una riga che non raggiunge nessuno."""
        text = _HEARTBEAT_PREAMBLE
        assert "third outcome, and it is NOT silence" in text
        assert "CHECK_FAILED <task number>:" in text
        assert "Those lines reach nobody" in text

    def test_it_says_an_instructed_silent_skip_is_not_a_failure(self) -> None:
        """Il task WaterBot reale dice "se hps è irraggiungibile salta il ciclo in
        silenzio": quello skip è ciò che gli è stato chiesto, non un guasto."""
        text = _HEARTBEAT_PREAMBLE
        assert "ITS OWN" in text
        assert "skip the cycle silently" in text
        assert "that is not a failure" in text

    def test_a_task_that_found_nothing_still_writes_no_line(self) -> None:
        assert "ran and found nothing is a success" in _HEARTBEAT_PREAMBLE

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
