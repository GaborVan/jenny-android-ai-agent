"""Fase 0.2 / 1.3 — Shutdown ordinato che drena i background task.

Storia:
- BASELINE (Fase 0): `stop()` faceva solo `_running=False` e `close_background_tasks`
  non era mai chiamato → una consolidation detached poteva scrivere durante il flush.
- Fase 1.3: `AgentLoop.shutdown()` drena turni/subagent/consolidation (con timeout
  hard) prima che il gateway faccia `flush_all()`.

`stop()` resta invariato (solo flag); `shutdown()` è il teardown pulito.
"""

from __future__ import annotations

import asyncio


async def test_stop_only_flips_running_and_leaves_background_running(loop_factory) -> None:
    loop = loop_factory()
    started = asyncio.Event()

    async def pending_bg() -> None:
        started.set()
        await asyncio.sleep(3600)

    loop._schedule_background(pending_bg())
    await asyncio.wait_for(started.wait(), timeout=1.0)

    loop.stop()

    # stop() non attende né cancella i background task.
    assert loop._running is False
    assert any(not t.done() for t in loop._background_tasks)

    for t in loop._background_tasks:
        t.cancel()
    await asyncio.gather(*loop._background_tasks, return_exceptions=True)


async def test_shutdown_drains_completable_background_task(loop_factory) -> None:
    loop = loop_factory()
    ran = asyncio.Event()

    async def bg() -> None:
        await asyncio.sleep(0.02)
        ran.set()

    loop._schedule_background(bg())

    await loop.shutdown(timeout_s=2.0)

    assert loop._running is False
    assert ran.is_set(), "il background task doveva completare prima del ritorno di shutdown()"
    assert not any(not t.done() for t in loop._background_tasks)


async def test_shutdown_cancels_slow_background_within_timeout(loop_factory) -> None:
    loop = loop_factory()

    async def slow() -> None:
        await asyncio.sleep(3600)

    loop._schedule_background(slow())

    # Non deve appendere: allo scadere del timeout i residui vengono cancellati.
    await asyncio.wait_for(loop.shutdown(timeout_s=0.05), timeout=2.0)

    assert loop._running is False
    assert not any(not t.done() for t in loop._background_tasks)
