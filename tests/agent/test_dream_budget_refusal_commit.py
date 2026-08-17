"""Un rifiuto di budget deve fermare il commit del run, anche se il run ha scritto altro.

La regola di commit dei run interni (``MemoryStore.internal_run_should_commit``,
condivisa da Dream e Atlas) guardava un solo contatore per run: ``writes_ok > 0``.
Ma i contatori sono *per run*, non per file. Un run di Dream che scrive con
successo una skill e si vede rifiutare da budget la scrittura su ``MEMORY.md``
aveva quindi ``writes_ok == 1``: il cursore avanzava, e il fatto che non è mai
finito in MEMORY.md non sarebbe più tornato in nessun batch. Perso.

Qui si fissano i quattro casi che quella regola deve distinguere, e il primo è la
regressione: la scrittura riuscita non deve poter coprire il rifiuto.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from jenny.agent.memory import MemoryStore
from jenny.agent.memory_budget import budget_report, make_write_size_guard
from jenny.agent.tools.file_state import FileStates


@pytest.fixture
def store(tmp_path: Path) -> MemoryStore:
    s = MemoryStore(tmp_path)
    s.soul_file.write_text("# Soul\n- Helpful", encoding="utf-8")
    s.memory_file.write_text("# Memory\n- Project X active", encoding="utf-8")
    return s


def _completed_resp() -> SimpleNamespace:
    return SimpleNamespace(metadata={"_stop_reason": "completed"})


def _refuse_only(name: str):
    """Guard che rifiuta le scritture su un solo file, per nome.

    Riproduce il budget vero: i file di memoria hanno un tetto, le skill no.
    """

    def guard(path: Path, text: str) -> str | None:
        if path.name == name:
            return f"{name} is full; consolidate before adding."
        return None

    return guard


class TestBudgetRefusalBlocksCommit:
    """I quattro esiti possibili di un run, visti dalla regola di commit."""

    async def test_a_successful_skill_write_does_not_cover_a_refused_memory_write(
        self, store: MemoryStore
    ) -> None:
        """La regressione: una scrittura riuscita accanto a un rifiuto NON commette.

        Il run fa esattamente ciò che Dream fa in pratica — deposita una skill e
        aggiorna MEMORY.md — e il budget rifiuta il secondo pezzo. Il contatore
        aggregato dice "una scrittura è andata"; il fatto rifiutato però non è
        su disco, quindi il cursore deve restare fermo e il batch va riproposto.
        """
        tools = store.build_dream_tools(write_size_guard=_refuse_only("MEMORY.md"))

        skill_path = store.workspace / "skills" / "nota" / "SKILL.md"
        ok = await tools.execute(
            "write_file", {"path": str(skill_path), "content": "# Nota\n"}
        )
        refused = await tools.execute(
            "edit_file",
            {
                "path": str(store.memory_file),
                "old_text": "Project X active",
                "new_text": "Project X active\n- fatto nuovo",
            },
        )

        assert "Successfully wrote" in ok
        assert "consolidate before adding" in refused
        states = tools.file_states
        assert states is not None
        assert (states.writes_ok, states.writes_refused_budget) == (1, 1)
        # La granularità del recupero è il file: la skill riuscita non chiude il
        # rifiuto su ``MEMORY.md``, che resta aperto.
        assert states.unrecovered_refusals == 1
        # Il fatto rifiutato non è su disco...
        assert "fatto nuovo" not in store.memory_file.read_text(encoding="utf-8")
        # ...quindi il run non può dichiarare digerito il proprio input.
        assert MemoryStore.internal_run_should_commit(_completed_resp(), states) is False
        assert MemoryStore.dream_should_advance_cursor(_completed_resp(), states) is False

    async def test_a_refusal_alone_does_not_commit(self, store: MemoryStore) -> None:
        """Il caso già coperto dai contatori di sempre: nessuna scrittura riuscita."""
        tools = store.build_dream_tools(write_size_guard=_refuse_only("MEMORY.md"))

        refused = await tools.execute(
            "edit_file",
            {
                "path": str(store.memory_file),
                "old_text": "Project X active",
                "new_text": "Project X active\n- fatto nuovo",
            },
        )

        assert "consolidate before adding" in refused
        states = tools.file_states
        assert states is not None
        assert (states.writes_attempted, states.writes_ok) == (1, 0)
        assert states.writes_refused_budget == 1
        assert MemoryStore.internal_run_should_commit(_completed_resp(), states) is False

    async def test_all_writes_landing_commits(self, store: MemoryStore) -> None:
        """Nessun rifiuto: il run ha scritto, il progresso si registra."""
        tools = store.build_dream_tools(write_size_guard=_refuse_only("MEMORY.md"))

        skill_path = store.workspace / "skills" / "nota" / "SKILL.md"
        edited = await tools.execute(
            "edit_file",
            {"path": str(store.soul_file), "old_text": "Helpful", "new_text": "Precise"},
        )
        written = await tools.execute(
            "write_file", {"path": str(skill_path), "content": "# Nota\n"}
        )

        assert "Successfully edited" in edited
        assert "Successfully wrote" in written
        states = tools.file_states
        assert states is not None
        assert (states.writes_ok, states.writes_refused_budget) == (2, 0)
        assert MemoryStore.internal_run_should_commit(_completed_resp(), states) is True

    async def test_no_write_attempted_commits(self, store: MemoryStore) -> None:
        """"Non c'era niente da cambiare": nessun tentativo, nessun rifiuto, si commette."""
        tools = store.build_dream_tools(write_size_guard=_refuse_only("MEMORY.md"))

        states = tools.file_states
        assert states is not None
        assert (states.writes_attempted, states.writes_refused_budget) == (0, 0)
        assert MemoryStore.internal_run_should_commit(_completed_resp(), states) is True


class TestRecoveringInTheSameTurnCommits:
    """Il caso che il tetto di 2.000 su un file da 3.019 rende ordinario.

    Il messaggio di rifiuto dice al modello di far spazio *nello stesso turno* e
    riscrivere il file accorciato. Un modello che obbedisce ha fatto esattamente
    il lavoro: il fatto è su disco e il file è rientrato sotto il tetto.

    Questa classe asseriva l'opposto — che quel run non commettesse comunque — e
    la sua motivazione era che *"la regola non può essere più fine: i contatori
    sono per run e non per file"*. Falso: la granularità che serve è il **file**,
    non il fatto. Una scrittura riuscita su ``MEMORY.md`` chiude un rifiuto su
    ``MEMORY.md`` e su nient'altro, ed è tutto ciò che occorre per distinguere il
    recupero dalla perdita.

    Il costo di sbagliarlo non era "un run in più", come diceva il testo. Con i
    tetti armati era lo stato normale: cursore fermo, stesso batch due ore dopo,
    ``stuck`` in salita fino al review forzato, e a 4 una notifica sul telefono
    che annunciava scritture rifiutate — mentre erano riuscite. In più
    ``/dream budget`` rispondeva *"nessun file è oltre budget, quindi le
    scritture sono fermate da qualcos'altro"*, perché il file era rientrato: un
    vicolo cieco diagnostico prodotto proprio dalla regola.
    """

    async def test_a_refusal_recovered_in_the_same_turn_commits(
        self, store: MemoryStore
    ) -> None:
        # 3.016 caratteri contro un tetto di 2.000: le due misure del device.
        store.memory_file.write_text("# Memory\n- head\n" + "z" * 3000, encoding="utf-8")
        report = budget_report(store, memory_chars=2000, user_chars=2000, soul_chars=0)
        tools = store.build_dream_tools(write_size_guard=make_write_size_guard(report))

        refused = await tools.execute(
            "edit_file",
            {
                "path": str(store.memory_file),
                "old_text": "- head",
                "new_text": "- head\n- fatto nuovo",
            },
        )
        # Il rifiuto dice di far spazio nello stesso turno: il modello pota il
        # blocco vecchio e ci scrive dentro il fatto.
        recovered = await tools.execute(
            "edit_file",
            {
                "path": str(store.memory_file),
                "old_text": "z" * 3000,
                "new_text": "- fatto nuovo",
            },
        )

        assert "Write refused" in refused
        assert "Successfully edited" in recovered
        # La potatura è atterrata: il fatto è su disco e il file è rientrato.
        text = store.memory_file.read_text(encoding="utf-8")
        assert "fatto nuovo" in text
        assert len(text) < 2000
        states = tools.file_states
        assert states is not None
        # Il rifiuto è avvenuto — il contatore cumulativo lo ricorda — ma non è
        # più aperto: la riscrittura sullo stesso file l'ha chiuso.
        assert (states.writes_ok, states.writes_refused_budget) == (1, 1)
        assert states.unrecovered_refusals == 0
        assert MemoryStore.dream_should_advance_cursor(_completed_resp(), states) is True

    async def test_the_run_after_it_commits_because_nothing_is_attempted(
        self, store: MemoryStore
    ) -> None:
        """Il giro seguente, dove il fatto c'è già e non si scrive niente."""
        store.memory_file.write_text("z" * 1900, encoding="utf-8")
        report = budget_report(store, memory_chars=2000, user_chars=2000, soul_chars=0)
        tools = store.build_dream_tools(write_size_guard=make_write_size_guard(report))

        states = tools.file_states
        assert states is not None
        assert MemoryStore.dream_should_advance_cursor(_completed_resp(), states) is True


class TestRefusalCounterTolerance:
    """La regola resta tollerante a registry che non sono quelli di Dream/Atlas."""

    def test_a_registry_without_the_refusal_counter_still_decides(self) -> None:
        """Senza ``writes_refused_budget`` si legge come zero, non come rifiuto.

        Un oggetto che non ha il contatore non ha nemmeno il gancio che lo
        incrementa (è ``_FsTool._check_write_size`` a scriverlo, su un
        ``FileStates`` vero): trattarlo come "rifiuto ignoto" bloccherebbe per
        sempre chiamanti che non possono rifiutare niente.
        """
        legacy = SimpleNamespace(writes_ok=1, writes_attempted=1)
        assert MemoryStore.internal_run_should_commit(_completed_resp(), legacy) is True

    def test_missing_write_counters_stay_conservative(self) -> None:
        assert MemoryStore.internal_run_should_commit(_completed_resp(), SimpleNamespace()) is False

    def test_an_open_refusal_blocks_even_a_clean_counter_pair(self, tmp_path: Path) -> None:
        """Un rifiuto aperto ha l'ultima parola sui due contatori aggregati."""
        fs = FileStates()
        fs.record_write_attempt()
        fs.writes_ok = 1
        fs.record_write_refused(tmp_path / "MEMORY.md")
        assert MemoryStore.internal_run_should_commit(_completed_resp(), fs) is False

    def test_the_cumulative_counter_alone_does_not_block(self, tmp_path: Path) -> None:
        """È il rifiuto rimasto aperto a decidere, non quante volte il tetto ha morso.

        Distinzione deliberata: ``writes_refused_budget`` resta la misura di
        quanto il budget ha lavorato — la vogliono i log e i test — mentre il
        commit guarda solo ciò che è rimasto irrisolto.
        """
        target = tmp_path / "MEMORY.md"
        target.write_text("- vecchio\n", encoding="utf-8")
        fs = FileStates()
        fs.record_write_attempt()
        fs.record_write_refused(target, "- vecchio\n- fatto nuovo\n")
        # La riscrittura pota il vecchio ma si porta dietro il fatto rifiutato.
        target.write_text("- fatto nuovo\n", encoding="utf-8")
        fs.record_write(target)

        assert fs.writes_refused_budget == 1
        assert fs.unrecovered_refusals == 0
        assert MemoryStore.internal_run_should_commit(_completed_resp(), fs) is True

    def test_a_rewrite_that_drops_the_refused_content_stays_open(self, tmp_path: Path) -> None:
        """Il caso che il solo percorso non distingueva, e che decide tutto.

        Il guard accetta una scrittura che rientra nel tetto **oppure** che
        rimpicciolisce il file, e i due rami finiscono nello stesso posto. Quindi
        "pota e si porta dietro il fatto" e "pota e lo butta" erano lo stesso
        evento — e il secondo è il più probabile, perché il messaggio di rifiuto
        spinge a riscrivere proprio quel file. Qui il fatto non è atterrato, e il
        run non deve poter dichiarare digerito il proprio input.
        """
        target = tmp_path / "MEMORY.md"
        target.write_text("- vecchio\n", encoding="utf-8")
        fs = FileStates()
        fs.record_write_attempt()
        fs.record_write_refused(target, "- vecchio\n- fatto nuovo\n")
        target.write_text("- altro ancora\n", encoding="utf-8")
        fs.record_write(target)

        assert fs.writes_ok == 1
        assert fs.unrecovered_refusals == 1
        assert MemoryStore.internal_run_should_commit(_completed_resp(), fs) is False

    def test_a_refusal_with_nothing_identifiable_to_add_closes_on_any_write(
        self, tmp_path: Path
    ) -> None:
        """Se la scrittura rifiutata non aggiungeva righe, non c'è un fatto da perdere.

        Caso di bordo reale: un riordino a pari dimensione su un file già oltre
        il tetto viene rifiutato pur non aggiungendo nulla. Senza niente da
        cercare si torna al comportamento conservativo — la prima scrittura
        riuscita chiude — invece di restare aperti per sempre.
        """
        target = tmp_path / "MEMORY.md"
        target.write_text("- a\n- b\n", encoding="utf-8")
        fs = FileStates()
        fs.record_write_attempt()
        fs.record_write_refused(target, "- b\n- a\n")
        target.write_text("- a\n", encoding="utf-8")
        fs.record_write(target)

        assert fs.unrecovered_refusals == 0

    def test_a_registry_with_only_the_old_counter_keeps_the_old_behaviour(self) -> None:
        """Un registry estraneo che espone solo il contatore ripiega su di esso.

        Nessun chiamante in produzione è in questo stato — è il fallback che
        impedisce alla regola di cambiare risposta sotto un oggetto che non ha la
        proprietà nuova.
        """
        foreign = SimpleNamespace(writes_ok=1, writes_attempted=2, writes_refused_budget=1)
        assert MemoryStore.internal_run_should_commit(_completed_resp(), foreign) is False
