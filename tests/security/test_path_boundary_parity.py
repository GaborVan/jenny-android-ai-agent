"""Fase 0.3 — Caratterizzazione: parità (e divergenze) tra i due gate di path.

Confronta il gate del core `security.workspace_policy.resolve_allowed_path` con
quello della WebUI `webui.workspace_files.validate_path`, per fissare il
comportamento ATTUALE prima dell'unificazione di Fase 2.1/2.2.

Scoperte documentate (che correggono l'audit iniziale):
1. Entrambi usano `.resolve()`, quindi ENTRAMBI bloccano l'escape via symlink e
   via `..` — NON c'è il "gap symlink" temuto. La vera divergenza è (2)+(3).
2. `resolve_allowed_path` è FAIL-OPEN quando `allowed_root is None` (ritorna il
   path risolto senza confine). `validate_path` richiede sempre la root → è
   fail-closed al suo call site. → Fase 2.2 rende il core fail-closed.
3. Tipi d'errore diversi: core `WorkspaceBoundaryError` (PermissionError) vs
   webui `ValueError`; feature diverse (allowlist di file esatti solo nel core).
   → Fase 2.1 unifica su un solo gate.
"""

from __future__ import annotations

import os

import pytest

from jenny.security.workspace_policy import (
    UNRESTRICTED,
    WorkspaceBoundaryError,
    resolve_allowed_path,
)
from jenny.webui.workspace_files import validate_path


@pytest.fixture
def ws(tmp_path):
    root = tmp_path / "workspace"
    root.mkdir()
    (root / "inside.txt").write_text("ok", encoding="utf-8")
    return root


def test_both_allow_a_file_inside_the_workspace(ws) -> None:
    core = resolve_allowed_path("inside.txt", workspace=ws, allowed_root=ws)
    webui = validate_path(ws, "inside.txt")
    assert core == (ws / "inside.txt").resolve()
    assert webui == (ws / "inside.txt").resolve()


def test_both_block_dotdot_traversal(ws, tmp_path) -> None:
    secret = tmp_path / "secret.txt"
    secret.write_text("s", encoding="utf-8")

    with pytest.raises(WorkspaceBoundaryError):
        resolve_allowed_path("../secret.txt", workspace=ws, allowed_root=ws)
    with pytest.raises(ValueError):
        validate_path(ws, "../secret.txt")


def test_both_block_symlink_escape(ws, tmp_path) -> None:
    """Un symlink DENTRO il workspace che punta FUORI è bloccato da entrambi
    (entrambi risolvono i symlink con `.resolve()`)."""
    secret = tmp_path / "secret.txt"
    secret.write_text("s", encoding="utf-8")
    link = ws / "escape"
    try:
        os.symlink(secret, link)
    except (OSError, NotImplementedError):
        pytest.skip("symlink non supportato su questa piattaforma")

    with pytest.raises(WorkspaceBoundaryError):
        resolve_allowed_path("escape", workspace=ws, allowed_root=ws)
    with pytest.raises(ValueError):
        validate_path(ws, "escape")


def test_core_is_fail_closed_without_allowed_root(ws, tmp_path) -> None:
    """Fase 2.2: senza `allowed_root` (né allowlist di file) il core RIFIUTA
    (fail-closed), invece del vecchio comportamento fail-open."""
    secret = tmp_path / "secret.txt"
    secret.write_text("s", encoding="utf-8")

    with pytest.raises(WorkspaceBoundaryError):
        resolve_allowed_path("../secret.txt", workspace=ws)


def test_unrestricted_sentinel_opts_out_of_the_boundary(ws, tmp_path) -> None:
    """L'accesso illimitato deliberato passa per il sentinel esplicito."""
    secret = tmp_path / "secret.txt"
    secret.write_text("s", encoding="utf-8")

    resolved = resolve_allowed_path(
        "../secret.txt", workspace=ws, allowed_root=UNRESTRICTED
    )
    assert resolved == secret.resolve()
