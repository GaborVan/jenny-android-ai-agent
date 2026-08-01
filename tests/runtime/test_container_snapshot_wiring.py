"""Test del wiring snapshot nel ``GatewayContainer``.

Verifica i contratti di integrazione senza costruire il grafo completo:
il checkpoint pre-Dream delega a ``snapshot_now("pre_dream")``, lo snapshot
di shutdown avviene DOPO il flush delle sessioni ed è fail-open.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from jenny.runtime.container import GatewayContainer


class _RecordingSnapshot:
    """SnapshotService finto che registra le chiamate in una lista condivisa."""

    def __init__(self, order: list[str], *, fail_shutdown: bool = False) -> None:
        self._order = order
        self._fail_shutdown = fail_shutdown

    async def start(self) -> None:
        self._order.append("snapshot_start")

    def stop(self) -> None:
        self._order.append("snapshot_stop")

    async def snapshot_now(self, trigger: str, label: str | None = None):
        self._order.append(f"snapshot:{trigger}")
        if self._fail_shutdown and trigger == "shutdown":
            raise RuntimeError("snapshot di shutdown guasto")
        return None


class _FakeAgent:
    def __init__(self, order: list[str]) -> None:
        self._order = order
        self.sessions = SimpleNamespace(flush_all=self._flush)

    def _flush(self) -> int:
        self._order.append("flush_all")
        return 0

    async def run(self) -> None:
        pass

    async def shutdown(self) -> None:
        self._order.append("agent_shutdown")


class _FakeChannels:
    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        pass


def _bare_container(order: list[str], *, fail_shutdown: bool = False) -> GatewayContainer:
    """Container senza __init__: solo gli attributi che ``run()`` usa davvero."""
    container = GatewayContainer.__new__(GatewayContainer)
    container.cron = SimpleNamespace(start=AsyncMock(), stop=MagicMock())
    container.snapshot = _RecordingSnapshot(order, fail_shutdown=fail_shutdown)
    container.channels = _FakeChannels()
    container._agent = _FakeAgent(order)
    return container


async def test_snapshot_before_dream_triggers_pre_dream() -> None:
    order: list[str] = []
    container = GatewayContainer.__new__(GatewayContainer)
    container.snapshot = _RecordingSnapshot(order)
    await container._snapshot_before_dream()
    assert order == ["snapshot:pre_dream"]


async def test_snapshot_before_dream_noop_without_service() -> None:
    container = GatewayContainer.__new__(GatewayContainer)
    container.snapshot = None
    await container._snapshot_before_dream()  # nessun errore


async def test_run_takes_shutdown_snapshot_after_flush() -> None:
    """Lo snapshot finale deve fotografare le sessioni GIÀ flushate su disco."""
    order: list[str] = []
    container = _bare_container(order)
    await container.run()
    assert "snapshot:shutdown" in order
    assert order.index("flush_all") < order.index("snapshot:shutdown")
    # Il drain dell'agente precede il flush (nessun writer attivo durante il flush).
    assert order.index("agent_shutdown") < order.index("flush_all")
    assert order[-1] == "snapshot:shutdown"


async def test_run_shutdown_snapshot_failure_is_swallowed() -> None:
    """Uno snapshot di shutdown guasto non deve far fallire lo shutdown."""
    order: list[str] = []
    container = _bare_container(order, fail_shutdown=True)
    await container.run()  # non solleva
    assert order[-1] == "snapshot:shutdown"
