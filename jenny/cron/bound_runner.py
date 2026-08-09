"""Execution helpers for session-bound cron jobs."""

from __future__ import annotations

import asyncio
import hashlib
import time
import uuid
from typing import Any, Protocol

from jenny.agent.tools.cron import CronTool
from jenny.bus.events import InboundMessage, OutboundMessage
from jenny.cron.session_delivery import origin_delivery_context
from jenny.cron.session_turns import (
    CRON_DEFER_UNTIL_IDLE_META,
    CRON_MONITOR_META,
    CRON_TRIGGER_META,
    cron_monitor_spoke,
    monitor_session_key,
)
from jenny.cron.types import CronJob, CronJobSilencedError
from jenny.cron.webui_metadata import cron_proactive_delivery_metadata
from jenny.runtime.power import keep_awake
from jenny.utils.prompt_templates import render_template

# Coda di storico tenuta nella sessione isolata di un monitor. Stesso valore del
# default di ``heartbeat.keep_recent_messages``: un monitor gira all'infinito e
# senza potatura la sua sessione cresce senza limite.
MONITOR_KEEP_RECENT_MESSAGES = 8

# Scadenza del wakelock che copre l'esecuzione di un job cron. Tag distinto da
# quello del turno perché il job può essere DIFFERITO (``CRON_DEFER_UNTIL_IDLE``)
# e restare in attesa che la sessione si liberi: il lavoro vero è tutto dentro
# questo blocco, e senza CPU un job che scade a schermo spento resta appeso
# invece di girare — è la causa misurata degli scarti fra 30 e 83 minuti su un
# cron da mezz'ora.
CRON_WAKELOCK_TIMEOUT_S = 1800.0


class BoundCronAgent(Protocol):
    tools: Any
    sessions: Any

    async def submit_cron_turn(self, msg: InboundMessage) -> OutboundMessage | None:
        ...


class CronRunRecorder(Protocol):
    def write_run_record(self, run_id: str, record: dict[str, Any]) -> None:
        ...


def _cron_prompt_ref(prompt: str, *, monitor: bool = False) -> dict[str, Any]:
    return {
        "id": "cron.agent_turn.monitor" if monitor else "cron.agent_turn.reminder",
        "version": 1,
        "sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
    }


def _bound_session_delivery_context(
    job: CronJob,
    *,
    turn_seed: str,
    source_label: str | None,
    monitor: bool = False,
) -> tuple[str, str, dict[str, Any]]:
    channel, chat_id, metadata = origin_delivery_context(job)

    if monitor:
        # ``origin_metadata`` è stato catturato quando l'utente ha creato il job,
        # quindi può contenere ``_wants_stream`` della sua sessione WebUI. Un
        # monitor ha l'outbound finale soppresso: lasciando il flag, il gate di
        # ``jenny/agent/loop.py`` streammerebbe comunque testo in chat per poi
        # non consegnare nulla — cioè proprio il silenzio che il monitor promette.
        metadata.pop("_wants_stream", None)

    if channel == "websocket":
        metadata["webui"] = True
        metadata.update(
            cron_proactive_delivery_metadata(
                "websocket",
                metadata,
                turn_seed=turn_seed,
                source_label=source_label,
            )
        )

    return channel, chat_id, metadata


def _prune_monitor_session(agent: BoundCronAgent, session_key: str) -> None:
    """Tiene limitata la sessione isolata di un monitor, che gira all'infinito."""
    session = agent.sessions.get_or_create(session_key)
    session.retain_recent_legal_suffix(MONITOR_KEEP_RECENT_MESSAGES)
    agent.sessions.save(session)


async def run_bound_cron_job(
    job: CronJob,
    *,
    agent: BoundCronAgent,
    cron: CronRunRecorder,
) -> str | None:
    """Execute a session-bound cron job as a normal agent session turn.

    Guscio sottile attorno a :func:`_run_bound_cron_job`: tiene la CPU sveglia
    per tutta la durata del job. Il turno che ne nasce prenderà anche il proprio
    lock ``"turn"`` — sono tag diversi e indipendenti, e il conteggio di ciascuno
    torna a zero per conto suo; qui serve comunque, perché la parte del job
    fuori dal turno (attesa della sessione libera, scrittura delle run record,
    potatura della sessione monitor) resta scoperta.
    """
    async with keep_awake("cron", timeout_s=CRON_WAKELOCK_TIMEOUT_S):
        return await _run_bound_cron_job(job, agent=agent, cron=cron)


async def _run_bound_cron_job(
    job: CronJob,
    *,
    agent: BoundCronAgent,
    cron: CronRunRecorder,
) -> str | None:
    """Corpo del job: identico a prima, senza la gestione del wakelock."""
    session_key = job.payload.session_key
    if not session_key:
        raise ValueError(f"cron job {job.id} is missing payload.session_key")

    monitor = job.payload.mode == "monitor"
    # Un monitor gira in una sessione tutta sua: ``payload.session_key`` resta il
    # target di consegna e il valore registrato nella run record, ma non è più la
    # sessione in cui il turno viene eseguito.
    turn_session_key = monitor_session_key(job.id) if monitor else session_key

    prompt = render_template(
        "agent/cron_monitor.md" if monitor else "agent/cron_reminder.md",
        strip=True,
        message=job.payload.message,
    )
    prompt_ref = _cron_prompt_ref(prompt, monitor=monitor)
    run_id = f"{job.id}:{int(time.time() * 1000)}:{uuid.uuid4().hex[:8]}"
    channel, chat_id, metadata = _bound_session_delivery_context(
        job,
        turn_seed=f"cron:{job.id}",
        source_label=job.name,
        monitor=monitor,
    )
    if monitor:
        metadata[CRON_MONITOR_META] = True
    metadata[CRON_TRIGGER_META] = {
        "job_id": job.id,
        "job_name": job.name,
        "run_id": run_id,
        "prompt_ref": prompt_ref,
        "persist_content": (
            f"Scheduled cron job triggered: {job.name}\n\n{job.payload.message}"
        ),
    }
    metadata[CRON_DEFER_UNTIL_IDLE_META] = True
    run_record_base: dict[str, Any] = {
        "job_id": job.id,
        "job_name": job.name,
        "session_key": session_key,
        "prompt_ref": prompt_ref,
        "prompt_vars": {"message": job.payload.message},
        "rendered_prompt": prompt,
    }

    cron.write_run_record(
        run_id,
        {
            **run_record_base,
            "status": "queued",
        },
    )

    cron_tool = agent.tools.get("cron")
    cron_token = None
    if isinstance(cron_tool, CronTool):
        cron_token = cron_tool.set_cron_context(True)
    try:
        resp = await agent.submit_cron_turn(
            InboundMessage(
                channel=channel,
                sender_id="cron",
                chat_id=chat_id,
                content=prompt,
                # Stesso oggetto dict, non una copia: per un monitor la FSM ci
                # scrive dentro ``CRON_SPOKE_META`` ed è l'unico segnale che
                # dice se il turno ha parlato (l'outbound finale è sempre None).
                metadata=metadata,
                session_key_override=turn_session_key,
            )
        )
    except (Exception, asyncio.CancelledError) as exc:
        error_text = str(exc) or exc.__class__.__name__
        cron.write_run_record(
            run_id,
            {
                **run_record_base,
                "status": "error",
                "error": error_text,
            },
        )
        raise
    finally:
        if isinstance(cron_tool, CronTool) and cron_token is not None:
            cron_tool.reset_cron_context(cron_token)

    response = resp.content if resp else ""

    if monitor:
        _prune_monitor_session(agent, turn_session_key)
        if not cron_monitor_spoke(metadata):
            cron.write_run_record(
                run_id,
                {
                    **run_record_base,
                    "status": "silenced",
                    "delivery": "suppressed",
                },
            )
            raise CronJobSilencedError(f"cron monitor job {job.id} had nothing to report")

    record: dict[str, Any] = {
        **run_record_base,
        "status": "ok",
        "response": response,
    }
    if monitor:
        # L'unica consegna possibile per un monitor è il tool ``message``.
        record["delivery"] = "agent_message"
    cron.write_run_record(run_id, record)
    return response
