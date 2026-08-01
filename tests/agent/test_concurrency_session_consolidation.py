"""Fase 1.4 — Dominio di lock unico sessione↔consolidator (chiude la data-race).

Prima: due lock distinti (turno vs consolidator) sulla stessa sessione → una
consolidation detached poteva mutare `session.messages` in concorrenza con un
nuovo turno. Ora `AgentLoop._session_locks` e `Consolidator.get_lock` ritornano
lo STESSO lock rientrante per key: turno e consolidation si escludono a vicenda.
"""

from __future__ import annotations

import asyncio


def test_loop_and_consolidator_share_one_lock_domain(loop_factory) -> None:
    loop = loop_factory()
    key = "internal:test"

    loop_lock = loop._session_locks.get(key)
    consolidator_lock = loop.consolidator.get_lock(key)

    # INVARIANTE (post Fase 1.4): un solo lock per sessione, condiviso.
    assert loop_lock is consolidator_lock


async def test_detached_consolidation_serializes_with_a_new_turn(loop_factory) -> None:
    """Una consolidation detached (task separato) e un "turno" concorrente sulla
    stessa key non si sovrappongono mai: si serializzano sul lock condiviso."""
    loop = loop_factory()
    key = "internal:test"
    lock = loop._session_locks.get(key)

    order: list[str] = []
    consolidation_started = asyncio.Event()
    release_consolidation = asyncio.Event()

    async def detached_consolidation() -> None:
        async with lock:
            order.append("consolidation:enter")
            consolidation_started.set()
            await release_consolidation.wait()
            order.append("consolidation:exit")

    async def new_turn() -> None:
        async with lock:  # stesso oggetto lock del consolidator
            order.append("turn:enter")
            order.append("turn:exit")

    c = asyncio.create_task(detached_consolidation())
    await asyncio.wait_for(consolidation_started.wait(), timeout=1.0)

    t = asyncio.create_task(new_turn())
    await asyncio.sleep(0.02)
    # Il turno NON può entrare finché la consolidation tiene il lock.
    assert "turn:enter" not in order

    release_consolidation.set()
    await asyncio.wait_for(asyncio.gather(c, t), timeout=1.0)

    assert order == [
        "consolidation:enter",
        "consolidation:exit",
        "turn:enter",
        "turn:exit",
    ]


async def test_inline_consolidation_under_turn_lock_does_not_deadlock(loop_factory) -> None:
    """La consolidation attesa INLINE dentro un turno gira nello stesso task che
    già tiene il lock: il lock rientrante evita il deadlock (asyncio.Lock non è
    rientrante)."""
    loop = loop_factory()
    key = "internal:test"
    lock = loop._session_locks.get(key)

    async def turn_with_inline_consolidation() -> str:
        async with lock:  # il turno tiene il lock
            async with lock:  # consolidation inline, stesso task → rientra
                return "ok"

    result = await asyncio.wait_for(turn_with_inline_consolidation(), timeout=1.0)
    assert result == "ok"
    # Rilasciato completamente: nessuna profondità residua.
    assert not lock.locked()
