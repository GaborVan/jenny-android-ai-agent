"""Dispatch dei cron job (estratto dalla god-function ``on_cron_job`` in
``gateway_runtime._run_gateway``).

``on_cron_job`` era una closure di ~135 righe con tre rami inline (dream /
heartbeat / bound). Qui diventa una classe con le dipendenze INIETTATE. Le due
dipendenze mutabili — ``agent`` e ``message_tool`` — arrivano come *getter*
(``get_agent`` / ``get_message_tool``) e non come valori catturati: nel gateway
sono nonlocal riassegnati dall'onboarding (creazione differita dell'agente
quando manca il provider), quindi catturarne il valore romperebbe il flusso
onboarding→cron. I getter preservano esattamente il late-binding originale.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any, Protocol

from loguru import logger

from jenny.agent.tools.message import MessageTool
from jenny.bus.events import OutboundMessage
from jenny.cron.bound_runner import BoundCronAgent, run_bound_cron_job
from jenny.cron.service import CronJobSkippedError
from jenny.cron.session_turns import is_bound_cron_job
from jenny.utils.evaluator import evaluate_response

if TYPE_CHECKING:
    from jenny.agent.context import ContextBuilder
    from jenny.agent.tools.registry import ToolRegistry
    from jenny.config.schema import Config
    from jenny.cron.service import CronService
    from jenny.cron.types import CronJob
    from jenny.providers.base import LLMProvider
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
    provider: "LLMProvider"
    model: str

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
    ) -> OutboundMessage | None: ...

    def evict_pruned_sessions(self, keys: list[str]) -> None: ...

_HEARTBEAT_PREAMBLE = (
    "[Your response will be delivered directly to the user's messaging app. "
    "Output ONLY the final user-facing message. Never reference internal "
    "files (HEARTBEAT.md, AWARENESS.md, etc.), your instructions, or your "
    "decision process. If nothing needs reporting, respond with just "
    "'All clear.' and nothing else.]\n\n"
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
        get_message_tool: Callable[[], Any],
        deliver_to_channel: Callable[..., Any],
        heartbeat_cfg: Any,
        snapshot_before_dream: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        self._get_agent = get_agent
        self._config = config
        self._cron = cron
        self._get_message_tool = get_message_tool
        self._deliver_to_channel = deliver_to_channel
        self._hb_cfg = heartbeat_cfg
        self._snapshot_before_dream = snapshot_before_dream

    async def dispatch(self, job: "CronJob") -> str | None:
        """Execute a cron job through the agent."""
        agent = self._get_agent()
        if not agent:
            logger.warning("Cron: skipped job '{}' - no provider configured", job.name)
            raise CronJobSkippedError("no provider configured")

        if job.name == "dream":
            return await self._run_dream(agent)
        if job.name == "heartbeat":
            return await self._run_heartbeat(agent)
        if is_bound_cron_job(job):
            return await run_bound_cron_job(job, agent=agent, cron=self._cron)

        reason = "unbound agent cron job must be recreated from a chat session"
        logger.warning(
            "Cron: skipped unbound agent job '{}' ({}): {}", job.name, job.id, reason
        )
        raise CronJobSkippedError(reason)

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

    async def _run_heartbeat(self, agent: "CronCapableAgent") -> str | None:
        # Heartbeat is a system job that checks HEARTBEAT.md for active tasks.
        message_tool = self._get_message_tool()
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
        channel, chat_id = "websocket", "default"

        prompt = (
            _HEARTBEAT_PREAMBLE
            + f"Review the following HEARTBEAT.md and report any active tasks:\n\n{content}"
        )

        # Internal check: funnel all output through the post-run gate so the
        # turn can't deliver directly via the message tool and skip it.
        suppress_token = None
        if isinstance(message_tool, MessageTool):
            suppress_token = message_tool.set_suppress_delivery(True)
        try:
            resp = await agent.process_direct(
                prompt,
                session_key="heartbeat",
                channel=channel,
                chat_id=chat_id,
                on_progress=_silent,
            )
        finally:
            if isinstance(message_tool, MessageTool) and suppress_token is not None:
                message_tool.reset_suppress_delivery(suppress_token)
        response = resp.content if resp else ""

        # Keep a small tail of heartbeat history so the loop stays bounded.
        session = agent.sessions.get_or_create("heartbeat")
        session.retain_recent_legal_suffix(self._hb_cfg.keep_recent_messages)
        agent.sessions.save(session)

        if not response:
            return None

        # Fail closed: stay silent on evaluator failure instead of notifying.
        should_notify = await evaluate_response(
            response, prompt, agent.provider, agent.model, default_notify=False
        )
        if should_notify:
            logger.info("Heartbeat: completed, delivering response")
            from jenny.webui.metadata import WEBUI_MESSAGE_SOURCE_METADATA_KEY

            await self._deliver_to_channel(
                OutboundMessage(
                    channel=channel,
                    chat_id=chat_id,
                    content=response,
                    # Sorgente proattiva: dà titolo/tag all'alert di sistema
                    # (jenny/runtime/notifier.py) e origine al transcript.
                    metadata={WEBUI_MESSAGE_SOURCE_METADATA_KEY: {"kind": "heartbeat"}},
                ),
                record=True,
                # Consegna proattiva: raggiunge l'utente su tutti i canali
                # accoppiati (WebUI + Telegram), non solo sul canale d'origine.
                proactive=True,
            )
        else:
            logger.info("Heartbeat: silenced by post-run evaluation")
        return response
