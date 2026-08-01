"""Posizione del dispositivo (solo Android), via ``LocationBridge`` nativo.

Stesso pattern di ``jenny/runtime/notifier.py``: la classe Kotlin è esposta a
Python via Chaquopy (``jclass``), istanziata una volta e cachata; fuori da
Android tutto degrada a no-op senza sollevare.

Modello a due livelli (deciso col design, niente refresh periodico):

* **last-known iniettato a ogni turno** — ``location_runtime_line`` legge una
  cache (``_CURRENT``) in modo istantaneo e sincrono, e pianifica un refresh
  fire-and-forget per il turno successivo (come ``notifier.notify_delivery``).
  Così ``ContextBuilder.build_messages`` resta sincrono e non tocca mai il JNI
  sul percorso caldo. Il fix last-known è gratis (fused, già in cache dell'OS).
* **fix fresco on-demand** — il tool ``get_location`` con ``precise=true`` accende
  il radio GPS una volta sola, quando serve davvero precisione.

La posizione condivisa via Telegram (``record_telegram_location``) fa da
override *per-canale* con validità ``telegram_ttl_s``: entro il TTL le risposte
del canale Telegram la usano come "posizione attuale", oltre il TTL anche
Telegram ricade sul GPS live.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any

from loguru import logger

from jenny.runtime.context import get_android_context

_BRIDGE_LOCK = asyncio.Lock()
_BRIDGE_INSTANCE: Any = None

# Ultimo fix GPS noto (aggiornato dal refresh fire-and-forget). Letto in modo
# sincrono da ``location_runtime_line``.
_CURRENT: "LocationFix | None" = None

# Override per-canale Telegram: chat_id -> fix condiviso dall'utente.
_TELEGRAM: dict[str, "LocationFix"] = {}

# Cache del reverse-geocoding: chiave = coordinate arrotondate, valore = place.
# Evita di ri-geocodificare a ogni refresh quando l'utente non si muove.
_GEOCODE_CACHE: dict[tuple[float, float], str] = {}
_GEOCODE_CACHE_MAX = 64

# Task fire-and-forget tenuti referenziati (asyncio tiene solo weakref).
_TASKS: set[asyncio.Task[Any]] = set()
# Evita di accodare più refresh in parallelo sui turni ravvicinati.
_REFRESH_INFLIGHT = False

_GEOCODE_PRECISION = 4  # ~11 m di risoluzione sulla chiave di cache


@dataclass(frozen=True)
class LocationFix:
    """Uno snapshot di posizione, sorgente-agnostico."""

    latitude: float
    longitude: float
    accuracy_m: float | None
    # Epoch millisecondi del fix (device time), confrontabile con time.time()*1000.
    time_ms: int
    place: str | None = None
    source: str = "gps"

    def age_seconds(self) -> float:
        return max(0.0, time.time() - self.time_ms / 1000.0)


def reset_location_state() -> None:
    """Azzera la cache del bridge e lo stato di modulo a un nuovo start del
    gateway. Simmetrico a ``notifier.reset_notifier_state`` — chiamato da
    ``android_entry.run_gateway`` prima del nuovo event loop (una ``asyncio.Lock``
    si lega al loop su cui è awaitata la prima volta).
    """
    global _BRIDGE_INSTANCE, _BRIDGE_LOCK, _CURRENT, _REFRESH_INFLIGHT
    _BRIDGE_LOCK = asyncio.Lock()
    _BRIDGE_INSTANCE = None
    _CURRENT = None
    _REFRESH_INFLIGHT = False
    _TELEGRAM.clear()
    _GEOCODE_CACHE.clear()


def _resolve_bridge_class() -> Any:
    """Risolve la classe Kotlin ``LocationBridge`` via Chaquopy."""
    from java import jclass  # importabile solo sotto il runtime Chaquopy

    return jclass("com.flagdizero.jenny.LocationBridge")


async def _get_bridge(context: Any) -> Any:
    """Costruisce o ritorna l'istanza cachata di ``LocationBridge`` (thread-safe)."""
    global _BRIDGE_INSTANCE
    if _BRIDGE_INSTANCE is not None:
        return _BRIDGE_INSTANCE
    async with _BRIDGE_LOCK:
        if _BRIDGE_INSTANCE is not None:
            return _BRIDGE_INSTANCE
        bridge_cls = _resolve_bridge_class()
        try:
            _BRIDGE_INSTANCE = bridge_cls(context)
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(f"Failed to construct LocationBridge: {exc}") from exc
        return _BRIDGE_INSTANCE


def _parse_fix(raw: Any, *, source: str) -> LocationFix | None:
    """Parsa la stringa del bridge ``"lat;lng;accuracy;timeMillis;provider"``.

    Il bridge ritorna ``None``/stringa vuota quando manca il permesso o non c'è
    alcun fix; qualunque formato inatteso viene trattato come assenza (mai
    un'eccezione: la posizione è best-effort).
    """
    if not raw:
        return None
    parts = str(raw).split(";")
    if len(parts) < 4:
        return None
    try:
        lat = float(parts[0])
        lng = float(parts[1])
        accuracy = float(parts[2]) if parts[2] not in ("", "null") else None
        time_ms = int(float(parts[3]))
    except (ValueError, IndexError):
        return None
    provider = parts[4] if len(parts) > 4 and parts[4] else source
    return LocationFix(
        latitude=lat,
        longitude=lng,
        accuracy_m=accuracy,
        time_ms=time_ms,
        source=provider,
    )


async def _reverse_geocode(bridge: Any, lat: float, lng: float) -> str | None:
    """Reverse-geocoding via ``Geocoder`` nativo, con cache sulle coordinate
    arrotondate. Best-effort: ritorna ``None`` su qualunque errore."""
    key = (round(lat, _GEOCODE_PRECISION), round(lng, _GEOCODE_PRECISION))
    cached = _GEOCODE_CACHE.get(key)
    if cached is not None:
        return cached or None
    try:
        place = await asyncio.to_thread(bridge.reverseGeocode, lat, lng)
    except Exception:  # noqa: BLE001
        logger.opt(exception=True).debug("Reverse-geocode failed for {},{}", lat, lng)
        return None
    place = (place or "").strip()
    if len(_GEOCODE_CACHE) >= _GEOCODE_CACHE_MAX:
        _GEOCODE_CACHE.clear()
    _GEOCODE_CACHE[key] = place
    return place or None


async def _fetch_fix(cfg: Any, *, precise: bool) -> LocationFix | None:
    """Legge un fix dal bridge (last-known o fresco) e lo reverse-geocoda.

    Ritorna ``None`` fuori da Android, senza permesso, o su qualunque errore.
    """
    context = get_android_context()
    if context is None:
        return None
    try:
        bridge = await _get_bridge(context)
        if precise:
            timeout = getattr(cfg, "fresh_timeout_s", 15)
            raw = await asyncio.wait_for(
                asyncio.to_thread(bridge.getFresh, int(timeout * 1000)),
                timeout=timeout + 5,
            )
            source = "gps"
        else:
            raw = await asyncio.to_thread(bridge.getLastKnown)
            source = "last-known"
    except Exception:  # noqa: BLE001
        logger.opt(exception=True).debug("LocationBridge fix failed (precise={})", precise)
        return None

    fix = _parse_fix(raw, source=source)
    if fix is None:
        return None
    place = await _reverse_geocode(bridge, fix.latitude, fix.longitude)
    if place:
        fix = LocationFix(
            latitude=fix.latitude,
            longitude=fix.longitude,
            accuracy_m=fix.accuracy_m,
            time_ms=fix.time_ms,
            place=place,
            source=fix.source,
        )
    return fix


async def refresh_current_location(cfg: Any) -> None:
    """Aggiorna la cache ``_CURRENT`` col last-known. Mai solleva."""
    global _CURRENT, _REFRESH_INFLIGHT
    try:
        if cfg is not None and getattr(cfg, "enable", True):
            fix = await _fetch_fix(cfg, precise=False)
            if fix is not None:
                _CURRENT = fix
    finally:
        _REFRESH_INFLIGHT = False


def _schedule_refresh(cfg: Any) -> None:
    """Pianifica (fire-and-forget) un refresh del last-known per il turno dopo.

    No-op fuori da Android, senza event loop, o se un refresh è già in volo.
    """
    global _REFRESH_INFLIGHT
    if _REFRESH_INFLIGHT or get_android_context() is None:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    _REFRESH_INFLIGHT = True
    task = loop.create_task(refresh_current_location(cfg))
    _TASKS.add(task)
    task.add_done_callback(_TASKS.discard)


def _humanize_age(seconds: float) -> str:
    """Età leggibile del fix per la riga di runtime context (inglese, come le
    altre etichette del blocco: ``Current Time``, ``Channel``…)."""
    if seconds < 90:
        return "just now"
    minutes = seconds / 60.0
    if minutes < 90:
        return f"~{round(minutes)} min ago"
    hours = minutes / 60.0
    if hours < 36:
        return f"~{round(hours)} h ago"
    return f"~{round(hours / 24.0)} d ago"


def _format_line(fix: LocationFix, *, shared: bool = False) -> str:
    """Costruisce la riga da iniettare nel runtime context."""
    where = fix.place or f"{fix.latitude:.5f}, {fix.longitude:.5f}"
    coords = f"({fix.latitude:.5f}, {fix.longitude:.5f})"
    if shared:
        return f"User location (shared via Telegram): {where} {coords}"
    return f"Device location ({_humanize_age(fix.age_seconds())}): {where} {coords}"


def record_telegram_location(chat_id: str, fix: LocationFix) -> None:
    """Registra la posizione condivisa via Telegram come override del canale."""
    if not chat_id:
        return
    _TELEGRAM[chat_id] = fix


async def build_telegram_fix(cfg: Any, lat: float, lng: float) -> LocationFix:
    """Costruisce un ``LocationFix`` da coordinate Telegram, reverse-geocodando
    se possibile. Timestamp = adesso (l'utente l'ha appena condivisa)."""
    now_ms = int(time.time() * 1000)
    place: str | None = None
    context = get_android_context()
    if context is not None and cfg is not None and getattr(cfg, "enable", True):
        try:
            bridge = await _get_bridge(context)
            place = await _reverse_geocode(bridge, lat, lng)
        except Exception:  # noqa: BLE001
            logger.opt(exception=True).debug("Telegram location geocode failed")
    return LocationFix(
        latitude=lat,
        longitude=lng,
        accuracy_m=None,
        time_ms=now_ms,
        place=place,
        source="telegram",
    )


def location_runtime_line(channel: str | None, chat_id: str | None, cfg: Any) -> str | None:
    """Riga posizione per il runtime context del turno (pura + istantanea).

    Legge la cache e pianifica un refresh per il prossimo turno. Ritorna ``None``
    quando il toggle è off o non c'è alcun fix disponibile.
    """
    if cfg is None or not getattr(cfg, "enable", True):
        return None
    _schedule_refresh(cfg)

    # Override Telegram: valido solo entro il TTL e solo per il canale Telegram.
    if channel == "telegram" and chat_id:
        shared = _TELEGRAM.get(chat_id)
        if shared is not None:
            ttl = getattr(cfg, "telegram_ttl_s", 3600)
            if shared.age_seconds() <= ttl:
                return _format_line(shared, shared=True)
            _TELEGRAM.pop(chat_id, None)

    if _CURRENT is not None:
        return _format_line(_CURRENT)
    return None


async def get_location(cfg: Any, *, precise: bool) -> LocationFix | None:
    """Punto d'ingresso del tool on-demand.

    ``precise=False`` → last-known (dalla cache se fresca, altrimenti un giro di
    refresh); ``precise=True`` → fix fresco (accende il GPS una volta)."""
    if cfg is None or not getattr(cfg, "enable", True):
        return None
    if precise:
        return await _fetch_fix(cfg, precise=True)
    if _CURRENT is not None:
        return _CURRENT
    return await _fetch_fix(cfg, precise=False)
