"""Subagent forwards loop-provided LLM wall-timeout resolver into AgentRunSpec."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from jenny.agent.runner import AgentRunResult
from jenny.agent.subagent import SubagentManager, SubagentStatus
from jenny.bus.queue import MessageBus


@pytest.mark.asyncio
async def test_subagent_forwards_resolver_to_agent_run_spec(tmp_path: Path) -> None:
    provider = MagicMock()
    provider.get_default_model.return_value = "m"
    mgr = SubagentManager(
        provider=provider,
        workspace=tmp_path,
        bus=MessageBus(),
        max_tool_result_chars=64,
        llm_wall_timeout_for_session=lambda sk: 0.0 if sk == "internal:direct" else None,
    )

    mgr.runner.run = AsyncMock(
        return_value=AgentRunResult(final_content="ok", messages=[], stop_reason="completed")
    )
    mgr._announce_result = AsyncMock()

    status = SubagentStatus(
        task_id="t1",
        label="lbl",
        task_description="task",
        started_at=0.0,
    )
    await mgr._run_subagent(
        "t1",
        "task",
        "lbl",
        {"channel": "internal", "chat_id": "direct", "session_key": "internal:direct"},
        status,
    )
    mgr.runner.run.assert_called_once()
    spec = mgr.runner.run.call_args[0][0]
    assert spec.session_key == "internal:direct"
    assert spec.llm_timeout_s == 0.0


def test_stale_goal_is_expired_at_first_turn_after_restart(tmp_path: Path) -> None:
    """Zombie goal (crash mid-goal) restores the LLM wall-timeout on the next turn.

    Reproduces the loop's start-of-turn sequence (``expire_stale_goal`` →
    ``runner_wall_llm_timeout_s``) against a real session that survived a restart with
    a goal stuck ``active`` and untouched for longer than the inactivity TTL.
    """
    from datetime import datetime, timedelta

    from jenny.session.goal_state import (
        GOAL_STATE_KEY,
        expire_stale_goal,
        runner_wall_llm_timeout_s,
    )
    from jenny.session.manager import SessionManager

    sm = SessionManager(tmp_path)
    sess = sm.get_or_create("unified:default")
    stale = (datetime.now() - timedelta(hours=48)).isoformat()
    sess.metadata = {GOAL_STATE_KEY: {"status": "active", "objective": "z", "started_at": stale}}

    # Before repair the zombie goal disables the wall-timeout for every future turn.
    assert runner_wall_llm_timeout_s(sm, "unified:default") == 0.0

    # Loop start-of-turn lazy repair (ttl 12h default).
    expired = expire_stale_goal(sess.metadata, ttl_h=12.0)
    assert expired is not None and expired["status"] == "expired"

    # Wiring loop -> timeout now yields the non-goal default again.
    assert runner_wall_llm_timeout_s(sm, "unified:default") is None
