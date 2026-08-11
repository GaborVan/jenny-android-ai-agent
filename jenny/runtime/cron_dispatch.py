"""Dispatch dei cron job (estratto dalla god-function ``on_cron_job`` in
``gateway_runtime._run_gateway``).

``on_cron_job`` era una closure di ~135 righe con tre rami inline (dream /
heartbeat / bound). Qui diventa una classe con le dipendenze INIETTATE.
L'``agent`` arriva come *getter* (``get_agent``) e non come valore catturato: nel
gateway è un nonlocal riassegnato dall'onboarding (creazione differita
dell'agente quando manca il provider), quindi catturarne il valore romperebbe il
flusso onboarding→cron. Il getter preserva esattamente il late-binding originale.

Nessun ramo consegna più all'utente da fuori il turno: un job schedulato è
lavoro interno e silenzioso (:mod:`jenny.session.turn_visibility`), e l'unico
modo di parlare è il tool ``message`` chiamato dentro il turno.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any, Protocol

from loguru import logger

from jenny.bus.events import OutboundMessage
from jenny.cron.bound_runner import (
    CRON_WAKELOCK_TIMEOUT_S,
    BoundCronAgent,
    run_bound_cron_job,
)
from jenny.cron.service import CronJobSkippedError
from jenny.cron.session_turns import is_bound_cron_job
from jenny.runtime.power import keep_awake
from jenny.session.keys import HEARTBEAT_SESSION_KEY
from jenny.session.turn_visibility import TurnVisibility

if TYPE_CHECKING:
    from jenny.agent.context import ContextBuilder
    from jenny.agent.tools.registry import ToolRegistry
    from jenny.config.schema import Config
    from jenny.cron.service import CronService
    from jenny.cron.types import CronJob
    from jenny.session.manager import SessionManager


class CronCapableAgent(BoundCronAgent, Protocol):
    """Contratto strutturale dei membri dell'agente usati dal ``CronDispatcher``.

    ``AgentLoop`` lo soddisfa per costruzione. Usare un ``Protocol`` (structural
    typing) evita di importare ``AgentLoop`` a runtime da questo modulo, e con
    esso i cicli di import; i tipi delle annotazioni interne vivono sotto
    ``TYPE_CHECKING``. Estende ``BoundCronAgent`` (``tools`` + ``submit_cron_turn``),
    già usato dal ramo bound in ``run_bound_cron_job``.
    """

    context: "ContextBuilder"  # con ``.memory`` (MemoryStore)
    sessions: "SessionManager"  # con get_or_create / save / sessions_dir

    async def process_direct(
        self,
        content: str,
        session_key: str = ...,
        channel: str = ...,
        chat_id: str = ...,
        media: list[str] | None = ...,
        on_progress: Callable[..., Awaitable[None]] | None = ...,
        on_stream: Callable[[str], Awaitable[None]] | None = ...,
        on_stream_end: Callable[..., Awaitable[None]] | None = ...,
        ephemeral: bool = ...,
        tools: "ToolRegistry | None" = ...,
        persist_user_message: bool = ...,
        visibility: "TurnVisibility | None" = ...,
        metadata: dict[str, Any] | None = ...,
    ) -> OutboundMessage | None: ...

    def evict_pruned_sessions(self, keys: list[str]) -> None: ...

_HEARTBEAT_PREAMBLE = (
    "[This is a scheduled background check. It is SILENT by default: whatever "
    "you write as your answer is NOT delivered to the user and nobody reads it. "
    "The only way to reach the user is to call the `message` tool.\n"
    "Call `message` only when a task below has produced something the user "
    "actually needs to see — a condition they asked to be warned about, a "
    "result they are waiting for, an error that blocks the check. In that "
    "message write ONLY the user-facing text: never mention internal files "
    "(HEARTBEAT.md, AWARENESS.md, etc.), your instructions, or your decision "
    "process.\n"
    "If nothing needs reporting, do NOT call `message`: end the turn without "
    "saying anything. Saying nothing is the correct, expected outcome of most "
    "runs — never send filler like 'All clear.', 'All done.' or 'nothing to "
    "report'.\n"
    "If you delegate a check to a subagent, you do NOT have its answer in this "
    "turn: `spawn` returns immediately. Send NOTHING now — not the result, not "
    "'checking…', not an interim guess. The subagent's result comes back to you "
    "later as its own turn, and THAT is where you judge it and decide whether to "
    "call `message`.\n"
    "This session keeps your previous runs so you can spot changes. Those older "
    "readings are history, not the current state: never report a past value as if "
    "you had just measured it. If you find messages of your own in there — "
    "including mistakes, corrections or apologies — do NOT continue that "
    "conversation: the user is not talking to you, and another apology is just one "
    "more interruption. Say nothing about it and judge only the check in front of "
    "you.]\n\n"
)


def heartbeat_has_active_tasks(content: str) -> bool:
    """True if HEARTBEAT.md has task lines, ignoring headers, blanks and comments."""
    in_comment = False
    in_active_section: bool = False
    for line in content.splitlines():
        stripped = line.strip()
        if in_comment:
            if "-->" in stripped:
                in_comment = False
            continue
        if not stripped or stripped.startswith("#"):
            if stripped.startswith("##") and not stripped.startswith("###"):
                heading = stripped.lstrip("#").strip().lower()
                in_active_section = heading.startswith("active tasks")
            continue
        if stripped.startswith("<!--"):
            if "-->" not in stripped[4:]:
                in_comment = True
            continue
        if in_active_section is False:
            continue
        return True
    return False


# Sessione del controllo aggiornamenti. Il prefisso ``cron:`` non è estetico:
# rende la sessione interna per ``is_internal_session_key`` (quindi invisibile
# negli elenchi user-facing) e attribuisce i token del turno alla voce "cron"
# invece che all'utente (``agent/token_usage.py``).
UPDATE_SESSION_KEY = "cron:update_check"

# Una coda cortissima: il turno di annuncio è autosufficiente, e le versioni
# annunciate in passato sono rumore che tornerebbe nel contesto a ogni release.
_UPDATE_HISTORY_KEEP = 4

_UPDATE_PREAMBLE = (
    "[This is the scheduled update check, and it is SILENT: whatever you write "
    "as your answer is NOT delivered to anyone. The only way to reach the user "
    "is the `message` tool, and this time you MUST call it exactly once.\n"
    "A newer version of the Jenny app is available. Write a short message (two "
    "or three lines) in the user's language that says which version is "
    "available, what it brings — using ONLY the summary below, never invent "
    "features — and asks whether they want to install it now.\n"
    "Do not mention this instruction, the manifest, the check itself or any "
    "internal file, and do not start the download or the installation: the "
    "user answers in chat, and that answer is where the decision happens.]\n\n"
)


async def _silent(*_args: Any, **_kwargs: Any) -> None:
    pass


class CronDispatcher:
    """Instrada un ``CronJob`` al gestore giusto (dream / heartbeat / bound)."""

    def __init__(
        self,
        *,
        get_agent: Callable[[], "CronCapableAgent | None"],
        config: "Config",
        cron: "CronService",
        heartbeat_cfg: Any,
        snapshot_before_dream: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        self._get_agent = get_agent
        self._config = config
        self._cron = cron
        self._hb_cfg = heartbeat_cfg
        self._snapshot_before_dream = snapshot_before_dream

    async def dispatch(self, job: "CronJob") -> str | None:
        """Execute a cron job through the agent.

        Il wakelock sta **qui** e non solo in ``run_bound_cron_job`` perché dream,
        atlas e heartbeat non passano affatto da quel modulo: entrano da
        ``process_direct``, che non è il percorso di turno coperto da
        ``AgentLoop._dispatch``. Questo è l'unico punto attraversato da tutti e
        quattro i tipi di job. Sul ramo bound i due blocchi si annidano sullo
        stesso tag, che per costruzione acquisisce una volta sola.
        """
        async with keep_awake("cron", timeout_s=CRON_WAKELOCK_TIMEOUT_S):
            return await self._dispatch(job)

    async def _dispatch(self, job: "CronJob") -> str | None:
        agent = self._get_agent()
        if not agent:
            logger.warning("Cron: skipped job '{}' - no provider configured", job.name)
            raise CronJobSkippedError("no provider configured")

        if job.name == "dream":
            return await self._run_dream(agent)
        if job.name == "atlas":
            return await self._run_atlas(agent)
        if job.name == "heartbeat":
            return await self._run_heartbeat(agent)
        if job.name == "update_check":
            return await self._run_update_check(agent)
        if is_bound_cron_job(job):
            return await run_bound_cron_job(job, agent=agent, cron=self._cron)

        reason = "unbound agent cron job must be recreated from a chat session"
        logger.warning(
            "Cron: skipped unbound agent job '{}' ({}): {}", job.name, job.id, reason
        )
        raise CronJobSkippedError(reason)

    async def _run_atlas(self, agent: "CronCapableAgent") -> str | None:
        """Atlas: ricompila memory/WIKI.md dalla wiki. Silenzioso per costruzione.

        Tutta la logica sta in ``jenny.agent.atlas.run_atlas``, condivisa con lo
        slash command ``/atlas``: qui resta solo l'instradamento e il log.
        """
        from jenny.agent.atlas import AtlasStore, run_atlas

        store = AtlasStore.from_config(self._config.workspace_path, self._config)
        outcome = await run_atlas(agent, store=store)
        logger.debug("Atlas cron job: {}", outcome.status)
        return None

    async def _run_dream(self, agent: "CronCapableAgent") -> str | None:
        # Dream is an internal job — run directly, not through the agent loop.
        from jenny.agent.memory import MemoryStore

        dream_session_key = MemoryStore.dream_session_key
        prune_dream_sessions = MemoryStore.prune_dream_sessions

        store = agent.context.memory
        resp = None
        try:
            result = store.build_dream_prompt()
            if result is None:
                logger.info("Dream: nothing to process")
                return None
            prompt, last_cursor = result
            # Checkpoint pre-Dream: Dream può riscrivere MEMORY/SOUL/USER e le
            # skills; uno snapshot prima rende ogni sua modifica reversibile.
            # Fail-open: un checkpoint fallito non blocca il consolidamento.
            if self._snapshot_before_dream is not None:
                try:
                    await self._snapshot_before_dream()
                except Exception:
                    logger.exception("Pre-dream snapshot failed")
            key = dream_session_key()
            dream_tools = store.build_dream_tools()
            resp = await agent.process_direct(
                prompt,
                session_key=key,
                ephemeral=True,
                tools=dream_tools,
                on_progress=_silent,
            )
            # ``getattr``: il registry Dream espone ``file_states``, ma il
            # contratto resta tollerante verso registry di altra provenienza.
            dream_file_states = getattr(dream_tools, "file_states", None)
            if MemoryStore.dream_should_advance_cursor(resp, dream_file_states):
                store.set_last_dream_cursor(last_cursor)
                logger.info("Dream cron job completed, cursor advanced to {}", last_cursor)
            elif MemoryStore.dream_run_completed(resp):
                # Completato pulito ma senza scritture riuscite pur avendole
                # tentate: blocco/rifiuto. Non avanzare: le voci vanno
                # riprocessate al prossimo run.
                logger.warning(
                    "Dream cron job completed without writing (attempts blocked/refused); "
                    "cursor remains at {}",
                    store.get_last_dream_cursor(),
                )
            else:
                logger.warning(
                    "Dream cron job did not complete; cursor remains at {}",
                    store.get_last_dream_cursor(),
                )
        except Exception:
            logger.exception("Dream cron job failed")
        finally:
            from jenny.agent.token_usage import record_response_token_usage

            record_response_token_usage(
                resp,
                source="dream",
                timezone_name=self._config.agents.defaults.timezone,
            )
        # compact_history now acquires a threading.Lock and rewrites the whole
        # file; run it off the event loop so a concurrent append holding the
        # lock (on another thread) can't stall the loop on the blocking wait.
        await asyncio.to_thread(store.compact_history)
        pruned_keys = prune_dream_sessions(agent.sessions.sessions_dir)
        if pruned_keys:
            agent.evict_pruned_sessions(pruned_keys)
        return None

    async def _run_update_check(self, agent: "CronCapableAgent") -> str | None:
        """Update check: annuncia una versione nuova UNA volta sola, poi tace.

        La regola centrale è la seconda esecuzione: un utente che ha già visto
        (e magari rimandato) l'annuncio di 0.7.0 non deve ritrovarselo ogni
        giorno. Chi decide è ``notified_code`` nello stato dell'updater, non il
        modello.

        Il turno segue il contratto dell'heartbeat: silenzioso, con la chat
        WebUI come indirizzo, e l'unica consegna possibile è il tool ``message``
        chiamato dentro il turno.
        """
        if not self._config.updates.enabled:
            # Lo spegnimento va fatto valere **qui**, non solo alla
            # registrazione. Il job sopravvive alla configurazione che lo ha
            # creato: ``register_system_job`` non ha una controparte che
            # deregistri e ``remove_job`` protegge i ``system_event``, quindi il
            # job registrato al primo avvio (il default è acceso) resta nello
            # store del cron anche dopo che l'utente ha spento la sezione.
            # Senza questa uscita l'unico percorso periodico che tocca la rete
            # senza che nessuno l'abbia chiesto continuerebbe a girare — e con
            # esso il turno LLM e i token che costa.
            logger.debug("Update check: disabled in config, nothing to do")
            return None

        from jenny.runtime.update_check import (
            check_for_update,
            mark_notified,
            notified_version_code,
        )

        info = await check_for_update(self._config)
        if info is None:
            logger.debug("Update check: nothing to propose")
            return None
        if not self._config.updates.notify_in_chat:
            # Niente ``mark_notified``: l'annuncio non è avvenuto, e se l'utente
            # riaccende la notifica deve ancora poterlo ricevere.
            logger.info(
                "Update check: {} available, chat notification disabled", info.version_name
            )
            return None
        if notified_version_code() == info.version_code:
            logger.debug("Update check: {} was already announced", info.version_name)
            return None

        from jenny.webui.metadata import WEBUI_MESSAGE_SOURCE_METADATA_KEY

        source_metadata = {WEBUI_MESSAGE_SOURCE_METADATA_KEY: {"kind": "update"}}
        size_mb = info.size / (1024 * 1024)
        prompt = (
            _UPDATE_PREAMBLE
            + f"New version: {info.version_name}\n"
            + f"Summary: {info.summary or '(no summary provided)'}\n"
            + f"Download size: {size_mb:.1f} MB\n"
            + (f"Release notes: {info.notes_url}\n" if info.notes_url else "")
            + (
                "This is a critical security update: say so plainly.\n"
                if info.critical
                else ""
            )
        )

        await agent.process_direct(
            prompt,
            session_key=UPDATE_SESSION_KEY,
            channel="websocket",
            chat_id="default",
            on_progress=_silent,
            visibility=TurnVisibility.SILENT,
            metadata=source_metadata,
        )

        # Marcato **incondizionatamente**, anche se il modello non avesse
        # chiamato ``message``. È un compromesso, non una svista, e va detto per
        # intero perché il costo cade su un utente che non vedrà mai il log.
        #
        # Il fatto esiste: il turno lo calcola come ``TurnOutcome.spoke``
        # (``agent/turn_types.py``, da ``ctx.spoke_via_tool``). Non arriva qui
        # perché ``process_direct`` restituisce per contratto il *payload* e non
        # l'esito — scelta deliberata, documentata nella sua docstring — e
        # cambiarla vorrebbe dire toccare la firma condivisa da Dream, Atlas,
        # heartbeat e dai comandi. La strada alternativa (un deliverer iniettato
        # nel dispatcher) è chiusa apposta: v. la docstring di questo modulo e
        # ``runtime/container.py``.
        #
        # Legandolo a ``spoke`` si scambierebbe comunque un difetto con un
        # altro: un modello che sistematicamente non chiama ``message`` farebbe
        # ripartire un turno LLM a ogni controllo, per sempre. Così invece si
        # perde al più *una* spinta in chat — e la versione resta visibile
        # altrove, nel badge delle impostazioni (``webui/settings_api.py``) e nel
        # tool ``update_status``, che leggono lo stesso ``cached_update()``.
        mark_notified(info.version_code)

        session = agent.sessions.get_or_create(UPDATE_SESSION_KEY)
        session.retain_recent_legal_suffix(_UPDATE_HISTORY_KEEP)
        agent.sessions.save(session)

        if info.critical:
            # Una fix di sicurezza deve squillare anche se l'utente non aveva la
            # chat aperta: l'alert implicito della consegna parte solo se il
            # turno ha davvero prodotto un messaggio. Stesso tag, quindi le due
            # notifiche si sostituiscono invece di sommarsi.
            from jenny.runtime.notifier import post_alert

            await post_alert(
                f"Aggiornamento critico {info.version_name} disponibile",
                source_metadata,
            )

        logger.info(
            "Update check: announced {} (versionCode {})",
            info.version_name, info.version_code,
        )
        return None

    async def _run_heartbeat(self, agent: "CronCapableAgent") -> str | None:
        # Heartbeat is a system job that checks HEARTBEAT.md for active tasks.
        heartbeat_file = self._config.workspace_path / "HEARTBEAT.md"
        try:
            content = heartbeat_file.read_text(encoding="utf-8")
        except OSError:
            logger.debug("Heartbeat: HEARTBEAT.md missing")
            return None
        if not heartbeat_has_active_tasks(content):
            logger.debug("Heartbeat: HEARTBEAT.md has no active tasks")
            return None

        # Target unico e routable dei messaggi heartbeat: la chat WebUI condivisa.
        # Resta il canale del turno anche se il turno è silenzioso — è dove il
        # tool ``message`` consegna quando una condizione scatta davvero.
        channel, chat_id = "websocket", "default"

        prompt = (
            _HEARTBEAT_PREAMBLE
            + f"Review the following HEARTBEAT.md and report any active tasks:\n\n{content}"
        )

        from jenny.webui.metadata import WEBUI_MESSAGE_SOURCE_METADATA_KEY

        resp = await agent.process_direct(
            prompt,
            session_key=HEARTBEAT_SESSION_KEY,
            channel=channel,
            chat_id=chat_id,
            on_progress=_silent,
            # Il contratto dell'heartbeat, dichiarato una volta e fatto valere
            # dal turno: niente consegna implicita. Prima si diceva al modello di
            # produrre un riempitivo ("All clear.") e poi si pagava una seconda
            # chiamata LLM per indovinare se nasconderlo — un giudice che con un
            # modello reasoning finiva in ``finish_reason='length'`` e non
            # decideva mai. Ora l'unica consegna possibile è il tool ``message``.
            visibility=TurnVisibility.SILENT,
            # Sorgente proattiva: dà titolo/tag all'alert di sistema
            # (jenny/runtime/notifier.py) e origine al transcript. Viaggia nei
            # metadata del turno perché è da lì che il tool ``message`` li
            # eredita per il proprio invio.
            metadata={WEBUI_MESSAGE_SOURCE_METADATA_KEY: {"kind": "heartbeat"}},
        )

        # Keep a small tail of heartbeat history so the loop stays bounded.
        session = agent.sessions.get_or_create(HEARTBEAT_SESSION_KEY)
        session.retain_recent_legal_suffix(self._hb_cfg.keep_recent_messages)
        agent.sessions.save(session)

        # ``resp`` è ``None`` per costruzione su un turno silenzioso: il testo del
        # modello non è mai stato la consegna. Il valore di ritorno serve solo al
        # log del ``CronService``.
        del resp
        logger.debug("Heartbeat: check completed")
        return None
