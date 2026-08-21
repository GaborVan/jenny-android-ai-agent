"""Il recupero dal tier freddo: cosa deve valere perché "non è perso" sia vero.

La fase 2 ha mantenuto la sua promessa sul disco. Questi test tengono ferma la
parte che riguarda ciò che Jenny *sa*: che l'archivio si possa interrogare senza
corrispondenze testuali, e che quando l'elenco non ci sta tutto lo dica invece di
accorciarsi in silenzio — che è il modo in cui ``grep`` falliva.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from jenny.agent.memory import MemoryStore
from jenny.agent.memory_archive import (
    ArchivedEntry,
    archive_entry,
    list_archived,
    read_archived,
)
from jenny.agent.tools.memory_recall import MemoryRecallTool


def _store(tmp_path: Path) -> MemoryStore:
    return MemoryStore(tmp_path)


def _archive(store: MemoryStore, text: str, *, when: date, ident: str, **kw) -> None:
    archive_entry(
        store.memory_dir,
        ArchivedEntry(id=ident, text=text, source=kw.pop("source", "USER.md"), **kw),
        when=when,
    )


class TestReadingBackWhatWasWritten:
    def test_an_archived_entry_survives_the_round_trip(self, tmp_path):
        store = _store(tmp_path)
        _archive(
            store, "- Il ficus sta sul balcone", when=date(2026, 3, 1), ident="aaaa1111",
            heading="Preferences", retention="permanent",
        )
        [entry] = list_archived(store.memory_dir)
        assert entry.id == "aaaa1111"
        # Il trattino del bullet appartiene al file di provenienza, non al fatto.
        assert entry.text == "Il ficus sta sul balcone"
        assert entry.source == "USER.md"
        assert entry.heading == "Preferences"
        assert entry.demoted == "2026-03-01"

    def test_a_file_someone_simplified_by_hand_is_still_readable(self, tmp_path):
        """La cartella è visibile nel file browser, quindi verrà aperta a mano.

        Perdere il fatto perché mancano i metadati sarebbe il fallimento esatto
        che l'archivio esiste per impedire.
        """
        store = _store(tmp_path)
        path = store.memory_dir / "archive" / "2026-03-02-bbbb2222.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("il fatto e basta\n", encoding="utf-8")
        entry = read_archived(path)
        assert entry is not None
        assert entry.text == "il fatto e basta"
        assert entry.id == "bbbb2222"

    def test_the_filename_id_wins_over_a_hand_edited_header(self, tmp_path):
        """Chi chiede un id deve riavere la voce che quell'id nomina."""
        store = _store(tmp_path)
        path = store.memory_dir / "archive" / "2026-03-03-cccc3333.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("---\nid: zzzz9999\n---\n\nun fatto\n", encoding="utf-8")
        entry = read_archived(path)
        assert entry is not None and entry.id == "cccc3333"

    def test_newest_first_because_that_is_who_survives_a_cut(self, tmp_path):
        store = _store(tmp_path)
        _archive(store, "vecchio", when=date(2026, 1, 1), ident="old00000")
        _archive(store, "recente", when=date(2026, 8, 1), ident="new00000")
        assert [e.id for e in list_archived(store.memory_dir)] == ["new00000", "old00000"]


class TestTheListIsNeverFiltered:
    async def test_every_entry_is_listed_whatever_language_it_is_in(self, tmp_path):
        """Il difetto vero di ``grep``: questa memoria è bilingue.

        Nessuna corrispondenza per sottostringa da nessuna parte, quindi un
        fatto scritto in italiano è raggiungibile da una domanda in inglese —
        perché a scegliere è il modello, non un confronto di caratteri.
        """
        store = _store(tmp_path)
        _archive(store, "La pianta sul balcone si chiama Brindolo", when=date(2026, 5, 1), ident="it000000")
        _archive(store, "The user prefers short replies", when=date(2026, 5, 2), ident="en000000")
        out = await MemoryRecallTool(tmp_path).execute()
        assert "Brindolo" in out and "short replies" in out

    async def test_an_empty_archive_says_nothing_was_lost(self, tmp_path):
        out = await MemoryRecallTool(tmp_path).execute()
        assert "empty" in out.lower()
        assert "lost" in out.lower()


class TestOpeningEntries:
    async def test_an_id_returns_the_fact_with_where_it_came_from(self, tmp_path):
        store = _store(tmp_path)
        _archive(
            store, "una decisione presa a marzo", when=date(2026, 3, 4), ident="dddd4444",
            source="memory/MEMORY.md", heading="Project Context",
        )
        out = await MemoryRecallTool(tmp_path).execute(ids=["dddd4444"])
        assert "una decisione presa a marzo" in out
        assert "memory/MEMORY.md" in out and "Project Context" in out

    async def test_an_unknown_id_is_reported_not_dropped(self, tmp_path):
        """Restituire "solo le altre" farebbe concludere che quel fatto non c'è."""
        store = _store(tmp_path)
        _archive(store, "presente", when=date(2026, 3, 5), ident="eeee5555")
        out = await MemoryRecallTool(tmp_path).execute(ids=["eeee5555", "ffff6666"])
        assert "presente" in out
        assert "ffff6666" in out


class TestTruncationIsNeverSilent:
    async def test_a_cut_list_says_how_many_it_did_not_show(self, tmp_path, monkeypatch):
        import jenny.agent.tools.memory_recall as mod

        monkeypatch.setattr(mod, "_INDEX_MAX_CHARS", 120)
        store = _store(tmp_path)
        for i in range(12):
            _archive(
                store, f"fatto numero {i} con abbastanza testo da occupare spazio",
                when=date(2026, 4, 1 + i), ident=f"id{i:06d}",
            )
        out = await MemoryRecallTool(tmp_path).execute()
        # Il totale vero è sempre nell'intestazione, anche quando l'elenco è corto.
        assert "12 facts" in out
        assert "not listed" in out

    async def test_the_index_logs_when_it_starts_to_crowd_its_cap(self, tmp_path, monkeypatch):
        """Il momento di costruire la 7.2 va misurato, non indovinato."""
        import jenny.agent.tools.memory_recall as mod

        lines: list[str] = []
        from loguru import logger

        monkeypatch.setattr(mod, "_INDEX_MAX_CHARS", 400)
        sink = logger.add(lines.append, level="INFO")
        try:
            store = _store(tmp_path)
            for i in range(6):
                _archive(
                    store, f"un fatto sufficientemente lungo da contare, numero {i}",
                    when=date(2026, 4, 1 + i), ident=f"cr{i:06d}",
                )
            await MemoryRecallTool(tmp_path).execute()
        finally:
            logger.remove(sink)
        assert any("phase 7.2" in line for line in lines)


class TestWhereItIsMounted:
    def test_the_main_agent_gets_it(self):
        from jenny.agent.tools.loader import _HARDCODED_TOOL_MODULES

        assert "memory_recall" in _HARDCODED_TOOL_MODULES
        assert "orchestrator" in MemoryRecallTool._scopes

    def test_dream_does_not_get_it(self, tmp_path):
        """Il prompt di Dream si è già dimostrato sensibile a ciò che gli si aggiunge.

        Non è un divieto di principio: è che non c'è ancora una misura che dica
        che gli serve, e la superficie in più lì è costata run interi.
        """
        tools = _store(tmp_path).build_dream_tools()
        assert "recall" not in tools.tool_names

    def test_it_is_read_only(self, tmp_path):
        assert MemoryRecallTool(tmp_path).read_only is True


class TestThePromptStopsPointingAtGrep:
    def test_the_archive_line_names_the_tool(self, tmp_path):
        store = _store(tmp_path)
        _archive(store, "qualcosa", when=date(2026, 6, 1), ident="gggg7777")
        line = store.get_archive_context()
        assert "`recall`" in line
        # ``grep`` resta nominato solo per dire perché non si usa.
        assert "not `grep`" in line

    def test_the_line_still_disappears_on_an_empty_archive(self, tmp_path):
        assert _store(tmp_path).get_archive_context() == ""
