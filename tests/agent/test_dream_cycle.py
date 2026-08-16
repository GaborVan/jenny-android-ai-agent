"""Il ciclo condiviso di Dream, e la prova che i suoi due chiamanti restano allineati.

``jenny/agent/dream_cycle.py`` esiste perché un run di Dream era implementato due
volte — il job cron e lo slash command ``/dream`` — e le due copie divergevano una
divergenza alla volta: il guard del budget montato solo di là, il gauge assente di
qua, i contatori del review che non avanzavano lanciando Dream a mano. Ognuna è
stata trovata e allineata a mano, ed è l'allineamento a mano la ragione per cui ne
sarebbe arrivata un'altra.

Questo file fa due mestieri distinti, e il secondo è quello per cui il primo vale
la pena:

1. prova il modulo condiviso una volta sola — trigger del review, checkpoint,
   ricostruzione delle misure, aritmetica dei contatori;
2. fa girare **i due chiamanti veri** sullo stesso stato e confronta ciò che si
   osserva da fuori: la traccia delle chiamate sullo store e le righe di log che
   il ciclo emette. È il test che fallisce il giorno in cui uno dei due percorsi
   ricomincia a fare storia a sé.

I vicini di casa sono ``tests/cron/test_dream_budget_dispatch.py`` e
``tests/command/test_dream_run_budget.py``, che continuano a coprire ciascuno il
proprio percorso con i propri fake: qui si guarda solo ciò che i due devono avere
in comune.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest
from loguru import logger

from jenny.agent.dream_cycle import (
    STUCK_FORCES_REVIEW,
    STUCK_IS_ALARMING,
    begin_dream_cycle,
    finish_dream_cycle,
    format_budget,
    take_dream_snapshot,
)
from jenny.agent.dream_review import STATUS_COMPLETED
from jenny.agent.tools.file_state import FileStates
from jenny.bus.events import InboundMessage
from jenny.command.builtin import register_builtin_commands
from jenny.command.router import CommandContext, CommandRouter
from jenny.config.schema import Config
from jenny.runtime.cron_dispatch import CronDispatcher

_REVIEW_TARGET = "jenny.agent.dream_review.run_dream_review"

_DREAM_JOB = SimpleNamespace(name="dream", id="job-dream")

# Scrittura enorme con cui si interroga il guard montato su un run: serve solo a
# sapere se l'enforcement è acceso, senza dover ispezionare l'oggetto.
_HUGE_WRITE = "y" * 1_000_000


# ---------------------------------------------------------------------------
# Fake condivisi dai due percorsi
# ---------------------------------------------------------------------------


class _FakeMemory:
    """``MemoryStore`` minimale con i tre file di memoria veri su disco.

    Veri perché ``budget_report`` li misura davvero: un fake che restituisse
    numeri inventati verificherebbe il cablaggio contro se stesso.

    Ogni chiamata che il ciclo fa sullo store finisce in ``events``, in ordine.
    È quella lista, e non un conteggio di invocazioni, la forma osservabile su
    cui i due percorsi vengono confrontati: dice anche *quando* le cose sono
    successe, che è metà di ciò che qui può divergere.
    """

    def __init__(
        self,
        workspace: Path,
        *,
        memory_text: str = "m",
        review_state: tuple[int, int] = (0, 0),
        has_work: bool = True,
        file_states: FileStates | None = None,
    ) -> None:
        workspace.mkdir(parents=True, exist_ok=True)
        self.memory_file = workspace / "MEMORY.md"
        self.user_file = workspace / "USER.md"
        self.soul_file = workspace / "SOUL.md"
        self.memory_file.write_text(memory_text, encoding="utf-8")
        self.user_file.write_text("u", encoding="utf-8")
        self.soul_file.write_text("s", encoding="utf-8")

        self._review_state = review_state
        self._has_work = has_work
        self._file_states = file_states

        self.events: list[str] = []
        self.gauges: list[str] = []
        self.guards: list[Any] = []
        self.cursor: int | None = None

    def get_review_state(self) -> tuple[int, int]:
        return self._review_state

    def set_review_state(self, *, runs_since_review: int, stuck_runs: int) -> None:
        self._review_state = (runs_since_review, stuck_runs)
        self.events.append(f"review_state:{runs_since_review},{stuck_runs}")

    def build_dream_prompt(self, *, max_entries: int = 20, gauge: str = ""):
        self.gauges.append(gauge)
        self.events.append(f"prompt:{gauge.splitlines()[1] if gauge else ''}")
        return ("prompt di consolidamento", 42) if self._has_work else None

    def build_dream_tools(self, *, write_size_guard=None):
        self.guards.append(write_size_guard)
        refuses = (
            write_size_guard is not None
            and write_size_guard(self.memory_file, _HUGE_WRITE) is not None
        )
        self.events.append(f"tools:refuses={refuses}")
        return SimpleNamespace(file_states=self._file_states)

    def set_last_dream_cursor(self, cursor: int) -> None:
        self.cursor = cursor
        self.events.append(f"cursor:{cursor}")

    def get_last_dream_cursor(self) -> int:
        return 7

    def compact_history(self) -> None:
        pass


class _FakeAgent:
    """Il minimo comune dei due chiamanti: agente del cron e loop del comando.

    Uno solo per entrambi non è pigrizia — è ciò che rende il confronto un
    confronto: se i due percorsi ricevessero fake diversi, una differenza fra i
    fake basterebbe a spiegare (o a nascondere) una differenza fra i percorsi.
    """

    def __init__(
        self,
        workspace: Path,
        memory: _FakeMemory,
        *,
        snapshot_before_dream: Any = None,
    ) -> None:
        self.context = SimpleNamespace(memory=memory, timezone=None)
        self.sessions = SimpleNamespace(sessions_dir=workspace / "sessions")
        self.bus = SimpleNamespace(publish_outbound=self._publish)
        self.published: list[Any] = []
        self.snapshot_before_dream = snapshot_before_dream
        self._memory = memory

    async def _publish(self, message: Any) -> None:
        self.published.append(message)

    async def process_direct(self, prompt: str, **_kwargs: Any):
        self._memory.events.append("process_direct")
        return SimpleNamespace(metadata={"_stop_reason": "completed"}, usage={})

    def evict_pruned_sessions(self, _keys: Any) -> None:
        pass


class _ReviewSpy:
    """Sostituto di ``run_dream_review`` che registra cosa gli è arrivato."""

    def __init__(self, memory: _FakeMemory, *, shrink_to: str | None = None) -> None:
        self._memory = memory
        self._shrink_to = shrink_to
        self.calls: list[dict[str, Any]] = []

    async def __call__(self, agent, *, store, report, snapshotted, write_size_guard=None):
        self._memory.events.append(f"review:snapshotted={snapshotted}")
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
            status=STATUS_COMPLETED,
            before=before,
            after=after,
            freed=sum(before[label] - after[label] for label in before),
        )

    @property
    def ran(self) -> bool:
        return bool(self.calls)


def _blocked_writes() -> FileStates:
    """Run che ha tentato di scrivere senza riuscirci: il cursore non avanza."""
    states = FileStates()
    states.record_write_attempt()
    return states


def _successful_writes(path: Path) -> FileStates:
    states = FileStates()
    states.record_write_attempt()
    states.record_write(path)
    return states


def _config(*, memory_budget: int = 0, review_every_runs: int = 12) -> Config:
    config = Config()
    dream = config.agents.defaults.dream
    dream.memory_budget_chars = memory_budget
    dream.review_every_runs = review_every_runs
    return config


def _dream_cfg(*, memory_budget: int = 0, review_every_runs: int = 12):
    return _config(
        memory_budget=memory_budget, review_every_runs=review_every_runs
    ).agents.defaults.dream


@contextmanager
def _cycle_logs() -> Iterator[list[str]]:
    """Raccogli le righe emesse **dal modulo condiviso**, e solo quelle.

    Sono la parte di comportamento che non passa dallo store: la riga del budget
    a ogni run (punto 7 del catalogo) e l'ERROR sul livelock conclamato (punto
    6). Erano entrambe solo lato cron, e senza guardarle il confronto fra i due
    percorsi le lascerebbe divergere di nuovo in silenzio.
    """
    messages: list[str] = []
    handler = logger.add(
        lambda m: messages.append(f"{m.record['level'].name}: {m.record['message']}"),
        level="DEBUG",
        filter=lambda record: record["name"] == "jenny.agent.dream_cycle",
    )
    try:
        yield messages
    finally:
        logger.remove(handler)


# ---------------------------------------------------------------------------
# 1. Il modulo condiviso
# ---------------------------------------------------------------------------


class TestReviewTrigger:
    async def test_over_budget_alone_does_not_trigger_a_review(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Sforare il budget non basta a far partire un review, ed è deliberato.

        Sembra il trigger più ovvio dei tre ed è l'unico che non sa fermarsi: un
        file può restare sopra la soglia dopo un review che ha già fatto tutto il
        possibile — il resto è roba che le regole marcano "never delete" — e il
        prompt del review dichiara *valido* un run che non cambia niente. La
        condizione resterebbe vera per sempre e brucerebbe un turno LLM ogni due
        ore. Chi copre il caso in cui essere sopra budget fa davvero danno è
        ``stuck``.
        """
        memory = _FakeMemory(tmp_path / "ws", memory_text="x" * 200)
        spy = _ReviewSpy(memory)
        monkeypatch.setattr(_REVIEW_TARGET, spy)

        prologue = await begin_dream_cycle(
            _FakeAgent(tmp_path, memory),
            store=memory,
            cfg=_dream_cfg(memory_budget=50),
        )

        assert not spy.ran
        assert prologue.review is None

    async def test_the_periodic_cadence_fires_it(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        memory = _FakeMemory(tmp_path / "ws", review_state=(4, 0))
        spy = _ReviewSpy(memory)
        monkeypatch.setattr(_REVIEW_TARGET, spy)

        prologue = await begin_dream_cycle(
            _FakeAgent(tmp_path, memory),
            store=memory,
            cfg=_dream_cfg(review_every_runs=4),
        )

        assert spy.ran
        assert prologue.review is not None
        # I contatori sono azzerati sia su disco sia nel prologo che il chiamante
        # passerà a ``finish_dream_cycle``: rileggerli lì sarebbe un secondo
        # accesso a uno stato che nel frattempo un altro run può aver riscritto.
        assert memory.get_review_state() == (0, 0)
        assert (prologue.runs_since_review, prologue.stuck) == (0, 0)

    async def test_stuck_runs_force_it(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """L'uscita di emergenza dal livelock, con la cadenza periodica lontana."""
        memory = _FakeMemory(tmp_path / "ws", review_state=(0, STUCK_FORCES_REVIEW))
        spy = _ReviewSpy(memory)
        monkeypatch.setattr(_REVIEW_TARGET, spy)

        await begin_dream_cycle(
            _FakeAgent(tmp_path, memory),
            store=memory,
            cfg=_dream_cfg(review_every_runs=99),
        )

        assert spy.ran

    async def test_report_and_guard_are_rebuilt_after_the_review(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Dopo il review i file sono cambiati: misure vecchie non valgono più."""
        memory = _FakeMemory(tmp_path / "ws", memory_text="x" * 200, review_state=(4, 0))
        spy = _ReviewSpy(memory, shrink_to="x" * 20)
        monkeypatch.setattr(_REVIEW_TARGET, spy)

        prologue = await begin_dream_cycle(
            _FakeAgent(tmp_path, memory),
            store=memory,
            cfg=_dream_cfg(memory_budget=50, review_every_runs=4),
        )

        # Il report che il chiamante userà per il gauge descrive il file potato.
        assert [item.chars for item in prologue.report if item.label == "MEMORY.md"] == [20]
        # E il guard non è quello passato al review: nasce dall'ultima misura,
        # insieme al report da cui deriva.
        assert prologue.guard is not spy.calls[0]["guard"]
        assert prologue.guard(memory.memory_file, "z" * 300) is not None


class TestSnapshot:
    async def test_no_callback_means_not_snapshotted(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``take_snapshot=None`` **è** ``snapshotted=False``, senza che nessuno lo scriva.

        È il punto del parametro: un percorso che non ha modo di prendere il
        checkpoint deve dire la verità al prompt del review, non dimenticare la
        domanda. Il ramo "le tue modifiche sono reversibili" è attaccato alla
        frase il cui unico scopo è far cancellare di più.
        """
        memory = _FakeMemory(tmp_path / "ws", review_state=(4, 0))
        spy = _ReviewSpy(memory)
        monkeypatch.setattr(_REVIEW_TARGET, spy)

        await begin_dream_cycle(
            _FakeAgent(tmp_path, memory),
            store=memory,
            cfg=_dream_cfg(review_every_runs=4),
            take_snapshot=None,
        )

        assert spy.calls[0]["snapshotted"] is False

    async def test_a_checkpoint_that_raises_is_fail_open_and_honest(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Uno snapshot che solleva non ferma il consolidamento e non mente."""
        memory = _FakeMemory(tmp_path / "ws", review_state=(4, 0))
        spy = _ReviewSpy(memory)
        monkeypatch.setattr(_REVIEW_TARGET, spy)

        async def broken() -> bool:
            memory.events.append("snapshot")
            raise RuntimeError("checkpoint guasto")

        prologue = await begin_dream_cycle(
            _FakeAgent(tmp_path, memory),
            store=memory,
            cfg=_dream_cfg(review_every_runs=4),
            take_snapshot=broken,
        )

        assert spy.calls[0]["snapshotted"] is False
        assert prologue.review is not None
        assert memory.events[:2] == ["snapshot", "review:snapshotted=False"]

    async def test_it_precedes_the_review(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        memory = _FakeMemory(tmp_path / "ws", review_state=(4, 0))
        spy = _ReviewSpy(memory)
        monkeypatch.setattr(_REVIEW_TARGET, spy)

        async def checkpoint() -> bool:
            memory.events.append("snapshot")
            return True

        await begin_dream_cycle(
            _FakeAgent(tmp_path, memory),
            store=memory,
            cfg=_dream_cfg(review_every_runs=4),
            take_snapshot=checkpoint,
        )

        assert memory.events[0] == "snapshot"
        assert spy.calls[0]["snapshotted"] is True

    async def test_a_checkpoint_that_reports_false_is_not_upgraded(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Con gli snapshot spenti il callback c'è ma non fa nulla, e lo dice."""
        memory = _FakeMemory(tmp_path / "ws", review_state=(4, 0))
        spy = _ReviewSpy(memory)
        monkeypatch.setattr(_REVIEW_TARGET, spy)

        async def disabled() -> bool:
            return False

        await begin_dream_cycle(
            _FakeAgent(tmp_path, memory),
            store=memory,
            cfg=_dream_cfg(review_every_runs=4),
            take_snapshot=disabled,
        )

        assert spy.calls[0]["snapshotted"] is False

    async def test_take_dream_snapshot_alone(self) -> None:
        """L'helper che i chiamanti riusano per il checkpoint pre-turno."""
        assert await take_dream_snapshot(None) is False

        async def ok() -> bool:
            return True

        async def broken() -> bool:
            raise RuntimeError("guasto")

        assert await take_dream_snapshot(ok) is True
        assert await take_dream_snapshot(broken) is False


class TestFinishArithmetic:
    async def test_a_run_that_advances_resets_stuck(self, tmp_path: Path) -> None:
        memory = _FakeMemory(tmp_path / "ws")

        assert finish_dream_cycle(
            memory, advanced=True, runs_since_review=3, stuck=1
        ) == (4, 0)
        assert memory.get_review_state() == (4, 0)

    async def test_a_run_that_does_not_advance_raises_stuck(self, tmp_path: Path) -> None:
        """Non avanzare è la semantica corretta: il fatto non è stato scritto.

        La via d'uscita è forzare il review, non allentare il commit — quindi il
        contatore sale e ``internal_run_should_commit`` resta dov'è.
        """
        memory = _FakeMemory(tmp_path / "ws")

        assert finish_dream_cycle(
            memory, advanced=False, runs_since_review=3, stuck=1
        ) == (4, 2)
        assert memory.get_review_state() == (4, 2)

    async def test_the_alarming_threshold_logs_at_error(self, tmp_path: Path) -> None:
        """Sopra la soglia ogni run è un turno LLM che non consolida niente.

        Il contatore ci arriva solo passandoglielo: dal ciclo intero non ci si
        arriva, perché il review azzera ``stuck`` e il valore satura a
        ``STUCK_FORCES_REVIEW`` (v. il commento sulla costante). Qui si verifica
        il contratto della funzione, non una traiettoria che oggi esista.
        """
        memory = _FakeMemory(tmp_path / "ws")

        with _cycle_logs() as messages:
            finish_dream_cycle(
                memory, advanced=False, runs_since_review=9, stuck=STUCK_IS_ALARMING - 1
            )

        assert any(m.startswith("ERROR: Dream has not advanced") for m in messages)

    async def test_below_the_threshold_says_nothing(self, tmp_path: Path) -> None:
        memory = _FakeMemory(tmp_path / "ws")

        with _cycle_logs() as messages:
            finish_dream_cycle(
                memory, advanced=False, runs_since_review=1, stuck=STUCK_IS_ALARMING - 2
            )

        assert not [m for m in messages if m.startswith("ERROR")]


class TestBudgetLogLine:
    async def test_it_is_emitted_on_every_run(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Con i budget a 0 questa riga è l'unica cosa che la feature produce.

        Ed è la riga da cui si scelgono i tetti veri, cioè quella che serve a chi
        sta tarando — che è esattamente chi lancia Dream a mano.
        """
        memory = _FakeMemory(tmp_path / "ws", memory_text="x" * 200, review_state=(3, 1))

        with _cycle_logs() as messages:
            await begin_dream_cycle(
                _FakeAgent(tmp_path, memory), store=memory, cfg=_dream_cfg(memory_budget=50)
            )

        assert messages == [
            "INFO: Dream memory budget: MEMORY.md 200/50 (400%), USER.md 1 (no budget), "
            "SOUL.md 1 (no budget) | runs since review: 3, stuck runs: 1"
        ]

    async def test_an_empty_report_still_renders(self) -> None:
        assert format_budget([]) == "no files"


# ---------------------------------------------------------------------------
# 2. I due chiamanti, sullo stesso stato
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Scenario:
    """Uno stato di partenza da far attraversare a entrambi i percorsi."""

    memory_text: str = "m"
    review_state: tuple[int, int] = (0, 0)
    memory_budget: int = 0
    review_every_runs: int = 12
    blocked_writes: bool = False
    with_snapshot: bool = True
    shrink_to: str | None = None


_SCENARIOS = {
    # Il caso di gran lunga più frequente: niente in scadenza, il cursore avanza.
    "plain": _Scenario(),
    # Cadenza periodica scaduta: parte il review, che pota davvero — così le
    # misure ricostruite dopo sono diverse da quelle di prima e un percorso che
    # riusasse le vecchie si vedrebbe.
    "cadence": _Scenario(
        memory_text="x" * 200,
        review_state=(4, 0),
        memory_budget=50,
        review_every_runs=4,
        shrink_to="x" * 20,
    ),
    # Livelock: il budget rifiuta le scritture, ``stuck`` ha già forzato il
    # review, e non c'è nessun servizio di snapshot da cui prendere il checkpoint.
    "stuck_without_snapshot": _Scenario(
        memory_text="x" * 200,
        review_state=(0, STUCK_FORCES_REVIEW),
        memory_budget=50,
        review_every_runs=99,
        blocked_writes=True,
        with_snapshot=False,
    ),
}


def _install_scenario(
    scenario: _Scenario, memory: _FakeMemory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cabla review spy e knob per un percorso.

    I knob passano da ``load_config``, non da un ``Config`` passato a mano: è
    così che li leggono **entrambi** i percorsi, a ogni run e da disco, perché il
    ``Config`` catturato dal container non viene aggiornato da nessuno quando
    ``/dream budget`` riscrive il file. Sostituire la lettura invece di
    scavalcarla è ciò che tiene il test aderente al comportamento vero.
    """
    monkeypatch.setattr(_REVIEW_TARGET, _ReviewSpy(memory, shrink_to=scenario.shrink_to))
    config = _config(
        memory_budget=scenario.memory_budget,
        review_every_runs=scenario.review_every_runs,
    )
    monkeypatch.setattr("jenny.config.loader.load_config", lambda *a, **k: config)


def _build(scenario: _Scenario, root: Path) -> tuple[_FakeMemory, _FakeAgent]:
    memory = _FakeMemory(
        root / "ws",
        memory_text=scenario.memory_text,
        review_state=scenario.review_state,
        file_states=(
            _blocked_writes()
            if scenario.blocked_writes
            else _successful_writes(root / "written.md")
        ),
    )

    async def checkpoint() -> bool:
        memory.events.append("snapshot")
        return True

    agent = _FakeAgent(
        root,
        memory,
        snapshot_before_dream=checkpoint if scenario.with_snapshot else None,
    )
    return memory, agent


async def _run_cron(
    scenario: _Scenario, root: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[list[str], list[str]]:
    memory, agent = _build(scenario, root)
    _install_scenario(scenario, memory, monkeypatch)
    dispatcher = CronDispatcher(
        get_agent=lambda: agent,
        config=Config(),
        cron=MagicMock(),
        heartbeat_cfg=SimpleNamespace(),
        snapshot_before_dream=agent.snapshot_before_dream,
    )

    with _cycle_logs() as messages:
        await dispatcher.dispatch(_DREAM_JOB)
    return memory.events, messages


async def _run_manual(
    scenario: _Scenario, root: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[list[str], list[str]]:
    memory, loop = _build(scenario, root)
    _install_scenario(scenario, memory, monkeypatch)
    router = CommandRouter()
    register_builtin_commands(router)
    msg = InboundMessage(
        channel="websocket", sender_id="u", chat_id="default", content="/dream"
    )
    ctx = CommandContext(
        msg=msg, session=None, key="k", raw="/dream", args="", loop=loop
    )

    with _cycle_logs() as messages:
        await router.dispatch(ctx)
        deadline = time.monotonic() + 5.0
        while not loop.published and time.monotonic() < deadline:
            await asyncio.sleep(0.005)
        assert loop.published, "`/dream` non ha pubblicato nessuna risposta"
    return memory.events, messages


class TestTheTwoCallersStayAligned:
    """Il test per cui l'estrazione esiste.

    Non confronta il codice dei due percorsi — confronta ciò che se ne vede da
    fuori: la sequenza esatta delle chiamate sullo store (checkpoint, review,
    scritture dei contatori, gauge nel prompt, guard montato sui tool, cursore) e
    le righe che il ciclo emette nei log. Sono le sette divergenze del catalogo,
    espresse in una forma che ne prende anche l'ottava: qualunque cosa uno dei
    due chiamanti smetta di fare, o cominci a fare in un altro momento, rompe
    l'uguaglianza.

    Ciò che resta legittimamente diverso — la risposta in chat contro la riga di
    log, il tempo trascorso, la sorgente del fuso per i token — non passa da qui
    per costruzione: non tocca lo store e non lo scrive ``dream_cycle``.
    """

    @pytest.mark.parametrize("name", sorted(_SCENARIOS))
    async def test_same_observable_trace(
        self, name: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        scenario = _SCENARIOS[name]

        with monkeypatch.context() as m:
            cron_events, cron_logs = await _run_cron(scenario, tmp_path / "cron", m)
        with monkeypatch.context() as m:
            manual_events, manual_logs = await _run_manual(
                scenario, tmp_path / "manual", m
            )

        assert cron_events == manual_events
        assert cron_logs == manual_logs

    async def test_the_trace_is_not_vacuously_equal(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """La rete sotto la rete: il confronto deve poter fallire.

        Un test di uguaglianza fra due tracce vuote passerebbe per sempre e non
        direbbe niente. Qui si fissa cosa deve contenere la traccia dello
        scenario più ricco — checkpoint, review, azzeramento, gauge misurato
        dopo il review, guard che rifiuta, e i contatori riscritti alla fine.
        """
        with monkeypatch.context() as m:
            events, logs = await _run_cron(_SCENARIOS["cadence"], tmp_path, m)

        assert events == [
            "snapshot",
            "review:snapshotted=True",
            "review_state:0,0",
            "prompt:MEMORY.md [40% — 20/50 chars]",
            "tools:refuses=True",
            "process_direct",
            "cursor:42",
            "review_state:1,0",
        ]
        assert [m.split(":")[0] for m in logs] == ["INFO", "INFO"]


# ---------------------------------------------------------------------------
# 3. Il checkpoint sul percorso manuale
# ---------------------------------------------------------------------------


class TestTheManualRunTakesTheSnapshot:
    """Punto 5 del catalogo: `/dream` a mano non prendeva **nessuno** checkpoint.

    Era già un buco quando quel comando faceva solo consolidamento incrementale.
    Da ``04de3cc`` può far partire un review pass — un turno esplicitamente
    autorizzato a ristrutturare e cancellare — e girarlo senza rete è un'altra
    cosa. Il servizio snapshot ce l'ha il container, che ora lo appende al loop.
    """

    async def test_the_incremental_turn_is_checkpointed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        events, _ = await _run_manual(_SCENARIOS["plain"], tmp_path, monkeypatch)

        assert "snapshot" in events
        assert events.index("snapshot") < events.index("process_direct")

    async def test_a_loop_without_the_service_reports_no_snapshot(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Senza il servizio il run prosegue, e il review pass lo sa.

        ``getattr(loop, "snapshot_before_dream", None)`` copre anche il loop che
        l'attributo non ce l'ha affatto — un test, un percorso che costruisce
        l'agente da sé — e la traduzione è la stessa: nel dubbio si mente al
        ribasso, mai al rialzo.
        """
        scenario = _SCENARIOS["stuck_without_snapshot"]
        events, _ = await _run_manual(scenario, tmp_path, monkeypatch)

        assert "snapshot" not in events
        assert "review:snapshotted=False" in events

    async def test_only_one_checkpoint_per_cycle(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Se il review è girato, il suo snapshot copre anche il turno che segue.

        Un secondo "pre_dream" archiviato dopo il review non sarebbe pre-niente.
        """
        events, _ = await _run_manual(_SCENARIOS["cadence"], tmp_path, monkeypatch)

        assert events.count("snapshot") == 1
