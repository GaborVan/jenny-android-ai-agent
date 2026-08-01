"""Fase 1.4 — Unit test del lock rientrante per-task e del registro."""

from __future__ import annotations

import asyncio

from jenny.agent.session_locks import ReentrantSessionLock, SessionLocks


def test_registry_returns_same_lock_per_key() -> None:
    locks = SessionLocks()
    assert locks.get("a") is locks.get("a")
    assert locks.get("a") is not locks.get("b")


def test_registry_evict_only_when_unheld() -> None:
    locks = SessionLocks()
    lock = locks.get("a")
    assert locks.is_locked("a") is False
    locks.evict("a")
    # dopo evict, get() crea un nuovo oggetto
    assert locks.get("a") is not lock


async def test_registry_evict_noop_while_held() -> None:
    locks = SessionLocks()
    lock = locks.get("a")
    async with lock:
        assert locks.is_locked("a") is True
        locks.evict("a")  # no-op perché tenuto
        assert locks.get("a") is lock


async def test_same_task_reenters() -> None:
    lock = ReentrantSessionLock()
    async with lock:
        assert lock.locked()
        async with lock:  # rientro nello stesso task
            async with lock:
                pass
        assert lock.locked()  # ancora tenuto al primo livello
    assert not lock.locked()  # rilasciato a profondità 0


async def test_different_tasks_are_mutually_exclusive() -> None:
    lock = ReentrantSessionLock()
    order: list[str] = []
    a_holds = asyncio.Event()
    a_release = asyncio.Event()

    async def task_a() -> None:
        async with lock:
            order.append("a:enter")
            a_holds.set()
            await a_release.wait()
            order.append("a:exit")

    async def task_b() -> None:
        await a_holds.wait()
        async with lock:
            order.append("b:enter")

    a = asyncio.create_task(task_a())
    b = asyncio.create_task(task_b())
    await asyncio.wait_for(a_holds.wait(), timeout=1.0)
    await asyncio.sleep(0.02)
    assert "b:enter" not in order  # B bloccato finché A tiene il lock

    a_release.set()
    await asyncio.wait_for(asyncio.gather(a, b), timeout=1.0)
    assert order == ["a:enter", "a:exit", "b:enter"]


async def test_lock_released_on_exception() -> None:
    lock = ReentrantSessionLock()
    try:
        async with lock:
            raise ValueError("boom")
    except ValueError:
        pass
    assert not lock.locked()


class TestRotate:
    """rotate(): sostituzione del lock orfano di un turno abbandonato."""

    async def test_rotate_replaces_held_lock(self):
        locks = SessionLocks()
        old = locks.get("k")
        acquired = asyncio.Event()
        release = asyncio.Event()

        async def holder():
            async with old:
                acquired.set()
                await release.wait()

        task = asyncio.create_task(holder())
        await acquired.wait()

        assert locks.rotate("k") is True
        new = locks.get("k")
        assert new is not old
        assert not new.locked()
        release.set()
        await task

    async def test_rotate_noop_when_unheld(self):
        locks = SessionLocks()
        lock = locks.get("k")
        assert locks.rotate("k") is False
        assert locks.get("k") is lock

    async def test_rotate_noop_when_absent(self):
        locks = SessionLocks()
        assert locks.rotate("missing") is False
