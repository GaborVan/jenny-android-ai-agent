"""Workspace path boundary helpers.

These helpers are application-level guards.  They make path decisions
consistent across tools, but they are not a replacement for an OS sandbox.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Iterable


def _safe_expanduser(path: str | Path) -> Path:
    """Return an expanded path, gracefully handling platforms without HOME."""
    p = Path(path)
    try:
        return p.expanduser()
    except (RuntimeError, OSError):
        if p.is_absolute():
            return p.resolve()
        first = str(p)
        if first.startswith("~"):
            return Path(first[1:].lstrip("/") if first.startswith("~/") else "")
        return p


WORKSPACE_BOUNDARY_NOTE = (
    " (this is a hard policy boundary, not a transient failure; "
    "do not retry with alternative tools, and ask "
    "the user how to proceed if the resource is genuinely required)"
)


class WorkspaceBoundaryError(PermissionError):
    """Raised when a requested path escapes an allowed workspace boundary."""


class _Unrestricted:
    """Sentinella per l'opt-out ESPLICITO dal confine di workspace.

    ``resolve_allowed_path`` è fail-closed: ``allowed_root=None`` (senza
    allowlist di file) viene rifiutato. Chi vuole davvero accesso illimitato
    deve passare ``allowed_root=UNRESTRICTED`` — così "nessun confine" è una
    scelta deliberata e greppabile, non un default accidentale."""

    _instance: "_Unrestricted | None" = None

    def __new__(cls) -> "_Unrestricted":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __repr__(self) -> str:
        return "UNRESTRICTED"


UNRESTRICTED = _Unrestricted()


def _resolve_path(path: str | Path, workspace: str | Path | None = None, *, strict: bool = False) -> Path:
    """Resolve *path*, interpreting relative paths against *workspace* when set."""
    candidate = _safe_expanduser(path)
    if not candidate.is_absolute() and workspace is not None:
        candidate = _safe_expanduser(workspace) / candidate
    return candidate.resolve(strict=strict)


def _resolve_logical_path(path: str | Path, workspace: str | Path | None = None) -> Path:
    """Return an absolute normalized path without following symlinks."""
    candidate = _safe_expanduser(path)
    if not candidate.is_absolute() and workspace is not None:
        candidate = _safe_expanduser(workspace) / candidate
    return Path(os.path.abspath(candidate))


def _path_key(path: str | Path) -> str:
    return os.path.normcase(os.fspath(path))


def _is_path_within(path: str | Path, root: str | Path) -> bool:
    """Return True when *path* resolves to *root* or a descendant of *root*."""
    try:
        resolved_path = _safe_expanduser(path).resolve(strict=False)
        resolved_root = _safe_expanduser(root).resolve(strict=False)
        resolved_path.relative_to(resolved_root)
        return True
    except (OSError, RuntimeError, TypeError, ValueError):
        return False


def _is_path_allowed(path: str | Path, roots: Iterable[str | Path]) -> bool:
    """Return True when *path* is inside any allowed root."""
    return any(_is_path_within(path, root) for root in roots)


def _is_path_exactly_allowed(
    logical_path: Path,
    resolved_path: Path,
    files: Iterable[str | Path],
) -> bool:
    """Return True when *path* resolves exactly to one of the allowed files."""
    logical_key = _path_key(logical_path)
    if _path_key(resolved_path) != logical_key:
        return False
    for file in files:
        try:
            allowed_file = _resolve_logical_path(file)
        except (OSError, RuntimeError, TypeError, ValueError):
            continue
        if _path_key(allowed_file) == logical_key:
            return True
    return False


def resolve_allowed_path(
    path: str | Path,
    *,
    workspace: str | Path | None = None,
    allowed_root: "str | Path | _Unrestricted | None" = None,
    extra_allowed_roots: Iterable[str | Path] | None = None,
    extra_allowed_files: Iterable[str | Path] | None = None,
    strict: bool = False,
) -> Path:
    """Resolve a path and enforce containment in allowed roots.

    Fail-closed: se non è dato né ``allowed_root`` né un'allowlist di file
    esatti (``extra_allowed_files``), il percorso è RIFIUTATO. Per l'accesso
    deliberatamente illimitato passare ``allowed_root=UNRESTRICTED``.
    """
    resolved = _resolve_path(path, workspace, strict=False)
    files = list(extra_allowed_files or [])
    if allowed_root is UNRESTRICTED:
        # Opt-out esplicito: nessun confine.
        return _resolve_path(path, workspace, strict=strict) if strict else resolved
    if allowed_root is None and not files:
        # FAIL-CLOSED: nessun confine e nessuna allowlist → deny.
        raise WorkspaceBoundaryError(
            f"Path {path} rejected: no workspace boundary configured "
            "(pass allowed_root=<dir> or the UNRESTRICTED sentinel to opt out)"
            + WORKSPACE_BOUNDARY_NOTE
        )

    roots = []
    if allowed_root is not None:
        roots.append(allowed_root)
    roots.extend(extra_allowed_roots or [])
    exact_allowed = bool(files) and _is_path_exactly_allowed(
        _resolve_logical_path(path, workspace),
        resolved,
        files,
    )
    if not _is_path_allowed(resolved, roots) and not exact_allowed:
        boundary = _safe_expanduser(allowed_root) if allowed_root is not None else "allowed files"
        raise WorkspaceBoundaryError(
            f"Path {path} is outside allowed directory {boundary}"
            + WORKSPACE_BOUNDARY_NOTE
        )
    if strict:
        return _resolve_path(path, workspace, strict=True)
    return resolved
