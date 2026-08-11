"""Alert di sistema Android per i messaggi consegnati all'utente.

Principio di design: la notifica non è un tool dell'agente ma una proprietà
del canale di consegna — ogni messaggio user-visible che attraversa il canale
WebSocket viene offerto al bridge Kotlin (``NotifierBridge``), che lo posta
come notifica di sistema solo se l'app NON è in foreground. La policy "se
squillare" vive quindi in Kotlin (unica fonte di verità sulla visibilità
dell'app); qui si decide solo il *contenuto* (titolo/corpo/tag).

Segue il pattern lazy-bridge di ``jenny.webui.android_apps_api``: la classe
Kotlin è risolta via Chaquopy alla prima chiamata; senza contesto Android
(desktop/test) ogni chiamata è un no-op silenzioso.
"""

from __future__ import annotations

import asyncio
from typing import Any

from loguru import logger

from jenny.runtime.context import get_android_context
from jenny.webui.metadata import WEBUI_MESSAGE_SOURCE_METADATA_KEY

_BRIDGE_LOCK = asyncio.Lock()
_BRIDGE_INSTANCE: Any = None

_ALERT_TIMEOUT_S = 10
_BODY_MAX_CHARS = 200

# I task fire-and-forget vanno tenuti referenziati fino al completamento,
# altrimenti il GC può cancellarli a metà (asyncio tiene solo weakref).
_TASKS: set[asyncio.Task[Any]] = set()


def reset_notifier_state() -> None:
    """Drop the cached NotifierBridge so a fresh gateway start can't inherit
    a stale bridge from a previous crashed loop.

    Simmetrico a ``android_apps_api.reset_installed_apps_state``; chiamato da
    ``android_entry.run_gateway``.
    """
    global _BRIDGE_INSTANCE, _BRIDGE_LOCK
    _BRIDGE_LOCK = asyncio.Lock()
    _BRIDGE_INSTANCE = None


def _resolve_bridge_class() -> Any:
    """Resolve the Kotlin NotifierBridge class via Chaquopy."""
    from java import jclass  # only importable under the Chaquopy runtime

    return jclass("com.flagdizero.jenny.NotifierBridge")


async def _get_bridge(context: Any) -> Any:
    """Build or return a cached NotifierBridge instance (thread-safe)."""
    global _BRIDGE_INSTANCE
    if _BRIDGE_INSTANCE is not None:
        return _BRIDGE_INSTANCE
    async with _BRIDGE_LOCK:
        if _BRIDGE_INSTANCE is not None:
            return _BRIDGE_INSTANCE
        bridge_cls = _resolve_bridge_class()
        try:
            _BRIDGE_INSTANCE = bridge_cls(context)
        except Exception as exc:
            raise RuntimeError(f"Failed to construct NotifierBridge: {exc}") from exc
        return _BRIDGE_INSTANCE


def alert_fields(content: str, metadata: dict[str, Any] | None) -> tuple[str, str, str]:
    """Deriva ``(title, body, tag)`` dell'alert dal messaggio outbound.

    Il titolo viene dal metadata di sorgente proattiva già esistente
    (``_webui_message_source``: cron/heartbeat); il tag coalizza gli alert
    della stessa sorgente (stesso tag → il nuovo sostituisce il vecchio).
    Funzione pura, testabile senza runtime Android.
    """
    source = (metadata or {}).get(WEBUI_MESSAGE_SOURCE_METADATA_KEY)
    kind = source.get("kind") if isinstance(source, dict) else None
    raw_label = source.get("label") if isinstance(source, dict) else None
    label = raw_label.strip() if isinstance(raw_label, str) and raw_label.strip() else None

    if kind == "cron":
        title = f"Jenny ⏰ {label}" if label else "Jenny ⏰ promemoria"
        tag = f"cron:{label}" if label else "cron"
    elif kind == "heartbeat":
        title = "Jenny · monitoraggio"
        tag = "heartbeat"
    elif kind == "update":
        # Tag dedicato: l'annuncio in chat e l'alert esplicito di un update
        # critico partono entrambi da qui e devono coalizzare, non sommarsi.
        title = "Jenny · aggiornamento"
        tag = "update"
    else:
        title = "Jenny"
        tag = "message"

    body = " ".join(content.split())
    if len(body) > _BODY_MAX_CHARS:
        body = body[: _BODY_MAX_CHARS - 1] + "…"
    if not body:
        body = "Nuovo messaggio"
    return title, body, tag


async def post_alert(content: str, metadata: dict[str, Any] | None) -> bool:
    """Posta l'alert via bridge. Ritorna False se soppresso (app in foreground),
    senza contesto Android, o su qualunque errore — mai un'eccezione: la
    consegna in chat è già avvenuta e non deve risentirne.
    """
    context = get_android_context()
    if context is None:
        return False
    title, body, tag = alert_fields(content, metadata)
    try:
        bridge = await _get_bridge(context)
        posted = await asyncio.wait_for(
            asyncio.to_thread(bridge.postAlert, title, body, tag), timeout=_ALERT_TIMEOUT_S
        )
    except Exception:
        logger.opt(exception=True).warning("Failed to post system alert (tag={})", tag)
        return False
    return bool(posted)


def notify_delivery(content: str, metadata: dict[str, Any] | None) -> None:
    """Pianifica (fire-and-forget) l'alert per un messaggio appena consegnato.

    Non blocca il percorso di invio WS: il bridge viene chiamato in un task
    separato. No-op fuori da Android o senza event loop attivo.
    """
    if get_android_context() is None:
        return
    try:
        task = asyncio.get_running_loop().create_task(post_alert(content, metadata))
    except RuntimeError:
        return
    _TASKS.add(task)
    task.add_done_callback(_TASKS.discard)
