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


class TestRecoveringInTheSameTurnStillCostsARun:
    """Il caso che il tetto di 2.000 su un file da 3.019 rende ordinario.

    Il messaggio di rifiuto dice al modello di far spazio *nello stesso turno* e
    riscrivere. Se ci riesce, il fatto finisce su disco — ma il run ha comunque
    un rifiuto all'attivo, quindi non commette. È voluto e non è un livelock:
    il batch torna al run seguente, dove il fatto risulta già scritto, nessuna
    scrittura viene tentata e ``writes_attempted == 0`` fa avanzare il cursore.
    Costa un run in più, non un blocco.

    La regola non può essere più fine di così: i contatori sono per run e non
    per file, e "il rifiuto e la scrittura riuscita riguardano lo stesso fatto"
    non è una cosa che da qui si possa sapere.
    """

    async def test_a_refusal_recovered_in_the_same_turn_does_not_commit(
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
        assert (states.writes_ok, states.writes_refused_budget) == (1, 1)
        assert MemoryStore.dream_should_advance_cursor(_completed_resp(), states) is False

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

    def test_a_refusal_blocks_even_a_clean_counter_pair(self) -> None:
        """Il rifiuto ha l'ultima parola sui due contatori aggregati."""
        fs = FileStates()
        fs.record_write_attempt()
        fs.writes_ok = 1
        fs.writes_refused_budget = 1
        assert MemoryStore.internal_run_should_commit(_completed_resp(), fs) is False
