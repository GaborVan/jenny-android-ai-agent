"""Automazione UI Android (solo Android), via ``UiAutomationBridge`` nativo.

Dà a Jenny occhi e mani sugli altri app: dump dell'albero di accessibilità,
tap (coordinate o testo), swipe, digitazione testo e azioni globali
(back/home/recents/notifications).

Stesso pattern di ``jenny/runtime/location.py``: la classe Kotlin è esposta a
Python via Chaquopy (``jclass``), istanziata una volta e cachata; fuori da
Android tutto degrada a no-op senza sollevare.

Il bridge Kotlin NON costruisce il servizio di accessibilità (lo istanzia il
sistema quando l'utente lo abilita in Impostazioni → Accessibilità): delega a
``UiAutomationService.instance``. Quando il servizio non è abilitato, il bridge
ritorna ``{"ok":false,"error":"service_not_enabled",...}`` — qui tradotto in
un errore leggibile per l'utente, con l'hint su come abilitarlo.

Confine di fiducia: l'accessibilità legge lo schermo e simula gesture su
qualunque app — un permesso di sistema concesso a mano dall'utente. Il toggle
``tools.ui_automation.enable`` (config) è la serratura lato agente.
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any

from loguru import logger

from jenny.config.paths import get_workspace_path
from jenny.runtime.chaquopy_bridge import BridgeCache
from jenny.runtime.context import get_android_context

_BRIDGE = BridgeCache("com.flagdizero.jenny.UiAutomationBridge")
_LAST_NODES: list[dict[str, Any]] = []

# Azioni globali accettate (whitelist: niente free-string verso il bridge).
_GLOBAL_KEYS = {"back", "home", "recents", "notifications"}


def reset_ui_automation_state() -> None:
    """Azzera la cache del bridge a un nuovo start del gateway.

    Simmetrico a ``reset_location_state``: il lock del BridgeCache si lega al
    loop su cui è awaitato la prima volta, quindi va ricreato a ogni ripartenza
    in-process. Chiamato da ``android_entry.run_gateway``.
    """
    _BRIDGE.reset()
    _LAST_NODES.clear()


def _resolve_bridge_class() -> Any:
    """Risolve la classe Kotlin ``UiAutomationBridge`` via Chaquopy.

    Funzione di modulo (non chiamata diretta a ``_BRIDGE.resolve_class``): è il
    seam su cui i test montano il finto bridge fuori dal telefono.
    """
    return _BRIDGE.resolve_class()


async def _get_bridge(context: Any) -> Any:
    """Costruisce o ritorna l'istanza cachata di ``UiAutomationBridge``."""
    return await _BRIDGE.get(context, resolve=_resolve_bridge_class)


def _parse_result(raw: Any) -> dict[str, Any] | None:
    """Parsa il JSON del bridge in un dict; ``None`` su formato inatteso.

    Il bridge ritorna sempre JSON string; un ``None``/vuoto dal lato Kotlin
    viene trattato come assenza di risultato (mai un'eccezione verso il tool).
    """
    if not raw:
        return None
    try:
        parsed = json.loads(str(raw))
        return parsed if isinstance(parsed, dict) else None
    except (ValueError, TypeError):
        logger.opt(exception=True).debug("UiAutomationBridge returned non-JSON")
        return None


def _error(message: str, **extra: Any) -> dict[str, Any]:
    """Dict d'errore uniforme per il tool (stesso shape del bridge Kotlin)."""
    return {"ok": False, "error": message, **extra}


async def ui_status() -> dict[str, Any] | None:
    """Stato del servizio di accessibilità + package in primo piano.

    ``None`` fuori da Android; altrimenti ``{"ok":true,"connected":true,...}``
    oppure ``{"ok":false,"error":"service_not_enabled",...}`` quando l'utente
    non ha ancora abilitato il servizio nelle Impostazioni di sistema.
    """
    context = get_android_context()
    if context is None:
        return None
    try:
        bridge = await _get_bridge(context)
        raw = await asyncio.to_thread(bridge.status)
    except Exception:  # noqa: BLE001
        logger.opt(exception=True).debug("UiAutomationBridge.status failed")
        return _error("bridge_unavailable")
    result = _parse_result(raw)
    if result is None:
        return _error("bridge_unavailable")
    if not result.get("connected", False):
        return _not_enabled()
    return result


async def open_accessibility_settings() -> dict[str, Any] | None:
    """Apre le Impostazioni di Accessibilità del sistema (per abilitare Jenny)."""
    context = get_android_context()
    if context is None:
        return None
    try:
        bridge = await _get_bridge(context)
        raw = await asyncio.to_thread(bridge.openSettings)
    except Exception:  # noqa: BLE001
        logger.opt(exception=True).debug("UiAutomationBridge.openSettings failed")
        return _error("bridge_unavailable")
    return _parse_result(raw) or _error("bridge_unavailable")


async def accessibility_enabled() -> bool | None:
    """True se il servizio di accessibilità è connesso; ``None`` fuori da Android.

    Chiamata sincrona e leggera sul bridge Kotlin (``isEnabled``), pensata per
    essere interrogata dalla UI: distinta da ``ui_status`` che fa un dump.
    """
    context = get_android_context()
    if context is None:
        return None
    try:
        bridge = await _get_bridge(context)
        return bool(await asyncio.to_thread(bridge.isEnabled))
    except Exception:  # noqa: BLE001
        logger.opt(exception=True).debug("UiAutomationBridge.isEnabled failed")
        return None


async def screen_dump() -> dict[str, Any] | None:
    """Dump JSON dell'albero di accessibilità della finestra attiva.

    Ritorna ``{"ok":true,"package":...,"nodes":[{text,desc,id,cls,bounds,...}]}``
    oppure un errore (``service_not_enabled`` incluso).
    """
    context = get_android_context()
    if context is None:
        return None
    try:
        bridge = await _get_bridge(context)
        raw = await asyncio.to_thread(bridge.screenDump)
    except Exception:  # noqa: BLE001
        logger.opt(exception=True).debug("UiAutomationBridge.screenDump failed")
        return _error("bridge_unavailable")
    result = _parse_result(raw) or _error("bridge_unavailable")
    if result.get("ok") is True and isinstance(result.get("nodes"), list):
        _LAST_NODES[:] = result["nodes"]
    return result


async def screenshot(save_dir: str | None = None) -> dict[str, Any] | None:
    """Cattura uno screenshot della finestra attiva e lo salva come PNG.

    ``save_dir`` è la cartella di destinazione (default: ``<workspace>/screenshots``,
    creata al volo). Ritorna ``{"ok":true,"path":...,"width":...,"height":...}``
    oppure un errore (richiede API 30+ e servizio di accessibilità attivo).
    """
    context = get_android_context()
    if context is None:
        return None
    workspace = get_workspace_path()
    base = save_dir or str((workspace / "screenshots") if workspace else Path.cwd() / "screenshots")
    try:
        Path(base).mkdir(parents=True, exist_ok=True)
    except OSError:
        logger.opt(exception=True).debug("screenshot dir creation failed")
        return _error("screenshot_dir_failed")
    filename = f"screenshot_{time.strftime('%Y%m%d-%H%M%S')}.png"
    dest = str(Path(base) / filename)
    try:
        bridge = await _get_bridge(context)
        raw = await asyncio.to_thread(bridge.captureScreenshot, dest)
    except Exception:  # noqa: BLE001
        logger.opt(exception=True).debug("UiAutomationBridge.captureScreenshot failed")
        return _error("bridge_unavailable")
    return _parse_result(raw) or _error("bridge_unavailable")


async def tap(x: int, y: int) -> dict[str, Any] | None:
    """Tocca il punto (x, y) dello schermo, coordinate assolute in px."""
    context = get_android_context()
    if context is None:
        return None
    try:
        bridge = await _get_bridge(context)
        raw = await asyncio.to_thread(bridge.tap, int(x), int(y))
    except Exception:  # noqa: BLE001
        logger.opt(exception=True).debug("UiAutomationBridge.tap failed")
        return _error("bridge_unavailable")
    return _parse_result(raw) or _error("bridge_unavailable")


async def tap_by_text(text: str) -> dict[str, Any] | None:
    """Trova un nodo il cui testo contiene `text` e lo attiva (click o tap)."""
    context = get_android_context()
    if context is None:
        return None
    try:
        bridge = await _get_bridge(context)
        raw = await asyncio.to_thread(bridge.tapByText, text)
    except Exception:  # noqa: BLE001
        logger.opt(exception=True).debug("UiAutomationBridge.tapByText failed")
        return _error("bridge_unavailable")
    return _parse_result(raw) or _error("bridge_unavailable")


async def swipe(
    x1: int, y1: int, x2: int, y2: int, duration_ms: int
) -> dict[str, Any] | None:
    """Swipe da (x1,y1) a (x2,y2) in `duration_ms` millisecondi."""
    context = get_android_context()
    if context is None:
        return None
    try:
        bridge = await _get_bridge(context)
        raw = await asyncio.to_thread(
            bridge.swipe, int(x1), int(y1), int(x2), int(y2), int(duration_ms)
        )
    except Exception:  # noqa: BLE001
        logger.opt(exception=True).debug("UiAutomationBridge.swipe failed")
        return _error("bridge_unavailable")
    return _parse_result(raw) or _error("bridge_unavailable")


async def type_text(text: str) -> dict[str, Any] | None:
    """Inserisce `text` nel campo editabile focalizzato (ACTION_SET_TEXT)."""
    context = get_android_context()
    if context is None:
        return None
    try:
        bridge = await _get_bridge(context)
        raw = await asyncio.to_thread(bridge.typeText, text)
    except Exception:  # noqa: BLE001
        logger.opt(exception=True).debug("UiAutomationBridge.typeText failed")
        return _error("bridge_unavailable")
    return _parse_result(raw) or _error("bridge_unavailable")


async def press_global(key: str) -> dict[str, Any] | None:
    """Azione globale: back | home | recents | notifications (whitelist)."""
    key = (key or "").lower()
    if key not in _GLOBAL_KEYS:
        return _error(f"unknown_key: {key}")
    context = get_android_context()
    if context is None:
        return None
    try:
        bridge = await _get_bridge(context)
        raw = await asyncio.to_thread(bridge.pressGlobal, key)
    except Exception:  # noqa: BLE001
        logger.opt(exception=True).debug("UiAutomationBridge.pressGlobal failed")
        return _error("bridge_unavailable")
    return _parse_result(raw) or _error("bridge_unavailable")


def _not_enabled() -> dict[str, Any]:
    """Errore standard quando il servizio di accessibilità non è abilitato."""
    return {
        "ok": False,
        "error": "service_not_enabled",
        "hint": (
            "Enable Jenny in Android Settings → Accessibility (or ask the user "
            "to), then run ui_status to verify before acting on the screen."
        ),
    }


def last_dump_nodes() -> list[dict[str, Any]]:
    return list(_LAST_NODES)


def _find_node(
    nodes: list[dict[str, Any]], *, index: int | None = None, node_id: str | None = None,
) -> dict[str, Any] | None:
    if index is not None:
        return nodes[index] if 0 <= index < len(nodes) else None
    return next((node for node in nodes if node_id is not None and node.get("id") == node_id), None)


def _node_center(node: dict[str, Any]) -> tuple[int, int] | None:
    bounds = node.get("bounds")
    if not isinstance(bounds, list) or len(bounds) != 4:
        return None
    if any(type(value) is not int for value in bounds):
        return None
    left, top, right, bottom = bounds
    if right <= left or bottom <= top:
        return None
    return (left + right) // 2, (top + bottom) // 2


async def tap_target(
    *, x: int | None = None, y: int | None = None,
    index: int | None = None, node_id: str | None = None,
) -> dict[str, Any] | None:
    """Tocca il centro del nodo dell'ultimo dump; richiede accessibilità abilitata."""
    if x is not None and y is not None:
        return await tap(x, y)
    if not _LAST_NODES:
        return _error("no_screen_dump", hint="Run ui.dump first, then tap by index/id.")
    node = _find_node(_LAST_NODES, index=index, node_id=node_id)
    if node is None:
        return _error("element_not_found")
    center = _node_center(node)
    if center is None:
        return _error("element_not_found", hint="The element has no bounds on screen.")
    return await tap(*center)


async def type_text_into(
    text: str, *, index: int | None = None, node_id: str | None = None,
) -> dict[str, Any] | None:
    """Con accessibilità abilitata, un tap prima del testo assegna il focus al campo.

    La breve attesa lascia stabilizzare il focus prima di ACTION_SET_TEXT.
    """
    if index is not None or node_id is not None:
        tapped = await tap_target(index=index, node_id=node_id)
        if tapped is None or tapped.get("ok") is False:
            return tapped
        await asyncio.sleep(0.3)
    return await type_text(text)
