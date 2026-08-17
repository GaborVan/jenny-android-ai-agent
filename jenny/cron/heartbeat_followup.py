"""Dove viene giudicato un controllo dell'heartbeat che è stato **delegato**.

B13 ha dato all'heartbeat il terzo esito per singolo task, ma lo registrava solo
dal testo finale del turno dell'heartbeat. L'agente principale gira però in
``orchestrator_mode`` e non ha ``python_exec``: ogni task che deve *fare*
qualcosa passa da ``spawn``, che ritorna subito. Quel turno finisce prima che il
subagent abbia iniziato, il suo testo non contiene nessun marcatore, e "nessun
marcatore = il task è stato eseguito" trasformava ogni controllo delegato in un
successo. Sul telefono, l'11 agosto: ``Heartbeat: check completed`` alle
12:48:02, il ``python_exec`` del subagent alle 12:48:08.

Il giudice giusto è il **turno d'annuncio del subagent** — lo dice già il
preambolo dell'heartbeat, che a quel turno affida la decisione di parlare: è
l'unico che abbia il risultato in mano. Questo modulo è ciò che gli dà anche il
modo di *registrarlo*: aggiunge al suo prompt il vocabolario del marcatore (solo
se quel turno cade nella sessione dell'heartbeat e c'è davvero un task in
sospeso) e ne rilegge il testo finale.

**Perché passa dal ``CronService`` e non da un'iniezione nel container.** Il
turno d'annuncio non nasce dal dispatcher cron: nasce dal bus, e lo esegue
``AgentLoop._process_system_message``. Fra il dispatcher — che conosce
l'heartbeat, il file e il job — e il loop, il servizio cron è l'unico oggetto
che entrambi hanno già in mano (``AgentLoop.cron_service``,
``CronDispatcher._cron``). Registrarsi lì costa un attributo e nessun nuovo
parametro di costruzione lungo la catena container → loop.

Ciò che questo modulo **non** fa: toccare ``last_status``, ``run_history`` o i
contatori di job. Quelli descrivono un *run*, e il run è finito regolarmente;
qui si scrive solo la mappa per-task, che è dove vive la risposta a "quale
controllo non sta funzionando" ed è ciò su cui si decide l'escalation.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger

from jenny.cron.could_not_check import (
    COULD_NOT_CHECK_MARKER,
    parse_could_not_check_marks,
    parse_ok_marks,
    parse_warned_marks,
)
from jenny.cron.heartbeat_tasks import (
    followup_block,
    parse_heartbeat_tasks,
    pending_tasks,
    rearm_after_user_message,
    record_followup_outcomes,
    tasks_already_warned,
    tasks_due_for_escalation,
)
from jenny.session.keys import HEARTBEAT_SESSION_KEY

if TYPE_CHECKING:
    # Solo annotazioni: il servizio cron resta un parametro, non una dipendenza
    # d'import di questo modulo, che l'``AgentLoop`` carica per riferimento.
    from jenny.cron.heartbeat_tasks import HeartbeatTask
    from jenny.cron.service import CronService
    from jenny.cron.types import CronJob

HEARTBEAT_JOB_ID = "heartbeat"


class HeartbeatFollowup:
    """Il turno d'annuncio di un subagent, visto dall'heartbeat."""

    def __init__(
        self,
        *,
        cron: "CronService",
        heartbeat_file: Callable[[], Path],
        now_ms: Callable[[], int],
        # Quando l'utente ha scritto per l'ultima volta, o ``None``. Arriva come
        # callback e non come ``SessionManager`` per la stessa ragione per cui
        # l'agente arriva come getter al dispatcher: fino all'onboarding l'agente
        # — e con lui le sessioni — non esiste ancora, e catturarlo qui
        # significherebbe catturare quel ``None`` per sempre.
        user_spoke_at_ms: Callable[[], int | None] = lambda: None,
    ) -> None:
        self._cron = cron
        self._heartbeat_file = heartbeat_file
        self._now_ms = now_ms
        self._user_spoke_at_ms = user_spoke_at_ms

    def _job_and_tasks(self, session_key: str) -> "tuple[CronJob, list[HeartbeatTask]] | None":
        """Job heartbeat + task del file, o ``None`` se qui non c'è niente da fare.

        Il controllo sulla session key sta in cima perché questo metodo viene
        chiamato per **ogni** annuncio di subagent, di qualunque sessione: fuori
        dall'heartbeat deve costare un confronto fra stringhe.

        Applica anche il riarmo, ed è il motivo per cui *entrambi* i metodi
        pubblici passano di qui: il turno d'annuncio è quello che decide per un
        controllo delegato, quindi se il riarmo non arrivasse anche a lui un
        utente che scrive fra un run e il suo annuncio si vedrebbe l'avviso
        subito, saltando la soglia che quel riarmo ha appena azzerato.
        """
        if session_key != HEARTBEAT_SESSION_KEY:
            return None
        job = self._cron.get_job(HEARTBEAT_JOB_ID)
        if job is None:
            return None
        try:
            content = self._heartbeat_file().read_text(encoding="utf-8")
        except OSError:
            return None
        tasks = parse_heartbeat_tasks(content)
        if not tasks:
            return None
        rearmed = rearm_after_user_message(
            job.state, user_spoke_at_ms=self._user_spoke_at_ms()
        )
        if rearmed:
            logger.debug(
                "Heartbeat follow-up: the user has written since, {} check(s) can be "
                "reported again: {}",
                len(rearmed),
                "; ".join(rearmed),
            )
        return job, tasks

    def prompt_block(self, session_key: str) -> str:
        """Le righe da aggiungere al turno d'annuncio. Stringa vuota il resto delle volte.

        Vuota anche quando l'heartbeat non ha delegato niente: un annuncio che
        non riguarda un controllo in sospeso vede esattamente il prompt di
        prima.
        """
        loaded = self._job_and_tasks(session_key)
        if loaded is None:
            return ""
        job, tasks = loaded
        pending = pending_tasks(job.state, tasks)
        if not pending:
            return ""
        return followup_block(
            pending,
            tasks_due_for_escalation(job.state, pending),
            tasks_already_warned(job.state, pending),
        )

    def record(self, session_key: str, *, final_text: str) -> None:
        """Registra i marcatori scritti dal turno d'annuncio: guasti e successi.

        Il silenzio non è ancora un verdetto — un annuncio che non dichiara
        niente non prova che il controllo sia andato bene, e non deve poter
        cancellare la sequenza di un altro task (v. ``record_followup_outcomes``).
        Ciò che è cambiato è che il successo ora **si può** dichiarare, e quando
        lo è chiude la voce; la voce che nessuno dichiara in nessun verso resta
        al run successivo, che la risolve tenendosi il ricordo di aver parlato.
        """
        marks = parse_could_not_check_marks(final_text)
        ok = parse_ok_marks(final_text)
        if not marks and not ok:
            return
        loaded = self._job_and_tasks(session_key)
        if loaded is None:
            return
        job, tasks = loaded
        pending = pending_tasks(job.state, tasks)
        if not pending:
            # Nessun controllo delegato in attesa: un marcatore qui non appartiene
            # a questo meccanismo, e attribuirlo a caso incolperebbe un task sano.
            logger.debug(
                "Heartbeat follow-up: {} mark(s) with nothing pending", len(marks) + len(ok)
            )
            return
        outcome = record_followup_outcomes(
            job.state,
            tasks,
            marks,
            now_ms=self._now_ms(),
            escalating=tasks_due_for_escalation(job.state, pending),
            # Non più ``spoke``, che il chiamante passava e che questo metodo
            # non chiede più: diceva che in quel turno un messaggio era uscito,
            # non di quale controllo parlasse (v. ``WARNED_MARKER``).
            warned=parse_warned_marks(final_text),
            ok=ok,
        )
        for task in outcome.recovered:
            logger.debug(
                "Heartbeat: delegated task '{}' reported back healthy, entry cleared", task.label
            )
        if not outcome.any_failure:
            # Un recupero non è un guasto, ma ha comunque cambiato lo stato: se
            # non lo si scrive, la voce chiusa riappare al riavvio e con lei il
            # ricordo di un avviso già dato.
            if outcome.recovered:
                self._cron.persist_job_state()
            return
        for task in outcome.failed:
            entry = job.state.task_checks[task.id]
            logger.warning(
                "Heartbeat: delegated task '{}' could not run ({} in a row): {}",
                task.label,
                entry.consecutive_could_not_check,
                outcome.reasons.get(task.id) or "no reason given",
            )
        if outcome.unattributed:
            logger.warning(
                "Heartbeat: {} unattributed {} line(s) on a subagent result: {}",
                len(outcome.unattributed),
                COULD_NOT_CHECK_MARKER,
                "; ".join(r or "no reason given" for r in outcome.unattributed),
            )
        if outcome.failed:
            self._cron.persist_job_state()
