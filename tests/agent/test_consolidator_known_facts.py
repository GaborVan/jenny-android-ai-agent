"""Fase 4 — il Consolidator smette di riestrarre ciò che è già registrato (D5).

Il difetto non era che il modello sbagliasse: era che estraeva alla cieca.
Questi test tengono ferme le tre cose che rendono il rimedio non peggiore del
male — che la coda in attesa conti quanto i file, che una correzione passi
comunque, e che il blocco non rubi il budget alla conversazione.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from jenny.agent.memory import Consolidator, MemoryStore, iter_fact_lines


@pytest.fixture
def store(tmp_path):
    return MemoryStore(tmp_path)


@pytest.fixture
def mock_provider():
    provider = MagicMock()
    provider.chat_with_retry = AsyncMock(
        return_value=MagicMock(content="- [durable] something", finish_reason="stop")
    )
    return provider


@pytest.fixture
def consolidator(store, mock_provider):
    sessions = MagicMock()
    sessions.save = MagicMock()
    return Consolidator(
        store=store,
        provider=mock_provider,
        model="test-model",
        sessions=sessions,
        context_window_tokens=100_000,
        build_messages=MagicMock(return_value=[]),
        get_tool_definitions=MagicMock(return_value=[]),
        max_completion_tokens=100,
    )


def _system_prompt(mock_provider) -> str:
    messages = mock_provider.chat_with_retry.await_args.kwargs["messages"]
    return messages[0]["content"]


class TestWhatTheBlockShows:
    def test_it_is_empty_when_nothing_is_recorded(self, store):
        assert store.get_known_facts_context() == ""

    def test_it_carries_the_entries_of_both_hot_files(self, store, tmp_path):
        (tmp_path / "USER.md").write_text(
            "# User Profile\n\n## Preferences\n- Prefers Italian for docs\n",
            encoding="utf-8",
        )
        store.memory_file.parent.mkdir(parents=True, exist_ok=True)
        store.memory_file.write_text("- The build target is Android only\n", encoding="utf-8")

        block = store.get_known_facts_context()

        assert "- Prefers Italian for docs" in block
        assert "- The build target is Android only" in block

    def test_it_carries_facts_extracted_but_not_yet_filed(self, store):
        """La sorgente che conta davvero.

        Una conversazione lunga viene consolidata più volte prima che Dream
        giri una sola: al secondo passaggio i file caldi sono ancora vuoti, e
        con quelli soli il duplicato passerebbe esattamente come prima.
        """
        store.append_history("- [durable] The gateway runs under Chaquopy")

        block = store.get_known_facts_context()

        assert "- The gateway runs under Chaquopy" in block

    def test_it_drops_what_dream_has_already_filed(self, store):
        store.append_history("- [durable] Old fact already consolidated")
        store.set_last_dream_cursor(store.get_last_dream_cursor() + 1)
        cursor = store.append_history("- [durable] Fact still waiting")
        store.set_last_dream_cursor(cursor - 1)

        block = store.get_known_facts_context()

        assert "Fact still waiting" in block
        assert "Old fact already consolidated" not in block

    def test_a_skipped_fact_is_not_shown_as_recorded(self, store):
        """``[skip]`` è un fatto giudicato indegno, non un fatto in memoria."""
        store.append_history(
            "- [durable] Kept fact\n- [skip] The user said hello",
        )

        block = store.get_known_facts_context()

        assert "- Kept fact" in block
        assert "The user said hello" not in block

    def test_a_raw_dump_never_reaches_the_prompt(self, store):
        """Un raw-dump è una conversazione intera, non fatti.

        Reiniettarla nel prompt della consolidation successiva rimetterebbe in
        circolo esattamente ciò che la consolidation esiste per togliere.
        """
        store.raw_archive([{"role": "user", "content": "a very long transcript"}])

        assert store.get_known_facts_context() == ""

    def test_the_rules_survive_a_truncation_that_eats_the_facts(self, store, tmp_path):
        (tmp_path / "USER.md").write_text(
            "\n".join(f"- Fact number {i} with some padding text" for i in range(200)),
            encoding="utf-8",
        )

        block = store.get_known_facts_context(max_tokens=250)

        assert "Do not extract them again" in block
        assert "[correction]" in block
        assert "further recorded facts are not listed here" in block

    def test_a_fact_is_never_cut_in_half(self, store, tmp_path):
        """Mezza voce si legge come un fatto *diverso*.

        E il confronto che il blocco esiste per permettere diventa un confronto
        con qualcosa che nessuno ha mai scritto.
        """
        facts = [f"- Fact number {i} with some padding text to spend the budget"
                 for i in range(200)]
        (tmp_path / "USER.md").write_text("\n".join(facts), encoding="utf-8")

        block = store.get_known_facts_context(max_tokens=250)

        shown = [line for line in block.splitlines() if line.startswith("- ")]
        assert shown
        assert all(line in facts for line in shown)

    def test_what_did_not_fit_is_counted_out_loud(self, store, tmp_path):
        (tmp_path / "USER.md").write_text(
            "\n".join(f"- Fact number {i} with some padding text" for i in range(30)),
            encoding="utf-8",
        )

        block = store.get_known_facts_context(max_tokens=250)
        shown = len([line for line in block.splitlines() if line.startswith("- ")])

        assert f"({30 - shown} further recorded facts are not listed here.)" in block

    def test_the_waiting_queue_is_served_before_the_files(self, store, tmp_path):
        """Il difetto misurato sul Titan 2 il 2026-08-19.

        Il blocco stava a 5.239 caratteri contro un tetto di 4.800 e il
        troncamento tagliava proprio le voci in attesa, che stavano in fondo:
        la sorgente dominante nella posizione che si perde per prima.
        """
        (tmp_path / "USER.md").write_text(
            "\n".join(f"- Old filed fact number {i} with padding" for i in range(200)),
            encoding="utf-8",
        )
        store.append_history("- [durable] The fact that is still waiting to be filed")

        block = store.get_known_facts_context(max_tokens=250)

        assert "- The fact that is still waiting to be filed" in block

    def test_a_stalled_dream_cannot_starve_the_files(self, store, tmp_path):
        """La quota è un pavimento per la coda, non un soffitto per il resto.

        Un Dream fermo da giorni è esattamente il guasto per cui esiste questo
        piano: se la coda prendesse tutto il blocco, farebbe riestrarre l'intero
        USER.md proprio mentre nessuno lo sta più aggiornando.
        """
        (tmp_path / "USER.md").write_text("- A fact filed long ago\n", encoding="utf-8")
        for i in range(20):
            store.append_history(
                f"- [durable] Backlog fact number {i} with a good deal of padding text"
            )

        block = store.get_known_facts_context(max_tokens=250)

        assert "- A fact filed long ago" in block

    def test_the_queue_gives_back_what_it_does_not_spend(self, store, tmp_path):
        (tmp_path / "USER.md").write_text(
            "\n".join(f"- Filed fact number {i}" for i in range(40)), encoding="utf-8",
        )
        lean = store.get_known_facts_context(max_tokens=250)
        store.append_history("- [durable] One single waiting fact")
        fat = store.get_known_facts_context(max_tokens=250)

        def _filed(block: str) -> int:
            return len([x for x in block.splitlines() if x.startswith("- Filed fact")])

        # Una sola voce in coda non deve costare ai file il 40% del blocco.
        assert _filed(fat) >= _filed(lean) - 1

    def test_the_only_bullets_are_facts(self, store, tmp_path):
        """Le istruzioni sono numerate proprio per questo.

        Sotto, ogni riga che comincia con un trattino è un fatto registrato: due
        istruzioni travestite da voci sarebbero due fatti che la memoria non
        contiene.
        """
        (tmp_path / "USER.md").write_text("- The one real fact\n", encoding="utf-8")

        bullets = [
            line for line in store.get_known_facts_context().splitlines()
            if line.startswith("- ")
        ]

        assert bullets == ["- The one real fact"]


class TestWhatItCostsTheConversation:
    async def test_the_block_is_subtracted_from_the_conversation_budget(self, consolidator):
        """Il system non è gratis: se non lo si sottrae, la richiesta sfora."""
        big = "x" * (consolidator._input_token_budget * 4)
        plain = consolidator._truncate_to_token_budget(big)
        reserved = consolidator._truncate_to_token_budget(big, reserved_tokens=500)

        assert len(reserved) < len(plain)

    async def test_a_reservation_larger_than_the_window_still_returns_text(
        self, consolidator,
    ):
        """Il ramo degenere non deve produrre un prompt vuoto."""
        out = consolidator._truncate_to_token_budget("hello", reserved_tokens=10**9)

        assert out


class TestWhatTheModelActuallyReceives:
    async def test_the_block_rides_in_the_system_message(
        self, consolidator, mock_provider, tmp_path,
    ):
        (tmp_path / "USER.md").write_text("- Lives in Italy\n", encoding="utf-8")

        await consolidator.archive([{"role": "user", "content": "hi"}])

        system = _system_prompt(mock_provider)
        assert "SNIP" in system
        assert "## Already recorded" in system
        assert "- Lives in Italy" in system

    async def test_an_empty_memory_leaves_the_prompt_untouched(
        self, consolidator, mock_provider,
    ):
        """Un'installazione nuova non paga token per dire che non sa niente."""
        await consolidator.archive([{"role": "user", "content": "hi"}])

        assert "Already recorded" not in _system_prompt(mock_provider)

    async def test_the_correction_escape_hatch_is_always_stated(
        self, consolidator, mock_provider, tmp_path,
    ):
        """La cosa che non deve rompersi.

        Senza questa riga, più Jenny sa e meno può aggiornare: un fatto che
        cambia verrebbe letto come "già registrato" e scartato, e la memoria si
        congelerebbe man mano che cresce.
        """
        (tmp_path / "USER.md").write_text("- Works at Acme\n", encoding="utf-8")

        await consolidator.archive([{"role": "user", "content": "hi"}])

        system = _system_prompt(mock_provider)
        assert "changes or contradicts" in system
        assert "[correction]" in system


class TestTheMeasurement:
    def test_it_counts_facts_and_ignores_skips(self):
        summary = "- [durable] one\n- [skip] two\n- [correction] three"

        assert [mark for mark, _ in iter_fact_lines(summary)] == [
            "durable", "skip", "correction",
        ]

    async def test_a_verbatim_repeat_is_reported(
        self, consolidator, mock_provider, store, tmp_path, caplog,
    ):
        """Sopra zero vuol dire che il blocco c'è e il modello lo ignora.

        È un limite inferiore per costruzione — un fatto riestratto con altre
        parole non lo tocca — e va bene così: serve come segnale, non come
        percentuale.
        """
        from loguru import logger

        (tmp_path / "USER.md").write_text("- Lives in Italy\n", encoding="utf-8")
        mock_provider.chat_with_retry.return_value = MagicMock(
            content="- [permanent] Lives in Italy", finish_reason="stop",
        )
        lines: list[str] = []
        sink = logger.add(lines.append, level="INFO")
        try:
            await consolidator.archive([{"role": "user", "content": "hi"}])
        finally:
            logger.remove(sink)

        assert any("1 verbatim repeats" in line for line in lines)
