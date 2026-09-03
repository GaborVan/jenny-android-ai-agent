"""Adapter locale per la sync memoria↔Drive: solo I/O su ``Path``, nessuna
decisione (quella è in ``drive_sync_algorithm.plan_sync``).

Scope sincronizzato: ``SOUL.md``/``USER.md`` alla radice del workspace, e
``memory/`` ricorsiva. In più lo scope condiviso: lo specchio locale
``shared/`` (sottocartelle profile/knowledge/notes) della cartella Drive
condivisa — v. ``drive_sync_algorithm.SHARED_SUBFOLDERS``. Niente altro —
non ``.jenny/``, non ``config.json``, non ``skills/``.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from pathlib import Path

from jenny.runtime.drive_sync_algorithm import (
    MEMORY_PREFIX,
    ROOT_SCOPE_FILES,
    SHARED_PREFIX,
    SHARED_SUBFOLDERS,
    FileMeta,
    decode_name,
    encode_name,
)


def _iter_scope_relpaths(workspace: Path) -> Iterator[str]:
    for name in ROOT_SCOPE_FILES:
        if (workspace / name).is_file():
            yield name
    memory_dir = workspace / "memory"
    if memory_dir.is_dir():
        for path in sorted(memory_dir.rglob("*")):
            if path.is_file():
                yield MEMORY_PREFIX + path.relative_to(memory_dir).as_posix()
    shared_root = workspace / "shared"
    if shared_root.is_dir():
        # Solo le sottocartelle autorizzate: un file messo direttamente in
        # shared/ o in una sottocartella estranea non partecipa alla sync.
        for folder in SHARED_SUBFOLDERS:
            subdir = shared_root / folder
            if not subdir.is_dir():
                continue
            for path in sorted(subdir.rglob("*")):
                if path.is_file():
                    yield SHARED_PREFIX + path.relative_to(shared_root).as_posix()


def scope_snapshot(workspace: Path) -> dict[str, FileMeta]:
    """Nome codificato -> ``FileMeta`` per ogni file dello scope presente su disco."""
    snapshot: dict[str, FileMeta] = {}
    for relpath in _iter_scope_relpaths(workspace):
        path = workspace / relpath
        try:
            data = path.read_bytes()
            mtime = path.stat().st_mtime
        except OSError:
            continue  # sparito tra il listing e la lettura: ignorato, non un errore di sync
        snapshot[encode_name(relpath)] = FileMeta(
            mtime=mtime, sha256=hashlib.sha256(data).hexdigest()
        )
    return snapshot


def resolve_scope_path(workspace: Path, encoded_name: str) -> Path | None:
    """Decodifica e valida che il risultato resti dentro ``workspace``.

    Seconda barriera oltre a ``decode_name``/``is_safe_scope_relpath``: quelle
    sono un controllo lessicale sulla stringa, questa risolve i symlink e
    conferma col filesystem reale — la stessa disciplina a due passaggi delle
    altre guardie path del progetto (v. ``resolveLocalFile`` in MainActivity.kt).
    """
    relpath = decode_name(encoded_name)
    if relpath is None:
        return None
    root = workspace.resolve(strict=False)
    candidate = (root / relpath).resolve(strict=False)
    try:
        candidate.relative_to(root)
    except ValueError:
        return None
    return candidate


def read_scope_file(workspace: Path, encoded_name: str) -> bytes | None:
    path = resolve_scope_path(workspace, encoded_name)
    if path is None or not path.is_file():
        return None
    try:
        return path.read_bytes()
    except OSError:
        return None


def write_scope_file(workspace: Path, encoded_name: str, data: bytes) -> Path | None:
    path = resolve_scope_path(workspace, encoded_name)
    if path is None:
        return None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    except OSError:
        return None
    return path
