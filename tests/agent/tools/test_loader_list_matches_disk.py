"""``_HARDCODED_TOOL_MODULES`` e i moduli su disco devono dire la stessa cosa.

La registrazione esplicita protegge da un tool che compare per sbaglio; non
protegge dal contrario. **Un modulo nuovo con la sua lista ``TOOLS`` e nessuna
voce nell'elenco non carica niente, in silenzio**: nessun errore all'avvio,
nessun log, semplicemente un tool che il modello non vede mai — e chi l'ha
scritto ha il file, i test del file, e nessun segnale.

Prima di questo test la cosa non era coperta. Tre test asserivano una singola
appartenenza ciascuno (``test_ssh.py``, ``test_browser.py``,
``test_memory_recall.py``): un pattern che protegge solo i moduli che qualcuno
si è ricordato di nominare, cioè non quello nuovo.

Il confronto è nei due sensi, perché sbagliano in due modi diversi: una voce
senza file fa fallire l'import all'avvio (rumoroso, si scopre subito), un file
senza voce non fa niente (silenzioso, è il caso che conta).
"""

from __future__ import annotations

import ast
from pathlib import Path

from jenny.agent.tools.loader import _HARDCODED_TOOL_MODULES

TOOLS_DIR = Path(__file__).resolve().parents[3] / "jenny" / "agent" / "tools"

# Non sono moduli di tool: l'infrastruttura del registro stesso.
_NOT_TOOL_MODULES = {"__init__", "loader", "registry", "base", "context"}


def _tools_list(path: Path) -> list[str] | None:
    """I nomi in ``TOOLS = [...]``, oppure ``None`` se il modulo non la dichiara."""
    tree = ast.parse(path.read_text("utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "TOOLS" for t in node.targets
        ):
            if isinstance(node.value, ast.List):
                return [e.id for e in node.value.elts if isinstance(e, ast.Name)]
            return []
    return None


def _on_disk() -> dict[str, list[str] | None]:
    return {
        path.stem: _tools_list(path)
        for path in sorted(TOOLS_DIR.glob("*.py"))
        if path.stem not in _NOT_TOOL_MODULES
    }


def test_every_module_that_declares_tools_is_in_the_loader_list() -> None:
    """Il verso che fallisce in silenzio, ed è il motivo per cui questo file esiste."""
    listed = set(_HARDCODED_TOOL_MODULES)
    declaring = {name for name, tools in _on_disk().items() if tools}

    unlisted = sorted(declaring - listed)
    assert not unlisted, (
        f"moduli con una lista TOOLS non vuota e nessuna voce in "
        f"_HARDCODED_TOOL_MODULES: {unlisted}. Non caricano niente e non lo dicono."
    )


def test_every_listed_module_exists_on_disk() -> None:
    on_disk = set(_on_disk())
    phantom = sorted(name for name in _HARDCODED_TOOL_MODULES if name not in on_disk)

    assert not phantom, (
        f"voci di _HARDCODED_TOOL_MODULES senza file: {phantom}. "
        "Questo fallisce già all'avvio, ma qui lo dice prima e per nome."
    )


def test_an_empty_tools_list_is_deliberate_and_documented() -> None:
    """``self.py`` dichiara ``TOOLS = []`` di proposito: ``MyTool`` si registra a mano.

    Serve una lista vuota e non l'assenza della lista, perché il loader tratta
    l'assenza come un errore di avvio. Se un altro modulo arriva a questa forma,
    o ha la stessa ragione — e allora va nominata qui — oppure è un modulo che ha
    perso i suoi tool senza che nessuno se ne accorgesse.
    """
    empty = sorted(name for name, tools in _on_disk().items() if tools == [])

    assert empty == ["self"], (
        f"moduli con TOOLS vuota: {empty}. Attesa solo `self` (MyTool ha bisogno "
        "di un riferimento vivo all'AgentLoop e si registra in loop.py)."
    )

    source = (TOOLS_DIR / "self.py").read_text("utf-8")
    assert "TOOLS" in source and "MyTool" in source


def test_tool_names_do_not_collide() -> None:
    """Una collisione fa fallire l'avvio; qui si sa *quali* due, prima di avviare."""
    seen: dict[str, str] = {}
    collisions = []
    for module, tools in _on_disk().items():
        if not tools:
            continue
        source = (TOOLS_DIR / f"{module}.py").read_text("utf-8")
        tree = ast.parse(source)
        for cls in [n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]:
            if cls.name not in tools:
                continue
            name = None
            for item in cls.body:
                if isinstance(item, ast.Assign) and any(
                    isinstance(t, ast.Name) and t.id == "name" for t in item.targets
                ) and isinstance(item.value, ast.Constant):
                    name = item.value.value
                if isinstance(item, ast.FunctionDef) and item.name == "name":
                    for stmt in ast.walk(item):
                        if isinstance(stmt, ast.Return) and isinstance(stmt.value, ast.Constant):
                            name = stmt.value.value
            if not isinstance(name, str):
                continue
            if name in seen:
                collisions.append(f"{name}: {seen[name]} e {module}")
            seen[name] = module

    assert not collisions, f"nomi di tool duplicati: {collisions}"
