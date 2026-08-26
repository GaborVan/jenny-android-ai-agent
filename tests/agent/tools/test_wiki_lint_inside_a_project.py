"""``wiki_lint`` deve girare da dentro un progetto, ed è il cancello di una skill.

Il difetto, misurato sul telefono il 26/08/2026. Dentro `project:salute` ogni
forma di ``wiki_lint`` — ``'wikis/salute'``, ``'.'``, ``''`` — veniva rifiutata
identica:

    WorkspaceBoundaryError: Path /data/user/0/…/files/workspace is outside
    allowed directory /data/data/…/workspace/wikis/salute
    WORKSPACE BOUNDARY: 2 path operation(s) refused: os.mkdir …; os.stat …

**Non dipendeva dall'argomento.** La causa è che :func:`get_workspace_path` — un
accessor, per nome e per docstring — chiamava ``ensure_dir``, cioè un
``mkdir(parents=True, exist_ok=True)`` sulla radice del workspace **a ogni
chiamata**. Dentro ``python_exec`` il confine di *lettura* è il workspace ma
quello di *mutazione* è la cartella del progetto (v.
``PythonExecTool._mutation_boundary``): quel ``mkdir`` è una scrittura su un
percorso fuori dal progetto, quindi la guardia lo rifiutava — correttamente. Il
``mkdir`` era anche inutile: la cartella la crea ``android_entry`` prima di
``set_workspace_dir``.

Quanto costava: ``wiki_lint``, ``wiki_audit`` e ``wiki_scaffold`` non erano
eseguibili in **nessun** turno legato a un progetto, e il «hard gate» della skill
``llm-wiki`` — *esegui il lint e incolla il suo output letterale* — era un passo
che non poteva riuscire. Un cancello che rifiuta sempre non è un cancello.

La scrittura del ``__pycache__`` accanto allo script, che la guardia rifiuta
anch'essa, **non** è un problema: ``WorkspaceBoundaryError`` è un
``PermissionError``, cioè un ``OSError``, e ``importlib`` inghiotte gli
``OSError`` della cache del bytecode. Vale la pena averlo scritto perché è la
ragione per cui la correzione è una riga e non un'apertura del confine.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from jenny.agent.tools.python_exec import PythonExecTool
from jenny.agent.tools.python_exec_builtins import _register_builtin_functions
from jenny.config.paths import get_workspace_path, set_workspace_dir
from jenny.config.tool_schemas import PythonExecConfig
from jenny.security.workspace_access import (
    bind_workspace_scope,
    reset_workspace_scope,
    validate_workspace_scope_payload,
)

_BOUNDARY_ERROR = "WorkspaceBoundaryError"

# Il lint vero è 105 kB e non serve: quel che si prova è che lo script **si
# carichi ed esegua** da dentro un progetto, non cosa stampa. Uno script finto
# rende il test veloce e indipendente dal contenuto del lint.
_FAKE_LINT = "def lint(root):\n    print(f'linted {root}')\n    return 0\n"


@pytest.fixture
def scoped_project(tmp_path: Path):
    """Workspace + skill + un progetto, con lo scope del turno legato al progetto.

    Il ``set_workspace_dir`` vero, non un monkeypatch di
    ``get_workspace_path``: è **quella funzione** l'oggetto del test, e
    sostituirla è esattamente il modo in cui questo difetto è vissuto in un
    test-suite verde per settimane (v. la fixture ``wiki_workspace`` in
    ``test_python_exec_builtins_paths.py``, che la sostituisce di proposito per
    provare altro).
    """
    ws = tmp_path / "workspace"
    project = ws / "wikis" / "salute"
    scripts = ws / "skills" / "llm-wiki" / "scripts"
    for d in (project / "wiki", project / "raw" / "journal", scripts):
        d.mkdir(parents=True)
    (scripts / "lint_wiki.py").write_text(_FAKE_LINT, encoding="utf-8")

    previous = None
    try:
        previous = get_workspace_path()
    except RuntimeError:
        previous = None
    set_workspace_dir(ws)
    scope = validate_workspace_scope_payload(
        {"project_path": str(project), "access_mode": "restricted"},
        default_workspace=ws,
        default_restrict_to_workspace=True,
    )
    token = bind_workspace_scope(scope)
    try:
        yield ws, project
    finally:
        reset_workspace_scope(token)
        set_workspace_dir(previous) if previous else set_workspace_dir("")


def _tool(workspace: Path) -> PythonExecTool:
    """Il tool costruito **sulla radice**, come lo costruisce ``AgentLoop``.

    La restrizione al progetto può venire solo dallo scope del turno: un tool
    costruito già sulla cartella del progetto farebbe coincidere i due confini e
    il difetto non si vedrebbe. Stessa ragione, e stesso commento, di
    ``test_project_write_boundary._tool``.
    """
    cfg = PythonExecConfig()
    tool = PythonExecTool(
        working_dir=str(workspace),
        timeout=30,
        allowed_modules=cfg.allowed_modules,
        blocked_modules=cfg.blocked_modules,
        restrict_to_workspace=True,
        workspace=str(workspace),
    )
    _register_builtin_functions(
        tool.namespace, workspace=str(workspace), restrict_to_workspace=True
    )
    return tool


async def test_wiki_lint_runs_from_inside_a_project(scoped_project) -> None:
    """**Il test che il 26/08 sarebbe stato rosso.**

    Prova la forma che il prompt di progetto insegna — percorso relativo alla
    radice del workspace — perché è quella che il modello scrive.
    """
    ws, _project = scoped_project

    out = await _tool(ws).execute(code="print(wiki_lint('wikis/salute'))")

    assert _BOUNDARY_ERROR not in out, f"il lint è ancora irraggiungibile: {out!r}"
    assert "linted" in out, f"lo script non ha girato: {out!r}"


async def test_the_project_boundary_is_still_closed(scoped_project) -> None:
    """Il contro-limite, e senza di lui la correzione potrebbe essere un'apertura.

    ``wiki_lint`` passa da ``_wiki_root``, che impone il confine di **scrittura**
    (lo condivide con ``wiki_scaffold``, che crea file): la wiki di un altro
    progetto resta fuori. Se questo test passasse a verde insieme al precedente
    per la ragione sbagliata — confine allargato — cadrebbe qui.
    """
    ws, _project = scoped_project
    (ws / "wikis" / "etf" / "wiki").mkdir(parents=True)

    out = await _tool(ws).execute(code="print(wiki_lint('wikis/etf'))")

    assert _BOUNDARY_ERROR in out, f"il confine si è aperto: {out!r}"


def test_the_accessor_does_not_create_the_directory(tmp_path: Path) -> None:
    """La causa, isolata: un accessor non crea niente.

    È l'asserzione che una mutazione sul ramo ``ensure_dir`` non può lasciare
    verde, e dice il *perché* meglio dei due test sopra: quel ``mkdir`` sulla
    radice del workspace è la scrittura che la guardia rifiutava.
    """
    missing = tmp_path / "mai-creata"
    previous = None
    try:
        previous = get_workspace_path()
    except RuntimeError:
        previous = None
    set_workspace_dir(missing)
    try:
        assert get_workspace_path() == missing
        assert not missing.exists(), (
            "`get_workspace_path` ha creato la cartella: dentro `python_exec` "
            "quel mkdir è una scrittura fuori dal progetto, e viene rifiutata"
        )
    finally:
        set_workspace_dir(previous) if previous else set_workspace_dir("")


# ── L'alias: la seconda metà della causa ─────────────────────────────────────


def _restore(previous: Path | None) -> None:
    set_workspace_dir(previous) if previous else set_workspace_dir("")


def test_the_workspace_is_stored_canonical(tmp_path: Path) -> None:
    """Il percorso si risolve **al setter**, e questa è l'altra metà del 26/08.

    Su Android la cartella dati ha due nomi: Java passa
    ``/data/user/0/<pkg>/files/workspace`` e ``resolve()`` la riscrive in
    ``/data/data/<pkg>/files/workspace``. Le due forme non combaciano come
    prefisso, quindi la guardia di ``python_exec`` — che confronta il percorso di
    ogni operazione col confine, e il confine è risolto — rifiutava anche letture
    legittime. È anche il motivo per cui il ``mkdir`` dell'accessor **rilanciava**
    invece di essere inghiottito: ``pathlib.mkdir(exist_ok=True)`` ripiega su un
    ``is_dir()``, quel ``stat`` cadeva pure lui, ``is_dir()`` tornava ``False`` e
    l'errore risaliva.

    Riprodotto con un symlink, che è la stessa forma del problema: una radice
    raggiungibile con due nomi.
    """
    real = tmp_path / "real"
    (real / "workspace").mkdir(parents=True)
    alias = tmp_path / "alias"
    alias.symlink_to(real, target_is_directory=True)

    previous = None
    try:
        previous = get_workspace_path()
    except RuntimeError:
        previous = None
    set_workspace_dir(alias / "workspace")
    try:
        assert get_workspace_path() == real / "workspace", (
            "il workspace è memorizzato nella forma non canonica: la guardia "
            "confronta col confine risolto e rifiuta operazioni legittime"
        )
    finally:
        _restore(previous)


async def test_wiki_lint_runs_when_the_workspace_was_given_by_an_alias(
    tmp_path: Path,
) -> None:
    """Lo scenario del telefono, intero: alias + scope di progetto + lint.

    È il test che vale più degli altri due, perché è l'unica combinazione che il
    26/08 era rossa **sul dispositivo e verde in locale** — in locale il
    workspace arrivava già canonico, quindi il difetto non si vedeva.
    """
    real = tmp_path / "real"
    ws = real / "workspace"
    project = ws / "wikis" / "salute"
    scripts = ws / "skills" / "llm-wiki" / "scripts"
    for d in (project / "wiki", scripts):
        d.mkdir(parents=True)
    (scripts / "lint_wiki.py").write_text(_FAKE_LINT, encoding="utf-8")
    alias = tmp_path / "alias"
    alias.symlink_to(real, target_is_directory=True)

    previous = None
    try:
        previous = get_workspace_path()
    except RuntimeError:
        previous = None
    # Come lo passa Java: la forma con l'alias.
    set_workspace_dir(alias / "workspace")
    scope = validate_workspace_scope_payload(
        {"project_path": str(project), "access_mode": "restricted"},
        default_workspace=ws,
        default_restrict_to_workspace=True,
    )
    token = bind_workspace_scope(scope)
    try:
        out = await _tool(ws).execute(code="print(wiki_lint('wikis/salute'))")
    finally:
        reset_workspace_scope(token)
        _restore(previous)

    assert _BOUNDARY_ERROR not in out, f"il lint è ancora irraggiungibile: {out!r}"
    assert "linted" in out, f"lo script non ha girato: {out!r}"


# ── Il subagent: la radice del tool È il progetto ────────────────────────────
#
# `SubagentManager._tool_context` costruisce i tool con `workspace` = la cartella
# del progetto, quindi per un subagent il confine di **lettura** del sandbox è il
# progetto e gli script della skill stanno fuori. E sotto `orchestratorMode`
# l'agente principale non ha `python_exec` affatto: i subagent sono gli unici che
# possono eseguire codice, quindi è questa la forma che decide se il cancello del
# lint esiste o no. Il 26/08 non esisteva.


def _subagent_tool(project: Path) -> PythonExecTool:
    """Il tool come lo costruisce ``SubagentManager``: radice = il progetto."""
    cfg = PythonExecConfig()
    tool = PythonExecTool(
        working_dir=str(project),
        timeout=30,
        allowed_modules=cfg.allowed_modules,
        blocked_modules=cfg.blocked_modules,
        restrict_to_workspace=True,
        workspace=str(project),
    )
    _register_builtin_functions(
        tool.namespace, workspace=str(project), restrict_to_workspace=True
    )
    return tool


async def test_wiki_lint_runs_for_a_subagent_confined_to_the_project(
    scoped_project,
) -> None:
    """**Il test che decide il cancello**, perché è l'unico attore che esegue codice."""
    _ws, project = scoped_project

    out = await _subagent_tool(project).execute(code="print(wiki_lint('.'))")

    assert _BOUNDARY_ERROR not in out, f"il lint è ancora irraggiungibile: {out!r}"
    assert "linted" in out, f"lo script non ha girato: {out!r}"


async def test_the_bypass_does_not_leak_to_the_models_own_code(scoped_project) -> None:
    """Il contro-limite, ed è la proprietà che rende accettabile un bypass.

    La finestra copre il **caricamento** dello script, non l'esecuzione: dopo di
    quella il codice che il modello scrive resta guardato come prima. Senza questa
    asserzione la correzione potrebbe essere un'apertura del sandbox, e il test
    sopra sarebbe verde per la ragione sbagliata.
    """
    ws, project = scoped_project
    skill_file = ws / "skills" / "llm-wiki" / "scripts" / "lint_wiki.py"

    out = await _subagent_tool(project).execute(
        code=f"import os; print(os.stat({str(skill_file)!r}).st_size)"
    )

    assert _BOUNDARY_ERROR in out, f"il bypass è colato nel codice del modello: {out!r}"


async def test_the_bypass_does_not_open_writes(scoped_project) -> None:
    """E nemmeno in scrittura: ``wiki_scaffold`` fuori dal progetto resta rifiutato.

    Passa dallo stesso ``_load_wiki_script``, quindi lo script lo apre — ed è poi
    ``_wiki_root``, col confine di scrittura, a fermarlo sul bersaglio.
    """
    ws, project = scoped_project
    (ws / "skills" / "llm-wiki" / "scripts" / "scaffold.py").write_text(
        "def scaffold(root, title):\n    print(f'scaffolded {root}')\n", encoding="utf-8"
    )

    out = await _subagent_tool(project).execute(
        code=f"print(wiki_scaffold({str(ws / 'wikis' / 'nuova')!r}, 'Nuova'))"
    )

    assert _BOUNDARY_ERROR in out, f"lo scaffold è uscito dal progetto: {out!r}"
