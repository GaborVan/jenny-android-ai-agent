"""Il turno dell'agente tiene il wakelock per tutta la sua durata.

Il contratto non è "``power.py`` funziona" — quello lo copre
``tests/runtime/test_power.py`` — ma "il percorso del turno ci passa dentro".
Senza questo test la regressione tipica è muta: qualcuno riscrive ``_dispatch``,
il ``async with`` sparisce, tutti i test restano verdi e l'unico sintomo è un
telefono che smette di rispondere a schermo spento.

``keep_awake`` viene spiata al posto di essere eseguita: il vero context manager
è un no-op fuori da Android, quindi eseguirlo non proverebbe nulla.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest

from jenny.agent.loop import _TURN_WAKELOCK_TIMEOUT_S
from jenny.bus.events import InboundMessage


def _spy_keep_awake(monkeypatch: pytest.MonkeyPatch, module: str) -> list[tuple[str, str, float]]:
    """Sostituisce ``keep_awake`` in ``module`` e registra entrate/uscite."""
    events: list[tuple[str, str, float]] = []

    @asynccontextmanager
    async def fake_keep_awake(tag: str, *, timeout_s: float = 0.0) -> AsyncIterator[bool]:
        events.append(("enter", tag, timeout_s))
        try:
            yield True
        finally:
            events.append(("exit", tag, timeout_s))

    monkeypatch.setattr(f"{module}.keep_awake", fake_keep_awake)
    return events


def _msg(content: str = "ciao") -> InboundMessage:
    return InboundMessage(
        channel="websocket", sender_id="u", chat_id="chat-1", content=content
    )


async def test_the_turn_is_wrapped_in_a_wakelock(loop_factory, monkeypatch) -> None:
    events = _spy_keep_awake(monkeypatch, "jenny.agent.loop")
    loop = loop_factory()
    seen_inside: list[list[tuple[str, str, float]]] = []

    async def fake_process(msg, **kwargs):
        # Fotografia dello stato *mentre* il turno gira: il lock deve essere già
        # preso qui, non essere richiesto dopo che il lavoro è finito.
        seen_inside.append(list(events))
        return None

    loop._process_message = fake_process  # type: ignore[method-assign]

    await loop._dispatch(_msg())

    assert [e[0] for e in events] == ["enter", "exit"]
    assert events[0][1] == "turn"
    assert events[0][2] == _TURN_WAKELOCK_TIMEOUT_S
    assert seen_inside == [[("enter", "turn", _TURN_WAKELOCK_TIMEOUT_S)]]


async def test_a_turn_that_blows_up_still_leaves_the_block(loop_factory, monkeypatch) -> None:
    # ``_dispatch`` assorbe l'eccezione e risponde con un errore all'utente: il
    # rilascio non può dipendere dal fatto che il turno sia andato bene.
    events = _spy_keep_awake(monkeypatch, "jenny.agent.loop")
    loop = loop_factory()

    async def boom(msg, **kwargs):
        raise RuntimeError("provider exploded")

    loop._process_message = boom  # type: ignore[method-assign]

    await loop._dispatch(_msg())

    assert [e[0] for e in events] == ["enter", "exit"]
