"""Cron types."""

from dataclasses import dataclass, field
from typing import Any, Literal


class CronJobSilencedError(Exception):
    """Il job monitor ha deciso di non parlare: è un esito **normale**.

    Non segnala un guasto — al contrario, è il caso previsto per un job in
    ``mode="monitor"``: ha guardato, non c'era niente da riferire, e tacere è
    la risposta giusta. Viaggia come eccezione per lo stesso motivo di
    ``CronJobSkippedError`` (``cron/service.py``): la decisione nasce in fondo
    alla callback, dove l'unico modo di dire "chiudi qui senza consegnare
    niente" senza far passare un valore sentinella per tutti gli strati
    intermedi è sollevare.

    Sta in ``types.py`` e non accanto a ``CronJobSkippedError`` perché la
    sollevano i chiamanti (il runner del turno), che importano già i tipi cron
    ma non devono dipendere dal servizio.
    """


class CronMonitorCouldNotCheckError(CronJobSilencedError):
    """Il monitor ha girato ma **il controllo non è avvenuto**: terzo stato.

    Fino a qui un monitor aveva due esiti soli, e producevano lo stesso identico
    output — niente. "Ho guardato e va tutto bene" e "non sono riuscito a
    guardare" erano indistinguibili, quindi un monitor rotto sembrava un
    giardino sano. Questa eccezione è quel terzo stato, e resta **muta**: come
    ``CronJobSilencedError`` non consegna nulla all'utente, si limita a farsi
    registrare (v. ``CronJobState.consecutive_could_not_check``).

    Sottoclasse di ``CronJobSilencedError`` di proposito: chiunque oggi tratti
    il silenzio come "nessuna consegna è avvenuta" continua ad avere ragione
    anche qui. Chi vuole distinguere lo fa mettendo il proprio ``except`` per
    primo — è ciò che fa ``CronService._execute_job``.

    La solleva anche l'heartbeat, che di suo esegue N task in un turno solo: lì
    è il **riassunto** del run ("almeno un task non è partito", col motivo di
    ciascuno in ``reason``), mentre quale task sia sta in
    ``CronJobState.task_checks``. Il nome dice "monitor" perché è dove il terzo
    esito è nato; rinominarla costerebbe più di quanto chiarisca.

    Attributes:
        reason: riga breve scritta dal modello su cosa lo ha bloccato.
        escalated: il turno aveva l'istruzione di avvisare l'utente **e** ha
            parlato. Il servizio lo usa per non ripetere l'avviso a ogni ciclo
            finché lo stato non cambia.
    """

    def __init__(
        self,
        message: str = "",
        *,
        reason: str | None = None,
        escalated: bool = False,
    ) -> None:
        super().__init__(message)
        self.reason = reason
        self.escalated = escalated


# Vocabolario degli esiti registrati per un run. ``silenced`` è il "ho
# controllato e non c'era niente da dire" del piano (``no_finding``); il nome
# resta quello già scritto negli store esistenti e nella WebUI.
CronRunStatus = Literal["ok", "error", "skipped", "silenced", "could_not_check"]


@dataclass
class CronSchedule:
    """Schedule definition for a cron job."""
    kind: Literal["at", "every", "cron"]
    # For "at": timestamp in ms
    at_ms: int | None = None
    # For "every": interval in ms
    every_ms: int | None = None
    # For "cron": cron expression (e.g. "0 9 * * *")
    expr: str | None = None
    # Timezone for cron expressions
    tz: str | None = None


@dataclass
class CronPayload:
    """What to do when the job runs."""
    kind: Literal["system_event", "agent_turn"] = "agent_turn"
    # "reminder" parla sempre; "monitor" gira in silenzio e parla solo se ha
    # qualcosa da dire. Default "reminder": gli store scritti prima di questo
    # campo si ricaricano con il comportamento che avevano.
    mode: Literal["reminder", "monitor"] = "reminder"
    message: str = ""
    session_key: str | None = None  # original session key for correct session recording
    origin_channel: str | None = None
    origin_chat_id: str | None = None
    origin_metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class CronRunRecord:
    """A single execution record for a cron job."""
    run_at_ms: int
    status: CronRunStatus
    duration_ms: int = 0
    error: str | None = None


@dataclass
class CronTaskCheckState:
    """Sequenza di controlli mancati di UN task dell'heartbeat.

    Un run dell'heartbeat copre N task scritti a mano in ``HEARTBEAT.md``: un
    contatore solo per tutto il file oscillerebbe per un task rotto su quattro
    sani, e all'utente arriverebbe "l'heartbeat è rotto" invece del nome del
    controllo che non parte. La chiave è l'id del task (v.
    ``jenny/cron/heartbeat_tasks.py``), e una voce esiste **solo** finché quel
    task è rotto: assente vuol dire sano.

    Attributes:
        consecutive_could_not_check: run consecutivi in cui il task non è stato
            eseguito.
        since_ms: inizio della sequenza in corso — "da quando è rotto?", che il
            conteggio da solo non dice.
        escalated: l'utente è già stato avvisato di QUESTA sequenza.
        label: prima riga del task, per nominarlo in un log o nella WebUI senza
            dover rileggere il file.
        pending_since_ms: il run che ha appena girato ha **delegato** questo
            task a un subagent, e l'esito non si sa ancora. Serve a una cosa
            sola: impedire che quel silenzio venga letto come "eseguito" e
            azzeri la sequenza prima che il verdetto arrivi. Vive al massimo
            un ciclo — il run successivo, se un verdetto non è arrivato, lo
            risolve in modo ottimistico (v. ``record_task_outcomes``), quindi
            una voce "in sospeso" non può restare appesa per sempre.
    """

    consecutive_could_not_check: int = 0
    since_ms: int | None = None
    escalated: bool = False
    label: str = ""
    pending_since_ms: int | None = None


@dataclass
class CronJobState:
    """Runtime state of a job."""
    next_run_at_ms: int | None = None
    last_run_at_ms: int | None = None
    last_status: CronRunStatus | None = None
    last_error: str | None = None
    run_history: list[CronRunRecord] = field(default_factory=list)

    # ── monitor: "non ho potuto controllare" ──
    # Il conteggio vive qui, nello store dei job, e non in un file a parte: è lo
    # stato per-job che la WebUI, il tool ``cron`` e il servizio leggono già
    # insieme al resto, e che eredita atomicità, backup e quarantena dello store.
    # Azzerato da un run che dimostra che il controllo è avvenuto (``ok`` o
    # ``silenced``); ``error`` e ``skipped`` lo lasciano com'è, perché non
    # dimostrano né l'una né l'altra cosa.
    consecutive_could_not_check: int = 0
    # Inizio della sequenza in corso: è la risposta a "da quando è rotto?", che
    # il conteggio da solo non dà.
    could_not_check_since_ms: int | None = None
    # L'utente è già stato avvisato di QUESTA sequenza. Ciò che rende l'avviso
    # uno solo per guasto invece di uno per ciclo.
    could_not_check_escalated: bool = False

    # ── heartbeat: lo stesso terzo stato, ma per singolo task ──
    # Solo il job ``heartbeat`` la popola: è l'unico che esegue N controlli in
    # un turno solo. I tre campi qui sopra restano il riassunto del run intero
    # ("almeno un task non è partito"), questa mappa dice quale.
    task_checks: dict[str, CronTaskCheckState] = field(default_factory=dict)


@dataclass
class CronJob:
    """A scheduled job."""
    id: str
    name: str
    enabled: bool = True
    schedule: CronSchedule = field(default_factory=lambda: CronSchedule(kind="every"))
    payload: CronPayload = field(default_factory=CronPayload)
    state: CronJobState = field(default_factory=CronJobState)
    created_at_ms: int = 0
    updated_at_ms: int = 0
    delete_after_run: bool = False

    @classmethod
    def from_dict(cls, kwargs: dict):
        state_kwargs = dict(kwargs.get("state", {}))
        state_kwargs["run_history"] = [
            record if isinstance(record, CronRunRecord) else CronRunRecord(**record)
            for record in state_kwargs.get("run_history", [])
        ]
        state_kwargs["task_checks"] = {
            task_id: entry if isinstance(entry, CronTaskCheckState) else CronTaskCheckState(**entry)
            for task_id, entry in (state_kwargs.get("task_checks") or {}).items()
        }
        kwargs["schedule"] = CronSchedule(**kwargs.get("schedule", {"kind": "every"}))
        kwargs["payload"] = CronPayload(**kwargs.get("payload", {}))
        kwargs["state"] = CronJobState(**state_kwargs)
        return cls(**kwargs)


@dataclass
class CronStore:
    """Persistent store for cron jobs."""
    version: int = 1
    jobs: list[CronJob] = field(default_factory=list)
