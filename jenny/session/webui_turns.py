"""Session turn helpers for WebUI-capable WebSocket sessions."""

from __future__ import annotations

import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from loguru import logger

from jenny.bus.events import INTERNAL_CHANNEL, InboundMessage, OutboundMessage
from jenny.bus.queue import MessageBus
from jenny.bus.runtime_events import (
    RuntimeEventBus,
    RuntimeEventContext,
    RuntimeModelChanged,
    SessionTurnStarted,
    TurnCompleted,
    TurnRunStatusChanged,
)
from jenny.cron.session_turns import CRON_HISTORY_META
from jenny.providers.base import LLMProvider
from jenny.session.keys import UNIFIED_SESSION_KEY
from jenny.session.manager import Session, SessionManager
from jenny.session.turn_visibility import resolve_turn_visibility
from jenny.utils.helpers import strip_think, truncate_text
from jenny.utils.llm_runtime import LLMRuntime
from jenny.webui.metadata import WEBUI_DEFAULT_CHAT_ID

WEBUI_SESSION_METADATA_KEY = "webui"
WEBUI_TITLE_METADATA_KEY = "title"
WEBUI_TITLE_USER_EDITED_METADATA_KEY = "title_user_edited"
TITLE_MAX_CHARS = 60
TITLE_GENERATION_MAX_TOKENS = 96
TITLE_GENERATION_REASONING_EFFORT = "none"

# Wall-clock turn start per ``chat_id`` (websocket only). Survives browser refresh while the
# gateway process stays up; cleared on idle/stop and implicitly dropped on restart.
_WEBSOCKET_TURN_WALL_STARTED_AT: dict[str, float] = {}


def mark_webui_session(session: Session, metadata: dict[str, Any]) -> bool:
    """Persist a WebUI marker only when the inbound websocket frame opted in."""
    if metadata.get(WEBUI_SESSION_METADATA_KEY) is not True:
        return False
    session.metadata[WEBUI_SESSION_METADATA_KEY] = True
    return True


def webui_view_target(ctx: RuntimeEventContext) -> tuple[str, str] | None:
    """Ritorna il target (channel, chat_id) della vista WebUI per un turno.

    La WebUI è la vista canonica della conversazione unificata: i turni
    websocket la aggiornano direttamente, quelli di altri canali utente
    (es. Telegram) vengono proiettati sul thread ``default``. I turni
    interni (cron, dream, heartbeat) e le sessioni non unificate non hanno
    proiezione.
    """
    # Il canale d'origine non basta a decidere: un heartbeat o un cron monitor
    # gira *su* ``websocket:default`` — è il target a cui potrà consegnare se la
    # condizione scatta — ma nessuno dei suoi marcatori di turno (spinner,
    # ``_turn_end``) appartiene alla conversazione dell'utente. Il discrimine è
    # la visibilità del turno, non il canale.
    if resolve_turn_visibility(
        ctx.metadata, channel=ctx.channel, session_key=ctx.session_key
    ).silent:
        return None
    if ctx.channel == "websocket":
        return (ctx.channel, ctx.chat_id)
    if ctx.channel == INTERNAL_CHANNEL or ctx.session_key != UNIFIED_SESSION_KEY:
        return None
    return ("websocket", WEBUI_DEFAULT_CHAT_ID)


def clean_generated_title(raw: str | None) -> str:
    text = (raw or "").strip()
    if not text:
        return ""
    text = re.sub(r"^\s*(title|标题)\s*[:：]\s*", "", text, flags=re.IGNORECASE)
    text = text.strip().strip("\"'`“”‘’")
    text = strip_think(text)
    text = re.sub(r"\s+", " ", text).strip()
    text = text.rstrip("。.!！?？,，;；:")
    if len(text) > TITLE_MAX_CHARS:
        text = text[: TITLE_MAX_CHARS - 1].rstrip() + "…"
    return text


def _title_inputs(session: Session) -> tuple[str, str]:
    user_text = ""
    assistant_text = ""
    for message in session.messages:
        if message.get("_command") is True:
            continue
        if message.get(CRON_HISTORY_META) is True:
            continue
        role = message.get("role")
        content = message.get("content")
        if not isinstance(content, str) or not content.strip():
            continue
        content = strip_think(content)
        if not content:
            continue
        if role == "user" and not user_text:
            user_text = content.strip()
        elif role == "assistant" and not assistant_text:
            assistant_text = content.strip()
        if user_text and assistant_text:
            break
    return user_text, assistant_text


async def maybe_generate_webui_title(
    *,
    sessions: SessionManager,
    session_key: str,
    provider: LLMProvider,
    model: str,
) -> bool:
    """Generate and persist a short title for WebUI-owned sessions only."""
    session = sessions.get_or_create(session_key)
    if session.metadata.get(WEBUI_SESSION_METADATA_KEY) is not True:
        return False
    if session.metadata.get(WEBUI_TITLE_USER_EDITED_METADATA_KEY) is True:
        return False
    current_title = session.metadata.get(WEBUI_TITLE_METADATA_KEY)
    if isinstance(current_title, str) and current_title.strip():
        cleaned_current_title = clean_generated_title(current_title)
        if cleaned_current_title:
            if cleaned_current_title != current_title:
                session.metadata[WEBUI_TITLE_METADATA_KEY] = cleaned_current_title
                sessions.save(session)
            return False
        session.metadata.pop(WEBUI_TITLE_METADATA_KEY, None)

    user_text, assistant_text = _title_inputs(session)
    if not user_text:
        return False

    prompt = (
        "Generate a concise title for this chat.\n"
        "Rules:\n"
        "- Use the same language as the user when practical.\n"
        "- 3 to 8 words.\n"
        "- No quotes.\n"
        "- No punctuation at the end.\n"
        "- Return only the title.\n\n"
        f"User: {truncate_text(user_text, 1_000)}"
    )
    if assistant_text:
        prompt += f"\nAssistant: {truncate_text(assistant_text, 1_000)}"

    try:
        response = await provider.chat_with_retry(
            [
                {
                    "role": "system",
                    "content": (
                        "You write short, neutral chat titles. "
                        "Return only the title text."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            tools=None,
            model=model,
            max_tokens=TITLE_GENERATION_MAX_TOKENS,
            temperature=0.2,
            reasoning_effort=TITLE_GENERATION_REASONING_EFFORT,
            retry_mode="standard",
        )
    except Exception:
        logger.debug("Failed to generate webui session title for {}", session_key, exc_info=True)
        return False

    title = clean_generated_title(response.content)
    if not title or title.lower().startswith("error"):
        logger.debug(
            "WebUI title generation returned no usable title for {} (finish_reason={})",
            session_key,
            response.finish_reason,
        )
        return False
    session.metadata[WEBUI_TITLE_METADATA_KEY] = title
    sessions.save(session)
    return True


async def maybe_generate_webui_title_after_turn(
    *,
    channel: str,
    metadata: dict[str, Any],
    sessions: SessionManager,
    session_key: str,
    provider: LLMProvider,
    model: str,
) -> bool:
    if channel != "websocket" or metadata.get(WEBUI_SESSION_METADATA_KEY) is not True:
        return False
    return await maybe_generate_webui_title(
        sessions=sessions,
        session_key=session_key,
        provider=provider,
        model=model,
    )


def websocket_turn_wall_started_at(chat_id: str) -> float | None:
    """Return ``time.time()`` when the active user turn began, if still running."""
    return _WEBSOCKET_TURN_WALL_STARTED_AT.get(chat_id)


async def publish_turn_run_status(
    bus: MessageBus,
    msg: InboundMessage,
    status: str,
    *,
    started_at: float | None = None,
) -> None:
    """Notify WebSocket clients while a user turn is executing (timing strip)."""
    if msg.channel != "websocket":
        return
    cid = str(msg.chat_id)
    meta: dict[str, Any] = {
        **dict(msg.metadata or {}),
        "_goal_status": True,
        "goal_status": status,
    }
    if status == "running":
        if isinstance(started_at, int | float) and started_at > 0:
            t0 = float(started_at)
        else:
            t0 = time.time()
        meta["started_at"] = t0
        _WEBSOCKET_TURN_WALL_STARTED_AT[cid] = t0
    else:
        _WEBSOCKET_TURN_WALL_STARTED_AT.pop(cid, None)
    await bus.publish_outbound(
        OutboundMessage(
            channel=msg.channel,
            chat_id=cid,
            content="",
            metadata=meta,
        ),
    )

@dataclass
class WebuiTurnCoordinator:
    """Translate generic runtime events into WebUI/WebSocket wire messages."""

    bus: MessageBus
    sessions: SessionManager
    schedule_background: Callable[[Awaitable[None]], None]

    def subscribe(self, runtime_events: RuntimeEventBus) -> Callable[[], None]:
        """Subscribe this coordinator to runtime events."""
        unsubscribe = [
            runtime_events.subscribe(
                self._handle_session_turn_started,
                SessionTurnStarted,
            ),
            runtime_events.subscribe(
                self._handle_run_status_changed,
                TurnRunStatusChanged,
            ),
            runtime_events.subscribe(
                self._handle_turn_completed_event,
                TurnCompleted,
            ),
            runtime_events.subscribe(
                self._handle_runtime_model_changed,
                RuntimeModelChanged,
            ),
        ]

        def _unsubscribe() -> None:
            for fn in reversed(unsubscribe):
                fn()

        return _unsubscribe

    @staticmethod
    def _view_msg(ctx: RuntimeEventContext) -> InboundMessage | None:
        """Messaggio sintetico indirizzato alla vista WebUI del turno (o None)."""
        target = webui_view_target(ctx)
        if target is None:
            return None
        channel, chat_id = target
        return InboundMessage(
            channel=channel,
            sender_id="runtime",
            chat_id=chat_id,
            content="",
            metadata=dict(ctx.metadata or {}),
            session_key_override=ctx.session_key,
        )

    async def _handle_session_turn_started(self, event: SessionTurnStarted) -> None:
        ctx = event.context
        if ctx.channel == "websocket":
            session = self.sessions.get_or_create(ctx.session_key)
            mark_webui_session(session, ctx.metadata)
            return
        # Turno partito da un altro canale utente: eco del messaggio utente
        # sulla vista WebUI, così la chat aperta lo mostra in tempo reale e
        # il transcript resta la storia completa della conversazione.
        target = webui_view_target(ctx)
        if target is None:
            return
        metadata = dict(ctx.metadata or {})
        # Le continuation interne mantengono il canale d'origine ma il loro
        # contenuto è un prompt sintetico, non un messaggio dell'utente.
        if metadata.get("_internal_continuation") or metadata.get("_skip_user_persist"):
            return
        text = (event.content or "").strip()
        if not text or text == "/stop":
            return
        channel, chat_id = target
        await self.bus.publish_outbound(
            OutboundMessage(
                channel=channel,
                chat_id=chat_id,
                content=event.content,
                media=list(event.media),
                metadata={
                    **metadata,
                    "_user_echo": True,
                    "origin_channel": ctx.channel,
                },
            )
        )

    async def _handle_run_status_changed(self, event: TurnRunStatusChanged) -> None:
        msg = self._view_msg(event.context)
        if msg is None:
            return
        await publish_turn_run_status(
            self.bus,
            msg,
            event.status,
            started_at=event.started_at,
        )

    async def _handle_turn_completed_event(self, event: TurnCompleted) -> None:
        msg = self._view_msg(event.context)
        if msg is None:
            return
        await self.handle_turn_end(msg, latency_ms=event.latency_ms)
        self._schedule_title_update_from_event(event)

    async def _handle_runtime_model_changed(self, event: RuntimeModelChanged) -> None:
        await self.bus.publish_outbound(
            OutboundMessage(
                channel="websocket",
                chat_id="*",
                content="",
                metadata={
                    "_runtime_model_updated": True,
                    "model": event.model,
                    "model_preset": event.model_preset,
                    "provider": event.provider,
                },
            )
        )

    async def handle_turn_end(
        self,
        msg: InboundMessage,
        *,
        latency_ms: int | None,
    ) -> None:
        if msg.channel != "websocket":
            return

        turn_metadata: dict[str, Any] = {**msg.metadata, "_turn_end": True}
        if latency_ms is not None:
            turn_metadata["latency_ms"] = int(latency_ms)
        await self.bus.publish_outbound(OutboundMessage(
            channel=msg.channel,
            chat_id=msg.chat_id,
            content="",
            metadata=turn_metadata,
        ))

    def _schedule_title_update_from_event(self, event: TurnCompleted) -> None:
        title_context = event.runtime
        if (
            event.context.metadata.get("webui") is not True
            or title_context is None
            or not isinstance(title_context, LLMRuntime)
        ):
            return

        async def _generate_title_and_notify(
            title_llm: LLMRuntime = title_context,
        ) -> None:
            generated = await maybe_generate_webui_title_after_turn(
                channel=event.context.channel,
                metadata=event.context.metadata,
                sessions=self.sessions,
                session_key=event.context.session_key,
                provider=title_llm.provider,
                model=title_llm.model,
            )
            if generated:
                await self.bus.publish_outbound(OutboundMessage(
                    channel=event.context.channel,
                    chat_id=event.context.chat_id,
                    content="",
                    metadata={
                        **event.context.metadata,
                        "_session_updated": True,
                        "_session_update_scope": "metadata",
                    },
                ))

        self.schedule_background(_generate_title_and_notify())
