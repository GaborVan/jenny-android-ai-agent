"""Workspace file management: CRUD operations for shared workspace."""

from __future__ import annotations

import json
import shutil
from fnmatch import fnmatch
from pathlib import Path
from typing import Any

from jenny.utils.path import atomic_write

# Oltre ai dotfile, questi sono stato/implementazione del runtime (non
# contenuto dell'utente): config.json contiene un secret e si edita dalle
# Impostazioni, agent/ e ui/ sono bundle rigenerati ad ogni avvio, cron/ e
# sessions/ sono storage interno dei rispettivi motori.
#
# ``config.json*`` con la stella, non il nome esatto: accanto al file vivo
# possono comparire il backup ``config.json.bak`` e i temporanei di
# ``atomic_write``, che contengono le stesse chiavi API e lo stesso secret.
# Nascondere solo il nome esatto li avrebbe esposti nel browser file.
#
# ``*.tmp``: il temporaneo di ``atomic_write`` è invisibile solo per il tempo
# di una scrittura, ma un processo ucciso in quel momento lo lascia lì per
# sempre. È un residuo del runtime, non un file dell'utente, e mostrarlo
# accanto all'originale invita solo ad aprire quello sbagliato.
#
# ``update_state.json``: lo scrive il controllo aggiornamenti nella radice del
# workspace (``runtime/update_check.py``, ``STATE_FILENAME``). È il diario di
# bordo dell'updater — ultima verifica, versione vista, esito — non qualcosa
# che l'utente abbia creato o debba modificare: editarlo a mano confonde
# soltanto la logica di controllo.
#
# ``__pycache__``: la genera l'interprete Python ovunque l'agente importi un
# modulo del workspace (oggi sotto ``skills/``, domani altrove), quindi non
# basta coprirla nella radice. Il nome secco funziona a **qualsiasi** profondità
# perché ``_is_internal`` prova il glob anche sul solo nome dell'item; le due
# varianti con ``/**`` servono per il contenuto della cartella, che ha nomi
# arbitrari (``*.pyc``, e la sottodirectory che i writer di bytecode possono
# aggiungere) e che si vede solo entrandoci in modalità avanzata. Sono pattern
# ancorati su ``__pycache__/`` e non su ``*__pycache__*``: una cartella
# dell'utente che contenga quella parola nel nome resta visibile.
_DEFAULT_INTERNAL_PATTERNS = [
    ".*",
    "*.tmp",
    "config.json*",
    "config.corrupt-*.json",
    "update_state.json",
    "__pycache__", "__pycache__/**", "*/__pycache__/**",
    "agent", "agent/**",
    "cron", "cron/**",
    "sessions", "sessions/**",
    "ui", "ui/**",
]


def validate_path(workspace_root: Path, requested_path: str) -> Path:
    """Validate and resolve a path within workspace.

    Prevents path traversal attacks. Delega all'UNICO gate di path del core
    (`security.workspace_policy.resolve_allowed_path`, symlink-safe e
    fail-closed) invece di reimplementare il controllo. Mantiene il contratto
    storico di sollevare ``ValueError`` fuori dai confini, atteso dai chiamanti
    delle route WebUI.
    """
    from jenny.security.workspace_policy import (
        WorkspaceBoundaryError,
        resolve_allowed_path,
    )

    try:
        return resolve_allowed_path(
            requested_path,
            workspace=workspace_root,
            allowed_root=workspace_root,
        )
    except WorkspaceBoundaryError as exc:
        raise ValueError("Path traversal detected") from exc


def _load_internal_patterns(workspace_root: Path) -> list[str]:
    """Legge <workspace_root>/.jenny/internal.json.

    Fallback silenzioso a [".*"] se il manifest manca o è malformato, per
    riprodurre il comportamento storico (i dotfile erano già nascosti nella
    vista tree) senza mai propagare un'eccezione alle route WebUI.
    """
    manifest = workspace_root / ".jenny" / "internal.json"
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
        patterns = data.get("patterns")
        if isinstance(patterns, list) and all(isinstance(p, str) for p in patterns):
            return patterns
    except (OSError, ValueError, AttributeError):
        pass
    return _DEFAULT_INTERNAL_PATTERNS


def _is_internal(rel_path: str, name: str, patterns: list[str]) -> bool:
    """Un item è interno se il nome o il path relativo matcha un pattern glob."""
    return any(fnmatch(name, p) or fnmatch(rel_path, p) for p in patterns)


def list_directory(path: Path, *, workspace_root: Path | None = None) -> list[dict[str, Any]]:
    """List directory contents with metadata.

    Raises FileNotFoundError if path does not exist, PermissionError if not accessible.
    """
    # Su Android /data/data/... e /data/user/0/... sono alias simlink dello
    # stesso path: risolvere qui allinea workspace_root alla forma canonica
    # già usata da `path` (arrivato via validate_path, che risolve i symlink),
    # altrimenti relative_to() fallisce per un mismatch puramente testuale.
    if workspace_root is not None:
        workspace_root = workspace_root.resolve()
    patterns = _load_internal_patterns(workspace_root) if workspace_root is not None else None
    items = []
    for item in sorted(path.iterdir()):
        stat = item.stat()
        internal = False
        if patterns is not None and workspace_root is not None:
            rel = str(item.relative_to(workspace_root))
            internal = _is_internal(rel, item.name, patterns)
        items.append({
            "name": item.name,
            "type": "directory" if item.is_dir() else "file",
            "size": stat.st_size if item.is_file() else None,
            "modified": stat.st_mtime,
            "extension": item.suffix if item.is_file() else None,
            "internal": internal,
        })
    return items


class WorkspaceBinaryFileError(ValueError):
    """Il file richiesto è binario e non può essere letto come testo."""


# Stessa euristica di webui.file_preview: un byte nullo nei primi 4 KB
# marca il file come binario. La decisione è sul contenuto, mai
# sull'estensione: qualsiasi file di testo resta leggibile.
_BINARY_SNIFF_BYTES = 4096


def read_file(path: Path, max_size: int = 1_000_000) -> str:
    """Read file content with size limit.

    Raises FileNotFoundError if path does not exist, PermissionError if not readable.
    Solleva ``WorkspaceBinaryFileError`` se il contenuto è binario.
    """
    if path.stat().st_size > max_size:
        raise ValueError(f"File too large (max {max_size} bytes)")
    raw = path.read_bytes()
    if b"\0" in raw[:_BINARY_SNIFF_BYTES]:
        raise WorkspaceBinaryFileError("binary file")
    return raw.decode("utf-8", errors="replace")


def write_file(path: Path, content: str) -> None:
    """Write content to file."""
    # Salvataggio dall'editor della WebUI: riscrive il file intero, quindi un
    # processo ucciso a metà lo troncherebbe. Il contenuto vecchio resta valido
    # fino al rename finale.
    atomic_write(path, content)


def create_directory(path: Path) -> None:
    """Create a directory."""
    path.mkdir(parents=True, exist_ok=True)


def rename_path(old_path: Path, new_path: Path) -> None:
    """Rename a file or directory."""
    old_path.rename(new_path)


def delete_path(path: Path) -> None:
    """Delete a file or directory."""
    if path.is_dir():
        shutil.rmtree(path)
    else:
        path.unlink()


def copy_path(src: Path, dest: Path) -> None:
    """Copy a file or directory."""
    if src.is_dir():
        shutil.copytree(src, dest)
    else:
        shutil.copy2(src, dest)
