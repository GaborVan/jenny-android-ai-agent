"""Il terzo esito di un monitor: "non ho potuto controllare" (B8).

Prima di questo stato un monitor aveva due soli finali e producevano lo stesso
identico output — niente. Un controllo delle piante rotto era indistinguibile da
un giardino sano, e la skill aveva pure un motivo legittimo di tacere ("se hps è
irraggiungibile salta il ciclo in silenzio"), quindi il guasto era perfettamente
mimetizzato.

Il vincolo che questi test difendono, nell'ordine:

1. **Un giro senza novità non parla.** È il valore dell'heartbeat: costa zero
   quando non c'è niente da dire. Se un monitor sano dicesse qualcosa, la
   feature sarebbe sbagliata, non incompleta.
2. Un singolo fallimento non parla nemmeno lui: le reti cadono.
3. Tre fallimenti di fila producono **un** messaggio, e uno solo, finché lo
   stato non cambia.

L'agente finto qui sotto non è un mock che restituisce copioni: implementa il
contratto del prompt (parla solo se il prompt glielo chiede), quindi il test
misura davvero la catena prompt → esito → stato → prompt del giro dopo.
"""

from __future__ import annotations

import json
from functools import cache
from pathlib import Path
from typing import Any

import pytest

from jenny.agent.tools.registry import ToolRegistry
from jenny.agent.turn_types import TurnOutcome
from jenny.bus.events import InboundMessage
from jenny.cron.bound_runner import (
    MONITOR_ESCALATE_AFTER_FAILURES,
    could_not_check_reason,
    run_bound_cron_job,
)
from jenny.cron.service import CronService
from jenny.cron.types import CronJobState, CronSchedule
from jenny.utils.prompt_templates import render_template

_MESSAGE = "controlla l'umidità delle piante e avvisami solo sotto il 15%"

@cache
def _base_prompt() -> str:
    """Il prompt di un monitor sano.

    L'agente finto distingue i giri di escalation confrontandosi con questo
    invece di cercare una frase: così il test non si rompe se la prosa
    dell'escalation viene riscritta, e resta rotto se il prompt di un giro
    normale cambia — che è l'invariante da difendere. Pigro perché il workspace
    dei template lo configura una fixture, non l'import.
    """
    return render_template("agent/cron_monitor.md", strip=True, message=_MESSAGE)


class _FakeSession:
    def __init__(self, key: str) -> None:
        self.key = key

    def retain_recent_legal_suffix(self, keep: int) -> None:
        pass


class _FakeSessions:
    def __init__(self) -> None:
        self._sessions: dict[str, _FakeSession] = {}

    def get_or_create(self, key: str) -> _FakeSession:
        return self._sessions.setdefault(key, _FakeSession(key))

    def save(self, session: _FakeSession) -> None:
        pass


class _FakeMonitorAgent:
    """Agente che *segue il prompt* invece di eseguire un copione.

    - ``healthy=True``: il controllo gira e non trova niente → silenzio, e
      nessun marcatore.
    - ``healthy=False``: il controllo non parte → marcatore nel testo finale,
      che non raggiunge nessuno.
    - Parla (tool ``message``) **solo** se il prompt di questo giro contiene
      l'istruzione di avvisare. È la sola cosa che il modello vero decide.
    """

    def __init__(self) -> None:
        self.tools = ToolRegistry()
        self.sessions = _FakeSessions()
        self.healthy = False
        self.messages: list[str] = []
        self.prompts: list[str] = []

    async def submit_cron_turn(self, msg: InboundMessage) -> TurnOutcome:
        self.prompts.append(msg.content)
        if self.healthy:
            return TurnOutcome.silent(final_text="Tutte le piante sopra il 15%.")
        escalating = msg.content != _base_prompt()
        if not escalating:
            return TurnOutcome.silent(final_text="CHECK_FAILED: hps non raggiungibile")
        self.messages.append("Il controllo delle piante non riesce a partire da un po'.")
        return TurnOutcome.spoke_via_tool(final_text="CHECK_FAILED: hps non raggiungibile")


def _monitor(tmp_path: Path) -> tuple[CronService, str, _FakeMonitorAgent]:
    agent = _FakeMonitorAgent()
    service = CronService(tmp_path / "cron" / "jobs.json")

    async def on_job(job: Any) -> str | None:
        return await run_bound_cron_job(job, agent=agent, cron=service)

    service.on_job = on_job
    job = service.add_job(
        name="piante",
        schedule=CronSchedule(kind="every", every_ms=1_800_000),
        message=_MESSAGE,
        mode="monitor",
        session_key="unified:default",
        origin_channel="websocket",
        origin_chat_id="chat-1",
    )
    return service, job.id, agent


async def _cycles(service: CronService, job_id: str, count: int) -> None:
    for _ in range(count):
        await service.run_job(job_id)


def _state(service: CronService, job_id: str) -> CronJobState:
    job = service.get_job(job_id)
    assert job is not None
    return job.state


class TestSilenceStaysFree:
    """Il caso che non deve regredire: un monitor sano non dice niente."""

    async def test_a_healthy_monitor_never_says_anything(self, tmp_path: Path) -> None:
        service, job_id, agent = _monitor(tmp_path)
        agent.healthy = True

        await _cycles(service, job_id, 10)

        assert agent.messages == []
        state = _state(service, job_id)
        assert state.last_status == "silenced"
        assert state.consecutive_could_not_check == 0

    async def test_a_healthy_monitor_gets_the_plain_prompt_every_time(
        self, tmp_path: Path
    ) -> None:
        """Nessuna riga in più nel prompt finché non c'è un guasto."""
        service, job_id, agent = _monitor(tmp_path)
        agent.healthy = True

        await _cycles(service, job_id, 5)

        assert agent.prompts == [_base_prompt()] * 5

    async def test_a_single_failed_check_produces_no_message(self, tmp_path: Path) -> None:
        service, job_id, agent = _monitor(tmp_path)

        await _cycles(service, job_id, 1)

        assert agent.messages == []
        state = _state(service, job_id)
        assert state.last_status == "could_not_check"
        assert state.consecutive_could_not_check == 1
        assert state.last_error == "hps non raggiungibile"

    async def test_two_failed_checks_still_produce_no_message(self, tmp_path: Path) -> None:
        """La soglia è tre: due sono ancora dentro il margine di una rete ballerina."""
        service, job_id, agent = _monitor(tmp_path)

        await _cycles(service, job_id, MONITOR_ESCALATE_AFTER_FAILURES - 1)

        assert agent.messages == []
        assert _state(service, job_id).consecutive_could_not_check == 2


class TestTheStreakSpeaksOnce:
    async def test_three_failed_checks_in_a_row_produce_exactly_one_message(
        self, tmp_path: Path
    ) -> None:
        service, job_id, agent = _monitor(tmp_path)

        await _cycles(service, job_id, MONITOR_ESCALATE_AFTER_FAILURES)

        assert len(agent.messages) == 1

    async def test_the_alert_is_not_repeated_while_the_monitor_stays_broken(
        self, tmp_path: Path
    ) -> None:
        """Un guasto che dura un giorno deve costare un messaggio, non quarantotto."""
        service, job_id, agent = _monitor(tmp_path)

        await _cycles(service, job_id, 12)

        assert len(agent.messages) == 1
        state = _state(service, job_id)
        assert state.consecutive_could_not_check == 12
        assert state.could_not_check_escalated is True

    async def test_the_run_that_alerted_still_counts_as_a_missed_check(
        self, tmp_path: Path
    ) -> None:
        """Legare l'esito a "ha parlato" azzererebbe la sequenza proprio lì.

        Il conteggio ripartirebbe da zero a ogni avviso, e l'avviso tornerebbe
        ogni tre cicli per sempre: il rumore che questa feature deve evitare.
        """
        service, job_id, agent = _monitor(tmp_path)

        await _cycles(service, job_id, MONITOR_ESCALATE_AFTER_FAILURES)

        assert agent.messages  # ha parlato
        state = _state(service, job_id)
        assert state.last_status == "could_not_check"
        assert state.consecutive_could_not_check == MONITOR_ESCALATE_AFTER_FAILURES

    async def test_a_model_that_ignores_the_instruction_is_asked_again(
        self, tmp_path: Path
    ) -> None:
        """L'avviso è "dato" solo se è uscito davvero."""
        service, job_id, agent = _monitor(tmp_path)

        async def mute(msg: InboundMessage) -> TurnOutcome:
            agent.prompts.append(msg.content)
            return TurnOutcome.silent(final_text="CHECK_FAILED: hps non raggiungibile")

        agent.submit_cron_turn = mute  # type: ignore[method-assign]
        await _cycles(service, job_id, 5)

        state = _state(service, job_id)
        assert state.could_not_check_escalated is False
        # Dal terzo giro in poi il prompt continua a chiedere di avvisare.
        assert agent.prompts[-1] != _base_prompt()

    async def test_the_broken_monitor_stays_armed(self, tmp_path: Path) -> None:
        """Non riuscire a controllare non è un motivo per smettere di provarci."""
        service, job_id, _agent = _monitor(tmp_path)

        await _cycles(service, job_id, 4)

        job = service.get_job(job_id)
        assert job is not None
        assert job.enabled is True
        assert job.state.next_run_at_ms is not None


class TestRecovery:
    async def test_a_check_that_works_again_resets_the_counter(self, tmp_path: Path) -> None:
        service, job_id, agent = _monitor(tmp_path)

        await _cycles(service, job_id, MONITOR_ESCALATE_AFTER_FAILURES)
        assert len(agent.messages) == 1

        agent.healthy = True
        await _cycles(service, job_id, 1)

        state = _state(service, job_id)
        assert state.last_status == "silenced"
        assert state.last_error is None
        assert state.consecutive_could_not_check == 0
        assert state.could_not_check_since_ms is None
        assert state.could_not_check_escalated is False
        # E il ritorno alla normalità non è una notizia: nessun secondo messaggio.
        assert len(agent.messages) == 1

    async def test_a_second_outage_can_alert_again(self, tmp_path: Path) -> None:
        service, job_id, agent = _monitor(tmp_path)

        await _cycles(service, job_id, MONITOR_ESCALATE_AFTER_FAILURES)
        agent.healthy = True
        await _cycles(service, job_id, 1)
        agent.healthy = False
        await _cycles(service, job_id, MONITOR_ESCALATE_AFTER_FAILURES)

        assert len(agent.messages) == 2

    async def test_a_spoken_finding_also_proves_the_check_ran(self, tmp_path: Path) -> None:
        """Un monitor che trova qualcosa e lo dice è ``ok``, e chiude la sequenza."""
        service, job_id, agent = _monitor(tmp_path)
        await _cycles(service, job_id, 2)

        async def found_something(msg: InboundMessage) -> TurnOutcome:
            agent.prompts.append(msg.content)
            return TurnOutcome.spoke_via_tool(final_text="Basilico all'11%.")

        agent.submit_cron_turn = found_something  # type: ignore[method-assign]
        await _cycles(service, job_id, 1)

        state = _state(service, job_id)
        assert state.last_status == "ok"
        assert state.consecutive_could_not_check == 0


class TestPersistedState:
    """Ciò che un lettore esterno (WebUI, tool ``cron``) troverà nello store."""

    async def test_the_streak_survives_a_restart(self, tmp_path: Path) -> None:
        service, job_id, _agent = _monitor(tmp_path)
        await _cycles(service, job_id, 2)

        reloaded = CronService(tmp_path / "cron" / "jobs.json").get_job(job_id)

        assert reloaded is not None
        assert reloaded.state.last_status == "could_not_check"
        assert reloaded.state.consecutive_could_not_check == 2
        assert reloaded.state.could_not_check_since_ms is not None
        assert reloaded.state.could_not_check_escalated is False

    async def test_the_state_is_written_in_camel_case_like_the_rest(
        self, tmp_path: Path
    ) -> None:
        service, job_id, _agent = _monitor(tmp_path)
        await _cycles(service, job_id, MONITOR_ESCALATE_AFTER_FAILURES)

        raw = json.loads((tmp_path / "cron" / "jobs.json").read_text(encoding="utf-8"))
        state = raw["jobs"][0]["state"]
        assert state["lastStatus"] == "could_not_check"
        assert state["consecutiveCouldNotCheck"] == MONITOR_ESCALATE_AFTER_FAILURES
        assert state["couldNotCheckEscalated"] is True
        assert isinstance(state["couldNotCheckSinceMs"], int)

    async def test_the_run_history_keeps_the_third_status(self, tmp_path: Path) -> None:
        service, job_id, agent = _monitor(tmp_path)
        await _cycles(service, job_id, 2)
        agent.healthy = True
        await _cycles(service, job_id, 1)

        state = _state(service, job_id)
        assert [r.status for r in state.run_history] == [
            "could_not_check",
            "could_not_check",
            "silenced",
        ]

    async def test_the_since_timestamp_marks_the_start_not_the_last_run(
        self, tmp_path: Path
    ) -> None:
        """"Da quando è rotto" è la domanda che il conteggio da solo non risponde."""
        service, job_id, _agent = _monitor(tmp_path)
        await _cycles(service, job_id, 1)
        first = _state(service, job_id).could_not_check_since_ms

        await _cycles(service, job_id, 3)

        assert _state(service, job_id).could_not_check_since_ms == first


class TestMarkerParsing:
    """Il marcatore è scritto da un modello: va letto con un po' di tolleranza."""

    def test_a_plain_marker_line_is_read_with_its_reason(self) -> None:
        assert could_not_check_reason("CHECK_FAILED: hps unreachable") == "hps unreachable"

    def test_a_marker_at_the_end_of_a_longer_answer_still_counts(self) -> None:
        text = "Ho provato tre volte a leggere la sonda.\n\nCHECK_FAILED: timeout"
        assert could_not_check_reason(text) == "timeout"

    def test_markdown_decoration_around_the_marker_is_ignored(self) -> None:
        assert could_not_check_reason("**CHECK_FAILED: import rotto**") == "import rotto"

    def test_a_marker_without_a_reason_is_still_a_marker(self) -> None:
        """Vuoto non è assente: il chiamante deve distinguere con ``is not None``."""
        assert could_not_check_reason("CHECK_FAILED") == ""

    def test_an_ordinary_answer_has_no_marker(self) -> None:
        assert could_not_check_reason("Tutte le piante sono sopra il 15%.") is None

    def test_no_answer_at_all_is_not_a_failure(self) -> None:
        assert could_not_check_reason("") is None
        assert could_not_check_reason(None) is None

    def test_the_reason_is_truncated_so_it_cannot_bloat_the_store(self) -> None:
        assert len(could_not_check_reason("CHECK_FAILED: " + "x" * 5000) or "") == 200


@pytest.mark.parametrize("mode", ["reminder"])
async def test_a_reminder_ignores_the_marker_entirely(tmp_path: Path, mode: str) -> None:
    """Il terzo stato è un fatto dei monitor: un reminder parla sempre e basta."""
    agent = _FakeMonitorAgent()
    service = CronService(tmp_path / "cron" / "jobs.json")

    async def on_job(job: Any) -> str | None:
        return await run_bound_cron_job(job, agent=agent, cron=service)

    service.on_job = on_job
    job = service.add_job(
        name="promemoria",
        schedule=CronSchedule(kind="every", every_ms=1_800_000),
        message=_MESSAGE,
        mode=mode,  # type: ignore[arg-type]
        session_key="unified:default",
        origin_channel="websocket",
        origin_chat_id="chat-1",
    )
    await service.run_job(job.id)

    state = _state(service, job.id)
    assert state.last_status == "ok"
    assert state.consecutive_could_not_check == 0
