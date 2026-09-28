"""Sintesi vocale di sistema Android (solo Android), via ``TtsBridge``.

Fa parlare il telefono attraverso il motore ``TextToSpeech`` di sistema:
niente cloud, nessuna dipendenza nuova, nessun permesso richiesto. Pensato per
un uso da assistente vocale — l'agente chiama ``speak`` per dare una risposta a
voce invece che (o oltre) in chat.

Stesso pattern degli altri runtime: classe Kotlin esposta via Chaquopy
(``jclass``), istanza cachata in un ``BridgeCache``; fuori da Android tutto
degrada a ``None``.

La validazione (testo vuoto, lunghezza, lingua, range del rate) avviene qui,
in Python, prima di raggiungere il bridge: la regola dell'estensione è che i
parametri liberi passati dal modello vanno filtrati lato Python, non lasciati
al codice Kotlin.
"""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any

from loguru import logger

from jenny.runtime.chaquopy_bridge import BridgeCache
from jenny.runtime.context import get_android_context

_BRIDGE = BridgeCache("com.flagdizero.jenny.TtsBridge")

MAX_SPEAK_CHARS = 4000
MIN_RATE = 0.5
MAX_RATE = 2.0

_LANGUAGE_RE = re.compile(r"^[A-Za-z]{2,3}(-[A-Za-z0-9]{2,8})*$")


def reset_tts_state() -> None:
    """Azzera la cache del bridge a un nuovo start del gateway."""
    _BRIDGE.reset()


def _resolve_bridge_class() -> Any:
    """Risolve la classe Kotlin ``TtsBridge`` via Chaquopy (seam per test)."""
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
        logger.opt(exception=True).debug("TtsBridge returned non-JSON")
        return None


def _error(message: str, **extra: Any) -> dict[str, Any]:
    return {"ok": False, "error": message, **extra}


async def speak(text: str, language: str = "", rate: float = 1.0) -> dict[str, Any] | None:
    """Fa leggere ``text`` ad alta voce dal motore TTS di sistema.

    ``None`` fuori da Android; altrimenti ``{"ok":true}`` o un errore
    (``empty_text``, ``text_too_long``, ``invalid_language``, oltre a quelli
    che il bridge stesso può riportare, come ``tts_unavailable``).
    """
    context = get_android_context()
    if context is None:
        return None

    stripped = text.strip()
    if not stripped:
        return _error("empty_text")
    if len(text) > MAX_SPEAK_CHARS:
        return _error("text_too_long", hint="Split it into shorter chunks.")
    if language and not _LANGUAGE_RE.match(language):
        return _error("invalid_language")
    try:
        rate_value = float(rate)
    except (TypeError, ValueError):
        rate_value = 1.0
    rate_value = max(MIN_RATE, min(MAX_RATE, rate_value))

    try:
        bridge = await _get_bridge(context)
        raw = await asyncio.to_thread(bridge.speak, text, language, rate_value)
    except Exception:  # noqa: BLE001
        logger.opt(exception=True).debug("TtsBridge.speak failed")
        return _error("bridge_unavailable")
    return _parse_result(raw) or _error("bridge_unavailable")


async def stop() -> dict[str, Any] | None:
    """Interrompe la sintesi vocale in corso."""
    context = get_android_context()
    if context is None:
        return None
    try:
        bridge = await _get_bridge(context)
        raw = await asyncio.to_thread(bridge.stop)
    except Exception:  # noqa: BLE001
        logger.opt(exception=True).debug("TtsBridge.stop failed")
        return _error("bridge_unavailable")
    return _parse_result(raw) or _error("bridge_unavailable")
