"""Notifiche di sistema Android (solo Android), via ``NotificationsBridge``.

Dà a Jenny le orecchie sugli altri app: leggere le notifiche attive (codici
2FA, messaggi, stati) e dismissarle.

Stesso pattern di ``jenny/runtime/ui_automation.py``: classe Kotlin esposta a
Python via Chaquopy (``jclass``), istanziata una volta e cachata; fuori da
Android tutto degrada a no-op senza sollevare.

Il bridge Kotlin NON costruisce il servizio di notifiche (lo istanzia il
sistema quando l'utente concede l'accesso in Impostazioni → Notifiche →
Accesso alle notifiche): delega a ``NotificationListenerBridge.instance``.

Confine di fiducia: le notifiche possono contenere dati personali (codici
usa-e-getta inclusi). L'accesso è concesso a mano dall'utente ed è la ragion
d'essere del servizio; i formatter dell'activity stream non ripetono MAI il
contenuto.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from loguru import logger

from jenny.runtime.chaquopy_bridge import BridgeCache
from jenny.runtime.context import get_android_context

_BRIDGE = BridgeCache("com.flagdizero.jenny.NotificationsBridge")


def reset_notifications_state() -> None:
    """Azzera la cache del bridge a un nuovo start del gateway.

    Simmetrico a ``reset_ui_automation_state`` (il lock del BridgeCache si lega
    al loop su cui è awaitato la prima volta).
    """
    _BRIDGE.reset()


def _resolve_bridge_class() -> Any:
    """Risolve la classe Kotlin ``NotificationsBridge`` via Chaquopy.

    Seam su cui i test montano il finto bridge fuori dal telefono.
    """
    return _BRIDGE.resolve_class()


async def _get_bridge(context: Any) -> Any:
    """Costruisce o ritorna l'istanza cachata di ``NotificationsBridge``."""
    return await _BRIDGE.get(context, resolve=_resolve_bridge_class)


def _parse_result(raw: Any) -> dict[str, Any] | None:
    """Parsa il JSON del bridge in un dict; ``None`` su formato inatteso."""
    if not raw:
        return None
    try:
        parsed = json.loads(str(raw))
        return parsed if isinstance(parsed, dict) else None
    except (ValueError, TypeError):
        logger.opt(exception=True).debug("NotificationsBridge returned non-JSON")
        return None


def _error(message: str) -> dict[str, Any]:
    return {"ok": False, "error": message}


def _not_enabled() -> dict[str, Any]:
    return {
        "ok": False,
        "error": "service_not_enabled",
        "hint": (
            "Enable Jenny in Android Settings → Notifications → Notification "
            "access, then run list_notifications to verify."
        ),
    }


async def open_notification_settings() -> dict[str, Any] | None:
    """Apre le Impostazioni di accesso alle notifiche di sistema."""
    context = get_android_context()
    if context is None:
        return None
    try:
        bridge = await _get_bridge(context)
        raw = await asyncio.to_thread(bridge.openSettings)
    except Exception:  # noqa: BLE001
        logger.opt(exception=True).debug("NotificationsBridge.openSettings failed")
        return _error("bridge_unavailable")
    return _parse_result(raw) or _error("bridge_unavailable")


async def list_notifications() -> dict[str, Any] | None:
    """Lista JSON delle notifiche attive: key, package, title, text, postTimeMs."""
    context = get_android_context()
    if context is None:
        return None
    try:
        bridge = await _get_bridge(context)
        raw = await asyncio.to_thread(bridge.getActiveNotifications)
    except Exception:  # noqa: BLE001
        logger.opt(exception=True).debug("NotificationsBridge.getActiveNotifications failed")
        return _error("bridge_unavailable")
    result = _parse_result(raw)
    if result is None:
        return _error("bridge_unavailable")
    if not result.get("ok", False) and result.get("error") == "service_not_enabled":
        return _not_enabled()
    return result


async def dismiss_notification(key: str) -> dict[str, Any] | None:
    """Rimuove una notifica per chiave (dal dump di list_notifications)."""
    context = get_android_context()
    if context is None:
        return None
    try:
        bridge = await _get_bridge(context)
        raw = await asyncio.to_thread(bridge.dismissNotification, key)
    except Exception:  # noqa: BLE001
        logger.opt(exception=True).debug("NotificationsBridge.dismissNotification failed")
        return _error("bridge_unavailable")
    result = _parse_result(raw)
    if result is None:
        return _error("bridge_unavailable")
    if not result.get("ok", False) and result.get("error") == "service_not_enabled":
        return _not_enabled()
    return result
