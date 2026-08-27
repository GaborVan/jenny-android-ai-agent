"""Lo stato che Jenny tiene per sé si scrive in modo atomico, dovunque lo scriva.

Tre punti di produzione dichiarano ``atomic_write`` come portante, e il commento
sul posto dice perché: su Android il processo muore quando vuole, e nessuno di
questi file ha un formato in cui un troncamento si veda. Un ``USER.md`` tagliato a
metà si legge benissimo — semplicemente non contiene più la seconda metà della
persona; un ``gardener.json`` troncato si rilegge come JSON invalido, cioè cursore
perso; e il file d'archivio è l'**unica copia rimasta** del fatto nell'istante in
cui ``remove`` lo toglie dal file caldo.

    jenny/agent/memory_archive.py        archive_entry
    jenny/agent/gardener_state.py        write_state
    jenny/agent/tools/memory_entries.py  MemoryEntryTool._commit

Tutti e tre sopravvivevano alla sostituzione con ``Path.write_text``: la proprietà
era documentata a commento e non verificata da niente. Qui lo è, e in due modi che
si tengono per mano — la spia che *lascia passare* dice «la scrittura è arrivata a
``atomic_write``», la spia che *blocca* dice «se non passa da lì, non si scrive
niente». La seconda è quella che uccide la mutazione anche se un domani il sito
scrivesse due volte.

Il punto di patch è il binding **del modulo che scrive**, non
``jenny.utils.path``: questi tre importano il nome a import-time, quindi patchare
la sorgente non ha alcun effetto e l'assert fallirebbe su codice corretto. È anche
la convenzione del resto della suite.

Complementare a ``tests/agent/tools/test_own_state_atomic_write.py`` (T1.5), che
copre il *review pass* — SOUL.md / USER.md / MEMORY.md / WIKI.md riscritti con
``edit_file`` e ``apply_patch``. Nessuna sovrapposizione: quello guarda i tool
file dal binding di ``filesystem.py``, questo guarda i tre siti che scrivono per
conto proprio. Restano due moduli perché stanno su due livelli diversi del
package, e quello lì è già arrivato.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from jenny.agent.gardener_state import (
    GardenerState,
    gardener_state_file,
    read_state,
    write_state,
)
from jenny.agent.memory_archive import ArchivedEntry, archive_entry
from jenny.agent.tools.memory_entries import MemoryEntryTool


class _Spy:
    """Registra ``(path, content)`` di ogni ``atomic_write``.

    Con ``passthrough=False`` non scrive: il file resta come era, e ogni
    contenuto che comparisse comunque è arrivato da un'altra strada.
    """

    def __init__(self, *, passthrough: bool) -> None:
        self.calls: list[tuple[Path, str | bytes]] = []
        self._passthrough = passthrough

    def __call__(self, path, content, **kwargs) -> None:
        self.calls.append((Path(path), content))
        if self._passthrough:
            from jenny.utils.path import atomic_write as real

            real(path, content, **kwargs)

    def paths(self) -> list[Path]:
        return [p.resolve() for p, _ in self.calls]

    def content_for(self, path: Path) -> str | bytes:
        target = Path(path).resolve()
        for seen, content in self.calls:
            if seen.resolve() == target:
                return content
        raise AssertionError(f"atomic_write non ha mai ricevuto {path}: {self.calls}")


def _spy_on(monkeypatch, module_path: str, *, passthrough: bool) -> _Spy:
    """Patcha ``atomic_write`` **nel modulo che scrive**, mai alla sorgente."""
    import importlib

    module = importlib.import_module(module_path)
    spy = _Spy(passthrough=passthrough)
    monkeypatch.setattr(module, "atomic_write", spy)
    return spy


# --------------------------------------------------------------------------- #
# jenny/agent/memory_archive.py :: archive_entry
# --------------------------------------------------------------------------- #

_ENTRY = ArchivedEntry(
    id="a1b2c3d4",
    text="- Preferisce risposte brevi",
    source="USER.md",
    heading="Preferences",
)


class TestArchiveEntry:
    """Il tier freddo: qui il file *è* la sola copia del fatto."""

    def test_the_archive_file_goes_through_atomic_write(self, tmp_path, monkeypatch):
        spy = _spy_on(monkeypatch, "jenny.agent.memory_archive", passthrough=True)

        path = archive_entry(tmp_path / "memory", _ENTRY, when=date(2026, 8, 18))

        assert path.resolve() in spy.paths()
        # Il contenuto consegnato ad ``atomic_write`` è il file finito, non un
        # pezzo: una scrittura in due tempi sarebbe atomica due volte e integra
        # mai.
        assert spy.content_for(path) == path.read_text(encoding="utf-8")
        assert "Preferisce risposte brevi" in path.read_text(encoding="utf-8")

    def test_nothing_lands_if_atomic_write_does_not_happen(self, tmp_path, monkeypatch):
        spy = _spy_on(monkeypatch, "jenny.agent.memory_archive", passthrough=False)

        path = archive_entry(tmp_path / "memory", _ENTRY, when=date(2026, 8, 18))

        assert len(spy.calls) == 1
        assert not path.exists(), f"{path} scritto fuori da atomic_write"


# --------------------------------------------------------------------------- #
# jenny/agent/gardener_state.py :: write_state
# --------------------------------------------------------------------------- #


@pytest.fixture()
def project(tmp_path) -> Path:
    """Una radice di progetto con un giorno di diario, così il cursore non si pota."""
    journal = tmp_path / "wiki" / "raw" / "journal"
    journal.mkdir(parents=True, exist_ok=True)
    (journal / "20260822.md").write_text("- una voce\n", encoding="utf-8")
    return tmp_path


_STATE = GardenerState(
    cursor={"wiki/raw/journal/20260822.md": 1},
    last_run_at="2026-08-22T10:00:00",
    witness={"wiki/raw/journal/20260822.md": "0123456789abcdef"},
)


class TestWriteState:
    """Il cursore del giardiniere: troncato vale perso, cioè rilettura da capo."""

    def test_the_cursor_goes_through_atomic_write(self, project, monkeypatch):
        spy = _spy_on(monkeypatch, "jenny.agent.gardener_state", passthrough=True)

        write_state(project, _STATE)

        path = gardener_state_file(project)
        assert path.resolve() in spy.paths()
        assert spy.content_for(path) == path.read_text(encoding="utf-8")
        assert read_state(project).cursor == _STATE.cursor

    def test_nothing_lands_if_atomic_write_does_not_happen(self, project, monkeypatch):
        spy = _spy_on(monkeypatch, "jenny.agent.gardener_state", passthrough=False)

        write_state(project, _STATE)

        assert len(spy.calls) == 1
        path = gardener_state_file(project)
        assert not path.exists(), f"{path} scritto fuori da atomic_write"

    def test_a_previous_cursor_is_not_touched_outside_atomic_write(
        self, project, monkeypatch
    ):
        """Il caso vero: sovrascrivere. Un troncamento in posto è cursore perso."""
        path = gardener_state_file(project)
        path.parent.mkdir(parents=True, exist_ok=True)
        before = '{"version": 2, "cursor": {}, "last_run_at": null, "witness": {}}'
        path.write_text(before, encoding="utf-8")

        _spy_on(monkeypatch, "jenny.agent.gardener_state", passthrough=False)
        write_state(project, _STATE)

        assert path.read_text(encoding="utf-8") == before


# --------------------------------------------------------------------------- #
# jenny/agent/tools/memory_entries.py :: MemoryEntryTool._commit
# --------------------------------------------------------------------------- #

_USER_MD = """# User Profile

## Preferences

- Risposte brevi, nessun report formale
"""


@pytest.fixture()
def entry_tool(tmp_path):
    """Il tool come lo monta il runtime, con ``USER.md`` nella forma che ha davvero."""
    (tmp_path / "USER.md").write_text(_USER_MD, encoding="utf-8")
    return MemoryEntryTool(tmp_path), tmp_path / "USER.md"


class TestMemoryEntryCommit:
    """Le due destinazioni a voci: mezzo ``USER.md`` si legge come un ``USER.md``."""

    @pytest.mark.asyncio
    async def test_add_goes_through_atomic_write(self, entry_tool, monkeypatch):
        tool, target = entry_tool
        spy = _spy_on(monkeypatch, "jenny.agent.tools.memory_entries", passthrough=True)

        result = await tool.execute(
            action="add", file="user", text="- Lavora sul Titan 2",
            heading="Preferences",
        )

        assert "Added" in result or "entries" in result, result
        assert target.resolve() in spy.paths()
        assert spy.content_for(target) == target.read_text(encoding="utf-8")
        assert "Titan 2" in target.read_text(encoding="utf-8")

    @pytest.mark.asyncio
    async def test_nothing_lands_if_atomic_write_does_not_happen(
        self, entry_tool, monkeypatch
    ):
        tool, target = entry_tool
        spy = _spy_on(monkeypatch, "jenny.agent.tools.memory_entries", passthrough=False)

        await tool.execute(
            action="add", file="user", text="- Lavora sul Titan 2",
            heading="Preferences",
        )

        assert len(spy.calls) == 1
        assert target.read_text(encoding="utf-8") == _USER_MD

    @pytest.mark.asyncio
    async def test_remove_rewrites_the_hot_file_atomically(self, entry_tool, monkeypatch):
        """Il momento in cui l'archivio è l'unica copia: ``remove`` sul file caldo."""
        tool, target = entry_tool
        spy = _spy_on(monkeypatch, "jenny.agent.tools.memory_entries", passthrough=True)

        result = await tool.execute(
            action="remove", file="user", target="Risposte brevi",
        )

        assert "Removed" in result, result
        assert target.resolve() in spy.paths()
        assert spy.content_for(target) == target.read_text(encoding="utf-8")
        assert "Risposte brevi" not in target.read_text(encoding="utf-8")
