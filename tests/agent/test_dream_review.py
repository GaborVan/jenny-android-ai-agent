"""Il review pass di Dream: il run che può solo ridurre.

Tre di questi test non provano una feature ma un'assenza — cursore, helper di
commit, snapshot. Sono le tre cose che il review pass condivide con Dream per
somiglianza e non deve condividere per comportamento: ognuna, se ci scivolasse
dentro, romperebbe qualcosa che nessun altro test guarda.
"""

from __future__ import annotations

import inspect
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from jenny.agent.dream_review import (
    STATUS_COMPLETED,
    STATUS_FAILED,
    STATUS_NO_CHANGE,
    review_session_key,
    run_dream_review,
)
from jenny.agent.memory import MemoryStore
from jenny.agent.memory_budget import FileBudget, budget_report
from jenny.session.keys import is_internal_session_key
from jenny.session.manager import SessionManager
from jenny.utils import prompt_templates
from jenny.utils.helpers import sync_workspace_templates

# Testo iniziale dei tre file misurati. Deve essere abbastanza lungo da poter
# essere accorciato in modo visibile dai test che simulano una potatura.
_MEMORY_TEXT = "# Memory\n" + "".join(f"- fact number {i}\n" for i in range(40))
_USER_TEXT = "# User\n- Name: Ludovico\n- Timezone: Europe/Rome\n"
_SOUL_TEXT = "# Soul\n- Helpful, concise.\n"


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """``MemoryStore`` su un workspace con i template davvero estratti.

    ``render_template`` carica da ``get_workspace_path()``, non dal package: un
    prompt che non arriva nel workspace non si rende affatto, quindi montare un
    workspace vero è l'unico modo di provare il rendering del review pass (ed è
    anche ciò che rende leggibile ``agent/dream.md``, v.
    ``TestTheCriteriaAreReachable``). L'ambiente Jinja è memoizzato per processo
    (``lru_cache``): va invalidato prima **e** dopo, o la prima chiamata della
    suite fissa la root per tutte le altre.
    """
    from jenny.runtime.context import get_runtime_context

    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True)
    sync_workspace_templates(workspace, silent=True)
    monkeypatch.setattr(get_runtime_context(), "workspace_dir", workspace)
    prompt_templates._environment.cache_clear()

    memory_store = MemoryStore(workspace)
    memory_store.memory_file.write_text(_MEMORY_TEXT, encoding="utf-8")
    memory_store.user_file.write_text(_USER_TEXT, encoding="utf-8")
    memory_store.soul_file.write_text(_SOUL_TEXT, encoding="utf-8")

    yield memory_store

    prompt_templates._environment.cache_clear()


class _FakeAgent:
    """Sostituto di ``AgentLoop`` per il solo ``process_direct``.

    *effect* è la coroutine che simula quel che il modello fa durante il turno:
    riceve il registry del run, così un test può potare davvero un file o
    provare a scrivere passando dai tool veri invece che dal filesystem.
    """

    def __init__(
        self,
        *,
        stop_reason: str = "completed",
        effect: Any = None,
        error: Exception | None = None,
    ) -> None:
        self.stop_reason = stop_reason
        self.effect = effect
        self.error = error
        self.calls: list[dict[str, Any]] = []

    async def process_direct(self, content: str, **kwargs: Any) -> Any:
        self.calls.append({"prompt": content, **kwargs})
        if self.error is not None:
            raise self.error
        if self.effect is not None:
            await self.effect(kwargs["tools"])
        return SimpleNamespace(metadata={"_stop_reason": self.stop_reason})

    @property
    def prompt(self) -> str:
        assert self.calls, "process_direct non è mai stato chiamato"
        return self.calls[-1]["prompt"]

    @property
    def session_key(self) -> str:
        return self.calls[-1]["session_key"]


def _report(store: MemoryStore, *, memory: int = 6000, user: int = 3000, soul: int = 0):
    return budget_report(store, memory_chars=memory, user_chars=user, soul_chars=soul)


async def _run(store: MemoryStore, agent: _FakeAgent, **kwargs: Any):
    """Chiama il review pass con i default dei test (report fresco, no snapshot)."""
    report = kwargs.pop("report", None)
    if report is None:
        report = _report(store)
    return await run_dream_review(
        agent,
        store=store,
        report=report,
        snapshotted=kwargs.pop("snapshotted", False),
        **kwargs,
    )


def _shrink(store: MemoryStore, *, to: str = "# Memory\n- one fact\n"):
    """Effetto che pota MEMORY.md, come farebbe il modello."""

    async def effect(_tools: Any) -> None:
        store.memory_file.write_text(to, encoding="utf-8")

    return effect


def _tree(root: Path) -> list[str]:
    return sorted(p.relative_to(root).as_posix() for p in root.rglob("*"))


# ---------------------------------------------------------------------------
# Le tre cose che non deve fare
# ---------------------------------------------------------------------------


class TestTheThreeThingsItMustNotDo:
    async def test_it_never_touches_the_dream_cursor(
        self, store: MemoryStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Il review pass non processa storia: non ha nessun cursore da avanzare.

        Avanzarlo qui dichiarerebbe digerite delle voci di ``history.jsonl`` che
        nessuno ha letto — perse per sempre — e riaprirebbe proprio il livelock
        che il review pass esiste per rompere. Il test spegne entrambi gli
        accessori: anche solo *leggere* il cursore qui dentro è il primo passo
        verso lo scriverlo.
        """

        def _boom(*_args: Any, **_kwargs: Any):
            raise AssertionError("il review pass ha toccato .dream_cursor")

        store.set_last_dream_cursor(42)
        monkeypatch.setattr(store, "get_last_dream_cursor", _boom)
        monkeypatch.setattr(store, "set_last_dream_cursor", _boom)

        outcome = await _run(store, _FakeAgent(effect=_shrink(store)))

        assert outcome.status == STATUS_COMPLETED
        assert store._dream_cursor_file.read_text(encoding="utf-8") == "42"

    async def test_it_does_not_ask_whether_progress_may_be_committed(
        self, store: MemoryStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``dream_should_advance_cursor`` risponde a una domanda che qui non esiste.

        Quella regola dice "questo input può dirsi digerito?" e, non avendo
        scritture riuscite, risponde no. Usarla come esito del review pass
        marchierebbe come fallito ogni run che non aveva niente da potare —
        cioè il caso che il prompt dichiara esplicitamente valido.
        ``internal_run_completed`` resta l'unico helper consentito.
        """

        def _boom(*_args: Any, **_kwargs: Any):
            raise AssertionError("esito deciso con gli helper del cursore")

        monkeypatch.setattr(MemoryStore, "dream_should_advance_cursor", staticmethod(_boom))
        monkeypatch.setattr(MemoryStore, "internal_run_should_commit", staticmethod(_boom))

        outcome = await _run(store, _FakeAgent(effect=_shrink(store)))

        assert outcome.status == STATUS_COMPLETED

    async def test_it_does_not_snapshot_the_workspace(
        self, store: MemoryStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Lo snapshot è del chiamante; qui arriva solo il flag che lo descrive.

        Due difetti in uno se scivolasse dentro: una copia del workspace a ogni
        review pass, e — peggio — un run che si crea da sé la reversibilità che
        il prompt promette, scollegando il ramo "puoi potare, è recuperabile"
        dalla configurazione che quella promessa la rende vera.
        """
        params = inspect.signature(run_dream_review).parameters
        assert set(params) == {
            "agent", "store", "report", "snapshotted", "write_size_guard",
        }
        # ``snapshotted`` è un bool, non un gancio: non c'è modo di passargli
        # una funzione da chiamare per fare il checkpoint.
        assert params["snapshotted"].annotation == "bool"

        # La contabilità dei token è spenta qui perché scrive sotto la dir webui,
        # che in un test risolve dentro il workspace: è una scrittura legittima
        # (v. ``TestTokenAccounting``) e senza questo neutralizzatore
        # sporcherebbe il confronto sull'albero, facendo fallire questo test per
        # un motivo che non è il suo.
        monkeypatch.setattr(
            "jenny.agent.dream_review.record_response_token_usage",
            lambda *_a, **_k: None,
        )

        before = _tree(store.workspace)
        await _run(store, _FakeAgent(), snapshotted=True)

        assert _tree(store.workspace) == before


# ---------------------------------------------------------------------------
# Il prompt
# ---------------------------------------------------------------------------


class TestPromptRendering:
    async def test_snapshotted_true_promises_reversibility(self, store: MemoryStore) -> None:
        agent = _FakeAgent()
        await _run(store, agent, snapshotted=True)

        assert "## Your edits are reversible" in agent.prompt
        assert "## Your edits are not reversible" not in agent.prompt

    async def test_snapshotted_false_says_the_deletion_is_final(
        self, store: MemoryStore
    ) -> None:
        """Il ramo conservativo: senza checkpoint il prompt deve dirlo.

        Dire "puoi recuperare tutto" attaccato alla frase il cui scopo è far
        cancellare di più, quando non è vero, è il modo più diretto di perdere
        memoria che l'utente non ha altrove.
        """
        agent = _FakeAgent()
        await _run(store, agent, snapshotted=False)

        assert "## Your edits are not reversible" in agent.prompt
        assert "when a decision is close, keep" in agent.prompt

    async def test_the_gauge_is_the_review_variant(self, store: MemoryStore) -> None:
        """``for_review=True`` non è opzionale.

        La variante incrementale del gauge dice "consolidate before adding": in
        un run dove per definizione non si aggiunge niente è un'istruzione che
        non descrive nessuna azione disponibile, e in un prompt che deve far
        cancellare è rumore che spinge nella direzione opposta.
        """
        agent = _FakeAgent()
        await _run(store, agent)

        assert "consolidate before adding" not in agent.prompt
        assert "does not need to shrink further" in agent.prompt

    async def test_the_measures_in_the_prompt_are_the_ones_in_the_outcome(
        self, store: MemoryStore
    ) -> None:
        """Gauge e ``before`` vengono dallo stesso report, o raccontano due run diversi."""
        agent = _FakeAgent()
        report = _report(store)
        outcome = await _run(store, agent, report=report)

        assert outcome.before == {item.label: item.chars for item in report}
        chars = outcome.before["MEMORY.md"]
        assert f"MEMORY.md [{chars * 100 // 6000}% — {chars:,}/6,000 chars]" in agent.prompt
        # SOUL.md ha budget 0: misurato, non applicato.
        assert f"SOUL.md [{outcome.before['SOUL.md']:,} chars — no budget]" in agent.prompt

    async def test_before_comes_from_the_report_not_from_a_fresh_measure(
        self, store: MemoryStore
    ) -> None:
        """Il chiamante ha appena misurato: rimisurare qui è lavoro doppio e divergente.

        Con un report volutamente diverso dal disco, ``before`` deve seguire il
        report — è il numero che il modello ha visto nel gauge, e l'unico
        rispetto al quale il delta del run significhi qualcosa.
        """
        stale = [FileBudget(label="MEMORY.md", path=store.memory_file, chars=999, budget=6000)]

        outcome = await _run(store, _FakeAgent(), report=stale)

        assert outcome.before == {"MEMORY.md": 999}


# ---------------------------------------------------------------------------
# Esito
# ---------------------------------------------------------------------------


class TestStatus:
    async def test_a_file_that_shrank_is_a_completed_run(self, store: MemoryStore) -> None:
        outcome = await _run(store, _FakeAgent(effect=_shrink(store)))

        assert outcome.status == STATUS_COMPLETED
        assert outcome.after["MEMORY.md"] < outcome.before["MEMORY.md"]
        assert outcome.freed == outcome.before["MEMORY.md"] - outcome.after["MEMORY.md"]

    async def test_a_run_that_changes_nothing_is_not_a_failure(
        self, store: MemoryStore
    ) -> None:
        """Il prompt lo dice al modello ("a valid outcome, not a failed one").

        Se il codice dicesse il contrario, il chiamante conterebbe come guasto
        proprio il comportamento che il prompt chiede: non fabbricare un edit
        pur di giustificare il turno.
        """
        outcome = await _run(store, _FakeAgent())

        assert outcome.status == STATUS_NO_CHANGE
        assert outcome.before == outcome.after
        assert outcome.freed == 0

    async def test_an_unclean_turn_is_a_failure(self, store: MemoryStore) -> None:
        outcome = await _run(store, _FakeAgent(stop_reason="error"))

        assert outcome.status == STATUS_FAILED

    async def test_an_unclean_turn_stays_a_failure_even_if_a_file_shrank(
        self, store: MemoryStore
    ) -> None:
        """Lo status descrive la salute del run, non un suo effetto collaterale.

        Un turno interrotto a metà che per caso ha accorciato un file resta un
        turno interrotto: di quanto abbia ridotto lo dicono ``before``/``after``,
        che restano popolati.
        """
        outcome = await _run(
            store, _FakeAgent(stop_reason="error", effect=_shrink(store))
        )

        assert outcome.status == STATUS_FAILED
        assert outcome.after["MEMORY.md"] < outcome.before["MEMORY.md"]

    async def test_an_exception_is_reported_not_raised(self, store: MemoryStore) -> None:
        """Un review pass è manutenzione: non deve portarsi via il tick del cron."""
        outcome = await _run(store, _FakeAgent(error=RuntimeError("provider down")))

        assert outcome.status == STATUS_FAILED
        assert set(outcome.before) == {"MEMORY.md", "USER.md", "SOUL.md"}
        assert set(outcome.after) == set(outcome.before)


# ---------------------------------------------------------------------------
# Session key
# ---------------------------------------------------------------------------


class TestSessionKey:
    async def test_the_run_uses_a_dream_prefixed_review_key(self, store: MemoryStore) -> None:
        agent = _FakeAgent()
        await _run(store, agent)

        assert agent.session_key.startswith("dream:review-")
        assert agent.calls[-1]["ephemeral"] is True

    def test_the_key_is_recognised_as_internal(self) -> None:
        """Se non lo fosse, il review pass comparirebbe negli elenchi user-facing."""
        assert is_internal_session_key(review_session_key())

    def test_the_session_file_is_pruned_by_the_existing_dream_pruner(
        self, tmp_path: Path
    ) -> None:
        """Il prefisso ``dream:`` è la ragione per cui non serve un secondo pruner.

        ``prune_dream_sessions`` globba ``dream_*.jsonl``. Se la chiave fosse
        coniata come ``review:...`` il glob non matcherebbe, e ogni review pass
        lascerebbe dietro un file di sessione per sempre — un accumulo silenzioso
        su un telefono, senza nessun errore a segnalarlo.
        """
        sessions_dir = tmp_path / "sessions"
        sessions_dir.mkdir()
        keys = [f"dream:review-2026081{i}-100000" for i in range(11)]
        for key in keys:
            (sessions_dir / f"{SessionManager.safe_key(key)}.jsonl").write_text(
                "{}\n", encoding="utf-8"
            )

        assert len(list(sessions_dir.glob("dream_*.jsonl"))) == len(keys)

        removed = MemoryStore.prune_dream_sessions(sessions_dir, keep=10)

        # Il pruner restituisce la chiave originale, non il nome del file: se il
        # round-trip non tornasse, il chiamante non saprebbe quale cache svuotare.
        assert removed and removed[0] in keys
        assert len(list(sessions_dir.glob("dream_*.jsonl"))) == 10


# ---------------------------------------------------------------------------
# Tool del run
# ---------------------------------------------------------------------------


class TestTools:
    async def test_the_write_size_guard_reaches_the_tools(self, store: MemoryStore) -> None:
        """Il guard passato al review pass deve arrivare fino alla scrittura.

        Se si perdesse per strada il run girerebbe uguale e senza errori, solo
        con il budget disattivato: la sola prova che è arrivato è un rifiuto
        prodotto da un tool vero.
        """
        seen: list[Path] = []
        results: list[str] = []

        def guard(path: Path, _text: str) -> str | None:
            seen.append(path)
            return "Refused: over budget."

        async def effect(tools: Any) -> None:
            results.append(
                await tools.execute(
                    "edit_file",
                    {"path": "memory/MEMORY.md", "old_text": "fact number 0", "new_text": "x"},
                )
            )

        await _run(store, _FakeAgent(effect=effect), write_size_guard=guard)

        assert seen, "il guard non è mai stato invocato"
        assert results and "Refused: over budget." in results[0]
        # Rifiutata davvero: il file su disco è intatto.
        assert store.memory_file.read_text(encoding="utf-8") == _MEMORY_TEXT

    async def test_without_a_guard_the_tools_write_normally(self, store: MemoryStore) -> None:
        """Il default resta "misurato ma non applicato": nessuna scrittura rifiutata."""

        async def effect(tools: Any) -> None:
            await tools.execute(
                "edit_file",
                {"path": "memory/MEMORY.md", "old_text": _MEMORY_TEXT, "new_text": "# Memory\n"},
            )

        outcome = await _run(store, _FakeAgent(effect=effect))

        assert outcome.status == STATUS_COMPLETED
        assert store.memory_file.read_text(encoding="utf-8") == "# Memory\n"


class TestTheCriteriaAreReachable:
    async def test_the_run_can_read_agent_dream_md(self, store: MemoryStore) -> None:
        """Il prompt rimanda a ``agent/dream.md`` invece di ricopiarne i criteri.

        Regge su un fatto sottile: ``build_dream_tools`` monta ``ReadFileTool``
        sull'intero workspace, e ``sync_workspace_templates`` ci estrae i prompt
        di sistema sotto ``agent/``. Se un domani quel tool set venisse ristretto
        ai soli file di memoria, le regole di potatura sparirebbero dal run in
        silenzio: resterebbe un modello a cui si chiede di cancellare senza
        dirgli in base a cosa, e nessun test se ne accorgerebbe.
        """
        read: list[str] = []

        async def effect(tools: Any) -> None:
            read.append(await tools.execute("read_file", {"path": "agent/dream.md"}))

        agent = _FakeAgent(effect=effect)
        await _run(store, agent)

        assert "Read `agent/dream.md` in the workspace" in agent.prompt
        assert read, "read_file non ha restituito nulla"
        assert "Delete-or-keep" in read[0], read[0][:200]


class TestTokenAccounting:
    """Il turno del review pass non deve sparire dalla contabilità.

    La registrazione sta dentro ``run_dream_review`` e non nel dispatcher — come
    in ``run_atlas`` — perché questa funzione è l'unico punto che vede la
    risposta del provider: restituisce un ``ReviewOutcome`` e non rilancia,
    quindi da fuori il ``resp`` non è raggiungibile. Senza, sarebbe un turno LLM
    completo, su un telefono, invisibile in una feature nata per contenere i
    costi.
    """

    async def test_the_review_turn_is_charged(
        self, store: MemoryStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        recorded: list[dict[str, Any]] = []
        monkeypatch.setattr(
            "jenny.agent.dream_review.record_response_token_usage",
            lambda response, *, source, timezone_name=None: recorded.append(
                {"response": response, "source": source, "tz": timezone_name}
            ),
        )

        agent = _FakeAgent()
        await _run(store, agent)

        assert len(recorded) == 1
        # ``dream`` e non ``dream_review``: ``_SOURCE_KEYS`` in
        # ``agent/token_usage.py`` è un elenco chiuso e ``_clean_source``
        # riscrive in silenzio tutto il resto in ``"system"``, che non
        # separerebbe i due run — li seppellirebbe nel secchio generico.
        assert recorded[0]["source"] == "dream"
        assert recorded[0]["response"] is not None

    async def test_a_turn_that_raises_is_still_charged(
        self, store: MemoryStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Il ``finally`` copre anche il turno che esplode: ``None`` è un no-op."""
        recorded: list[Any] = []
        monkeypatch.setattr(
            "jenny.agent.dream_review.record_response_token_usage",
            lambda response, *, source, timezone_name=None: recorded.append(response),
        )

        class _Exploding(_FakeAgent):
            async def process_direct(self, prompt: str, **kwargs: Any) -> Any:
                raise RuntimeError("provider giù")

        outcome = await _run(store, _Exploding())

        assert outcome.status == STATUS_FAILED
        assert recorded == [None]
