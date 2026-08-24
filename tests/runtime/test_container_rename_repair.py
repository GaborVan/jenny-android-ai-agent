"""Il ripristino dei rinomini di progetto interrotti è cablato all'avvio.

``follow_renamed_project`` scrive un giornale prima del primo ``rename`` e lo
chiude dopo l'ultimo, così un processo ucciso in mezzo lascia una traccia di
cosa stava facendo. Ma quella traccia serve a qualcosa solo se qualcuno la
legge, e l'unico momento in cui i file di traccia non li sta guardando nessuno è
l'avvio, prima che agente e canali possano aprire una sessione di progetto.

Questi test tengono il cablaggio, non il rimedio (quello sta in
``tests/session/test_project_rename_following.py``): che la chiamata ci sia,
che stia dopo il ``SessionManager``, e che un ripristino guasto non impedisca
al gateway di partire.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from jenny.runtime.container import GatewayContainer


def _container(tmp_path: Path) -> GatewayContainer:
    container = GatewayContainer.__new__(GatewayContainer)
    container.config = MagicMock()
    container.config.workspace_path = tmp_path
    return container


def test_the_boot_completes_a_pending_rename(tmp_path, monkeypatch):
    seen: list[Path] = []
    monkeypatch.setattr(
        "jenny.session.project_rename.repair_pending_project_renames",
        lambda workspace: (seen.append(workspace) or [("project:vecchio", "project:nuovo")]),
    )

    _container(tmp_path)._repair_pending_renames()

    assert seen == [tmp_path], "il ripristino non è stato chiamato col workspace"


def test_a_broken_repair_does_not_stop_the_gateway(tmp_path, monkeypatch):
    """Il ramo non è «rinomini finiti contro rinomini a metà», è «un rinomino a
    metà contro nessun gateway»."""

    def explode(workspace):
        raise OSError("disco pieno")

    monkeypatch.setattr(
        "jenny.session.project_rename.repair_pending_project_renames", explode
    )

    _container(tmp_path)._repair_pending_renames()  # non solleva


def test_the_repair_runs_after_the_session_manager_is_built():
    """L'ordine è il contratto: prima il ``SessionManager``, poi il ripristino,
    e comunque prima che qualcuno possa aprire una sessione di progetto."""
    import inspect

    source = inspect.getsource(GatewayContainer.build)
    manager = source.index("self.session_manager = SessionManager(")
    repair = source.index("self._repair_pending_renames()")
    assert manager < repair
    # E prima del dispatcher, che è il primo che può consegnare un messaggio.
    assert repair < source.index("WebSocketDispatcher(")
