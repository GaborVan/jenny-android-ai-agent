"""Rilevamento della timezone del dispositivo Android.

Modulo leaf: l'import di ``java`` è disponibile solo sotto il runtime
Chaquopy, quindi è guardato — su host la funzione ritorna sempre ``None``.
"""

from __future__ import annotations

from loguru import logger


def detect_device_timezone() -> str | None:
    """Ritorna l'ID IANA della timezone del device, o ``None``.

    Usa ``java.util.TimeZone.getDefault().getID()`` via Chaquopy: su Android
    gli ID restituiti sono nomi IANA (es. "Europe/Rome"). Best-effort: ogni
    fallimento (host senza Chaquopy, errore JNI) degrada a ``None``.
    """
    try:
        from java import jclass  # type: ignore[import-not-found]
    except ImportError:
        return None
    try:
        tz_id = str(jclass("java.util.TimeZone").getDefault().getID()).strip()
    except Exception:
        logger.opt(exception=True).debug("Could not detect device timezone")
        return None
    return tz_id or None
