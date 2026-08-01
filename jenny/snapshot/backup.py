"""Export/import del backup cifrato e staging dei ripristini.

L'export produce un singolo file ``.jbk``: un container AES-256-GCM (vedi
``crypto.py``) il cui payload in chiaro è uno zip *leggibile* — l'albero del
workspace così com'è (``tree/``) più lo store degli snapshot (``snapshots/``).
Disaster recovery senza Jenny = decifrare e aprire lo zip.

L'import e il ripristino da snapshot non toccano MAI il workspace vivo:
preparano un albero in staging e scrivono il marker; lo swap avviene al boot
successivo (vedi ``restore_marker.py``), dopo il riavvio del processo.
"""

from __future__ import annotations

import asyncio
import io
import json
import shutil
import time
import zipfile
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any

from loguru import logger

from jenny.snapshot.crypto import (
    BACKUP_FILE_EXTENSION,
    decrypt_container,
    encrypt_container,
)
from jenny.snapshot.locations import (
    STAGED_SNAPSHOTS_DIR_NAME,
    STAGED_WORKSPACE_DIR_NAME,
    backup_staging_dir_for,
    runtime_root_for,
)
from jenny.snapshot.restore_marker import write_marker, write_staging_sanity
from jenny.utils.path import atomic_write

if TYPE_CHECKING:
    from jenny.snapshot.service import SnapshotService

BACKUP_FORMAT_VERSION = 1
IMPORT_STAGED_FILENAME = f"import{BACKUP_FILE_EXTENSION}"


class BackupError(Exception):
    """Errore utente-visibile delle operazioni di backup (→ HTTP 400)."""


class BackupManager:
    """Orchestra engine, crypto e protocollo di restore."""

    def __init__(self, service: "SnapshotService") -> None:
        self._service = service
        self._engine = service.engine
        self._cfg = service.config
        self._runtime_root = runtime_root_for(self._engine.root)
        self._staging = backup_staging_dir_for(self._engine.root)
        self._lock = asyncio.Lock()

    @property
    def import_staged_path(self) -> Path:
        """Path fisso dove il lato Android deposita il file scelto dall'utente."""
        return self._staging / IMPORT_STAGED_FILENAME

    def list_snapshots(self, limit: int | None = None) -> list[dict[str, Any]]:
        return self._engine.list_snapshots(limit)

    async def create_snapshot(self, label: str | None = None) -> dict[str, Any] | None:
        manifest = await self._service.snapshot_now("manual", label=label)
        return manifest.summary() if manifest else None

    @property
    def retention_max_age_days(self) -> int:
        """Orizzonte di retention corrente in giorni (0 = per sempre)."""
        return self._cfg.retention_max_age_days

    async def set_retention_max_age(self, max_age_days: int) -> dict[str, Any]:
        """Persiste il nuovo orizzonte di retention e lo applica subito."""

        def _persist() -> None:
            from jenny.config.loader import load_config, save_config

            config = load_config()
            config.snapshots.retention_max_age_days = max_age_days
            save_config(config)

        await asyncio.to_thread(_persist)
        # La config viva del service è lo stesso oggetto costruito al boot:
        # set_retention_max_age la aggiorna e applica retention+gc sotto lock.
        removed = await self._service.set_retention_max_age(max_age_days)
        return {
            "ok": True,
            "retention_max_age_days": max_age_days,
            "removed": removed,
        }

    # -- export -----------------------------------------------------------------

    async def export_backup(self, passphrase: str) -> dict[str, Any]:
        """Crea il file ``.jbk`` cifrato in staging e ne ritorna i riferimenti."""
        if not passphrase:
            raise BackupError("passphrase required")
        async with self._lock:
            # Punto di export fotografato anche nella storia locale.
            await self._service.snapshot_now("pre_export")
            plaintext = await asyncio.to_thread(self._build_zip)
            blob = await encrypt_container(
                passphrase, plaintext, iterations=self._cfg.pbkdf2_iterations
            )
            filename = f"jenny-backup-{time.strftime('%Y%m%d-%H%M%S')}{BACKUP_FILE_EXTENSION}"
            path = self._staging / filename
            await asyncio.to_thread(self._replace_staged_exports, path, blob)
            logger.info("Backup exported to staging: {} ({} bytes)", filename, len(blob))
            return {
                "staged_path": str(path),
                "suggested_filename": filename,
                "size_bytes": len(blob),
            }

    def _replace_staged_exports(self, path: Path, blob: bytes) -> None:
        """Scrive il nuovo export e rimuove i precedenti (uno alla volta in staging)."""
        self._staging.mkdir(parents=True, exist_ok=True)
        for old in self._staging.glob(f"jenny-backup-*{BACKUP_FILE_EXTENSION}"):
            old.unlink(missing_ok=True)
        atomic_write(path, blob)

    def _build_zip(self) -> bytes:
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            metadata = {
                "format_version": BACKUP_FORMAT_VERSION,
                "exported_at_ms": int(time.time() * 1000),
                "jenny_version": _jenny_version(),
            }
            archive.writestr("metadata.json", json.dumps(metadata, ensure_ascii=False))
            # Il workspace è vivo durante l'export: un file sparito tra lo
            # scan e la write non deve far fallire l'intero backup.
            for rel, full in self._engine.iter_tracked_files():
                try:
                    archive.write(full, f"tree/{rel}")
                except OSError:
                    logger.warning("Backup export: skipping vanished file {}", rel)
            snapshots_dir = self._engine.snapshots_dir
            if snapshots_dir.is_dir():
                for item in sorted(snapshots_dir.rglob("*")):
                    if item.is_file() and not item.is_symlink():
                        rel = item.relative_to(snapshots_dir).as_posix()
                        try:
                            archive.write(item, f"snapshots/{rel}")
                        except OSError:
                            logger.warning("Backup export: skipping vanished file {}", rel)
        return buffer.getvalue()

    # -- import -----------------------------------------------------------------

    async def stage_import(self, staged_path: str, passphrase: str) -> dict[str, Any]:
        """Decifra e valida un ``.jbk``, prepara staging e marker di restore.

        Raises:
            CryptoAuthError: passphrase errata o file corrotto/non riconosciuto.
            BackupError: contenuto dello zip non valido.
            FileNotFoundError: file staged assente.
        """
        if not passphrase:
            raise BackupError("passphrase required")
        source = self._validated_staged_file(staged_path)
        async with self._lock:
            data = await asyncio.to_thread(source.read_bytes)
            plaintext = await decrypt_container(passphrase, data)
            metadata = await asyncio.to_thread(self._extract_backup, plaintext)
            # Fotografa lo stato corrente PRIMA di impegnare il restore.
            await self._service.snapshot_now("pre_restore")
            staged_dir = self._runtime_root / STAGED_WORKSPACE_DIR_NAME
            await asyncio.to_thread(write_staging_sanity, staged_dir)
            write_marker(self._runtime_root, source="backup_file")
            logger.info("Backup import staged; restart required to apply")
            return {"metadata": metadata}

    def _validated_staged_file(self, staged_path: str) -> Path:
        candidate = Path(staged_path).resolve()
        staging = self._staging.resolve()
        if not candidate.is_relative_to(staging):
            raise BackupError("staged file must live inside the backup staging directory")
        if not candidate.is_file():
            raise FileNotFoundError(f"staged backup file not found: {candidate.name}")
        return candidate

    def _extract_backup(self, plaintext: bytes) -> dict[str, Any]:
        """Estrae lo zip decifrato negli staging dir. Ritorna il metadata."""
        staged_ws = self._runtime_root / STAGED_WORKSPACE_DIR_NAME
        staged_snap = self._runtime_root / STAGED_SNAPSHOTS_DIR_NAME
        shutil.rmtree(staged_ws, ignore_errors=True)
        shutil.rmtree(staged_snap, ignore_errors=True)

        try:
            archive = zipfile.ZipFile(io.BytesIO(plaintext))
        except zipfile.BadZipFile as exc:
            raise BackupError("decrypted payload is not a valid backup archive") from exc

        with archive:
            names = archive.namelist()
            if "metadata.json" not in names:
                raise BackupError("backup archive is missing metadata.json")
            try:
                metadata = json.loads(archive.read("metadata.json").decode("utf-8"))
            except (ValueError, UnicodeDecodeError) as exc:
                raise BackupError("backup metadata.json is unreadable") from exc

            extracted_tree = 0
            for name in names:
                dest_root: Path | None = None
                if name.startswith("tree/"):
                    dest_root, rel = staged_ws, name[len("tree/"):]
                elif name.startswith("snapshots/"):
                    dest_root, rel = staged_snap, name[len("snapshots/"):]
                else:
                    continue
                if not rel or name.endswith("/"):
                    continue
                # Guardia zip-slip: niente path assoluti né risalite.
                pure = PurePosixPath(rel)
                if pure.is_absolute() or ".." in pure.parts:
                    raise BackupError(f"backup archive contains an unsafe path: {name}")
                target = dest_root / Path(*pure.parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(archive.read(name))
                if dest_root is staged_ws:
                    extracted_tree += 1

        if extracted_tree == 0:
            shutil.rmtree(staged_ws, ignore_errors=True)
            shutil.rmtree(staged_snap, ignore_errors=True)
            raise BackupError("backup archive contains no workspace tree")
        return metadata

    # -- restore da storia locale --------------------------------------------------

    async def stage_snapshot_restore(self, snapshot_id: str) -> dict[str, Any]:
        """Prepara il ripristino a uno snapshot della storia locale.

        Raises:
            FileNotFoundError: snapshot inesistente.
        """
        async with self._lock:
            manifest = await asyncio.to_thread(self._engine.load_manifest, snapshot_id)
            # Fotografa lo stato corrente: il restore resta sempre annullabile.
            await self._service.snapshot_now("pre_restore")
            staged_dir = self._runtime_root / STAGED_WORKSPACE_DIR_NAME
            await asyncio.to_thread(shutil.rmtree, staged_dir, True)
            await asyncio.to_thread(self._engine.restore_snapshot, snapshot_id, staged_dir)
            await asyncio.to_thread(write_staging_sanity, staged_dir)
            write_marker(self._runtime_root, source="snapshot", snapshot_id=snapshot_id)
            logger.info(
                "Snapshot restore staged ({}); restart required to apply", snapshot_id[:12]
            )
            return {
                "snapshot_id": snapshot_id,
                "created_at_ms": manifest.created_at_ms,
                "file_count": manifest.file_count,
            }


def _jenny_version() -> str:
    try:
        from jenny import __version__

        return __version__
    except Exception:
        return "unknown"
