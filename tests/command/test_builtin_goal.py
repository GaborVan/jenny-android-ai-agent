"""Regression tests for cmd_goal's active-task check (see builtin.py:cmd_goal)."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from jenny.bus.events import InboundMessage
from jenny.command.builtin import cmd_goal
from jenny.command.router import CommandContext


def _make_ctx(*, active_tasks, running_subagents=0, key="websocket:chat1"):
    msg = InboundMessage(channel="websocket", sender_id="u1", chat_id="chat1", content="/goal do X")
    loop = SimpleNamespace(
        _active_tasks={key: active_tasks},
        subagents=MagicMock(get_running_count_by_session=MagicMock(return_value=running_subagents)),
    )
    return CommandContext(msg=msg, session=None, key=key, raw="/goal do X", args="do X", loop=loop)


@pytest.mark.asyncio
async def test_goal_not_blocked_on_fresh_chat_with_no_active_tasks():
    ctx = _make_ctx(active_tasks=[])
    result = await cmd_goal(ctx)
    assert result is None
    assert ctx.msg.metadata["original_command"] == "/goal"


@pytest.mark.asyncio
async def test_goal_not_blocked_by_its_own_in_flight_task():
    """The task currently executing /goal registers itself in _active_tasks before
    running; cmd_goal must exclude asyncio.current_task() from the running count."""

    async def probe():
        current = asyncio.current_task()
        ctx = _make_ctx(active_tasks=[current])
        return await cmd_goal(ctx)

    result = await asyncio.create_task(probe())
    assert result is None


@pytest.mark.asyncio
async def test_goal_blocked_when_another_task_is_running():
    other_task = SimpleNamespace(done=lambda: False)
    ctx = _make_ctx(active_tasks=[other_task])
    result = await cmd_goal(ctx)
    assert result is not None
    assert "already running" in result.content


@pytest.mark.asyncio
async def test_goal_blocked_when_subagent_running_for_session():
    ctx = _make_ctx(active_tasks=[], running_subagents=1)
    result = await cmd_goal(ctx)
    assert result is not None
    assert "already running" in result.content
