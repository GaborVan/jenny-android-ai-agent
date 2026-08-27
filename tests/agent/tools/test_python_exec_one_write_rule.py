"""I due confini di scrittura di ``python_exec`` dicono la stessa cosa.

Passo **T4.14** di ``roadmap/audit-taccuino-corrections.md``, trovato da T4.4.

Dentro ``python_exec.py`` vivono due strade verso il filesystem, e ognuna aveva
la sua idea di dove una scrittura potesse atterrare:

* la superficie ``os`` (``os.remove``, ``os.rename``, ``os.mkdir``,
  ``shutil.rmtree`` …) passa da ``_mutation_boundary``, che **non allarga mai**:
  se il confine con cui il tool e' stato costruito e' piu' stretto della radice
  dello scope, vince il confine;
* la superficie ``open``/``io``/``os.open`` passa da
  ``_resolve_workspace_write``, che prendeva la radice dello scope **senza**
  quella clausola.

Misurate su una matrice di 40 righe (radice del tool × restrizione × scope ×
``boundary``): **36 identiche, 4 divergenti**, e le quattro sono tutte la stessa
forma — tool costruito su una cartella di progetto mentre e' legato lo scope
*personale*. La' la prima teneva la cartella e la seconda tornava alla radice
dell'installazione.

**Nessuna costruzione dell'albero produce quella forma, oggi.** Enumerate a
mano, sono due (piu' i test):

* ``AgentLoop._register_default_tools`` (``jenny/agent/loop.py``) costruisce il
  ``ToolContext`` con ``workspace=str(self.workspace)``, cioe' la radice
  dell'installazione: la radice del tool e' sempre **larga almeno quanto** lo
  scope, quindi la clamp non morde;
* ``SubagentManager._run_subagent`` (``jenny/agent/subagent.py``) usa
  ``root = workspace_scope.project_path if workspace_scope is not None else
  self.workspace`` e poi lega **quello stesso scope** con
  ``enter_workspace_scope(workspace_scope)``. Con lo scope: radice del tool ==
  radice dello scope. Senza: radice dell'installazione, come sopra.

Quindi T4.14 e' un refactoring a comportamento invariato — la regola sta ora in
un posto solo, ``_mutation_boundary``, e la chiamano entrambe — e questo file e'
la matrice che se ne accorgerebbe se una **terza** costruzione arrivasse a
mettere il tool dentro lo scope. Che e' letteralmente come e' nato T4.2: due
confini nello stesso file, d'accordo per coincidenza.

**Tutto passa dal tool vero e da ``await``**, non da ``PythonNamespace.execute``
diretta: il confine di scrittura arriva da ``current_tool_workspace()``, che
legge un ContextVar, e ``loop.run_in_executor`` non copia il contesto. Un
confine provato in modo sincrono non dice niente su quello che gira in
produzione (v. il commento in ``run_python_async``).
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from jenny.agent.tools.python_exec import PythonExecTool
from jenny.agent.tools.python_exec_builtins import _register_builtin_functions
from jenny.config.tool_schemas import PythonExecConfig
from jenny.security.workspace_access import (
    WorkspaceScope,
    build_workspace_scope,
    enter_workspace_scope,
)


@pytest.fixture
def install(tmp_path: Path) -> Path:
    """Un'installazione con due progetti e un file personale in cima."""
    (tmp_path / "SOUL.md").write_text("io\n", encoding="utf-8")
    for name in ("p", "q"):
        (tmp_path / "wikis" / name / "wiki").mkdir(parents=True)
        (tmp_path / "wikis" / name / "pagina.md").write_text("x\n", encoding="utf-8")
    return tmp_path


def _tool(root: Path) -> PythonExecTool:
    """Il tool costruito su *root*, come fa ``create()`` da ``ctx.workspace``."""
    cfg = PythonExecConfig()
    tool = PythonExecTool(
        working_dir=str(root),
        timeout=30,
        allowed_modules=cfg.allowed_modules,
        blocked_modules=cfg.blocked_modules,
        restrict_to_workspace=True,
        workspace=str(root),
    )
    _register_builtin_functions(
        tool.namespace, workspace=str(root), restrict_to_workspace=True
    )
    return tool


def _scope(install: Path, project: Path | None) -> WorkspaceScope:
    base = build_workspace_scope(install, "restricted")
    return base if project is None else dataclasses.replace(base, project_path=project)


# Le due superfici, e come si chiede a ognuna «questo percorso lo accetti?».
# ``os.chmod`` e' una mutazione che non lascia tracce da ripulire e passa da
# ``_guarded_os_path``; ``open(..., 'r+')`` passa da ``_resolve_workspace_write``
# senza troncare, quindi le due domande sono confrontabili sullo stesso file.
_SURFACES = {
    "os": "import os; os.chmod({p!r}, 0o644)",
    "open": "open({p!r}, 'r+').close()",
}

_ROOTS = {
    "install": lambda i: i,
    "wikis/p": lambda i: i / "wikis" / "p",
}

_SCOPES = {
    "personal": lambda i: None,
    "project-p": lambda i: i / "wikis" / "p",
    "project-q": lambda i: i / "wikis" / "q",
}

_TARGETS = {
    "SOUL.md": lambda i: i / "SOUL.md",
    "wikis/p/pagina.md": lambda i: i / "wikis" / "p" / "pagina.md",
    "wikis/q/pagina.md": lambda i: i / "wikis" / "q" / "pagina.md",
}


async def _accepted(tool: PythonExecTool, surface: str, target: Path) -> bool:
    """``True`` se quella superficie ha lasciato passare *target*."""
    out = await tool.execute(code=_SURFACES[surface].format(p=str(target)))
    if "WorkspaceBoundaryError" in out or "outside the allowed workspace" in out:
        return False
    assert "Traceback" not in out, f"caduta per altro su {surface}/{target}: {out!r}"
    return True


@pytest.mark.parametrize("root_id", list(_ROOTS), ids=list(_ROOTS))
@pytest.mark.parametrize("scope_id", list(_SCOPES), ids=list(_SCOPES))
@pytest.mark.parametrize("target_id", list(_TARGETS), ids=list(_TARGETS))
async def test_the_two_surfaces_answer_the_same(
    install: Path, root_id: str, scope_id: str, target_id: str
) -> None:
    """Diciotto celle, due risposte ognuna, e devono essere la stessa risposta.

    La cella che conta e' ``root_id="wikis/p"`` con ``scope_id="personal"``:
    e' la forma in cui le due formule divergevano prima di T4.14 — ``os``
    teneva ``wikis/p`` e ``open`` risaliva alla radice dell'installazione.
    Nessuna costruzione dell'albero la produce (v. il docstring del modulo),
    quindi e' la riga che difende dalla terza.
    """
    root = _ROOTS[root_id](install)
    target = _TARGETS[target_id](install)
    scope = _scope(install, _SCOPES[scope_id](install))

    with enter_workspace_scope(scope):
        tool = _tool(root)
        via_os = await _accepted(tool, "os", target)
        via_open = await _accepted(tool, "open", target)

    assert via_os == via_open, (
        f"tool su {root_id}, scope {scope_id}, target {target_id}: "
        f"os={'accetta' if via_os else 'rifiuta'} ma "
        f"open={'accetta' if via_open else 'rifiuta'} — due confini di scrittura "
        "nello stesso file sono come e' nato T4.2"
    )


async def test_the_narrower_tool_boundary_wins_over_a_wider_scope(
    install: Path,
) -> None:
    """La regola in una riga: **non si allarga mai**.

    Tool costruito su ``wikis/p``, scope personale (radice dell'installazione).
    Prendere la radice dello scope a occhi chiusi riporterebbe il tool a poter
    scrivere su ``SOUL.md``, che e' il contrario di quello per cui e' stato
    costruito stretto.
    """
    tool_root = install / "wikis" / "p"
    with enter_workspace_scope(_scope(install, None)):
        tool = _tool(tool_root)
        assert await _accepted(tool, "open", tool_root / "pagina.md")
        assert not await _accepted(tool, "open", install / "SOUL.md")
        assert not await _accepted(tool, "os", install / "SOUL.md")


async def test_the_scope_still_narrows_a_wider_tool(install: Path) -> None:
    """La controprova, e la riga che davvero gira in produzione.

    Tool sulla radice (``AgentLoop``), scope su ``wikis/p``: e' lo scope a
    stringere, su **entrambe** le superfici — il difetto di T4.2 era proprio che
    ``os`` non lo faceva.
    """
    with enter_workspace_scope(_scope(install, install / "wikis" / "p")):
        tool = _tool(install)
        assert await _accepted(tool, "open", install / "wikis" / "p" / "pagina.md")
        assert not await _accepted(tool, "open", install / "SOUL.md")
        assert not await _accepted(tool, "os", install / "SOUL.md")
        assert not await _accepted(tool, "os", install / "wikis" / "q" / "pagina.md")
