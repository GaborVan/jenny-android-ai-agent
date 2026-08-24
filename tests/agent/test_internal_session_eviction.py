"""Lo sfratto delle sessioni interne, e il quarto registro che dimenticava.

**T2.11**, seguito diretto di T2.5. ``AgentLoop`` tiene quattro registri indicizzati
per chiave di sessione, e ``evict_pruned_sessions`` ne sgomberava **tre**:
``_active_tasks``, ``_session_locks`` e la cache di ``SessionManager``. Il quarto —
``_file_state_store`` — restava, e ``AgentLoop`` una voce ce la mette *sempre*, a
ogni turno. Ogni run interno che conia una chiave nuova lasciava quindi una voce
morta per la vita del processo.

**Chi conia davvero una chiave per esecuzione, misurato e non dedotto:**

| job | chiave | quante volte |
|---|---|---|
| Dream | ``dream:<timestamp>`` | ogni 2 h (``DreamConfig.interval_h``) |
| Atlas | ``atlas:<timestamp>`` | ogni 6 h (``AtlasConfig.interval_h``), e solo a wiki cambiata |
| giardiniere | ``gardener:<progetto>-<timestamp>`` | fino a una per progetto ogni 6 h |
| cron | ``cron:<job_id>`` | **una sola, stabile** |
| heartbeat | ``heartbeat`` | **una sola, nuda** |

Le ultime due righe sono la correzione: le loro chiavi non portano timestamp, quindi
il loro spazio è finito per costruzione e non entrano in questo discorso.

**Due rimedi, e non uno**, perché la potatura sgombera le chiavi *potate*:

1. la riga in ``evict_pruned_sessions`` rende lo spazio **limitato** — con
   ``keep=10`` restano dieci voci più quella in corso, invece di una per run;
2. chi non può aspettare dieci run se la dimentica da sé nel proprio ``finally``
   (giardiniere in T2.5, Atlas qui).

Per Dream il ritardo di dieci run resta, ed è una decisione: gira ogni due ore, quindi
dieci run sono meno di un giorno, e la sua chiave la conia ``MemoryStore`` — un file
di cui questo cambiamento non ha bisogno.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from jenny.agent.atlas import AtlasStore, run_atlas
from jenny.agent.loop import AgentLoop
from jenny.agent.loop_tasks import LoopTasksMixin
from jenny.agent.session_locks import SessionLocks
from jenny.agent.tools.file_state import FileStates, FileStateStore

pytestmark = pytest.mark.usefixtures("_configure_jenny_workspace")


class _RegistryAgent:
    """Un agente coi **registri veri** di ``AgentLoop``, e nient'altro.

    Stessa costruzione del ``_RegistryAgent`` di ``test_gardener.py`` (T2.5), qui
    per Atlas: le classi dei registri sono quelle di produzione e i due metodi di
    sgombero sono le funzioni di ``AgentLoop`` prese come sono. Quel che il fake
    fa a mano sono le due righe che ``AgentLoop`` esegue all'ingresso di ogni
    turno — ``_session_locks.get(key)`` e ``_file_state_store.for_session(key)``
    — perché sono loro a creare la voce che si sta contando.
    """

    evict_pruned_sessions = LoopTasksMixin.evict_pruned_sessions
    forget_file_reads = AgentLoop.forget_file_reads

    def __init__(self, sessions_dir: Path) -> None:
        self._session_locks = SessionLocks()
        self._file_state_store = FileStateStore()
        self._active_tasks: dict[str, list] = {}
        self.sessions = SimpleNamespace(
            sessions_dir=sessions_dir, invalidate=lambda _key: None
        )
        self.context = SimpleNamespace(timezone=None)
        self.calls: list[str] = []

    async def process_direct(self, prompt: str, **kwargs):  # noqa: ARG002
        key = kwargs.get("session_key")
        assert isinstance(key, str)
        self.calls.append(key)
        self._session_locks.get(key)
        self._file_state_store.for_session(key)
        return SimpleNamespace(metadata={"_stop_reason": "completed"})

    @property
    def file_state_keys(self) -> int:
        return len(self._file_state_store._states_by_key)


def _atlas_store(tmp_path: Path, *, writes: int = 1) -> AtlasStore:
    """Uno store con una wiki vera e una cassetta finta che dichiara una scrittura."""
    wiki = tmp_path / "wikis" / "orto"
    (wiki / "wiki").mkdir(parents=True, exist_ok=True)
    (wiki / "AGENTS.md").write_text("# orto\n", encoding="utf-8")
    (wiki / "wiki" / "semine.md").write_text("# Semine\n", encoding="utf-8")
    (tmp_path / "memory").mkdir(exist_ok=True)
    store = AtlasStore(tmp_path, default_wiki="orto")
    states = FileStates()
    states.writes_attempted = writes
    states.writes_ok = writes
    store.build_tools = lambda: SimpleNamespace(file_states=states)  # type: ignore[method-assign]
    return store


@pytest.fixture
def moving_clock(monkeypatch: pytest.MonkeyPatch):
    """Un orologio che avanza di un minuto a ogni lettura, dentro ``atlas``.

    **Senza, questi test non misurano niente.** La chiave ha risoluzione di un
    secondo e un run di test dura molto meno: con l'orologio vero due coniature
    danno la **stessa stringa**, quindi venti run condividono una chiave sola e il
    conteggio sta a uno anche col difetto in piedi (T2.5 ci è inciampato).
    """
    real_now = datetime.now()
    ticks = iter(range(1, 5000))

    class _Clock(datetime):
        @classmethod
        def now(cls, tz=None):  # noqa: ARG003 — la firma è quella di datetime
            return real_now + timedelta(minutes=next(ticks))

    monkeypatch.setattr("jenny.agent.atlas.datetime", _Clock)
    return _Clock


# ── Il quarto registro ───────────────────────────────────────────────────────


def test_the_eviction_drops_the_file_state_entry_too(tmp_path: Path) -> None:
    """La riga che T2.11 aggiunge, misurata da sola.

    Le chiavi qui sono quelle vere di Dream e di Atlas: sono i due job che una
    potatura deve raggiungere, dato che il giardiniere si dimentica da sé.
    """
    agent = _RegistryAgent(tmp_path)
    keys = ["dream:20260823-020000", "atlas:20260823-030000"]
    for key in keys:
        agent._file_state_store.for_session(key)
        agent._session_locks.get(key)

    assert agent.file_state_keys == 2

    agent.evict_pruned_sessions(keys)

    assert agent.file_state_keys == 0
    assert agent._session_locks._locks == {}


def test_a_key_with_work_in_flight_is_left_alone(tmp_path: Path) -> None:
    """Il cancello che c'era prima non è stato allargato: una sessione con il lock
    in mano non si sfratta, e il ``FileStateStore`` non deve fare eccezione — sono
    le letture di un turno **vivo**."""
    agent = _RegistryAgent(tmp_path)
    key = "dream:20260823-020000"
    agent._file_state_store.for_session(key)
    lock = agent._session_locks.get(key)

    async def _hold() -> None:
        async with lock:
            agent.evict_pruned_sessions([key])

    import asyncio

    asyncio.run(_hold())

    assert agent.file_state_keys == 1


def test_dream_keys_become_bounded_instead_of_disappearing(tmp_path: Path) -> None:
    """Per Dream il rimedio è il **tetto**, non lo sfratto immediato: è la decisione.

    Venti run lasciano dieci voci e non venti — e le dieci sono il ``keep`` che la
    potatura ha già scelto. Con Dream ogni due ore quel ritardo è meno di un
    giorno, e la sua chiave la conia ``MemoryStore``: portarci un ``finally``
    vorrebbe dire toccare quel file per 1,5 kB di byte morti.
    """
    from jenny.agent.memory import MemoryStore

    agent = _RegistryAgent(tmp_path)
    base = datetime(2026, 8, 23, 2, 0, 0)
    for run in range(20):
        stamp = (base + timedelta(hours=2 * run)).strftime("%Y%m%d-%H%M%S")
        path = tmp_path / f"dream_{stamp}.jsonl"
        path.write_text("{}\n", encoding="utf-8")
        # mtime a mano: venti write nello stesso secondo non hanno un ordine.
        import os

        os.utime(path, (run + 1, run + 1))
        agent._file_state_store.for_session(f"dream:{stamp}")

    assert agent.file_state_keys == 20

    agent.evict_pruned_sessions(MemoryStore.prune_dream_sessions(tmp_path))

    assert agent.file_state_keys == 10


# ── Atlas: lo sfratto immediato, e la potatura che mancava su un ramo ────────


async def test_twenty_atlas_runs_do_not_leave_twenty_entries(
    tmp_path: Path, moving_clock
) -> None:
    """Il conto che dà il nome al task, sul job che ne aveva bisogno."""
    agent = _RegistryAgent(tmp_path)
    for _ in range(20):
        store = _atlas_store(tmp_path)
        # Il fingerprint deve risultare cambiato a ogni giro, altrimenti Atlas
        # esce prima di coniare la chiave e il conto misura solo il gate.
        store.write_state("stantio")
        await run_atlas(agent, store=store)

    assert len(agent.calls) == 20, "i run non sono girati: il conto non misura niente"
    assert len(set(agent.calls)) == 20, "l'orologio non si è mosso: le chiavi coincidono"
    assert agent.file_state_keys == 0


async def test_the_key_of_the_run_is_the_key_forgotten(
    tmp_path: Path, moving_clock
) -> None:
    """La chiave vera, non una nuova.

    ``AtlasStore.session_key()`` legge l'orologio a ogni chiamata: ricalcolarla
    nel ``finally`` dimenticherebbe una chiave mai esistita e lascerebbe lì
    quella del run.
    """
    agent = _RegistryAgent(tmp_path)
    forgotten: list[str] = []
    real = agent.forget_file_reads

    def _spy(key: str) -> None:
        forgotten.append(key)
        real(key)

    agent.forget_file_reads = _spy  # type: ignore[method-assign]
    store = _atlas_store(tmp_path)
    store.write_state("stantio")

    await run_atlas(agent, store=store)

    assert forgotten == agent.calls


async def test_a_run_that_blew_up_forgets_its_key_too(
    tmp_path: Path, moving_clock
) -> None:
    """Il ramo ``failed`` tornava **prima** della potatura.

    È il ramo che si prende un provider giù, cioè quello in cui le voci morte si
    accumulano più in fretta: ogni sei ore, per giorni, senza che nulla poti.
    """

    class _Boom(_RegistryAgent):
        async def process_direct(self, prompt: str, **kwargs):
            await super().process_direct(prompt, **kwargs)
            raise RuntimeError("provider is down")

    agent = _Boom(tmp_path)
    store = _atlas_store(tmp_path)
    store.write_state("stantio")

    outcome = await run_atlas(agent, store=store)

    assert outcome.status == "failed"
    assert agent.calls, "il run non è nemmeno partito"
    assert agent.file_state_keys == 0


async def test_a_run_that_never_started_forgets_nothing(tmp_path: Path) -> None:
    """Il ramo simmetrico: senza wiki, o a wiki ferma, non c'è chiave da scordare
    perché non c'è mai stata una sessione. La potatura di T1.4 sul giardiniere fa
    la stessa distinzione (``skipped_no_delta`` torna prima del guardato)."""
    agent = _RegistryAgent(tmp_path)
    (tmp_path / "memory").mkdir(exist_ok=True)
    store = AtlasStore(tmp_path)

    outcome = await run_atlas(agent, store=store)

    assert outcome.status == "skipped_no_wikis"
    assert agent.calls == []
    assert agent.file_state_keys == 0
