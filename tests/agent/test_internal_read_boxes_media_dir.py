"""``<workspace>/.jenny/media`` e le cassette di lettura di Dream e Atlas (T9.10).

T9.2 ha spento la media dir nella cassetta del **giardiniere**, e il passo dopo
chiedeva di guardare le due che hanno «la forma identica». Non l'hanno: la radice
di lettura del giardiniere è **un progetto** (``wikis/<nome>``), quella di Dream e
di Atlas è il **workspace intero** — e la media dir sta dentro il workspace.
Quindi lì il flag ``read_media_dir`` non ha niente da allargare: è inerte, e
metterlo a ``False`` sarebbe un placebo — una riga che *sembra* un confine e non
ne mette nessuno.

Questi test fissano le due metà di quella decisione, entrambe misurate passando
per i tool veri:

1. le due cassette **raggiungono** quella cartella, e ci arrivano perché è dentro
   la loro radice di lettura;
2. spegnere il flag su una cassetta con quella radice **non cambia la risposta**.

La seconda è quella che serve al prossimo che legge T9.10 e vuole «chiuderla per
simmetria»: cade se qualcuno trasformasse il flag in un divieto (una sottrazione
dalla radice invece di una mancata aggiunta), che è il modo in cui quel fix
sembrerebbe funzionare senza esserlo.

**Il test gira nel workspace configurato e non sotto ``tmp_path``**, ed è la
trappola che T9.2 ha misurato: ``get_media_dir()`` legge il workspace *vero*,
quindi con una radice sotto ``tmp_path`` il percorso relativo cade fuori per una
ragione accidentale — due radici diverse — e il test passa qualunque cosa faccia
il codice.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from jenny.agent.atlas import AtlasStore
from jenny.agent.memory import MemoryStore
from jenny.config.paths import get_media_dir, get_workspace_path
from jenny.security.workspace_access import (
    bind_workspace_scope,
    default_workspace_scope,
    reset_workspace_scope,
)

pytestmark = pytest.mark.usefixtures("_configure_jenny_workspace")

_SECRET = "appunto personale di un'altra conversazione"


@pytest.fixture
def media_note() -> Iterator[Path]:
    """Un file dentro ``<workspace>/.jenny/media``, con lo scope di default legato.

    Lo scope legato è la forma di produzione: una passata interna gira con lo
    scope di default (l'installazione), non con quello di un progetto.
    """
    workspace = get_workspace_path()
    note = get_media_dir() / "segreto-t910.md"
    note.write_text(_SECRET + "\n", encoding="utf-8")
    token = bind_workspace_scope(default_workspace_scope(workspace, True))
    try:
        yield note
    finally:
        reset_workspace_scope(token)
        # Il workspace configurato è di **sessione**: quel che si lascia qui lo
        # trovano i test dopo.
        note.unlink(missing_ok=True)


def test_the_media_dir_is_inside_the_workspace(media_note: Path) -> None:
    """La ragione strutturale di tutto il resto, detta una volta.

    Se un giorno la media dir uscisse dal workspace, i test qui sotto
    cambierebbero significato e la decisione di T9.10 andrebbe rifatta.
    """
    assert get_media_dir().resolve().is_relative_to(get_workspace_path().resolve())


async def test_dreams_read_box_reaches_the_media_dir(media_note: Path) -> None:
    """La cassetta vera di Dream, non una ricostruita a mano."""
    tools = MemoryStore(get_workspace_path()).build_dream_tools()

    out = await tools.get("read_file").execute(path=".jenny/media/segreto-t910.md")

    assert _SECRET in out, out


@pytest.mark.parametrize("tool,call", [
    ("read_file", {"path": ".jenny/media/segreto-t910.md"}),
    ("list_dir", {"path": ".jenny/media"}),
    ("grep", {"pattern": "personale", "path": ".jenny/media"}),
    ("find_files", {"pattern": "*.md", "path": ".jenny/media"}),
])
async def test_atlas_read_box_reaches_the_media_dir(
    media_note: Path, tool: str, call: dict
) -> None:
    """Atlas legge tutta l'installazione **di proposito** (lo dice la sua docstring),
    quindi la media dir è dentro la superficie dichiarata del run e non un
    allargamento: qui si fissa che ci sta, con tutti e quattro i tool."""
    tools = AtlasStore(get_workspace_path()).build_tools()

    out = await tools.get(tool).execute(**call)

    assert "outside allowed directory" not in out, out
    assert "segreto-t910.md" in out or _SECRET in out, out


def _with_the_flag_off(tool):
    """Il flag spento **sulla cassetta vera**, e non su una ricostruita a mano.

    È l'unico modo di chiedere «questo flag, *qui*, cambierebbe qualcosa?» al
    tool che il codice costruisce davvero. Una copia fatta a mano con le stesse
    due righe risponderebbe per la copia: misurato, con la radice di Dream
    stretta a ``memory/`` la copia continuava a passare mentre la cassetta vera
    era diventata un'altra cosa.
    """
    tool._read_media_dir = False
    return tool


async def test_the_flag_is_inert_in_dreams_box(media_note: Path) -> None:
    """**Il placebo, misurato.** Spegnere il flag nella cassetta di Dream non
    chiude niente: la media dir è dentro la sua radice di lettura, e il flag
    decide solo se aggiungerla come radice *extra*."""
    tools = MemoryStore(get_workspace_path()).build_dream_tools()

    out = await _with_the_flag_off(tools.get("read_file")).execute(
        path=".jenny/media/segreto-t910.md"
    )

    assert _SECRET in out, out


@pytest.mark.parametrize("tool,call", [
    ("read_file", {"path": ".jenny/media/segreto-t910.md"}),
    ("list_dir", {"path": ".jenny/media"}),
    ("grep", {"pattern": "personale", "path": ".jenny/media"}),
    ("find_files", {"pattern": "*.md", "path": ".jenny/media"}),
])
async def test_the_flag_is_inert_in_atlas_box(
    media_note: Path, tool: str, call: dict
) -> None:
    """Lo stesso per tutti e quattro i tool di lettura di Atlas."""
    tools = AtlasStore(get_workspace_path()).build_tools()

    out = await _with_the_flag_off(tools.get(tool)).execute(**call)

    assert "outside allowed directory" not in out, out
    assert "segreto-t910.md" in out or _SECRET in out, out
