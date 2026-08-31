"""Il lock delle scritture di config.json non deve sopravvivere al suo event loop.

``_LOCK`` è una globale di modulo, e il gateway riparte *apposta* nello stesso
processo: il retry di ``run_gateway`` e il restart lato Kotlin aprono un secondo
``asyncio.run``. Una ``asyncio.Lock`` legata al loop precedente rifiuta ogni
accodamento successivo, e siccome tutte le scritture della config passano da
``store.mutate`` il risultato sarebbe una ``config.json`` di sola lettura fino
al force-stop dell'app.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import patch

import pytest

from jenny.android_entry import run_gateway
from jenny.config import store
from jenny.config.bootstrap import ensure_minimal_config
from jenny.runtime.context import get_runtime_context


@pytest.fixture(autouse=True)
def _fresh_store_lock():
    """Questi test legano di proposito ``store._LOCK`` a loop che poi muoiono:
    senza ripulire, il resto della suite erediterebbe il lock avvelenato."""
    yield
    store.reset_config_store_state()


def _is_loop_bound(lock: asyncio.Lock) -> bool:
    """True se il lock ha già memorizzato un event loop.

    Legge ``_loop`` di ``asyncio.mixins._LoopBoundMixin``: è un dettaglio
    interno di CPython, e viene usato solo come *precondizione* dei test — se
    una versione futura smettesse di legare i lock, questi test devono
    accorgersene invece di passare a vuoto.
    """
    return getattr(lock, "_loop", None) is not None


async def _wait_until_queued(lock: asyncio.Lock, *, timeout: float = 1.0) -> None:
    """Cede il controllo finché *lock* non ha un waiter in coda.

    Era ``for _ in range(5): await asyncio.sleep(0)``. Cinque giri bastano oggi,
    ma il numero non descrive niente: quanti ne servano dipende da quante volte
    la catena rimbalza prima che il task arrivi ad ``acquire``. La condizione
    osservabile è invece esatta, ed è quella che il test vuole davvero.

    ``_waiters`` è un interno di CPython, come ``_loop`` letto qui sopra: lo si
    usa per la stessa ragione e con la stessa riserva — se sparisce, questi test
    devono accorgersene invece di passare a vuoto.
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        waiters = getattr(lock, "_waiters", None)
        if waiters:
            return
        await asyncio.sleep(0)
    raise AssertionError("nessuno scrittore si è accodato sul lock")


async def _mutate_with_a_queued_writer(config_path: Path) -> None:
    """Una scrittura via ``mutate`` con un secondo scrittore in coda sul lock.

    L'accodamento è l'unica cosa che lega una ``asyncio.Lock`` al suo loop: la
    strada senza contesa non passa mai da ``_get_loop``. E il corpo di
    ``mutate`` è oggi interamente sincrono, quindi su un solo loop due
    scrittori non si incrociano da soli. Qui il lock viene tenuto a mano per il
    tempo di far partire il secondo: è la riproduzione fedele di ciò che
    succede da sé appena un ``await`` entra nella sezione critica, o appena un
    secondo event loop (un thread con il suo ``asyncio.run``) chiama
    ``mutate``.
    """
    async with store._LOCK:
        queued = asyncio.create_task(
            store.mutate(lambda _cfg: None, config_path=config_path)
        )
        await _wait_until_queued(store._LOCK)
    await queued


def test_asyncio_lock_binds_to_the_first_loop_that_queues_on_it():
    """Il fatto di linguaggio su cui poggia il reset (CPython 3.11+).

    Se questo test cade, la semantica di ``asyncio.Lock`` è cambiata e i
    ``reset_*`` dell'entry point vanno rivalutati, non aggiornati d'ufficio.
    """
    lock = asyncio.Lock()

    async def uncontended() -> None:
        async with lock:
            pass

    async def contended() -> None:
        async def hold() -> None:
            async with lock:
                await asyncio.sleep(0)

        await asyncio.gather(hold(), hold())

    # Senza contesa il lock non si lega: due giri di seguito passano lisci.
    asyncio.run(uncontended())
    asyncio.run(uncontended())
    assert not _is_loop_bound(lock)

    # Basta un accodamento perché il lock adotti quel loop...
    asyncio.run(contended())
    assert _is_loop_bound(lock)

    # ...e da lì in poi ogni accodamento su un loop diverso è un errore.
    with pytest.raises(RuntimeError, match="bound to a different event loop"):
        asyncio.run(contended())


def test_config_store_lock_is_reset_between_gateway_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Due ``asyncio.run`` nello stesso processo, con scritture config in mezzo.

    Prima del reset in ``run_gateway`` il secondo giro moriva con
    ``RuntimeError: ... is bound to a different event loop`` e da quel momento
    nessuna impostazione era più salvabile.
    """
    monkeypatch.setattr(get_runtime_context(), "workspace_dir", None)
    monkeypatch.setattr("jenny.android_entry.RETRY_DELAY_S", 0)

    workspace = tmp_path / "workspace"
    ensure_minimal_config(workspace)
    config_path = workspace / "config.json"

    # Giro 1: il lock di modulo adotta l'event loop di questo ``asyncio.run``.
    asyncio.run(_mutate_with_a_queued_writer(config_path))
    assert _is_loop_bound(store._LOCK), "precondizione: il giro 1 deve legare il lock"

    # Giro 2: ``run_gateway`` apre il suo ``asyncio.run`` nello stesso processo.
    async def _fake_run(**_kwargs) -> None:
        await _mutate_with_a_queued_writer(config_path)

    with patch("jenny.gateway_runtime._run_gateway", new=_fake_run):
        run_gateway(str(tmp_path), host="127.0.0.1", port=18042)

    assert config_path.is_file()


def test_reset_config_store_state_replaces_the_lock():
    """Il reset consegna un lock nuovo, non lo stesso ripulito."""
    before = store._LOCK
    store.reset_config_store_state()
    after = store._LOCK

    assert after is not before
    assert not after.locked()
    assert not _is_loop_bound(after)
