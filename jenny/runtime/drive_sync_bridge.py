"""Bridge verso la cartella Google Drive scelta dall'utente (``DriveSyncBridge``).

Stesso pattern di ``runtime/clipboard.py``: classe Kotlin esposta via Chaquopy
(``jclass``), istanza cachata in un ``BridgeCache``; fuori da Android tutto
degrada a ``None``. Tutte le risposte sono JSON parsato; il contenuto dei file
viaggia in base64 (``readFile``/``writeFile``) perché Chaquopy passa stringhe,
non byte grezzi.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from loguru import logger

from jenny.runtime.chaquopy_bridge import BridgeCache
from jenny.runtime.context import get_android_context

_BRIDGE = BridgeCache("com.flagdizero.jenny.DriveSyncBridge")


def reset_drive_sync_bridge_state() -> None:
    """Azzera la cache del bridge a un nuovo start del gateway."""
    _BRIDGE.reset()


def _resolve_bridge_class() -> Any:
    """Risolve la classe Kotlin ``DriveSyncBridge`` via Chaquopy (seam per test)."""
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
        logger.opt(exception=True).debug("DriveSyncBridge returned non-JSON")
        return None


def _error(message: str) -> dict[str, Any]:
    return {"ok": False, "error": message}


async def drive_folder_info() -> dict[str, Any] | None:
    """``None`` fuori da Android; altrimenti ``{"ok":true,"name":...,"uri":...}``
    o ``{"ok":false,"error":"no_folder"}`` se l'utente non ha ancora scelto."""
    context = get_android_context()
    if context is None:
        return None
    try:
        bridge = await _get_bridge(context)
        raw = await asyncio.to_thread(bridge.getFolderInfo)
    except Exception:  # noqa: BLE001
        logger.opt(exception=True).debug("DriveSyncBridge.getFolderInfo failed")
        return _error("bridge_unavailable")
    return _parse_result(raw) or _error("bridge_unavailable")


async def drive_list_files() -> dict[str, Any] | None:
    """``{"ok":true,"files":[{"name":...,"mtime":...,"size":...}]}``."""
    context = get_android_context()
    if context is None:
        return None
    try:
        bridge = await _get_bridge(context)
        raw = await asyncio.to_thread(bridge.listFiles)
    except Exception:  # noqa: BLE001
        logger.opt(exception=True).debug("DriveSyncBridge.listFiles failed")
        return _error("bridge_unavailable")
    return _parse_result(raw) or _error("bridge_unavailable")


async def drive_read_file(name: str) -> dict[str, Any] | None:
    """``{"ok":true,"content":"<base64>"}``."""
    context = get_android_context()
    if context is None:
        return None
    try:
        bridge = await _get_bridge(context)
        raw = await asyncio.to_thread(bridge.readFile, name)
    except Exception:  # noqa: BLE001
        logger.opt(exception=True).debug("DriveSyncBridge.readFile failed")
        return _error("bridge_unavailable")
    return _parse_result(raw) or _error("bridge_unavailable")


async def drive_write_file(name: str, content_b64: str) -> dict[str, Any] | None:
    """``{"ok":true}``."""
    context = get_android_context()
    if context is None:
        return None
    try:
        bridge = await _get_bridge(context)
        raw = await asyncio.to_thread(bridge.writeFile, name, content_b64)
    except Exception:  # noqa: BLE001
        logger.opt(exception=True).debug("DriveSyncBridge.writeFile failed")
        return _error("bridge_unavailable")
    return _parse_result(raw) or _error("bridge_unavailable")


async def drive_delete_file(name: str) -> dict[str, Any] | None:
    """``{"ok":true}``."""
    context = get_android_context()
    if context is None:
        return None
    try:
        bridge = await _get_bridge(context)
        raw = await asyncio.to_thread(bridge.deleteFile, name)
    except Exception:  # noqa: BLE001
        logger.opt(exception=True).debug("DriveSyncBridge.deleteFile failed")
        return _error("bridge_unavailable")
    return _parse_result(raw) or _error("bridge_unavailable")


# ── Operazioni nelle sottocartelle (scope condiviso "Apex-Pamyat") ──────────
# Omologhe *In delle funzioni sopra: stesso pattern, ``folder`` (la
# sottocartella reale dentro la cartella Drive) sempre primo argomento.
# Fuori da Android tutto degrada a ``None`` come le altre.


async def drive_ensure_folder(folder: str) -> dict[str, Any] | None:
    """Crea la sottocartella se manca; idempotente: ``{"ok":true}`` se esiste già."""
    context = get_android_context()
    if context is None:
        return None
    try:
        bridge = await _get_bridge(context)
        raw = await asyncio.to_thread(bridge.ensureFolder, folder)
    except Exception:  # noqa: BLE001
        logger.opt(exception=True).debug("DriveSyncBridge.ensureFolder failed")
        return _error("bridge_unavailable")
    return _parse_result(raw) or _error("bridge_unavailable")


async def drive_list_files_in(folder: str) -> dict[str, Any] | None:
    """``{"ok":true,"files":[...]}``; ``not_found`` se la sottocartella manca."""
    context = get_android_context()
    if context is None:
        return None
    try:
        bridge = await _get_bridge(context)
        raw = await asyncio.to_thread(bridge.listFilesIn, folder)
    except Exception:  # noqa: BLE001
        logger.opt(exception=True).debug("DriveSyncBridge.listFilesIn failed")
        return _error("bridge_unavailable")
    return _parse_result(raw) or _error("bridge_unavailable")


async def drive_read_file_in(folder: str, name: str) -> dict[str, Any] | None:
    """``{"ok":true,"content":"<base64>"}``."""
    context = get_android_context()
    if context is None:
        return None
    try:
        bridge = await _get_bridge(context)
        raw = await asyncio.to_thread(bridge.readFileIn, folder, name)
    except Exception:  # noqa: BLE001
        logger.opt(exception=True).debug("DriveSyncBridge.readFileIn failed")
        return _error("bridge_unavailable")
    return _parse_result(raw) or _error("bridge_unavailable")


async def drive_write_file_in(
    folder: str, name: str, content_b64: str
) -> dict[str, Any] | None:
    """``{"ok":true}``; la sottocartella deve esistere (ensureFolder prima)."""
    context = get_android_context()
    if context is None:
        return None
    try:
        bridge = await _get_bridge(context)
        raw = await asyncio.to_thread(bridge.writeFileIn, folder, name, content_b64)
    except Exception:  # noqa: BLE001
        logger.opt(exception=True).debug("DriveSyncBridge.writeFileIn failed")
        return _error("bridge_unavailable")
    return _parse_result(raw) or _error("bridge_unavailable")


async def drive_delete_file_in(folder: str, name: str) -> dict[str, Any] | None:
    """``{"ok":true}``; idempotente (file o sottocartella già assenti)."""
    context = get_android_context()
    if context is None:
        return None
    try:
        bridge = await _get_bridge(context)
        raw = await asyncio.to_thread(bridge.deleteFileIn, folder, name)
    except Exception:  # noqa: BLE001
        logger.opt(exception=True).debug("DriveSyncBridge.deleteFileIn failed")
        return _error("bridge_unavailable")
    return _parse_result(raw) or _error("bridge_unavailable")
