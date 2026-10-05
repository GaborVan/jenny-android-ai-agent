"""Riconoscimento vocale di sistema Android (solo Android), via ``SttBridge``.

Trascrive un **file** audio con il ``SpeechRecognizer`` di Android: nessuna
dipendenza nuova, nessuna chiave, nessuna cloud di Jenny. Il file viene
decodificato sul dispositivo (``MediaExtractor`` + ``MediaCodec``) e consegnato
al motore di riconoscimento che l'utente ha installato — che può essere locale
oppure no (v. ``config.voice.prefer_offline``).

Stesso pattern degli altri runtime: classe Kotlin esposta via Chaquopy
(``jclass``), istanza cachata in un ``BridgeCache``; fuori da Android tutto
degrada a ``None``.

Ascolta anche il microfono su richiesta del nodo OpenClaw: servono RECORD_AUDIO
e, da Android 10, app in primo piano o foreground service di tipo microfono.
Il permesso resta una scelta dell’utente.

La trascrizione da file **non è un tool**. La chiama
il canale che ha appena scaricato il messaggio vocale (``channels/telegram.py``),
sul file che ha scaricato lui. Se fosse un tool, il modello potrebbe nominare un
path arbitrario del workspace e far leggere audio a un motore di terze parti:
qui non può, e il perimetro è quello che serve.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from loguru import logger

from jenny.runtime.chaquopy_bridge import BridgeCache
from jenny.runtime.context import get_android_context

_BRIDGE = BridgeCache("com.flagdizero.jenny.SttBridge")

# Lingue dell'app → tag BCP-47. Un tag già formato passa intatto (normalizzando
# il separatore ``_``), tutto il resto diventa "" e lascia decidere al motore:
# meglio la lingua di sistema che una lingua sbagliata, che sul parlato si
# traduce in una trascrizione sbagliata per intero.
_LANGUAGE_TAGS: dict[str, str] = {"it": "it-IT", "en": "en-US", "uk": "uk-UA"}


def reset_stt_state() -> None:
    """Azzera la cache del bridge a un nuovo start del gateway."""
    _BRIDGE.reset()


def _resolve_bridge_class() -> Any:
    """Risolve la classe Kotlin ``SttBridge`` via Chaquopy (seam per test)."""
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
        logger.opt(exception=True).debug("SttBridge returned non-JSON")
        return None


def _error(message: str, **extra: Any) -> dict[str, Any]:
    return {"ok": False, "error": message, **extra}


def language_tag_for(language: str) -> str:
    """Tag BCP-47 per il motore, o "" quando non si sa che lingua chiedere.

    Accetta sia il codice corto dell'app (``uk``) sia un tag già formato
    (``uk-UA``, ``uk_UA``). Un valore che non è né l'uno né l'altro viene
    scartato: ``EXTRA_LANGUAGE`` con spazzatura non è meglio che senza.
    """
    value = (language or "").strip()
    if not value:
        return ""
    if value in _LANGUAGE_TAGS:
        return _LANGUAGE_TAGS[value]
    candidate = value.replace("_", "-")
    parts = candidate.split("-")
    head = parts[0]
    if len(head) in (2, 3) and head.isalpha() and all(part.isalnum() for part in parts[1:]):
        return candidate
    return ""


async def transcribe_file(
    path: str,
    *,
    language: str = "",
    prefer_offline: bool = False,
) -> dict[str, Any] | None:
    """Trascrive il file audio *path*.

    ``None`` fuori da Android (host, test): il chiamante degrada consegnando il
    file così com'è. Altrimenti un dict: ``{"ok":true,"text":...}`` oppure
    ``{"ok":false,"error":<codice>}`` — con ``file_not_found``,
    ``stt_unavailable`` o ``stt_file_source_unsupported`` fra i casi previsti.
    Non solleva mai.
    """
    context = get_android_context()
    if context is None:
        return None
    if not path:
        return _error("file_not_found")
    if not Path(path).is_file():
        # Controllo in Python prima del bridge: la regola dell'estensione è che
        # i parametri liberi li filtra questo lato, non il codice Kotlin.
        return _error("file_not_found")
    try:
        bridge = await _get_bridge(context)
        raw = await asyncio.to_thread(
            bridge.transcribeFile, str(path), language_tag_for(language), bool(prefer_offline)
        )
    except Exception:  # noqa: BLE001
        logger.opt(exception=True).debug("SttBridge.transcribeFile failed")
        return _error("bridge_unavailable")
    return _parse_result(raw) or _error("bridge_unavailable")


async def listen(seconds: int = 5, *, language: str = "") -> dict[str, Any] | None:
    """Ascolta il microfono su richiesta, con permesso concesso dall'utente."""
    context = get_android_context()
    if context is None:
        return None
    try:
        secs = max(1, min(15, int(seconds)))
    except (TypeError, ValueError, OverflowError):
        return _error("invalid_seconds")
    try:
        bridge = await _get_bridge(context)
        raw = await asyncio.to_thread(bridge.listen, secs, language_tag_for(language))
    except Exception:  # noqa: BLE001
        logger.opt(exception=True).debug("SttBridge.listen failed")
        return _error("bridge_unavailable")
    return _parse_result(raw) or _error("bridge_unavailable")
