"""Le scritture che usavano la radice dell'installazione invece di quella del turno.

Passo **6** di ``roadmap/progetti-passi.md``.

Sono una famiglia, non due casi isolati: un tool che si porta la destinazione
scritta dentro — ``<workspace>/downloads/``, ``apps/<nome>/data/`` — non passa da
``resolve_allowed_path``, quindi **il confine di scrittura non ha niente da
guardare**. Da dentro un progetto scrivevano fuori, mentre il prompt di quel
progetto diceva «scrivi solo dentro questa cartella»: la stessa classe di difetto
che il passo 2 ha sistemato nel prompt, qui sul lato dei file.

Sono venute a galla facendo il passo 4: **fare la sola lettura obbliga a trovare
tutte le scritture.** La Todo il 22/08 sul telefono (chiedendo a Jenny di
aggirare il divieto), ``downloads/`` leggendo il codice subito dopo.

Le due si chiudono in modo diverso, e la differenza è una decisione sul
prodotto (22/08):

- ``downloads/`` **segue il turno**: un file scaricato lavorando a un progetto è
  materiale di quel progetto;
- le mini-app sono **personali**: da un progetto si leggono e non si scrivono. Una
  Todo per progetto darebbe sette liste vuote invece di quella che usi, e
  dichiararle condivise sarebbe costato una riga di prompt a ogni turno per
  raccontare l'eccezione.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from jenny.agent.tools.download import DOWNLOADS_SUBDIR, DownloadFileTool
from jenny.apps.manifest import AppAction
from jenny.apps.storage import StorageError, execute_storage_action
from jenny.security.workspace_access import build_workspace_scope, enter_workspace_scope


@pytest.fixture
def install(tmp_path: Path) -> Path:
    """Un'installazione con un progetto vero dentro (``wiki/`` compresa)."""
    (tmp_path / "wikis" / "patreon" / "wiki").mkdir(parents=True)
    (tmp_path / "apps" / "todo" / "data").mkdir(parents=True)
    return tmp_path


def _project(install: Path) -> Path:
    return install / "wikis" / "patreon"


def _action(op: str) -> AppAction:
    return AppAction(name=op, description="", kind="storage", collection="todos", op=op)


# ── downloads/ segue il turno ────────────────────────────────────────────


def test_downloads_lands_inside_the_project(install: Path) -> None:
    project = _project(install)
    tool = DownloadFileTool(str(install))
    with enter_workspace_scope(build_workspace_scope(project, "restricted")):
        root = tool._downloads_root()
    assert root == project, (
        "prima del passo 6 era la radice dell'installazione, cioè una scrittura fuori "
        "dal progetto che il confine non vedeva passare"
    )
    assert (root / DOWNLOADS_SUBDIR).parent == project


def test_the_personal_chat_still_downloads_into_the_installation(install: Path) -> None:
    tool = DownloadFileTool(str(install))
    with enter_workspace_scope(build_workspace_scope(install, "full")):
        assert tool._downloads_root() == install


def test_without_a_bound_scope_it_falls_back_to_the_constructor(install: Path) -> None:
    """Sessioni interne e test: là la radice del turno *è* quella del costruttore."""
    assert DownloadFileTool(str(install))._downloads_root() == install


async def test_a_download_is_refused_in_read_only_before_the_network(install: Path) -> None:
    """Controprova incrociata col passo 4: le due chiusure convivono."""
    client = MagicMock()
    client.get = MagicMock(side_effect=AssertionError("la rete non va toccata"))
    scope = build_workspace_scope(_project(install), "restricted").without_write_access()
    with enter_workspace_scope(scope):
        result = await DownloadFileTool(str(install), client=client).execute(
            url="https://example.invalid/a.jpg"
        )
    assert "read-only" in result


# ── le mini-app sono personali ───────────────────────────────────────────


@pytest.mark.parametrize("op", ["append", "set", "update", "delete"])
async def test_a_project_cannot_change_the_personal_app_data(install: Path, op: str) -> None:
    """La scrittura vista sul telefono il 22/08: la Todo personale, da una chat di lavoro."""
    with enter_workspace_scope(build_workspace_scope(_project(install), "restricted")):
        with pytest.raises(StorageError) as err:
            await execute_storage_action(
                install / "apps" / "todo", _action(op), {"text": "x", "id": "1"}
            )
    text = str(err.value)
    assert "personal" in text.lower()
    assert "chip" in text.lower(), "il rifiuto deve dire dove si fa, come quello del passo 3"


async def test_a_project_can_still_read_them(install: Path) -> None:
    """``query`` resta aperta: leggere la propria lista non è cambiarla."""
    app = install / "apps" / "todo"
    (app / "data" / "todos.jsonl").write_text('{"id":"1","text":"ciao"}\n', encoding="utf-8")
    with enter_workspace_scope(build_workspace_scope(_project(install), "restricted")):
        result = await execute_storage_action(app, _action("query"), {})
    assert result.get("ok") is not False


async def test_the_personal_chat_writes_them_as_always(install: Path) -> None:
    app = install / "apps" / "todo"
    with enter_workspace_scope(build_workspace_scope(install, "full")):
        result = await execute_storage_action(app, _action("append"), {"text": "x"})
    assert result["ok"] is True


async def test_a_bound_scope_that_is_not_a_wiki_is_not_a_project(install: Path) -> None:
    """La domanda è «sono in un progetto», e un progetto **è una wiki**.

    Si chiede alla cartella e non alla chiave di sessione, per la stessa ragione
    del blocco di prompt del 2.1: chi ha bisogno di questa risposta è anche il
    subagent, che la chiave non ce l'ha mai. Uno scope scelto a mano su una
    cartella qualunque non è un progetto e non deve inciampare qui.
    """
    plain = install / "altro"
    plain.mkdir()
    with enter_workspace_scope(build_workspace_scope(plain, "restricted")):
        result = await execute_storage_action(
            install / "apps" / "todo", _action("append"), {"text": "x"}
        )
    assert result["ok"] is True


# ── e il guardiano dell'inventario copre entrambe ────────────────────────


def test_both_are_still_in_the_write_inventory() -> None:
    """Chiuse, non sparite: restano tool che scrivono senza passare dal cancello.

    Toglierle dall'inventario del 4.1 significherebbe smettere di controllare
    che continuino a chiedere.
    """
    from tests.security.test_readonly_write_surfaces import _ASKS_FOR_ITSELF

    assert "download.py" in _ASKS_FOR_ITSELF
    assert "storage.py" in _ASKS_FOR_ITSELF
