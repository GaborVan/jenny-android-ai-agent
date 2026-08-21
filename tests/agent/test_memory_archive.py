"""Il tier freddo: dove va una voce quando lascia i file caldi.

Il formato è il contratto di questa fase, perché è ciò che un lettore fra sei mesi
— umano o modello — dovrà riuscire a interrogare. I test qui sotto tengono ferme
le tre proprietà su cui si regge: un file per voce, il fatto separato dai
metadati, e un nome che si ordina da solo.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from jenny.agent.memory import MemoryStore
from jenny.agent.memory_archive import (
    ArchivedEntry,
    archive_dir,
    archive_entry,
    archive_filename,
    find_archived,
    render_archived,
)
from jenny.agent.tools.memory_entries import (
    entry_id,
    make_entry_archiver,
    parse_entries,
)

_ENTRY = ArchivedEntry(
    id="a1b2c3d4",
    text="- Preferisce risposte brevi e concrete",
    source="USER.md",
    heading="Preferences",
    retention="durable",
)


class TestTheFileName:
    def test_it_sorts_by_time_on_its_own(self):
        names = [
            archive_filename("ffff0000", date(2026, 8, 18)),
            archive_filename("0000ffff", date(2026, 1, 2)),
            archive_filename("aaaa1111", date(2026, 12, 31)),
        ]

        assert sorted(names) == [
            "2026-01-02-0000ffff.md",
            "2026-08-18-ffff0000.md",
            "2026-12-31-aaaa1111.md",
        ]

    def test_it_carries_the_content_id(self):
        """Lo stesso hash del tool per voci: una voce degradata si ritrova a
        partire dal suo testo, senza aprire niente."""
        text = "- Preferisce risposte brevi e concrete"

        assert entry_id(text) in archive_filename(entry_id(text), date(2026, 8, 18))


class TestTheFileBody:
    def test_the_fact_is_the_body_and_nothing_else(self):
        out = render_archived(_ENTRY)
        body = out.split("---\n\n", 1)[1]

        assert body.strip() == "Preferisce risposte brevi e concrete"

    def test_the_bullet_dash_does_not_come_along(self):
        """Il trattino apparteneva all'elenco di provenienza: qui è una frase."""
        assert "\n- Preferisce" not in render_archived(_ENTRY)

    def test_the_metadata_says_where_it_came_from(self):
        out = render_archived(_ENTRY)

        assert "source: USER.md" in out
        assert "heading: Preferences" in out
        assert "retention: durable" in out

    def test_an_unknown_field_is_omitted_not_left_empty(self):
        """Una riga ``retention:`` vuota direbbe che l'informazione è stata cercata
        e persa. La verità è che non c'era: i tag vengono tolti dal testo prima
        che arrivi nei file."""
        out = render_archived(
            ArchivedEntry(id="x", text="- Un fatto", source="USER.md"),
        )

        assert "retention:" not in out
        assert "heading:" not in out

    def test_it_is_greppable_for_the_fact_itself(self):
        """L'agente cerca qui con ``grep``: la frase deve stare su una riga sua,
        senza etichette davanti."""
        lines = render_archived(_ENTRY).splitlines()

        assert "Preferisce risposte brevi e concrete" in lines

    def test_it_ends_with_a_newline(self):
        assert render_archived(_ENTRY).endswith("\n")


class TestWriting:
    def test_it_creates_the_directory_under_memory(self, tmp_path: Path):
        path = archive_entry(tmp_path, _ENTRY, when=date(2026, 8, 18))

        assert path.parent == archive_dir(tmp_path)
        assert path.parent.name == "archive"
        assert path.name == "2026-08-18-a1b2c3d4.md"

    def test_the_fact_is_on_disk_and_readable(self, tmp_path: Path):
        path = archive_entry(tmp_path, _ENTRY, when=date(2026, 8, 18))

        assert "Preferisce risposte brevi e concrete" in path.read_text(encoding="utf-8")

    def test_one_file_per_entry(self, tmp_path: Path):
        """Non un registro che cresce: ``grep`` salta i file grandi in silenzio, e
        quel falso negativo si legge come "non l'ho mai saputo"."""
        for i in range(3):
            archive_entry(
                tmp_path,
                ArchivedEntry(id=f"id{i}", text=f"- Fatto {i}", source="USER.md"),
                when=date(2026, 8, 18),
            )

        assert len(list(archive_dir(tmp_path).glob("*.md"))) == 3

    def test_the_demoted_date_is_stamped_when_not_given(self, tmp_path: Path):
        path = archive_entry(tmp_path, _ENTRY, when=date(2026, 8, 18))

        assert "demoted: 2026-08-18" in path.read_text(encoding="utf-8")

    def test_an_explicit_demoted_date_is_kept(self, tmp_path: Path):
        entry = ArchivedEntry(
            id="b2", text="- Un fatto", source="USER.md", demoted="2026-01-01",
        )

        path = archive_entry(tmp_path, entry, when=date(2026, 8, 18))

        assert "demoted: 2026-01-01" in path.read_text(encoding="utf-8")


class TestItIsASetOfFactsNotALogOfEvents:
    """La stessa voce tolta due volte è lo stesso fatto.

    ``remove`` chiamerà questa funzione ogni volta che degrada, e un fatto può
    essere riaggiunto e ritolto. Due file con lo stesso testo e date diverse
    sarebbero solo rumore per chi cerca.
    """

    def test_re_archiving_does_not_make_a_second_file(self, tmp_path: Path):
        first = archive_entry(tmp_path, _ENTRY, when=date(2026, 8, 18))
        second = archive_entry(tmp_path, _ENTRY, when=date(2026, 9, 30))

        assert first == second
        assert len(list(archive_dir(tmp_path).glob("*.md"))) == 1

    def test_the_first_demotion_keeps_its_date(self, tmp_path: Path):
        archive_entry(tmp_path, _ENTRY, when=date(2026, 8, 18))
        again = archive_entry(tmp_path, _ENTRY, when=date(2026, 9, 30))

        assert again.name.startswith("2026-08-18")
        assert "demoted: 2026-08-18" in again.read_text(encoding="utf-8")

    def test_find_returns_nothing_before_the_first_demotion(self, tmp_path: Path):
        assert find_archived(tmp_path, "a1b2c3d4") is None

    def test_find_survives_a_missing_directory(self, tmp_path: Path):
        assert find_archived(tmp_path / "nope", "a1b2c3d4") is None

    def test_find_matches_by_id_not_by_date(self, tmp_path: Path):
        archive_entry(tmp_path, _ENTRY, when=date(2026, 8, 18))

        found = find_archived(tmp_path, "a1b2c3d4")

        assert found is not None and found.name == "2026-08-18-a1b2c3d4.md"


class TestItRoundTripsWithTheEntryTool:
    """L'archivio e i file caldi devono parlare la stessa lingua, o rimettere a
    posto una voce diventa un lavoro di traduzione."""

    def test_an_entry_parsed_from_a_file_archives_by_its_own_id(self, tmp_path: Path):
        source = "# User Profile\n\n## Preferences\n\n- Preferisce risposte brevi\n"
        entry = parse_entries(source)[0]

        path = archive_entry(
            tmp_path,
            ArchivedEntry(
                id=entry.id, text=entry.text, source="USER.md", heading=entry.heading,
            ),
            when=date(2026, 8, 18),
        )

        assert path.name == f"2026-08-18-{entry.id}.md"
        assert find_archived(tmp_path, entry.id) == path

    def test_the_archived_body_can_go_back_as_an_entry(self, tmp_path: Path):
        """Il corpo, rimesso come bullet, ritrova lo stesso id: la degradazione è
        reversibile senza perdere l'identità della voce."""
        source = "## Preferences\n\n- Preferisce risposte brevi\n"
        entry = parse_entries(source)[0]
        path = archive_entry(
            tmp_path,
            ArchivedEntry(id=entry.id, text=entry.text, source="USER.md"),
            when=date(2026, 8, 18),
        )

        body = path.read_text(encoding="utf-8").split("---\n\n", 1)[1].strip()

        assert entry_id(f"- {body}") == entry.id


_HOT = """# User Profile

## Preferences

- Preferisce risposte brevi
- Non vuole report formali

## Work

- Lavora su un progetto open source
"""


class TestTheFileBoundaryArchiver:
    """La difesa che copre **anche** il review pass.

    ``memory remove`` degrada, ma il review pass non lo usa: pota con
    ``apply_patch`` e ``edit_file`` sui file interi, perché è ciò che gli serve
    per ristrutturare. Ed è proprio lui lo scrittore i cui errori sono definitivi
    — il 2026-08-18 un secondo passaggio consecutivo ha tolto cinque voci vere da
    ``USER.md``, recuperabili solo da uno snapshot. Quindi la rete sta al confine
    del file: qualunque cosa riscriva quei due file ci passa.
    """

    def _archiver(self, tmp_path: Path):
        (tmp_path / "USER.md").write_text(_HOT, encoding="utf-8")
        return make_entry_archiver(tmp_path), tmp_path / "USER.md"

    def test_an_entry_dropped_by_a_whole_file_rewrite_is_saved(self, tmp_path: Path):
        archiver, hot = self._archiver(tmp_path)
        pruned = _HOT.replace("- Non vuole report formali\n", "")

        archiver(hot, pruned)

        archived = list(archive_dir(tmp_path / "memory").glob("*.md"))
        assert len(archived) == 1
        assert "Non vuole report formali" in archived[0].read_text(encoding="utf-8")

    def test_a_pass_that_drops_several_saves_them_all(self, tmp_path: Path):
        """Il caso misurato: cinque voci in un colpo solo."""
        archiver, hot = self._archiver(tmp_path)

        archiver(hot, "# User Profile\n\n## Preferences\n\n- Preferisce risposte brevi\n")

        assert len(list(archive_dir(tmp_path / "memory").glob("*.md"))) == 2

    def test_surviving_entries_are_not_archived(self, tmp_path: Path):
        archiver, hot = self._archiver(tmp_path)

        archiver(hot, _HOT)

        assert not archive_dir(tmp_path / "memory").exists()

    def test_it_keeps_the_section_the_entry_lived_in(self, tmp_path: Path):
        archiver, hot = self._archiver(tmp_path)

        archiver(hot, _HOT.replace("- Lavora su un progetto open source\n", ""))

        text = next(archive_dir(tmp_path / "memory").glob("*.md")).read_text(encoding="utf-8")
        assert "heading: Work" in text
        assert "source: USER.md" in text

    def test_a_reworded_entry_keeps_its_old_version(self, tmp_path: Path):
        """L'id è l'hash del contenuto, quindi riscrivere una voce la fa sparire
        come voce. Un po' di rumore in archivio in cambio del fatto che nessuna
        formulazione precedente sia irrecuperabile: il verso giusto dello scambio.
        """
        archiver, hot = self._archiver(tmp_path)

        archiver(hot, _HOT.replace("- Preferisce risposte brevi", "- Preferisce risposte brevissime"))

        text = next(archive_dir(tmp_path / "memory").glob("*.md")).read_text(encoding="utf-8")
        assert "Preferisce risposte brevi" in text

    def test_files_that_are_not_memory_are_ignored(self, tmp_path: Path):
        archiver, _ = self._archiver(tmp_path)
        other = tmp_path / "skills" / "x" / "SKILL.md"
        other.parent.mkdir(parents=True)
        other.write_text("## S\n\n- una voce\n", encoding="utf-8")

        archiver(other, "## S\n")

        assert not archive_dir(tmp_path / "memory").exists()

    def test_the_memory_file_is_covered_too(self, tmp_path: Path):
        hot = tmp_path / "memory" / "MEMORY.md"
        hot.parent.mkdir(parents=True)
        hot.write_text("## Project Context\n- Il progetto X è attivo\n", encoding="utf-8")
        archiver = make_entry_archiver(tmp_path)

        archiver(hot, "## Project Context\n")

        text = next(archive_dir(tmp_path / "memory").glob("*.md")).read_text(encoding="utf-8")
        assert "source: memory/MEMORY.md" in text

    def test_a_file_that_does_not_exist_yet_archives_nothing(self, tmp_path: Path):
        archiver = make_entry_archiver(tmp_path)

        archiver(tmp_path / "USER.md", "## S\n\n- prima voce\n")

        assert not archive_dir(tmp_path / "memory").exists()

    def test_it_never_raises_and_so_never_blocks_a_write(self, tmp_path: Path, monkeypatch):
        """Qui la degradazione è una rete, non una condizione: un archivio che non
        si scrive non deve impedire una scrittura legittima. L'ordine forte —
        archivia, e solo allora togli — sta in ``memory remove``, dove la voce da
        salvare si conosce con certezza."""
        archiver, hot = self._archiver(tmp_path)
        monkeypatch.setattr(
            "jenny.agent.tools.memory_entries.archive_entry",
            lambda *a, **k: (_ for _ in ()).throw(OSError("disco pieno")),
        )

        archiver(hot, "# User Profile\n")  # non deve sollevare


class TestTheModelIsToldTheArchiveExists:
    """Un indice che nessuno sa esistere non viene mai aperto.

    È la stessa ragione per cui la rubrica di Atlas sta nel prompt. Un archivio
    invisibile al modello è, dal suo punto di vista, indistinguibile da una
    cancellazione — e allora tanto varrebbe cancellare.
    """

    def test_an_empty_archive_costs_nothing(self, tmp_path: Path):
        """Un'installazione nuova non paga token per una cartella che non c'è."""
        assert MemoryStore(tmp_path).get_archive_context() == ""

    def test_it_appears_once_there_is_something_to_find(self, tmp_path: Path):
        store = MemoryStore(tmp_path)
        archive_entry(store.memory_dir, _ENTRY, when=date(2026, 8, 18))

        out = store.get_archive_context()

        assert out.startswith("## Archive")
        assert "memory/archive/" in out

    def test_it_is_flat_in_the_size_of_the_archive(self, tmp_path: Path):
        """Un abstract per voce spenderebbe il budget caldo che questa fase esiste
        per proteggere. Qui c'è un numero, e non cresce."""
        store = MemoryStore(tmp_path)
        for i in range(2):
            archive_entry(
                store.memory_dir,
                ArchivedEntry(id=f"a{i}", text=f"- Fatto {i}", source="USER.md"),
                when=date(2026, 8, 18),
            )
        two = store.get_archive_context()
        for i in range(2, 40):
            archive_entry(
                store.memory_dir,
                ArchivedEntry(id=f"a{i}", text=f"- Fatto {i}", source="USER.md"),
                when=date(2026, 8, 18),
            )
        forty = store.get_archive_context()

        assert len(two.splitlines()) == len(forty.splitlines()) == 2
        assert abs(len(forty) - len(two)) <= 2  # solo la cifra del conteggio

    def test_it_counts_what_is_in_there(self, tmp_path: Path):
        store = MemoryStore(tmp_path)
        for i in range(3):
            archive_entry(
                store.memory_dir,
                ArchivedEntry(id=f"b{i}", text=f"- Fatto {i}", source="USER.md"),
                when=date(2026, 8, 18),
            )

        assert "(3 so far)" in store.get_archive_context()

    def test_it_says_the_directory_is_not_writable_by_the_model(self, tmp_path: Path):
        """Il percorso finisce anche nel prompt di Dream, la cui allowlist non lo
        comprende — e per lui una scrittura rifiutata non è un tentativo a vuoto
        ma un run intero che non commette niente."""
        store = MemoryStore(tmp_path)
        archive_entry(store.memory_dir, _ENTRY, when=date(2026, 8, 18))

        out = store.get_archive_context()

        assert "you never write to that directory" in out

    def test_it_says_how_to_look(self, tmp_path: Path):
        store = MemoryStore(tmp_path)
        archive_entry(store.memory_dir, _ENTRY, when=date(2026, 8, 18))

        assert "grep" in store.get_archive_context()

