"""Le directory `scripts/` delle skill finiscono su `sys.path`, e non se ne vanno.

Quattro test le inseriscono e nessuno le rimuove. Tre lo fanno a livello di
modulo (``test_app_creator_scripts``, ``test_skill_creator_scripts``,
``llm_wiki/test_scripts``) perché importano quegli script per nome — ``import
validate_app`` — e a quel punto un ``insert`` in un fixture è già tardi: l'import
avviene in fase di raccolta. Il quarto (``webui/test_project_create``) lo fa
dentro un helper, quindi a sorpresa e a metà sessione. Da lì in poi **ogni** test
ha quelle directory in testa a ``sys.path``, che è la posizione che vince su
tutto, stdlib compresa.

Chi invece si mette una directory sul path *dentro* un test la toglie già in un
``finally`` (``test_python_exec_boundary_completeness``,
``test_python_exec_working_dir``): quelli non c'entrano.

Finché i nomi lì dentro sono distinti non succede niente, e oggi lo sono: la
misura dice zero collisioni. La fragilità è per il futuro, e ha una forma
precisa: il giorno che una skill si porta uno ``scripts/types.py`` o
``scripts/queue.py``, ogni ``import types`` **successivo** — in un test, in
``jenny/``, dentro pytest — prende quel file. Il rosso che ne esce non nomina
questo file e non nomina quella skill.

Chiudere il buco per davvero vorrebbe dire caricare quegli script per posizione
invece che per nome, come fanno gli altri due test di `llm_wiki`. Non è gratis:
``scaffold.py`` importa i fratelli per nome, quindi caricarlo per posizione senza
registrarlo in ``sys.modules`` ne creerebbe una seconda copia — un problema più
sottile di quello che risolve. Meglio lasciare l'``insert`` e rendere rumorosa
l'unica cosa che lo rende pericoloso.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SKILLS = REPO / "jenny" / "skills"

# I nomi di primo livello del progetto: uno script che si chiamasse così
# vincerebbe su di loro esattamente come su un modulo di stdlib.
_PROJECT_TOP_LEVEL = {
    path.stem if path.suffix == ".py" else path.name
    for path in REPO.iterdir()
    if (path.suffix == ".py" or (path.is_dir() and (path / "__init__.py").exists()))
}


def _script_dirs() -> list[Path]:
    return sorted(p for p in SKILLS.glob("*/scripts") if p.is_dir())


def _scripts() -> list[Path]:
    return sorted(f for d in _script_dirs() for f in d.glob("*.py"))


def test_there_are_script_dirs_to_check() -> None:
    """Se la struttura delle skill cambia, questo test smette di misurare."""
    assert _scripts(), (
        f"nessuno script trovato sotto {SKILLS}/*/scripts: il perimetro di questo "
        "file è vuoto, quindi non protegge niente. Aggiornalo o rimuovilo."
    )


@pytest.mark.parametrize("script", _scripts(), ids=lambda p: f"{p.parent.parent.name}/{p.name}")
def test_a_script_name_does_not_shadow_a_module(script: Path) -> None:
    name = script.stem

    assert name not in sys.stdlib_module_names, (
        f"{script.relative_to(REPO)} si chiama come un modulo di stdlib. La sua "
        "directory sta in testa a sys.path per il resto della sessione di test, "
        f"quindi ogni `import {name}` successivo prende questo file. Rinominalo."
    )
    assert name not in _PROJECT_TOP_LEVEL, (
        f"{script.relative_to(REPO)} si chiama come un modulo o package di primo "
        f"livello del repo (`{name}`), e lo scavalcherebbe su sys.path. Rinominalo."
    )


def test_two_skills_do_not_ship_the_same_script_name() -> None:
    """Due `scripts/` sul path e lo stesso nome: vince quella inserita per ultima.

    Quale sia "per ultima" dipende dall'ordine di raccolta di pytest, cioè
    cambia con `-p randomly` o con un `-k`. È la peggiore specie di rosso.
    """
    by_name: dict[str, list[str]] = {}
    for script in _scripts():
        by_name.setdefault(script.stem, []).append(
            str(script.relative_to(REPO))
        )
    clashes = {name: paths for name, paths in by_name.items() if len(paths) > 1}
    assert not clashes, f"stesso nome di script in più skill: {clashes}"
