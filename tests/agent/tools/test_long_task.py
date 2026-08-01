"""Tests for sustained goal tools (`long_task`, `complete_goal`)."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

from jenny.agent.loop import AgentLoop
from jenny.agent.tools.context import RequestContext
from jenny.agent.tools.long_task import (
    CompleteGoalTool,
    LongTaskTool,
)
from jenny.bus.queue import MessageBus
from jenny.session.goal_state import GOAL_STATE_KEY
from jenny.session.manager import SessionManager


def _tools(sm: SessionManager) -> tuple[LongTaskTool, CompleteGoalTool]:
    lt = LongTaskTool(sessions=sm)
    cg = CompleteGoalTool(sessions=sm)
    rc = RequestContext(
        channel="websocket",
        chat_id="c1",
        session_key="websocket:c1",
        metadata={},
    )
    lt.set_context(rc)
    cg.set_context(rc)
    return lt, cg


@pytest.mark.asyncio
async def test_long_task_records_goal_metadata(tmp_path):
    sm = SessionManager(tmp_path)
    lt, _cg = _tools(sm)

    out = await lt.execute(goal="Do the thing", ui_summary="thing")
    assert "Goal recorded" in out

    sess = sm.get_or_create("websocket:c1")
    blob = sess.metadata.get(GOAL_STATE_KEY)
    assert isinstance(blob, dict)
    assert blob["status"] == "active"
    assert blob["objective"] == "Do the thing"
    assert blob["ui_summary"] == "thing"


@pytest.mark.asyncio
async def test_long_task_rejects_second_active_goal(tmp_path):
    sm = SessionManager(tmp_path)
    lt, _cg = _tools(sm)

    await lt.execute(goal="First")
    out = await lt.execute(goal="Second")
    assert "already active" in out


@pytest.mark.asyncio
async def test_complete_goal_closes_active_goal(tmp_path):
    sm = SessionManager(tmp_path)
    lt, cg = _tools(sm)

    await lt.execute(goal="X")
    out = await cg.execute(recap="Done.")
    assert "marked complete" in out

    sess = sm.get_or_create("websocket:c1")
    blob = sess.metadata.get(GOAL_STATE_KEY)
    assert blob["status"] == "completed"
    assert blob["recap"] == "Done."


@pytest.mark.asyncio
async def test_goal_tools_keep_request_context_per_task(tmp_path):
    sm = SessionManager(tmp_path)
    lt = LongTaskTool(sessions=sm)
    cg = CompleteGoalTool(sessions=sm)
    ctx_a = RequestContext(channel="websocket", chat_id="a", session_key="websocket:a")
    ctx_b = RequestContext(channel="websocket", chat_id="b", session_key="websocket:b")

    lt.set_context(ctx_a)
    task_a = asyncio.create_task(lt.execute(goal="Goal A"))
    lt.set_context(ctx_b)
    task_b = asyncio.create_task(lt.execute(goal="Goal B"))
    await asyncio.gather(task_a, task_b)

    assert sm.get_or_create("websocket:a").metadata[GOAL_STATE_KEY]["objective"] == "Goal A"
    assert sm.get_or_create("websocket:b").metadata[GOAL_STATE_KEY]["objective"] == "Goal B"

    cg.set_context(ctx_a)
    done_a = asyncio.create_task(cg.execute(recap="Done A"))
    cg.set_context(ctx_b)
    done_b = asyncio.create_task(cg.execute(recap="Done B"))
    await asyncio.gather(done_a, done_b)

    assert sm.get_or_create("websocket:a").metadata[GOAL_STATE_KEY]["recap"] == "Done A"
    assert sm.get_or_create("websocket:b").metadata[GOAL_STATE_KEY]["recap"] == "Done B"


@pytest.mark.asyncio
async def test_goal_tools_context_isolated_across_tool_types(tmp_path):
    """LongTaskTool and CompleteGoalTool must not share routing context."""
    sm = SessionManager(tmp_path)
    lt = LongTaskTool(sessions=sm)
    cg = CompleteGoalTool(sessions=sm)
    ctx = RequestContext(channel="websocket", chat_id="a", session_key="websocket:a")

    lt.set_context(ctx)
    assert cg._request_ctx.get() is None

    cg.set_context(ctx)
    assert lt._request_ctx.get() is ctx
    assert cg._request_ctx.get() is ctx


@pytest.mark.asyncio
async def test_complete_goal_without_active_is_noop_message(tmp_path):
    sm = SessionManager(tmp_path)
    _lt, cg = _tools(sm)

    out = await cg.execute(recap="n/a")
    assert "No active" in out


@pytest.mark.asyncio
async def test_long_task_and_complete_goal_registered(tmp_path):
    bus = MessageBus()
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    loop = AgentLoop(bus=bus, provider=provider, workspace=tmp_path, model="test-model")

    lt = loop.tools.get("long_task")
    cg = loop.tools.get("complete_goal")
    assert lt is not None and lt.name == "long_task"
    assert cg is not None and cg.name == "complete_goal"
