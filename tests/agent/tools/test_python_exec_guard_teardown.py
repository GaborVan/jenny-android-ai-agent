"""Ingresso e uscita dal guard devono reggere un'eccezione ASINCRONA.

``PythonExecInterrupted`` è iniettata con ``PyThreadState_SetAsyncExc`` e può
atterrare su QUALUNQUE confine di bytecode — anche dentro ``_enter_guard`` e
``_exit_guard``, e il percorso di timeout la spara esattamente mentre
``execute()`` sta per tornare. Due conseguenze, entrambe permanenti:

* se l'interrupt cade prima che i thread-local siano azzerati, il worker del
  default executor torna nel pool con ``boundary``/``rules`` ancora attivi e
  ogni ``asyncio.to_thread`` successivo che ci finisce sopra gira dentro il
  guard — snapshot, notifier, backup che falliscono con
  ``WorkspaceBoundaryError`` su un thread non identificabile;
* se cade fra l'inserimento in ``sys.path`` e la sua registrazione, la voce
  resta in testa a ``sys.path`` senza che nulla la ricordi: una directory
  scrivibile dall'agente in ``sys.path[0]`` per tutta la vita del processo.

La finestra si simula facendo SOLLEVARE la chiamata in mezzo, non correndo con
i thread: l'iniezione asincrona non è riproducibile in modo deterministico.
"""

from __future__ import annotations

import os
import pathlib
import sys
import warnings

import pytest

from jenny.agent.tools import python_exec as px
from jenny.agent.tools.python_exec import (
    PythonExecInterrupted,
    PythonNamespace,
    _import_guard_state,
    _path_guard_state,
)
from jenny.config.tool_schemas import PythonExecConfig

_REFUSED = "outside allowed directory"


def _namespace(workspace_dir, *, restrict: bool = True) -> PythonNamespace:
    cfg = PythonExecConfig()
    return PythonNamespace(
        working_dir=str(workspace_dir),
        allowed_modules=cfg.allowed_modules,
        blocked_modules=cfg.blocked_modules,
        restrict_to_workspace=restrict,
        workspace=str(workspace_dir),
    )


@pytest.fixture
def workspace(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "inside.txt").write_text("inside")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("secret")
    return ws, outside


@pytest.fixture(autouse=True)
def _clean_guard_state():
    """Nessun test qui deve lasciare stato di guard sul thread di pytest."""
    yield
    _path_guard_state.boundary = None
    _path_guard_state.base = None
    _path_guard_state.bypass = False
    _path_guard_state.sys_path_entry = None
    _import_guard_state.rules = None


# ---------------------------------------------------------------------------
# `_exit_guard`: prima i thread-local, poi `sys.path`
# ---------------------------------------------------------------------------


def test_exit_guard_clears_thread_locals_even_if_the_sys_path_pop_raises(
    workspace, monkeypatch
) -> None:
    """Il pop di ``sys.path`` prende un lock e scorre una lista: è la parte
    FALLIBILE del teardown, e prima di questa correzione girava per prima."""
    ws, _ = workspace
    ns = _namespace(ws)

    def _boom() -> None:
        raise PythonExecInterrupted()

    monkeypatch.setattr(PythonNamespace, "_pop_exec_sys_path", staticmethod(_boom))

    with pytest.raises(PythonExecInterrupted):
        ns.execute("1 + 1")

    assert getattr(_path_guard_state, "boundary", None) is None
    assert getattr(_path_guard_state, "base", None) is None
    assert getattr(_import_guard_state, "rules", None) is None


def test_exit_guard_clears_a_leaked_bypass(workspace) -> None:
    """``bypass`` è l'unico pezzo di stato che ``_exit_guard`` non azzerava.

    Un ``True`` rimasto acceso spegne ``restrict_to_workspace`` per il resto
    della vita del processo, senza una riga di log.
    """
    ws, _ = workspace
    ns = _namespace(ws)
    _path_guard_state.bypass = True

    ns.execute("1 + 1")

    assert getattr(_path_guard_state, "bypass", False) is False


def test_enter_guard_normalises_a_leaked_bypass_before_running(workspace) -> None:
    """Fail-closed: un bypass ereditato da un worker del pool riciclato non
    deve poter far girare l'exec successivo fuori dal confine.

    La sonda passa da ``os.listdir`` e non da ``open``: l'``open`` del namespace
    guardato applica il confine da sé (``_resolve_workspace_write`` ricade su
    ``self.workspace``), mentre TUTTI i wrapper globali sono gated su
    ``_active_path_boundary()``, che un bypass acceso spegne.
    """
    ws, outside = workspace
    ns = _namespace(ws)
    _path_guard_state.bypass = True

    _, stderr, _ = ns.execute(f"import os; os.listdir({str(outside)!r})")

    assert _REFUSED in stderr


# ---------------------------------------------------------------------------
# `_push_exec_sys_path`: registrare prima di inserire
# ---------------------------------------------------------------------------


class _RaisingPath(list):
    """``sys.path`` che inserisce e POI viene interrotta.

    Riproduce esattamente la finestra: l'inserimento è già avvenuto, la riga
    successiva non gira più.
    """

    def insert(self, index, value):  # type: ignore[override]
        super().insert(index, value)
        raise PythonExecInterrupted()


def test_sys_path_entry_is_recorded_before_it_is_inserted(workspace, monkeypatch) -> None:
    ws, _ = workspace
    skill = ws / "skills" / "foo"
    skill.mkdir(parents=True)
    ns = _namespace(ws)

    original = list(sys.path)
    monkeypatch.setattr(sys, "path", _RaisingPath(original))
    try:
        with pytest.raises(PythonExecInterrupted):
            ns.execute("1 + 1", working_dir=str(skill))
        leftover = list(sys.path)
    finally:
        monkeypatch.undo()

    assert leftover == original, "an agent-writable directory leaked into sys.path"


def test_sys_path_is_restored_on_the_normal_path(workspace) -> None:
    """Controprova: senza interruzioni il comportamento non cambia."""
    ws, _ = workspace
    skill = ws / "skills" / "foo"
    skill.mkdir(parents=True)
    ns = _namespace(ws)

    before = list(sys.path)
    ns.execute("1 + 1", working_dir=str(skill))

    assert sys.path == before
    assert getattr(px._path_guard_state, "sys_path_entry", None) is None


# ---------------------------------------------------------------------------
# R5 — la sostituzione di `sys` nei moduli globali non deve sopravvivere al guard
# ---------------------------------------------------------------------------


def test_the_sys_proxy_does_not_leak_into_host_code(workspace) -> None:
    """Dopo un exec, `os.sys.modules` deve tornare a essere il dizionario VERO.

    ``_patch_sys_backreferences`` sostituisce ``os.sys``, ``pathlib.sys``,
    ``posixpath.sys``, ``warnings.sys`` &c. con un proxy, e — a differenza di
    ogni altro patch di questo file — la sostituzione è PERMANENTE: non c'è
    un punto in cui viene disfatta, e non potrebbe esserci senza correre con
    gli altri thread. L'unica difesa è che il proxy sia trasparente quando
    nessun guard è attivo. Quando non lo era, il primo ``python_exec`` del
    processo degradava il gateway per sempre: ``sys.modules`` restava un
    oggetto senza ``copy``, senza assegnazione, senza ``pop``.
    """
    ws, _ = workspace
    ns = _namespace(ws)
    ns.execute("import os, pathlib, io\n")

    for mod in (os, os.path, pathlib, warnings):
        assert mod.sys.modules is sys.modules, f"{mod.__name__}.sys.modules è un proxy"
        # Ciò che `inspect._signature_from_builtin` fa su ogni builtin sulla
        # 3.11: è da qui che il guasto è emerso, come 453 AttributeError.
        assert isinstance(mod.sys.modules.copy(), dict)


def test_the_filter_still_applies_inside_the_guard(workspace) -> None:
    """Controprova: la trasparenza è guard-gated, non una resa."""
    ws, _ = workspace
    ns = _namespace(ws)
    stdout, _, _ = ns.execute(
        "import os\n"
        "print(type(os.sys.modules).__name__)\n"
        "print('subprocess' in os.sys.modules)\n"
    )
    assert stdout.splitlines() == ["_GuardedSysModules", "False"]
