"""Fase 4.2 — python_exec non muta più lo stato globale dell'interprete.

Prima, importare python_exec sostituiva process-wide ``builtins.__import__`` e
``importlib.import_module``/``reload`` con wrapper "guardati" — un hazard di
stato globale (e un arms-race che non conteneva davvero, dato che os/sys sono
consentiti). Il guardrail ora è solo namespace-local.
"""

from __future__ import annotations

import builtins
import importlib
import os

from jenny.agent.tools.python_exec import (
    PythonNamespace,
    _register_builtin_functions,
)
from jenny.config.tool_schemas import PythonExecConfig


def _restricted_namespace(workspace: str) -> PythonNamespace:
    """Namespace configurato come in produzione sotto restrizione workspace."""
    cfg = PythonExecConfig()
    ns = PythonNamespace(
        working_dir=workspace,
        allowed_modules=cfg.allowed_modules,
        blocked_modules=cfg.blocked_modules,
        restrict_to_workspace=True,
        workspace=workspace,
    )
    _register_builtin_functions(ns, workspace=workspace, restrict_to_workspace=True)
    return ns


def test_importing_python_exec_does_not_patch_global_import_hooks() -> None:
    # Import (idempotente) del modulo tool.
    import jenny.agent.tools.python_exec  # noqa: F401

    # I callable globali devono essere quelli reali dell'interprete, non wrapper
    # definiti in python_exec.
    for fn in (builtins.__import__, importlib.import_module, importlib.reload):
        module_name = getattr(fn, "__module__", "") or ""
        assert not module_name.startswith("jenny"), (
            f"{fn!r} risulta ancora monkeypatchato da jenny ({module_name})"
        )

    # E un import normale funziona senza passare da un guard.
    import json as _json

    assert _json is not None


def test_os_open_patch_does_not_leak_to_host_code(tmp_path) -> None:
    """Regressione: dopo un exec guardato che importa ``os``, il patch di
    ``os.open`` sul modulo globale non deve confinare anche il codice host.

    Era il bug che mandava in 500 la tab Wiki: python_exec (scaffold) patchava
    ``os.open`` globalmente senza gate né ripristino, poi il gateway importava
    ``markdown`` → estrazione della ``.so`` nativa via ``tempfile`` → ``os.open``
    di un file temporaneo FUORI dal workspace → ``WorkspaceBoundaryError``.
    """
    workspace = tmp_path / "ws"
    workspace.mkdir()
    ns = _restricted_namespace(str(workspace))

    # Innesca il patch globale di os.open (come fa qualunque `import os` guardato).
    _, stderr, _ = ns.execute("import os")
    assert stderr == ""

    # Codice host, nessun guard attivo su questo thread: os.open di un file
    # FUORI dal workspace deve funzionare (passthrough al vero os.open).
    outside = tmp_path / "host_outside.txt"
    fd = os.open(str(outside), os.O_WRONLY | os.O_CREAT, 0o600)
    try:
        os.write(fd, b"host")
    finally:
        os.close(fd)
    assert outside.read_text() == "host"


def test_guarded_os_open_stays_contained(tmp_path) -> None:
    """Il confine resta attivo DENTRO l'exec guardato (contenimento preservato)."""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    ns = _restricted_namespace(str(workspace))

    outside = tmp_path / "guarded_outside.txt"
    _, stderr, _ = ns.execute(
        f"import os; os.close(os.open({str(outside)!r}, os.O_WRONLY | os.O_CREAT))"
    )
    assert "outside allowed directory" in stderr
    assert not outside.exists()


def test_blocked_os_functions_do_not_leak_to_host_code(tmp_path) -> None:
    """Part B: dopo un exec guardato le funzioni os bloccate restano usabili dal
    codice host (lo stub delega alla funzione reale quando nessun guard è attivo),
    ma restano bloccate dentro l'exec guardato."""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    ns = _restricted_namespace(str(workspace))

    _, stderr, _ = ns.execute("import os")
    assert stderr == ""

    # Host: la chiamata non deve sollevare lo stub "not available on this platform".
    # (Può sollevare un OSError diverso — es. assenza di tty — e va bene: prova
    # comunque che è stata invocata la funzione reale, non lo stub.)
    try:
        os.get_terminal_size()
    except OSError as exc:
        assert "not available on this platform" not in str(exc)

    # Guarded: la stessa funzione resta bloccata.
    _, stderr, _ = ns.execute("import os; os.get_terminal_size()")
    assert "not available on this platform" in stderr
