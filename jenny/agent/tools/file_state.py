"""Track file-read state for read-before-edit warnings and read deduplication."""

from __future__ import annotations

import hashlib
import os
from contextvars import ContextVar, Token
from dataclasses import dataclass
from pathlib import Path


@dataclass(slots=True)
class ReadState:
    mtime: float
    offset: int
    limit: int | None
    content_hash: str | None
    can_dedup: bool


def _hash_file(p: str) -> str | None:
    try:
        return hashlib.sha256(Path(p).read_bytes()).hexdigest()
    except OSError:
        return None


class FileStates:
    """Per-session read/write tracker.

    Owns its own state dict so read-dedup ("File unchanged since last read")
    and read-before-edit warnings stay scoped to one agent session and do
    not leak across sessions sharing this process.
    """

    __slots__ = ("_state", "writes_ok", "writes_attempted", "writes_refused_budget")

    def __init__(self) -> None:
        self._state: dict[str, ReadState] = {}
        # Contatori di attività di scrittura per la sessione. ``writes_attempted``
        # conta ogni intento di scrittura (anche quelli bloccati da policy o
        # falliti), ``writes_ok`` solo le scritture andate a buon fine. Servono a
        # Dream per distinguere "nulla da scrivere" (avanza il cursore) da
        # "voleva scrivere ma è stato bloccato" (NON avanza) — vedi
        # ``MemoryStore.dream_should_advance_cursor``.
        self.writes_ok: int = 0
        self.writes_attempted: int = 0
        # Solo diagnostica: distingue nei log "bloccato da policy" da "rifiutato
        # perché il risultato sfora il budget". Deliberatamente *non* letto da
        # ``MemoryStore.internal_run_should_commit``: un rifiuto di budget resta
        # un ``writes_attempted`` che non è diventato ``writes_ok``, quindi il
        # cursore non avanza — che è esattamente il comportamento voluto, un
        # fatto che Dream voleva scrivere e non ha scritto va riproposto.
        self.writes_refused_budget: int = 0

    def record_write_attempt(self) -> None:
        """Registra un intento di scrittura, prima della risoluzione del path.

        Chiamato all'inizio di ``_FsTool._resolve_write`` così da contare anche
        gli intenti bloccati (``PermissionError``) o falliti, non solo quelli
        riusciti tracciati da :meth:`record_write`.
        """
        self.writes_attempted += 1

    def record_read(self, path: str | Path, offset: int = 1, limit: int | None = None) -> None:
        """Record that a file was read (called after successful read)."""
        p = str(Path(path).resolve())
        try:
            mtime = os.path.getmtime(p)
        except OSError:
            return
        self._state[p] = ReadState(
            mtime=mtime,
            offset=offset,
            limit=limit,
            content_hash=_hash_file(p),
            can_dedup=True,
        )

    def record_write(self, path: str | Path) -> None:
        """Record that a file was written (updates mtime in state)."""
        # Chiamato solo dopo una scrittura riuscita: conta come esito positivo.
        self.writes_ok += 1
        p = str(Path(path).resolve())
        try:
            mtime = os.path.getmtime(p)
        except OSError:
            self._state.pop(p, None)
            return
        self._state[p] = ReadState(
            mtime=mtime,
            offset=1,
            limit=None,
            content_hash=_hash_file(p),
            can_dedup=False,
        )

    def check_read(self, path: str | Path) -> str | None:
        """Check if a file has been read and is fresh.

        Returns None if OK, or a warning string.
        When mtime changed but file content is identical (e.g. touch, editor save),
        the check passes to avoid false-positive staleness warnings.
        """
        p = str(Path(path).resolve())
        entry = self._state.get(p)
        if entry is None:
            return "Warning: file has not been read yet. Read it first to verify content before editing."
        try:
            current_mtime = os.path.getmtime(p)
        except OSError:
            return None
        if current_mtime != entry.mtime:
            if entry.content_hash and _hash_file(p) == entry.content_hash:
                entry.mtime = current_mtime
                return None
            return "Warning: file has been modified since last read. Re-read to verify content before editing."
        # mtime unchanged - still check content hash to detect quick modifications
        if entry.content_hash and _hash_file(p) != entry.content_hash:
            return "Warning: file has been modified since last read. Re-read to verify content before editing."
        return None

    def get(self, path: str | Path) -> ReadState | None:
        """Return the raw ReadState entry for a path, or None."""
        return self._state.get(str(Path(path).resolve()))

    def clear(self) -> None:
        """Clear all tracked state (useful for testing)."""
        self._state.clear()
        self.writes_ok = 0
        self.writes_attempted = 0
        self.writes_refused_budget = 0


class FileStateStore:
    """Lookup table for per-session file read/write state."""

    __slots__ = ("_states_by_key",)

    def __init__(self) -> None:
        self._states_by_key: dict[str, FileStates] = {}

    def for_session(self, session_key: str | None) -> FileStates:
        key = session_key or "__default__"
        states = self._states_by_key.get(key)
        if states is None:
            states = FileStates()
            self._states_by_key[key] = states
        return states

    def clear(self) -> None:
        self._states_by_key.clear()


_current_file_states: ContextVar[FileStates | None] = ContextVar(
    "jenny_file_states",
    default=None,
)


def current_file_states(default: FileStates) -> FileStates:
    """Return the FileStates bound to the current agent task, or a fallback."""
    return _current_file_states.get() or default


def bind_file_states(file_states: FileStates) -> Token[FileStates | None]:
    """Bind file read/write state for the current async task."""
    return _current_file_states.set(file_states)


def reset_file_states(token: Token[FileStates | None]) -> None:
    _current_file_states.reset(token)
