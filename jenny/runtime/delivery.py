"""Consegna di messaggi verso i canali + mirroring nella sessione (estratto dalla
closure ``_deliver_to_channel`` di ``gateway_runtime._run_gateway``).

Pubblica un messaggio user-visible sul bus e, quando ``record`` è attivo, lo
rispecchia nella sessione del canale così la cronologia resta coerente.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from loguru import logger

from jenny.bus.events import INTERNAL_CHANNEL, OutboundMessage
from jenny.session.keys import session_key_for_channel

if TYPE_CHECKING:
    from jenny.bus.queue import MessageBus
    from jenny.session.manager import SessionManager

# Firma dell'hook di registrazione fornito dall'AgentLoop
# (``AgentLoop.record_channel_delivery``).
RecordHook = Callable[..., Awaitable[None]]


class ChannelDeliverer:
    def __init__(
        self,
        *,
        bus: "MessageBus",
        session_manager: "SessionManager",
        extra_targets: "Callable[[], list[tuple[str, str]]] | None" = None,
        record_hook: "Callable[[], RecordHook | None] | None" = None,
    ) -> None:
        self._bus = bus
        self._session_manager = session_manager
        # Target aggiuntivi (canale, chat_id) per il fan-out proattivo,
        # es. la chat Telegram accoppiata. Late-binding: letto a ogni deliver.
        self._extra_targets = extra_targets
        # Getter late-binding dell'hook di registrazione (l'agente nasce dopo il
        # deliverer, e con l'onboarding può nascere molto dopo). Quando c'è, la
        # scrittura in sessione passa da lui: è l'unico che possa serializzarla
        # col lock di sessione del turno. Senza hook resta la scrittura diretta
        # qui sotto — vale per la fase pre-onboarding e per i test.
        self._record_hook = record_hook

    def _channel_session_key(self, channel: str, chat_id: str) -> str:
        return session_key_for_channel(channel, chat_id)

    async def deliver(
        self,
        msg: OutboundMessage,
        *,
        record: bool = False,
        session_key: str | None = None,
        proactive: bool = False,
    ) -> None:
        """Publish a user-visible message and mirror it into that channel's session.

        Il fan-out cross-canale (verso gli ``extra_targets``, es. la chat
        Telegram accoppiata) avviene SOLO su intento proattivo esplicito
        (``proactive=True`` dai chiamanti cron/heartbeat, oppure il flag
        ``_proactive_fanout`` nei metadata impostato dal tool ``message`` per
        gli invii davvero cross-canale). Una consegna nella conversazione
        corrente (risposta con allegato) resta sul solo canale d'origine e non
        deve "sfuggire" agli altri canali dell'utente.

        Con ``record`` attivo il testo viene anche registrato come messaggio
        ``assistant`` nella sessione del canale (marker ``_channel_delivery``),
        così il turno successivo dell'utente trova nel proprio contesto quello
        che Jenny gli ha appena detto. La registrazione avviene UNA sola volta,
        qui: le copie ``_mirror`` del fan-out vanno dritte sul bus.
        """
        session_manager = self._session_manager
        metadata = dict(msg.metadata or {})
        record = record or bool(metadata.pop("_record_channel_delivery", False))
        proactive = proactive or bool(metadata.pop("_proactive_fanout", False))
        if metadata != (msg.metadata or {}):
            msg = OutboundMessage(
                channel=msg.channel,
                chat_id=msg.chat_id,
                content=msg.content,
                media=msg.media,
                metadata=metadata,
                buttons=msg.buttons,
            )
        if record and msg.channel != INTERNAL_CHANNEL and msg.content.strip():
            key = session_key or self._channel_session_key(msg.channel, msg.chat_id)
            hook = self._record_hook() if self._record_hook is not None else None
            if hook is not None:
                # Un errore qui non deve far fallire la consegna: il messaggio
                # all'utente parte comunque, e perdere la riga di cronologia è
                # meno grave che perdere l'avviso.
                try:
                    await hook(
                        session_key=key,
                        content=msg.content,
                        media=list(msg.media) if msg.media else None,
                    )
                except Exception:
                    logger.exception("Failed to record channel delivery for session {}", key)
            elif hasattr(session_manager, "get_or_create") and hasattr(session_manager, "save"):
                session = session_manager.get_or_create(key)
                extra: dict[str, Any] = {"_channel_delivery": True}
                if msg.media:
                    extra["media"] = list(msg.media)
                session.add_message("assistant", msg.content, **extra)
                session_manager.save(session)

        if msg.channel == INTERNAL_CHANNEL or self._extra_targets is None or not proactive:
            await self._bus.publish_outbound(msg)
            return

        # Fan-out proattivo: il target websocket è SEMPRE incluso (è lui a
        # scrivere la riga nel transcript WebUI); gli altri target ricevono
        # copie marcate ``_mirror`` che non ri-persistono nulla.
        targets: dict[str, str] = {msg.channel: msg.chat_id}
        targets.setdefault("websocket", "default")
        try:
            for name, chat in self._extra_targets():
                if name and chat:
                    targets.setdefault(name, chat)
        except Exception:  # pragma: no cover - difensivo sul callback
            pass
        primary = "websocket" if "websocket" in targets else msg.channel
        await self._bus.publish_outbound(
            OutboundMessage(
                channel=primary,
                chat_id=targets.pop(primary),
                content=msg.content,
                media=msg.media,
                metadata=metadata,
                buttons=msg.buttons,
            )
        )
        for name, chat in targets.items():
            await self._bus.publish_outbound(
                OutboundMessage(
                    channel=name,
                    chat_id=chat,
                    content=msg.content,
                    media=msg.media,
                    metadata={**metadata, "_mirror": True},
                    buttons=msg.buttons,
                )
            )
