"""Orchestrazione della sync memoria↔Drive: cuce insieme algoritmo puro,
adapter locale e bridge Android; possiede lo stato locale
(``<workspace>/.jenny/drive_sync_state.json``) e il manifest remoto
(``apex-sync-manifest.json`` nella cartella scelta dall'utente).

Due contorni convivono in un giro solo:

* **istanza** — come sempre: ``SOUL.md``/``USER.md`` alla radice e ``memory/``,
  per qualunque cartella scelta;
* **condiviso** — attivo solo quando la cartella scelta si chiama
  ``Apex-Pamyat``: lo specchio locale ``shared/`` (profile/knowledge/notes)
  contro le sottocartelle reali omonime della cartella Drive. Per qualunque
  altra cartella i file di ``shared/`` restano locali e non finiscono né
  caricati né nel manifest (v. docs/using/shared-memory.md).

Lo stato locale non è config: è cache di dispositivo (id dispositivo, ultimo
manifest visto, riepilogo dell'ultimo giro) e non deve mai finire nel backup
cifrato del workspace né essere trattato come una scelta dell'utente da
migrare — per questo vive sotto ``.jenny/`` e non in ``config.json``.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import time
import uuid
from pathlib import Path
from typing import Any

from loguru import logger

from jenny.runtime.drive_sync_algorithm import (
    SHARED_PREFIX,
    SHARED_SUBFOLDERS,
    FileMeta,
    decode_name,
    is_shared_encoded_name,
    plan_sync,
    split_shared_encoded,
)
from jenny.runtime.drive_sync_bridge import (
    drive_delete_file,
    drive_delete_file_in,
    drive_ensure_folder,
    drive_folder_info,
    drive_list_files,
    drive_list_files_in,
    drive_read_file,
    drive_read_file_in,
    drive_write_file,
    drive_write_file_in,
)
from jenny.runtime.drive_sync_local import read_scope_file, scope_snapshot, write_scope_file

STATE_FILENAME = "drive_sync_state.json"
MANIFEST_REMOTE_NAME = "apex-sync-manifest.json"
MANIFEST_SCHEMA = 1

# Nome esatto della cartella Drive condivisa: il contorno condiviso è attivo
# solo quando l'utente sceglie questa cartella (v. docs/using/shared-memory.md).
SHARED_FOLDER_NAME = "Apex-Pamyat"


def _state_path(workspace: Path) -> Path:
    return workspace / ".jenny" / STATE_FILENAME


def _load_state(workspace: Path) -> dict[str, Any]:
    try:
        raw = json.loads(_state_path(workspace).read_text("utf-8"))
        return raw if isinstance(raw, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_state(workspace: Path, state: dict[str, Any]) -> None:
    path = _state_path(workspace)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)


def _device_id(state: dict[str, Any]) -> str:
    device_id = state.get("device_id")
    return device_id if isinstance(device_id, str) and device_id else uuid.uuid4().hex


def _files_from_dict(raw: Any) -> dict[str, FileMeta]:
    if not isinstance(raw, dict):
        return {}
    out: dict[str, FileMeta] = {}
    for name, meta in raw.items():
        if not isinstance(meta, dict):
            continue
        mtime, sha = meta.get("mtime"), meta.get("sha256")
        if isinstance(mtime, (int, float)) and isinstance(sha, str):
            out[name] = FileMeta(mtime=float(mtime), sha256=sha)
    return out


async def sync_status(workspace: Path) -> dict[str, Any]:
    """Stato per la card Settings: bridge disponibile, cartella scelta, ultimo giro."""
    info = await drive_folder_info()
    state = _load_state(workspace)
    last_sync = state.get("last_sync")
    folder = info if info and info.get("ok") else None
    return {
        "available": info is not None,
        "folder": folder,
        # Il contorno condiviso è attivo solo con la cartella Apex-Pamyat:
        # lo dice lo status perché la card possa mostrarlo senza indovinare.
        "shared_active": bool(folder and folder.get("name") == SHARED_FOLDER_NAME),
        "last_sync": last_sync if isinstance(last_sync, dict) else None,
    }


def _active_scope_snapshot(
    workspace: Path, *, include_shared: bool
) -> dict[str, FileMeta]:
    """Snapshot locale per il giro: senza il contorno condiviso i file di
    ``shared/`` non devono né caricarsi né finire nel manifest di una cartella
    che non è ``Apex-Pamyat``."""
    snapshot = scope_snapshot(workspace)
    if include_shared:
        return snapshot
    return {
        name: meta
        for name, meta in snapshot.items()
        if not is_shared_encoded_name(name)
    }


async def _read_remote_file(name: str) -> dict[str, Any] | None:
    """Legge un file remoto instradando sul bridge giusto: *In per lo scope
    condiviso, radice per l'istanza."""
    shared = split_shared_encoded(name)
    if shared is not None:
        folder, remote_name = shared
        return await drive_read_file_in(folder, remote_name)
    return await drive_read_file(name)


async def _delete_remote_file(name: str) -> dict[str, Any] | None:
    shared = split_shared_encoded(name)
    if shared is not None:
        folder, remote_name = shared
        return await drive_delete_file_in(folder, remote_name)
    return await drive_delete_file(name)


async def _upload_remote_file(
    name: str, content_b64: str, ensured: set[str]
) -> dict[str, Any] | None:
    """Scrive un file remoto; per lo scope condiviso crea prima la sottocartella
    (una sola volta per giro) e scrive *dentro* di essa."""
    shared = split_shared_encoded(name)
    if shared is None:
        return await drive_write_file(name, content_b64)
    folder, remote_name = shared
    if folder not in ensured:
        result = await drive_ensure_folder(folder)
        if not result or not result.get("ok"):
            return result or {"ok": False, "error": "ensure_folder_failed"}
        ensured.add(folder)
    return await drive_write_file_in(folder, remote_name, content_b64)


async def run_sync(workspace: Path) -> dict[str, Any]:
    """Un ciclo di sync completo. Non solleva mai: gli errori per-file finiscono
    in ``errors`` e non bloccano gli altri file."""
    info = await drive_folder_info()
    if info is None:
        return {"ok": False, "error": "unavailable"}
    if not info.get("ok"):
        return {"ok": False, "error": info.get("error") or "no_folder"}

    shared_active = info.get("name") == SHARED_FOLDER_NAME

    listing = await drive_list_files()
    if not listing or not listing.get("ok"):
        return {"ok": False, "error": (listing or {}).get("error") or "list_failed"}

    state = _load_state(workspace)
    device_id = _device_id(state)
    manifest_files = _files_from_dict(state.get("manifest_files"))

    local_snapshot = _active_scope_snapshot(workspace, include_shared=shared_active)

    errors: list[dict[str, str]] = []
    remote_entries: dict[str, dict[str, Any]] = {}

    # ── contorno istanza: file nella radice della cartella Drive ──
    for entry in listing.get("files") or []:
        raw_name = entry.get("name") if isinstance(entry, dict) else None
        # Nome non riconosciuto (manifest remoto, file dell'utente): ignorato,
        # mai toccato — v. decode_name. I nomi ``shared__...`` alla radice non
        # sono nostri: lo scope condiviso vive nelle sottocartelle reali.
        if isinstance(raw_name, str):
            relpath = decode_name(raw_name)
            if relpath is not None and not relpath.startswith(SHARED_PREFIX):
                remote_entries[raw_name] = entry

    # ── contorno condiviso: sottocartelle reali della cartella Apex-Pamyat ──
    if shared_active:
        for folder in SHARED_SUBFOLDERS:
            sub_listing = await drive_list_files_in(folder)
            if sub_listing is None or not sub_listing.get("ok"):
                sub_error = (sub_listing or {}).get("error")
                if sub_error == "not_found":
                    continue  # sottocartella mai creata: nessun file remoto, non è un errore
                errors.append({"file": f"shared/{folder}", "error": sub_error or "list_failed"})
                continue
            for entry in sub_listing.get("files") or []:
                raw_name = entry.get("name") if isinstance(entry, dict) else None
                if not isinstance(raw_name, str):
                    continue
                encoded = f"shared__{folder}__{raw_name}"
                # Anti-traversal: un nome che decodifica fuori dallo scope
                # (``..``, backslash, sottocartella estranea) viene ignorato.
                if decode_name(encoded) is not None:
                    remote_entries[encoded] = entry

    remote_snapshot: dict[str, FileMeta] = {}
    downloaded: dict[str, bytes] = {}

    for name, entry in remote_entries.items():
        raw_mtime = entry.get("mtime")
        mtime = float(raw_mtime) if isinstance(raw_mtime, (int, float)) else 0.0
        cached = manifest_files.get(name)
        if cached is not None and cached.mtime == mtime:
            # Invariato dall'ultimo sync: lo sha in cache resta valido, niente
            # download.
            remote_snapshot[name] = cached
            continue
        read = await _read_remote_file(name)
        if not read or not read.get("ok"):
            errors.append({"file": name, "error": (read or {}).get("error") or "read_failed"})
            continue
        try:
            data = base64.b64decode(read["content"])
        except Exception:  # noqa: BLE001
            errors.append({"file": name, "error": "decode_failed"})
            continue
        downloaded[name] = data
        remote_snapshot[name] = FileMeta(mtime=mtime, sha256=hashlib.sha256(data).hexdigest())

    plan = plan_sync(local_snapshot, remote_snapshot, manifest_files)

    pushed: list[str] = []
    ensured_folders: set[str] = set()
    for name in plan.uploads:
        data = read_scope_file(workspace, name)
        if data is None:
            errors.append({"file": name, "error": "local_missing"})
            continue
        result = await _upload_remote_file(
            name, base64.b64encode(data).decode("ascii"), ensured_folders
        )
        if not result or not result.get("ok"):
            errors.append({"file": name, "error": (result or {}).get("error") or "write_failed"})
            continue
        pushed.append(name)

    pulled: list[str] = []
    for name in plan.downloads:
        data = downloaded.get(name)
        if data is None:
            read = await _read_remote_file(name)
            if not read or not read.get("ok"):
                errors.append({"file": name, "error": (read or {}).get("error") or "read_failed"})
                continue
            try:
                data = base64.b64decode(read["content"])
            except Exception:  # noqa: BLE001
                errors.append({"file": name, "error": "decode_failed"})
                continue
        if write_scope_file(workspace, name, data) is None:
            errors.append({"file": name, "error": "local_write_failed"})
            continue
        pulled.append(name)

    deleted: list[str] = []
    for name in plan.deletes_remote:
        result = await _delete_remote_file(name)
        if not result or not result.get("ok"):
            errors.append({"file": name, "error": (result or {}).get("error") or "delete_failed"})
            continue
        deleted.append(name)

    # Manifest finale: lo stato locale dopo la sync, perché a questo punto è
    # quello che entrambi i lati condividono (upload e download sono già
    # scritti su disco). Per una cartella non condivisa i file di ``shared/``
    # restano fuori anche dal manifest.
    final_snapshot = _active_scope_snapshot(workspace, include_shared=shared_active)
    new_manifest_files = {
        name: {"mtime": meta.mtime, "sha256": meta.sha256} for name, meta in final_snapshot.items()
    }
    manifest_payload = {"schema": MANIFEST_SCHEMA, "device": device_id, "files": new_manifest_files}
    manifest_write = await drive_write_file(
        MANIFEST_REMOTE_NAME,
        base64.b64encode(json.dumps(manifest_payload).encode("utf-8")).decode("ascii"),
    )
    if not manifest_write or not manifest_write.get("ok"):
        errors.append({
            "file": MANIFEST_REMOTE_NAME,
            "error": (manifest_write or {}).get("error") or "write_failed",
        })

    summary = {
        "ok": not errors,
        "at": time.time(),
        "pushed": sorted(pushed),
        "pulled": sorted(pulled),
        "deleted": sorted(deleted),
        "skipped": sorted(plan.skipped),
        "errors": errors,
    }
    state["device_id"] = device_id
    state["folder_name"] = info.get("name")
    state["manifest_files"] = new_manifest_files
    state["last_sync"] = summary
    _save_state(workspace, state)

    return summary


async def run_startup_sync(workspace: Path) -> None:
    """Sync automatica all'avvio del gateway: best-effort, non fa mai fallire il boot."""
    try:
        from jenny.config.loader import load_config

        if not load_config().drive_sync.enabled:
            return
        result = await run_sync(workspace)
        if not result.get("ok"):
            logger.info(
                "[drive-sync] startup sync incomplete: {}",
                result.get("error") or result.get("errors"),
            )
    except Exception:  # noqa: BLE001
        logger.opt(exception=True).debug("[drive-sync] startup sync failed")
