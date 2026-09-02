"""Appunti di sistema Android (solo Android), via ``ClipboardBridge``.

Lettura/scrittura degli appunti: utile per incollare link o codici in altri
app (insieme a ``ui_type``/``ui_tap``) o per salvare testo copiato altrove.

Stesso pattern degli altri runtime: classe Kotlin esposta via Chaquopy
(``jclass``), istanza cachata; fuori da Android tutto degrada a no-op.

Limiti Android 10+: la lettura degli appunti è permessa solo con l'app in
primo piano o come IME predefinita; il bridge ritorna un errore chiaro
(``clipboard_read_blocked``) quando il sistema blocca. La scrittura è sempre
permessa.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from loguru import logger

from jenny.runtime.chaquopy_bridge import BridgeCache
from jenny.runtime.context import get_android_context

_BRIDGE = BridgeCache("com.flagdizero.jenny.ClipboardBridge")


def reset_clipboard_state() -> None:
    """Azzera la cache del bridge a un nuovo start del gateway."""
    _BRIDGE.reset()


def _resolve_bridge_class() -> Any:
    """Risolve la classe Kotlin ``ClipboardBridge`` via Chaquopy (seam per test)."""
    return _BRIDGE.resolve_class()


async def _get_bridge(context: Any) -> Any:
    return await _BRIDGE.get(context, resolve=_resolve_bridge_class)


def _parse_result(raw: Any) -> dict[str, Any] | None:
    if not raw:
        return None
    try:
        parsed = json.loads(str(raw))
        return parsed if isinstance(parsed, dict) else None
    except (ValueError, TypeError):
        logger.opt(exception=True).debug("ClipboardBridge returned non-JSON")
        return None


def _error(message: str) -> dict[str, Any]:
    return {"ok": False, "error": message}


async def clipboard_get() -> dict[str, Any] | None:
    """Legge il testo corrente dagli appunti.

    ``None`` fuori da Android; altrimenti ``{"ok":true,"text":...}`` (text può
    essere vuoto) o un errore (``clipboard_read_blocked`` incluso quando
    Android 10+ blocca la lettura senza focus).
    """
    context = get_android_context()
    if context is None:
        return None
    try:
        bridge = await _get_bridge(context)
        raw = await asyncio.to_thread(bridge.getClipboard)
    except Exception:  # noqa: BLE001
        logger.opt(exception=True).debug("ClipboardBridge.getClipboard failed")
        return _error("bridge_unavailable")
    return _parse_result(raw) or _error("bridge_unavailable")


async def clipboard_set(text: str) -> dict[str, Any] | None:
    """Scrive ``text`` negli appunti di sistema."""
    context = get_android_context()
    if context is None:
        return None
    try:
        bridge = await _get_bridge(context)
        raw = await asyncio.to_thread(bridge.setClipboard, text)
    except Exception:  # noqa: BLE001
        logger.opt(exception=True).debug("ClipboardBridge.setClipboard failed")
        return _error("bridge_unavailable")
    return _parse_result(raw) or _error("bridge_unavailable")
