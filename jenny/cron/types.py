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
    status: Literal["ok", "error", "skipped", "silenced"]
    duration_ms: int = 0
    error: str | None = None


@dataclass
class CronJobState:
    """Runtime state of a job."""
    next_run_at_ms: int | None = None
    last_run_at_ms: int | None = None
    last_status: Literal["ok", "error", "skipped", "silenced"] | None = None
    last_error: str | None = None
    run_history: list[CronRunRecord] = field(default_factory=list)


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
        kwargs["schedule"] = CronSchedule(**kwargs.get("schedule", {"kind": "every"}))
        kwargs["payload"] = CronPayload(**kwargs.get("payload", {}))
        kwargs["state"] = CronJobState(**state_kwargs)
        return cls(**kwargs)


@dataclass
class CronStore:
    """Persistent store for cron jobs."""
    version: int = 1
    jobs: list[CronJob] = field(default_factory=list)
