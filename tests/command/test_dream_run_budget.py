"""Il budget di memoria dentro il `/dream` manuale.

Il budget e il review pass erano cablati sul solo percorso cron. `/dream`
lanciato a mano costruiva i tool di Dream **senza** il guard, il prompt senza
gauge, e non leggeva né scriveva lo stato del review: un'intera feature di
enforcement con una porta di servizio aperta, e per giunta annunciata — `/dream
budget` stampa all'utente tetti e percentuali che l'altra metà dello stesso
comando ignorava.

Il test che conta è il primo: una scrittura che sfora, lanciata da `/dream`,
deve essere rifiutata. Il secondo che conta è l'ultimo,
``TestTheShippedDefaultsEnforce``: da quando i tetti di ``MEMORY.md`` e
``USER.md`` valgono 2.000 il rifiuto non aspetta più che qualcuno configuri
qualcosa, e ``SOUL.md`` è l'unico rimasto a "misurato ma non applicato".
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from jenny.agent.memory import MemoryStore
from jenny.bus.events import InboundMessage
from jenny.command.builtin import register_builtin_commands
from jenny.command.router import CommandContext, CommandRouter
from jenny.config.loader import load_config, save_config
from jenny.config.schema import Config
from jenny.utils.helpers import sync_workspace_templates

_REVIEW_TARGET = "jenny.agent.dream_review.run_dream_review"

# Il seme di MEMORY.md è ciò che il "modello" sostituisce nei test di scrittura:
# ``edit_file`` rifiuta ``old_text=""`` su un file non vuoto, quindi serve un
# ancoraggio reale invece di un append.
_SEED = "- seed fact"
_MEMORY_TEXT = f"# Memory\n{_SEED}\n"


@pytest.fixture()
def router() -> CommandRouter:
    r = CommandRouter()
    register_builtin_commands(r)
    return r


@pytest.fixture()
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Workspace vero con i template estratti e un `config.json` di default.

    Come in ``test_dream_budget_command.py``: ``render_template`` legge da
    ``get_workspace_path()``, non dal package, e l'ambiente Jinja è memoizzato
    per processo.
    """
    from jenny.runtime.context import get_runtime_context
    from jenny.utils import prompt_templates

    ws = tmp_path / "workspace"
    ws.mkdir(parents=True)
    sync_workspace_templates(ws, silent=True)

    ctx = get_runtime_context()
    monkeypatch.setattr(ctx, "workspace_dir", ws)
    monkeypatch.setattr(ctx, "config_path", ws / "config.json")
    prompt_templates._environment.cache_clear()
    save_config(Config(), ws / "config.json")
    yield ws
    prompt_templates._environment.cache_clear()


@pytest.fixture()
def memory(workspace: Path) -> MemoryStore:
    store = MemoryStore(workspace)
    store.memory_file.write_text(_MEMORY_TEXT, encoding="utf-8")
    store.user_file.write_text("# User\n", encoding="utf-8")
    store.soul_file.write_text("# Soul\n", encoding="utf-8")
    # Senza storia da processare ``build_dream_prompt`` ritorna None e il run
    # incrementale non parte affatto.
    store.append_history("user: ricordati che il gateway gira su Android")
    return store


class _FakeLoop:
    """Il minimo di ``AgentLoop`` che il ramo senza argomento di `/dream` tocca.

    ``on_turn`` è il posto del modello: riceve il registry di tool costruito per
    il run, così un test può far *davvero* tentare una scrittura e osservare
    cosa il guard risponde. È l'unico modo di verificare l'enforcement da questo
    percorso invece di verificarne il cablaggio contro se stesso.
    """

    def __init__(self, workspace: Path, memory: MemoryStore) -> None:
        self.bus = SimpleNamespace(publish_outbound=self._publish)
        self.context = SimpleNamespace(memory=memory, timezone=None)
        self.sessions = SimpleNamespace(sessions_dir=workspace / "sessions")
        self.published: list[Any] = []
        self.prompts: list[str] = []
        self.tool_results: list[str] = []
        self.on_turn = None
        self.stop_reason = "completed"

    async def _publish(self, message: Any) -> None:
        self.published.append(message)

    async def process_direct(self, prompt: str, *, tools: Any = None, **_kwargs: Any):
        self.prompts.append(prompt)
        if self.on_turn is not None and tools is not None:
            await self.on_turn(tools)
        return SimpleNamespace(
            content="done", metadata={"_stop_reason": self.stop_reason}
        )

    def evict_pruned_sessions(self, _keys: Any) -> None:
        pass


@pytest.fixture()
def loop(workspace: Path, memory: MemoryStore) -> _FakeLoop:
    return _FakeLoop(workspace, memory)


class _ReviewSpy:
    """Sostituto di ``run_dream_review`` che registra cosa gli è arrivato."""

    def __init__(self, *, shrink_to: str | None = None, status: str = "completed") -> None:
        self.calls: list[dict[str, Any]] = []
        self._shrink_to = shrink_to
        self._status = status

    async def __call__(self, agent, *, store, report, snapshotted, write_size_guard=None):
        self.calls.append({
            "agent": agent,
            "report": report,
            "snapshotted": snapshotted,
            "guard": write_size_guard,
        })
        before = {item.label: item.chars for item in report}
        if self._shrink_to is not None:
            store.memory_file.write_text(self._shrink_to, encoding="utf-8")
        after = {item.label: len(item.path.read_text(encoding="utf-8")) for item in report}
        return SimpleNamespace(
            status=self._status,
            before=before,
            after=after,
            freed=sum(before[label] - after[label] for label in before),
        )

    @property
    def ran(self) -> bool:
        return bool(self.calls)


def _install_review(monkeypatch: pytest.MonkeyPatch, spy: _ReviewSpy) -> None:
    monkeypatch.setattr(_REVIEW_TARGET, spy)


def _set_dream_config(workspace: Path, **fields: int) -> None:
    path = workspace / "config.json"
    config = load_config(path)
    for name, value in fields.items():
        setattr(config.agents.defaults.dream, name, value)
    save_config(config, path)


def _ctx(loop: _FakeLoop, raw: str) -> CommandContext:
    args = raw[len("/dream"):].strip()
    msg = InboundMessage(
        channel="websocket", sender_id="u", chat_id="default", content=raw
    )
    return CommandContext(msg=msg, session=None, key="k", raw=raw, args=args, loop=loop)


async def _drain(loop: _FakeLoop, *, timeout: float = 5.0) -> None:
    """Attende la risposta del task fire-and-forget lanciato da `/dream`.

    La pubblicazione è l'ultima cosa che il run fa (il ramo "niente da
    processare" pubblica prima del ``finally``, che però non tocca più lo stato
    osservato dai test), quindi aspettarla equivale ad aspettare il run.
    """
    deadline = time.monotonic() + timeout
    while not loop.published and time.monotonic() < deadline:
        await asyncio.sleep(0.005)
    assert loop.published, "`/dream` non ha pubblicato nessuna risposta"


def _replace_seed(memory: MemoryStore, new_text: str):
    """Un "modello" che sostituisce il seme di MEMORY.md con *new_text*."""

    async def turn(tools) -> None:
        tool = tools.get("edit_file")
        assert tool is not None
        result = await tool.execute(
            path=str(memory.memory_file), old_text=_SEED, new_text=new_text
        )
        turn.results.append(result)  # type: ignore[attr-defined]

    turn.results = []  # type: ignore[attr-defined]
    return turn


# ---------------------------------------------------------------------------
# 1. L'enforcement
# ---------------------------------------------------------------------------


class TestTheBackDoor:
    """Il test che conta: `/dream` a mano non deve poter aggirare il budget."""

    @pytest.mark.asyncio
    async def test_a_write_over_budget_is_refused(self, router, loop, memory, workspace):
        _set_dream_config(workspace, memory_budget_chars=50)
        turn = _replace_seed(memory, "x" * 300)
        loop.on_turn = turn

        await router.dispatch(_ctx(loop, "/dream"))
        await _drain(loop)

        assert turn.results and "Write refused" in turn.results[0]
        assert "over its 50 char budget" in turn.results[0]
        # E il rifiuto non è solo una frase: il file su disco è intatto.
        assert memory.memory_file.read_text(encoding="utf-8") == _MEMORY_TEXT

    @pytest.mark.asyncio
    async def test_a_write_within_budget_still_lands(
        self, router, loop, memory, workspace
    ):
        """Il guard rifiuta chi sfora, non chi scrive."""
        _set_dream_config(workspace, memory_budget_chars=500)
        turn = _replace_seed(memory, "- gateway runs on Android")
        loop.on_turn = turn

        await router.dispatch(_ctx(loop, "/dream"))
        await _drain(loop)

        assert turn.results and "Write refused" not in turn.results[0]
        assert "gateway runs on Android" in memory.memory_file.read_text(encoding="utf-8")

    @pytest.mark.asyncio
    async def test_a_refused_write_leaves_the_cursor_where_it_was(
        self, router, loop, memory, workspace
    ):
        """Il fatto non è stato consolidato: avanzare lo perderebbe."""
        _set_dream_config(workspace, memory_budget_chars=50)
        loop.on_turn = _replace_seed(memory, "x" * 300)

        await router.dispatch(_ctx(loop, "/dream"))
        await _drain(loop)

        assert memory.get_last_dream_cursor() == 0
        assert "wrote nothing" in loop.published[0].content


# ---------------------------------------------------------------------------
# 2. Il gauge
# ---------------------------------------------------------------------------


class TestGauge:
    @pytest.mark.asyncio
    async def test_the_prompt_carries_the_gauge(self, router, loop, memory, workspace):
        """Senza la misura il modello non sa quanto spazio ha."""
        _set_dream_config(workspace, memory_budget_chars=500)

        await router.dispatch(_ctx(loop, "/dream"))
        await _drain(loop)

        prompt = loop.prompts[0]
        assert "Long-term memory budget (characters)" in prompt
        assert f"MEMORY.md [{len(_MEMORY_TEXT) * 100 // 500}% — {len(_MEMORY_TEXT)}/500" in prompt


# ---------------------------------------------------------------------------
# 3-4. I contatori
# ---------------------------------------------------------------------------


class TestReviewCounters:
    @pytest.mark.asyncio
    async def test_runs_since_review_advances(self, router, loop, memory):
        """Senza questo, su un'installazione a `/dream` manuale il review non parte mai."""
        await router.dispatch(_ctx(loop, "/dream"))
        await _drain(loop)

        assert memory.get_review_state() == (1, 0)

    @pytest.mark.asyncio
    async def test_stuck_rises_when_the_cursor_does_not_advance(
        self, router, loop, memory, workspace
    ):
        _set_dream_config(workspace, memory_budget_chars=50)
        loop.on_turn = _replace_seed(memory, "x" * 300)

        await router.dispatch(_ctx(loop, "/dream"))
        await _drain(loop)

        assert memory.get_review_state() == (1, 1)

    @pytest.mark.asyncio
    async def test_stuck_resets_on_a_run_that_advances(self, router, loop, memory):
        memory.set_review_state(runs_since_review=3, stuck_runs=1)

        await router.dispatch(_ctx(loop, "/dream"))
        await _drain(loop)

        assert memory.get_review_state() == (4, 0)


# ---------------------------------------------------------------------------
# 5-6. Il review pass
# ---------------------------------------------------------------------------


class TestReviewPass:
    @pytest.mark.asyncio
    async def test_the_periodic_cadence_fires_from_the_command(
        self, router, loop, memory, monkeypatch
    ):
        spy = _ReviewSpy()
        _install_review(monkeypatch, spy)
        memory.set_review_state(runs_since_review=12, stuck_runs=0)

        await router.dispatch(_ctx(loop, "/dream"))
        await _drain(loop)

        assert spy.ran
        # Il review azzera, poi il turno incrementale riconta da lì.
        assert memory.get_review_state() == (1, 0)

    @pytest.mark.asyncio
    async def test_two_stuck_runs_force_a_review(self, router, loop, memory, monkeypatch):
        spy = _ReviewSpy()
        _install_review(monkeypatch, spy)
        memory.set_review_state(runs_since_review=0, stuck_runs=2)

        await router.dispatch(_ctx(loop, "/dream"))
        await _drain(loop)

        assert spy.ran

    @pytest.mark.asyncio
    async def test_the_review_is_told_there_is_no_snapshot(
        self, router, loop, memory, monkeypatch
    ):
        """La verità, non una comodità.

        Il checkpoint pre-Dream lo prende il container e lo passa al dispatcher
        cron; da un comando non c'è nessun gancio. Il prompt del review ha due
        rami e ``True`` sceglierebbe quello che promette modifiche reversibili,
        attaccato proprio alla frase il cui scopo è far cancellare di più.
        """
        spy = _ReviewSpy()
        _install_review(monkeypatch, spy)
        memory.set_review_state(runs_since_review=12, stuck_runs=0)

        await router.dispatch(_ctx(loop, "/dream"))
        await _drain(loop)

        assert spy.calls[0]["snapshotted"] is False

    @pytest.mark.asyncio
    async def test_the_reply_says_the_review_ran_and_how_much_it_freed(
        self, router, loop, memory, monkeypatch
    ):
        """Un turno LLM in più dentro un comando manuale non parte in silenzio."""
        spy = _ReviewSpy(shrink_to="# Memory\n")
        _install_review(monkeypatch, spy)
        memory.set_review_state(runs_since_review=12, stuck_runs=0)

        await router.dispatch(_ctx(loop, "/dream"))
        await _drain(loop)

        freed = len(_MEMORY_TEXT) - len("# Memory\n")
        content = loop.published[0].content
        assert "A memory review pass ran first" in content
        assert f"freed {freed:,} chars" in content
        # E la risposta del run incrementale resta.
        assert "Dream completed in" in content

    @pytest.mark.asyncio
    async def test_a_review_with_nothing_to_shrink_still_says_so(
        self, router, loop, memory, monkeypatch
    ):
        spy = _ReviewSpy()
        _install_review(monkeypatch, spy)
        memory.set_review_state(runs_since_review=12, stuck_runs=0)

        await router.dispatch(_ctx(loop, "/dream"))
        await _drain(loop)

        assert "nothing was freed" in loop.published[0].content

    @pytest.mark.asyncio
    async def test_the_review_is_reported_even_with_no_history_to_process(
        self, router, loop, memory, monkeypatch, workspace
    ):
        """Il review gira prima di sapere se c'è storia: se gira, va detto comunque."""
        spy = _ReviewSpy(shrink_to="# Memory\n")
        _install_review(monkeypatch, spy)
        # Cursore oltre l'unica voce: ``build_dream_prompt`` ritorna None.
        memory.set_last_dream_cursor(9999)
        memory.set_review_state(runs_since_review=12, stuck_runs=0)

        await router.dispatch(_ctx(loop, "/dream"))
        await _drain(loop)

        content = loop.published[0].content
        assert "A memory review pass ran first" in content
        assert "no conversation history to process" in content

    @pytest.mark.asyncio
    async def test_report_and_guard_are_rebuilt_after_the_review(
        self, router, loop, memory, monkeypatch, workspace
    ):
        """Il review ha appena riscritto quei file: riusarli mentirebbe al turno dopo.

        Il gauge del turno incrementale mostrerebbe altrimenti un riempimento
        che il review ha già smontato — cioè chiederebbe al modello di far
        spazio che è già stato fatto.
        """
        _set_dream_config(workspace, memory_budget_chars=500)
        shrunk = "# Memory\n"
        spy = _ReviewSpy(shrink_to=shrunk)
        _install_review(monkeypatch, spy)
        memory.set_review_state(runs_since_review=12, stuck_runs=0)

        guards: list[Any] = []
        real_build = memory.build_dream_tools

        def _record(*, write_size_guard=None):
            guards.append(write_size_guard)
            return real_build(write_size_guard=write_size_guard)

        monkeypatch.setattr(memory, "build_dream_tools", _record)

        await router.dispatch(_ctx(loop, "/dream"))
        await _drain(loop)

        # Il gauge del turno incrementale è misurato DOPO il review.
        assert f"MEMORY.md [{len(shrunk) * 100 // 500}% — {len(shrunk)}/500" in loop.prompts[0]
        # E il guard non è lo stesso oggetto passato al review.
        assert guards and guards[0] is not spy.calls[0]["guard"]


# ---------------------------------------------------------------------------
# 7-8. Regressioni
# ---------------------------------------------------------------------------


class TestTheShippedDefaultsEnforce:
    """I default non sono più inerti, e questa classe è dove il cambio si vede.

    ``memory_budget_chars`` e ``user_budget_chars`` sono nati a 0 — "misurato ma
    non applicato" — perché servivano le misure vere, e perché un rifiuto poteva
    ancora far avanzare il cursore di Dream buttando via il fatto rifiutato. Ora
    valgono 2.000, il numero letto sul device, e la precondizione è chiusa
    (``internal_run_should_commit``).

    ``SOUL.md`` resta l'unico a 0, e non per dimenticanza: mescola identità e
    vincoli di piattaforma, e un tetto non sa su quale delle due sta premendo.
    """

    @pytest.mark.asyncio
    async def test_a_write_over_the_shipped_cap_is_refused_without_configuring_anything(
        self, router, loop, memory
    ):
        # Nessun ``_set_dream_config``: è il default di spedizione a rifiutare.
        turn = _replace_seed(memory, "y" * 5000)
        loop.on_turn = turn

        await router.dispatch(_ctx(loop, "/dream"))
        await _drain(loop)

        assert turn.results and "Write refused" in turn.results[0]
        assert "over its 2,000 char budget" in turn.results[0]
        assert memory.memory_file.read_text(encoding="utf-8") == _MEMORY_TEXT

    @pytest.mark.asyncio
    async def test_a_write_under_the_shipped_cap_still_lands(
        self, router, loop, memory
    ):
        turn = _replace_seed(memory, "- gateway runs on Android")
        loop.on_turn = turn

        await router.dispatch(_ctx(loop, "/dream"))
        await _drain(loop)

        assert turn.results and "Successfully edited" in turn.results[0]
        assert "gateway runs on Android" in memory.memory_file.read_text(encoding="utf-8")

    @pytest.mark.asyncio
    async def test_the_gauge_shows_two_caps_and_soul_without_one(self, router, loop):
        """Il gauge distingue i due stati, e sono entrambi presenti di default.

        ``render_gauge`` mostra i tre file comunque; ciò che cambia con i tetti
        di spedizione è che due righe su tre ora portano una percentuale, cioè
        una soglia che il modello deve rispettare. La terza no, ed è la
        controprova che "misurato ma non applicato" esiste ancora.
        """
        await router.dispatch(_ctx(loop, "/dream"))
        await _drain(loop)

        prompt = loop.prompts[0]
        assert "Long-term memory budget" in prompt
        assert f"MEMORY.md [{len(_MEMORY_TEXT) * 100 // 2000}% — {len(_MEMORY_TEXT)}/2,000" in prompt
        assert "SOUL.md [7 chars — no budget]" in prompt

    @pytest.mark.asyncio
    async def test_the_reply_is_the_one_from_before(self, router, loop):
        ack = await router.dispatch(_ctx(loop, "/dream"))
        await _drain(loop)

        assert ack.content == "Dreaming..."
        content = loop.published[0].content
        assert content.startswith("Dream completed in")
        assert "budget" not in content.lower()
        assert "review" not in content.lower()

    @pytest.mark.asyncio
    async def test_no_review_pass_on_a_fresh_install(self, router, loop, monkeypatch):
        spy = _ReviewSpy()
        _install_review(monkeypatch, spy)

        await router.dispatch(_ctx(loop, "/dream"))
        await _drain(loop)

        assert not spy.ran

    @pytest.mark.asyncio
    async def test_the_config_is_not_rewritten(self, router, loop, workspace):
        before = (workspace / "config.json").stat().st_mtime_ns

        await router.dispatch(_ctx(loop, "/dream"))
        await _drain(loop)

        assert (workspace / "config.json").stat().st_mtime_ns == before


class TestBudgetBranchRunsNothing:
    """`/dream budget ...` legge e scrive numeri, non consolida."""

    @pytest.mark.asyncio
    async def test_reading_the_budget_starts_no_run(self, router, loop, monkeypatch):
        spy = _ReviewSpy()
        _install_review(monkeypatch, spy)

        await router.dispatch(_ctx(loop, "/dream budget"))
        await asyncio.sleep(0)

        assert loop.prompts == []
        assert loop.published == []
        assert not spy.ran

    @pytest.mark.asyncio
    async def test_writing_a_budget_starts_no_run(self, router, loop, memory, monkeypatch):
        spy = _ReviewSpy()
        _install_review(monkeypatch, spy)
        memory.set_review_state(runs_since_review=99, stuck_runs=9)

        await router.dispatch(_ctx(loop, "/dream budget memory 500"))
        await asyncio.sleep(0)

        assert loop.prompts == []
        assert not spy.ran
        # E nemmeno i contatori si muovono: non è passato nessun run.
        assert memory.get_review_state() == (99, 9)
