"""I tre file di Jenny si riscrivono in modo atomico, anche dal review pass.

Il percorso incrementale (``MemoryEntryTool._commit``) passava da ``atomic_write``,
il review pass no: ristruttura MEMORY.md / SOUL.md / USER.md con ``edit_file`` e
``apply_patch``, e quei due scrivevano con un troncamento seguito da una write.
Su Android il processo muore quando vuole, e quel che resta è un file *visibile*
mezzo scritto — che si legge come integro.

Il discriminante è ``extra_write_allowed_files``: un tool costruito con una
allowlist di file esatti esiste solo per riscrivere stato di Jenny. I file
dell'utente restano scritti in posto, ed è deliberato (``.agent/gotchas.md``).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from jenny.agent.memory import MemoryStore
from jenny.agent.tools.apply_patch import ApplyPatchTool
from jenny.agent.tools.filesystem import EditFileTool, WriteFileTool


@pytest.fixture()
def atomic_spy(monkeypatch):
    """Registra i path passati ad ``atomic_write``, lasciando avvenire la scrittura."""
    import jenny.agent.tools.filesystem as fs_module
    from jenny.utils.path import atomic_write as real_atomic_write

    seen: list[Path] = []

    def spy(path, content, **kwargs):
        seen.append(Path(path))
        return real_atomic_write(path, content, **kwargs)

    # Il punto di patch è il binding del modulo che scrive, non
    # ``jenny.utils.path``: ``filesystem.py`` importa il nome a import-time, e
    # ``apply_patch`` scrive tramite ``_FsTool._commit_write``, cioè lo stesso
    # binding. È anche la convenzione del resto della suite
    # (``jenny.agent.skills.atomic_write``, ``jenny.webui.wiki.atomic_write``).
    monkeypatch.setattr(fs_module, "atomic_write", spy)
    return seen


@pytest.fixture()
def dream(tmp_path):
    """I tool esattamente come li costruisce Dream, non un tool fatto a mano."""
    store = MemoryStore(tmp_path)
    for target in (store.memory_file, store.soul_file, store.user_file):
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("- fatto vecchio\n", encoding="utf-8")
    return store, store.build_dream_tools()


def _own_files(store: MemoryStore) -> list[Path]:
    return [store.memory_file, store.soul_file, store.user_file]


@pytest.mark.asyncio
@pytest.mark.parametrize("which", ["memory_file", "soul_file", "user_file"])
async def test_edit_file_writes_jennys_own_files_atomically(dream, atomic_spy, which):
    store, tools = dream
    target = getattr(store, which)

    result = await tools.get("edit_file").execute(
        path=str(target), old_text="fatto vecchio", new_text="fatto nuovo",
    )

    assert "Successfully edited" in result, result
    assert target.read_text(encoding="utf-8") == "- fatto nuovo\n"
    assert target.resolve() in [p.resolve() for p in atomic_spy]


@pytest.mark.asyncio
@pytest.mark.parametrize("which", ["memory_file", "soul_file", "user_file"])
async def test_apply_patch_writes_jennys_own_files_atomically(dream, atomic_spy, which):
    store, tools = dream
    target = getattr(store, which)

    result = await tools.get("apply_patch").execute(
        edits=[{
            "path": str(target),
            "action": "replace",
            "old_text": "fatto vecchio",
            "new_text": "fatto nuovo",
        }],
    )

    assert "Patch applied" in result, result
    assert target.read_text(encoding="utf-8") == "- fatto nuovo\n"
    assert target.resolve() in [p.resolve() for p in atomic_spy]


@pytest.mark.asyncio
async def test_apply_patch_covers_all_three_in_one_call(dream, atomic_spy):
    """Una patch multi-file non deve lasciarne fuori nessuno."""
    store, tools = dream

    result = await tools.get("apply_patch").execute(
        edits=[
            {
                "path": str(target),
                "action": "replace",
                "old_text": "fatto vecchio",
                "new_text": "fatto nuovo",
            }
            for target in _own_files(store)
        ],
    )

    assert "Patch applied" in result, result
    written = {p.resolve() for p in atomic_spy}
    assert {p.resolve() for p in _own_files(store)} <= written


@pytest.mark.asyncio
async def test_wiki_file_is_atomic_too(tmp_path, atomic_spy):
    """La allowlist di Atlas è memory/WIKI.md, ed è stato di Jenny quanto gli altri."""
    from jenny.agent.atlas import AtlasStore

    store = AtlasStore(tmp_path)
    store.wiki_file.parent.mkdir(parents=True, exist_ok=True)
    store.wiki_file.write_text("- voce vecchia\n", encoding="utf-8")

    tools = store.build_tools()
    result = await tools.get("edit_file").execute(
        path=str(store.wiki_file), old_text="voce vecchia", new_text="voce nuova",
    )

    assert "Successfully edited" in result, result
    assert store.wiki_file.resolve() in [p.resolve() for p in atomic_spy]


class TestUserFilesStayInPlace:
    """L'eccezione di ``gotchas.md``: i file dell'utente non diventano atomici."""

    @pytest.mark.asyncio
    async def test_write_file_on_a_user_file_is_not_atomic(self, tmp_path, atomic_spy):
        tool = WriteFileTool(workspace=tmp_path, allowed_dir=tmp_path)
        target = tmp_path / "note.txt"

        result = await tool.execute(path=str(target), content="ciao")

        assert "Successfully wrote" in result, result
        assert target.read_text(encoding="utf-8") == "ciao"
        assert atomic_spy == []

    @pytest.mark.asyncio
    async def test_edit_file_on_a_user_file_keeps_the_inode(self, tmp_path, atomic_spy):
        tool = EditFileTool(workspace=tmp_path, allowed_dir=tmp_path)
        target = tmp_path / "note.txt"
        target.write_text("prima\n", encoding="utf-8")
        inode = target.stat().st_ino

        result = await tool.execute(path=str(target), old_text="prima", new_text="dopo")

        assert "Successfully edited" in result, result
        assert target.read_text(encoding="utf-8") == "dopo\n"
        assert target.stat().st_ino == inode
        assert atomic_spy == []

    @pytest.mark.asyncio
    async def test_apply_patch_on_a_user_file_keeps_the_inode(self, tmp_path, atomic_spy):
        tool = ApplyPatchTool(workspace=tmp_path, allowed_dir=tmp_path)
        target = tmp_path / "note.txt"
        target.write_text("prima\n", encoding="utf-8")
        inode = target.stat().st_ino

        result = await tool.execute(edits=[{
            "path": str(target),
            "action": "replace",
            "old_text": "prima",
            "new_text": "dopo",
        }])

        assert "Patch applied" in result, result
        assert target.read_text(encoding="utf-8") == "dopo\n"
        assert target.stat().st_ino == inode
        assert atomic_spy == []

    @pytest.mark.asyncio
    async def test_a_user_file_next_to_the_allowlist_stays_in_place(
        self, dream, atomic_spy, tmp_path
    ):
        """Solo i file *esatti* della allowlist, non i loro vicini.

        Dream può scrivere anche sotto ``skills/``: quelle sono skill dell'utente
        e non devono cambiare inode solo perché il tool ha una allowlist.
        """
        _store, tools = dream
        skill = tmp_path / "skills" / "SKILL.md"
        skill.parent.mkdir(parents=True, exist_ok=True)
        skill.write_text("prima\n", encoding="utf-8")
        inode = skill.stat().st_ino

        result = await tools.get("edit_file").execute(
            path=str(skill), old_text="prima", new_text="dopo",
        )

        assert "Successfully edited" in result, result
        assert skill.stat().st_ino == inode
        assert atomic_spy == []
