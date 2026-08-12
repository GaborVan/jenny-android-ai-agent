"""Parcheggio di un goal sostenuto che aspetta una risposta dell'utente.

Lato loop il fix del 2026-08-12 ha tre pezzi:

1. quando il runner rifiuta di spronare il goal (``AgentRunResult.goal_stalled``)
   il goal viene marcato in attesa invece di restare a disposizione del modello,
   che ne usciva chiudendolo con un recap falso;
2. finché è in attesa, ``goal_active_predicate`` è False: nessun turno spende una
   chiamata LLM per ripetergli «continua»;
3. un messaggio vero dell'utente chiude l'attesa e il goal torna spronabile.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from jenny.agent.loop import AgentLoop
from jenny.agent.runner import AgentRunResult
from jenny.bus.queue import MessageBus
from jenny.providers.base import LLMResponse
from jenny.session.goal_state import GOAL_STATE_KEY, goal_awaiting_input
from jenny.session.turn_visibility import TURN_VISIBILITY_META, TurnVisibility


def _make_loop(tmp_path: Path) -> AgentLoop:
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    provider.generation = SimpleNamespace(max_tokens=4096)
    provider.chat_with_retry = AsyncMock(return_value=LLMResponse(content="ok"))
    return AgentLoop(bus=MessageBus(), provider=provider, workspace=tmp_path, model="test-model")


def _active_goal(loop: AgentLoop, key: str = "unified:default", **extra):
    session = loop.sessions.get_or_create(key)
    session.metadata[GOAL_STATE_KEY] = {
        "status": "active",
        "objective": "Create the shopping-list app.",
        # Necessario: senza timestamp il goal è un blob legacy e il lazy repair
        # a inizio turno (``expire_stale_goal``) lo fa scadere.
        "started_at": datetime.now().isoformat(),
        **extra,
    }
    loop.sessions.save(session)
    return session


@pytest.mark.asyncio
async def test_stalled_goal_is_parked_waiting_for_the_user(tmp_path: Path) -> None:
    loop = _make_loop(tmp_path)
    session = _active_goal(loop)

    async def fake_run(_spec):
        return AgentRunResult(
            final_content="cosa dovrebbe fare questa app?",
            messages=[{"role": "assistant", "content": "cosa dovrebbe fare questa app?"}],
            goal_stalled=True,
        )

    loop.runner.run = fake_run  # type: ignore[method-assign]

    await loop._run_agent_loop(
        [],
        session=session,
        channel="websocket",
        chat_id="default",
        session_key=session.key,
    )

    assert goal_awaiting_input(session.metadata) is True
    # Parcheggiato, non chiuso: è la differenza con il complete_goal di comodo.
    assert session.metadata[GOAL_STATE_KEY]["status"] == "active"


@pytest.mark.asyncio
async def test_goal_not_parked_when_runner_reports_progress(tmp_path: Path) -> None:
    loop = _make_loop(tmp_path)
    session = _active_goal(loop)

    async def fake_run(_spec):
        return AgentRunResult(
            final_content="fatto",
            messages=[{"role": "assistant", "content": "fatto"}],
        )

    loop.runner.run = fake_run  # type: ignore[method-assign]

    await loop._run_agent_loop(
        [],
        session=session,
        channel="websocket",
        chat_id="default",
        session_key=session.key,
    )

    assert goal_awaiting_input(session.metadata) is False


@pytest.mark.asyncio
async def test_parked_goal_is_not_nudged(tmp_path: Path) -> None:
    """Il predicate spegne le continuation sintetiche finché il goal aspetta."""
    loop = _make_loop(tmp_path)
    session = _active_goal(loop, awaiting_input=True, awaiting_since="2026-08-12T20:41:00")
    seen: dict[str, bool] = {}

    async def fake_run(spec):
        seen["active"] = spec.goal_active_predicate()
        return AgentRunResult(final_content="ok", messages=[])

    loop.runner.run = fake_run  # type: ignore[method-assign]

    await loop._run_agent_loop(
        [],
        session=session,
        channel="websocket",
        chat_id="default",
        session_key=session.key,
        # Turno interno: non è una risposta dell'utente, l'attesa non si chiude.
        metadata={TURN_VISIBILITY_META: TurnVisibility.SILENT.value},
    )

    assert seen["active"] is False
    assert goal_awaiting_input(session.metadata) is True


@pytest.mark.asyncio
async def test_user_reply_ends_the_wait_and_reactivates_nudges(tmp_path: Path) -> None:
    loop = _make_loop(tmp_path)
    session = _active_goal(loop, awaiting_input=True, awaiting_since="2026-08-12T20:41:00")
    seen: dict[str, bool] = {}

    async def fake_run(spec):
        seen["active"] = spec.goal_active_predicate()
        return AgentRunResult(final_content="ok", messages=[])

    loop.runner.run = fake_run  # type: ignore[method-assign]

    await loop._run_agent_loop(
        [],
        session=session,
        channel="websocket",
        chat_id="default",
        session_key=session.key,
        metadata={"message_id": "m1"},
    )

    assert goal_awaiting_input(session.metadata) is False
    assert seen["active"] is True
