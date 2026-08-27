"""Dentro un progetto non si programma niente, e il tool è l'unico a dirlo.

Passo **3** di ``roadmap/progetti-passi.md``.

Il guasto che questo cancello previene non è un errore: oggi un promemoria
creato dentro un progetto **funziona**. Il job si porta dietro
``session_key=project:foo`` e i metadati d'origine, quindi scatta in quella
sessione, con la cartella giusta, e consegna in *quel* thread — che è la stanza
in cui entri quando decidi di lavorare a quell'argomento. L'avviso arriva dove
non c'è nessuno, e sembra riuscito.

**Non c'è nessuna riga nel prompt di sistema** (deciso il 22/08). Una riga nel
blocco se la guadagna la regola che sbatteresti addosso di continuo e che ti
costringe a ripianificare — è il caso del confine di scrittura; un promemoria è
raro e sta in piedi da solo. Quindi questo rifiuto è *l'unico* posto in cui
Jenny lo viene a sapere, ed è il motivo per cui
``test_the_refusal_says_where_reminders_do_live`` non è un test sul wording:
senza quella frase, il turno finisce in un "non posso" che manda via a mani
vuote invece che nella chat personale.

Il cancello copre tutte e tre le azioni, non solo ``add``: ``list`` mostrerebbe
la sveglia personale a una conversazione di lavoro, e ``remove`` la
cancellerebbe da lì dentro.
"""

from __future__ import annotations

from typing import Any

import pytest

from jenny.agent.tools.context import RequestContext
from jenny.agent.tools.cron import CronTool
from jenny.cron.types import CronJob, CronSchedule


class _RecordingCronService:
    """CronService finto: registra ogni scrittura, così un cancello bucato si vede."""

    def __init__(self, jobs: list[CronJob] | None = None) -> None:
        self.added: list[dict[str, Any]] = []
        self.removed: list[str] = []
        self._jobs = jobs or []

    def add_job(self, **kwargs: Any) -> CronJob:
        self.added.append(kwargs)
        return CronJob(id="job-1", name=kwargs.get("name", "x"))

    def list_jobs(self) -> list[CronJob]:
        return list(self._jobs)

    def get_job(self, _job_id: str) -> None:
        return None

    def remove_job(self, job_id: str) -> str:
        self.removed.append(job_id)
        return "removed"


_PERSONAL_JOB = CronJob(
    id="job-sveglia",
    name="pillola-serale",
    schedule=CronSchedule(kind="cron", expr="0 21 * * *", tz="UTC"),
)


def _tool(session_key: str, jobs: list[CronJob] | None = None) -> tuple[CronTool, _RecordingCronService]:
    service = _RecordingCronService(jobs)
    tool = CronTool(service, default_timezone="UTC")
    tool.set_context(
        RequestContext(channel="websocket", chat_id="default", session_key=session_key)
    )
    return tool, service


# ── Il cancello, azione per azione ────────────────────────────────────────


@pytest.mark.parametrize(
    "params",
    [
        {"action": "add", "message": "ricordami la riunione", "at": "2026-09-01T09:00:00"},
        {"action": "add", "message": "controlla ogni ora", "every_seconds": 3600},
        {"action": "list"},
        {"action": "remove", "job_id": "job-sveglia"},
    ],
    ids=["add-at", "add-every", "list", "remove"],
)
async def test_no_action_gets_through_from_a_project(params: dict[str, Any]) -> None:
    tool, service = _tool("project:patreon", jobs=[_PERSONAL_JOB])

    result = await tool.execute(**params)

    assert "Not here" in result
    assert service.added == [], "un job creato da qui scatterebbe in un thread che nessuno guarda"
    assert service.removed == [], "la sveglia personale non si cancella da una chat di lavoro"


async def test_list_does_not_leak_the_personal_schedule() -> None:
    """``list`` è una lettura, ma di cosa: «chi sei viaggia, dove altro lavori no»."""
    tool, _ = _tool("project:patreon", jobs=[_PERSONAL_JOB])

    result = await tool.execute(action="list")

    assert _PERSONAL_JOB.name not in result
    assert _PERSONAL_JOB.id not in result


def test_the_refusal_says_where_reminders_do_live() -> None:
    """Nessuna riga nel prompt lo dice: se non lo dice qui, non lo dice nessuno."""
    from jenny.agent.tools.cron import _PROJECT_REFUSAL

    lowered = _PROJECT_REFUSAL.lower()
    assert "personal chat" in lowered, (
        "il rifiuto deve indirizzare, non solo negare: è l'unico posto in cui Jenny impara "
        "dove i promemoria si fanno, e da cui lo ridice all'utente"
    )


# ── E non tocca nient'altro ───────────────────────────────────────────────


@pytest.mark.parametrize(
    "session_key",
    ["unified:default", "cron:update_check", "dream:default"],
    ids=["personale", "interna-cron", "interna-dream"],
)
async def test_everywhere_else_still_schedules(session_key: str) -> None:
    """Il cancello è acceso da una chiave sola: le interne restano com'erano.

    Le due interne non sono decorazione. Dream e il controllo aggiornamenti
    riarmano i propri job passando di qui, e un cancello scritto come «tutto
    quel che non è la chat personale» li spegnerebbe in silenzio.
    """
    tool, service = _tool(session_key)

    result = await tool.execute(
        action="add", message="ricordami la riunione", at="2026-09-01T09:00:00"
    )

    assert "Not here" not in result
    assert len(service.added) == 1
    assert service.added[0]["session_key"] == session_key


async def test_without_a_session_the_old_error_survives() -> None:
    """Senza contesto il rifiuto non è questo, ed è giusto così.

    ``set_context`` potrebbe non essere passato: chiudere di default per una
    svista di cablaggio spegnerebbe i promemoria ovunque, in silenzio. Qui il
    tool deve dare l'errore che dava già prima del passo 3.
    """
    tool, service = _tool("")

    result = await tool.execute(
        action="add", message="ricordami la riunione", at="2026-09-01T09:00:00"
    )

    assert "Not here" not in result
    assert "must be created from a chat session" in result
    assert service.added == []
