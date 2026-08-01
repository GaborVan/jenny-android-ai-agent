"""Fase 1.5 — La race di injection è chiusa.

Prima: `run()` controllava `effective_key in self._pending_queues` ma `_dispatch`
registrava la coda solo DOPO aver preso il lock; un secondo messaggio arrivato
nella finestra tra `create_task` e la registrazione generava un turno
competitivo invece di essere iniettato. Ora `run()` registra la coda PRIMA di
`create_task` (atomico nel consumer singolo) → il follow-up viene iniettato.
"""

from __future__ import annotations

import asyncio
from contextlib import suppress

from jenny.bus.events import InboundMessage


def _msg(content: str) -> InboundMessage:
    return InboundMessage(
        channel="websocket", sender_id="u", chat_id="chat-1", content=content
    )


async def test_second_message_is_injected_not_a_competing_task(loop_factory) -> None:
    loop = loop_factory()

    call_count = 0
    first_running = asyncio.Event()
    release = asyncio.Event()
    injected: list[str] = []

    async def fake_process(msg, *, pending_queue=None, **kwargs):
        nonlocal call_count
        call_count += 1
        first_running.set()
        await release.wait()
        while pending_queue is not None and not pending_queue.empty():
            injected.append(pending_queue.get_nowait().content)
        return None

    loop._process_message = fake_process  # type: ignore[method-assign]
    loop._running = True

    run_task = asyncio.create_task(loop.run())
    try:
        await loop.bus.publish_inbound(_msg("first"))
        await asyncio.wait_for(first_running.wait(), timeout=1.0)

        # Secondo messaggio per la stessa sessione mentre il primo è "in corso".
        await loop.bus.publish_inbound(_msg("second"))
        await asyncio.sleep(0.05)  # dà a run() il tempo di instradarlo

        # Nessun turno competitivo: _process_message chiamato una sola volta.
        assert call_count == 1

        release.set()
        await asyncio.sleep(0.05)

        # Il follow-up è stato iniettato nella pending-queue del turno attivo.
        assert injected == ["second"]
    finally:
        loop.stop()
        run_task.cancel()
        with suppress(asyncio.CancelledError):
            await run_task
