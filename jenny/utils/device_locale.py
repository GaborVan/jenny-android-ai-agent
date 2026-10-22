"""Rilevamento della locale del dispositivo Android.

Modulo leaf: l'import di ``java`` è disponibile solo sotto il runtime
Chaquopy, quindi è guardato — su host la funzione ritorna sempre ``None``.
"""

from __future__ import annotations

from loguru import logger


def detect_device_locale() -> str | None:
    """Ritorna il codice lingua ISO-639-1 della locale del device, o ``None``.

    Usa ``java.util.Locale.getDefault().getLanguage()`` via Chaquopy. Best-effort:
    ogni fallimento (host senza Chaquopy, errore JNI) degrada a ``None``.
    """
    try:
        from java import jclass  # type: ignore[import-not-found]
    except ImportError:
        return None
    try:
        code = str(jclass("java.util.Locale").getDefault().getLanguage()).strip().lower()
    except Exception:
        logger.opt(exception=True).debug("Could not detect device locale")
        return None
    return code or None
