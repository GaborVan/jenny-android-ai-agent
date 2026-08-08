"""Il path che Atlas riceve nel prompt deve essere uno su cui può scrivere.

Il difetto, trovato in audit e riprodotto qui: `build_prompt` passava al modello
la forma *logica* del path assoluto (`str(self.wiki_file)`), mentre la allowlist
di scrittura tiene la forma *risolta* (`build_tools` fa `.resolve()`). Su Android
le due divergono, perché `/data/user/0` è un symlink a `/data/data`.

Il modello riceveva così l'unico path su cui è autorizzato a scrivere in una
forma che la guardia rifiuta. Nessun errore visibile: ogni run finiva con
`writes_attempted > 0, writes_ok = 0`, il fingerprint non avanzava, e Atlas
ribruciava una chiamata al provider ogni dodici ore per sempre.

I 49 test esistenti non lo vedevano perché scrivono tutti con path relativi.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from jenny.agent.atlas import AtlasStore


@pytest.fixture
def symlinked_workspace(tmp_path: Path):
    """Workspace raggiunto attraverso un symlink, come `/data/user/0` su Android."""
    from jenny.config import paths as paths_mod
    from jenny.runtime.context import get_runtime_context
    from jenny.utils.helpers import sync_workspace_templates

    real = tmp_path / "real" / "files"
    real.mkdir(parents=True)
    (tmp_path / "user0").symlink_to("real")
    workspace = tmp_path / "user0" / "files" / "workspace"
    (workspace / "memory").mkdir(parents=True)

    previous = get_runtime_context().workspace_dir
    paths_mod.set_workspace_dir(str(workspace))
    sync_workspace_templates(workspace, silent=True)
    try:
        yield workspace
    finally:
        paths_mod.set_workspace_dir(str(previous) if previous else "")


def _path_from_prompt(prompt: str) -> str:
    match = re.search(r"maintain one file — `([^`]+)`", prompt)
    assert match, "il prompt non nomina piu il file da scrivere"
    return match.group(1)


async def test_atlas_can_write_to_the_path_its_prompt_gives_it(symlinked_workspace):
    """L'unica proprietà che conta, e quella che mancava."""
    store = AtlasStore(symlinked_workspace)
    target = _path_from_prompt(store.build_prompt())

    result = await store.build_tools().get("write_file").execute(
        path=target, content="# Wiki\n",
    )

    assert "Error" not in result, result
    assert (symlinked_workspace / "memory" / "WIKI.md").read_text() == "# Wiki\n"


async def test_the_prompt_path_stays_relative(symlinked_workspace):
    """Relativo come fa Dream: immune a ogni divergenza di canonicalizzazione.

    Un assoluto qui tornerebbe a funzionare in test e a fallire sul telefono,
    che è esattamente come questo difetto è arrivato in produzione.
    """
    target = _path_from_prompt(AtlasStore(symlinked_workspace).build_prompt())

    assert target == "memory/WIKI.md"
    assert not Path(target).is_absolute()


async def test_the_other_memory_files_stay_out_of_reach(symlinked_workspace):
    """La correzione allarga il path, non i permessi."""
    tools = AtlasStore(symlinked_workspace).build_tools()

    for forbidden in ("SOUL.md", "USER.md", "memory/MEMORY.md"):
        result = await tools.get("write_file").execute(path=forbidden, content="x")
        assert "Error" in result, f"{forbidden} risulta scrivibile"
