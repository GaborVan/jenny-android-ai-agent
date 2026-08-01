"""Tests for the ui_query RPC coordinator (server→client, ui_view tool)."""

from __future__ import annotations

import asyncio

import pytest

from jenny.channels.ui_query import (
    MAX_PAYLOAD_BYTES,
    UiQueryCoordinator,
    UiQueryTimeoutError,
    UiQueryUnavailableError,
)


class _FakeChannel:
    """Cattura le send_ui_query e permette di scoprire il correlation_id emesso."""

    def __init__(self, *, deliver: bool = True):
        self.sent: list[tuple[str, str]] = []
        self._deliver = deliver

    async def send_ui_query(self, conn_id: str, correlation_id: str) -> bool:
        self.sent.append((conn_id, correlation_id))
        return self._deliver


def _coordinator(deliver: bool = True) -> tuple[UiQueryCoordinator, _FakeChannel]:
    coord = UiQueryCoordinator()
    channel = _FakeChannel(deliver=deliver)
    coord.set_channel(channel)
    return coord, channel


async def test_query_resolves_on_result():
    coord, channel = _coordinator()
    task = asyncio.create_task(coord.query("conn-1", timeout_s=2.0))
    await asyncio.sleep(0)  # lascia partire la send
    _, cid = channel.sent[0]
    coord.handle_ui_result("conn-1", {"correlation_id": cid, "payload": {"view": "wiki"}})
    result = await task
    assert result == {"view": "wiki"}


async def test_query_times_out():
    coord, _ = _coordinator()
    with pytest.raises(UiQueryTimeoutError):
        await coord.query("conn-1", timeout_s=0.05)


async def test_query_unavailable_when_not_delivered():
    coord, _ = _coordinator(deliver=False)
    with pytest.raises(UiQueryUnavailableError):
        await coord.query("conn-1", timeout_s=1.0)


async def test_query_unavailable_without_channel():
    coord = UiQueryCoordinator()  # nessun canale collegato
    with pytest.raises(UiQueryUnavailableError):
        await coord.query("conn-1", timeout_s=1.0)


async def test_result_from_wrong_conn_is_ignored():
    coord, channel = _coordinator()
    task = asyncio.create_task(coord.query("conn-1", timeout_s=0.2))
    await asyncio.sleep(0)
    _, cid = channel.sent[0]
    # Una risposta da un'altra connessione non deve risolvere la Future.
    coord.handle_ui_result("conn-EVIL", {"correlation_id": cid, "payload": {"x": 1}})
    with pytest.raises(UiQueryTimeoutError):
        await task


async def test_duplicate_result_is_ignored():
    coord, channel = _coordinator()
    task = asyncio.create_task(coord.query("conn-1", timeout_s=2.0))
    await asyncio.sleep(0)
    _, cid = channel.sent[0]
    coord.handle_ui_result("conn-1", {"correlation_id": cid, "payload": {"n": 1}})
    # Il duplicato non deve sollevare né sovrascrivere.
    coord.handle_ui_result("conn-1", {"correlation_id": cid, "payload": {"n": 2}})
    assert await task == {"n": 1}


async def test_invalid_correlation_id_dropped():
    coord, _ = _coordinator()
    # Non deve sollevare: correlation_id malformato ignorato.
    coord.handle_ui_result("conn-1", {"correlation_id": "bad id!", "payload": {}})
    coord.handle_ui_result("conn-1", {"correlation_id": 123, "payload": {}})


async def test_error_reply_raises_unavailable():
    coord, channel = _coordinator()
    task = asyncio.create_task(coord.query("conn-1", timeout_s=2.0))
    await asyncio.sleep(0)
    _, cid = channel.sent[0]
    coord.handle_ui_result("conn-1", {"correlation_id": cid, "error": "collect_failed"})
    with pytest.raises(UiQueryUnavailableError):
        await task


async def test_oversized_payload_rejected():
    coord, channel = _coordinator()
    task = asyncio.create_task(coord.query("conn-1", timeout_s=2.0))
    await asyncio.sleep(0)
    _, cid = channel.sent[0]
    big = {"html": "x" * (MAX_PAYLOAD_BYTES + 1)}
    coord.handle_ui_result("conn-1", {"correlation_id": cid, "payload": big})
    with pytest.raises(UiQueryUnavailableError):
        await task


async def test_cancel_for_conn_fails_pending():
    coord, channel = _coordinator()
    task = asyncio.create_task(coord.query("conn-1", timeout_s=2.0))
    await asyncio.sleep(0)
    coord.cancel_for_conn("conn-1")
    with pytest.raises(UiQueryUnavailableError):
        await task
