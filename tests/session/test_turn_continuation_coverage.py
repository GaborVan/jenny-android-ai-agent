"""Copertura complementare per ``jenny.session.turn_continuation``.

``test_turn_continuation.py`` copre gia' il percorso felice di ``maybe_continue_turn``,
il round limit e le combinazioni principali di ``should_stream_budget_response`` /
``should_finalize_on_max_iterations``. Qui si aggiungono i rami rimasti scoperti:
guardie di ``maybe_continue_turn`` (sessione/coda assenti, stop_reason non da budget),
``should_persist_user_message``, ``prepare_save_boundary``/``clear_internal_continuation_state``,
il prompt di continuazione senza goal, e ``_strip_terminal_assistant``.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from jenny.bus.events import InboundMessage
from jenny.session.goal_state import GOAL_STATE_KEY
from jenny.session.turn_continuation import (
    INTERNAL_CONTINUATION_META,
    INTERNAL_CONTINUATION_PENDING_META,
    SKIP_USER_PERSIST_META,
    _goal_continuation_prompt,
    _internal_continuation_metadata,
    _strip_terminal_assistant,
    clear_internal_continuation_state,
    internal_continuation_inbound,
    internal_continuation_run_started_at,
    maybe_continue_turn,
    prepare_save_boundary,
    should_persist_user_message,
)


def _ctx(**overrides):
    base = dict(
        session=SimpleNamespace(metadata={GOAL_STATE_KEY: {"status": "active", "objective": "x"}}),
        msg=InboundMessage(channel="websocket", sender_id="u1", chat_id="c1", content="go"),
        session_key="websocket:c1",
        pending_queue=asyncio.Queue(),
        stop_reason="max_iterations",
        final_content="paused",
        all_messages=[],
        suppress_response=False,
        visible_run_started_at=None,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


# --- maybe_continue_turn guard clauses ----------------------------------------


async def test_maybe_continue_turn_false_without_session():
    ctx = _ctx(session=None)
    assert await maybe_continue_turn(ctx) is False


async def test_maybe_continue_turn_false_without_pending_queue():
    ctx = _ctx(pending_queue=None)
    assert await maybe_continue_turn(ctx) is False


async def test_maybe_continue_turn_false_when_stop_reason_not_budget():
    ctx = _ctx(stop_reason="completed")
    assert await maybe_continue_turn(ctx) is False
    assert ctx.pending_queue.empty()


async def test_maybe_continue_turn_false_when_goal_not_active():
    ctx = _ctx(session=SimpleNamespace(metadata={}))
    assert await maybe_continue_turn(ctx) is False


# --- internal_continuation_inbound --------------------------------------------


def test_internal_continuation_inbound_true_only_when_flag_is_exactly_true():
    assert internal_continuation_inbound({INTERNAL_CONTINUATION_META: True}) is True
    assert internal_continuation_inbound({INTERNAL_CONTINUATION_META: "true"}) is False
    assert internal_continuation_inbound({}) is False
    assert internal_continuation_inbound(None) is False


# --- should_persist_user_message -----------------------------------------------


def test_should_persist_user_message_false_when_skip_flag_set():
    assert should_persist_user_message({SKIP_USER_PERSIST_META: True}) is False


def test_should_persist_user_message_false_for_internal_continuation():
    assert should_persist_user_message({INTERNAL_CONTINUATION_META: True}) is False


def test_should_persist_user_message_true_for_ordinary_message():
    assert should_persist_user_message({}) is True
    assert should_persist_user_message(None) is True


# --- internal_continuation_run_started_at --------------------------------------


def test_run_started_at_none_when_missing_or_wrong_type():
    assert internal_continuation_run_started_at(None) is None
    assert internal_continuation_run_started_at({}) is None
    assert internal_continuation_run_started_at({"_internal_continuation_run_started_at": "x"}) is None


def test_run_started_at_none_when_not_positive():
    assert internal_continuation_run_started_at({"_internal_continuation_run_started_at": 0}) is None
    assert internal_continuation_run_started_at({"_internal_continuation_run_started_at": -5}) is None


def test_run_started_at_returns_positive_float():
    assert internal_continuation_run_started_at({"_internal_continuation_run_started_at": 12}) == 12.0


# --- prepare_save_boundary / clear_internal_continuation_state ------------------


def test_prepare_save_boundary_clears_continuation_rounds_when_goal_inactive():
    metadata = {"_sustained_goal_continuation_rounds": 3}
    ctx = SimpleNamespace(
        session=SimpleNamespace(metadata=metadata),
        msg=InboundMessage(channel="websocket", sender_id="u", chat_id="c", content="x"),
        initial_messages=[{"role": "system"}, {"role": "user", "content": "x"}],
        history=[],
        user_persisted_early=True,
    )
    prepare_save_boundary(ctx)
    assert "_sustained_goal_continuation_rounds" not in metadata
    assert ctx.save_skip == len(ctx.initial_messages)


def test_prepare_save_boundary_keeps_rounds_when_goal_still_active():
    metadata = {
        GOAL_STATE_KEY: {"status": "active", "objective": "x"},
        "_sustained_goal_continuation_rounds": 3,
    }
    ctx = SimpleNamespace(
        session=SimpleNamespace(metadata=metadata),
        msg=InboundMessage(channel="websocket", sender_id="u", chat_id="c", content="x"),
        initial_messages=[{"role": "system"}, {"role": "user", "content": "x"}],
        history=[],
        user_persisted_early=True,
    )
    prepare_save_boundary(ctx)
    assert metadata["_sustained_goal_continuation_rounds"] == 3


def test_prepare_save_boundary_noop_when_session_is_none():
    ctx = SimpleNamespace(
        session=None,
        msg=InboundMessage(channel="websocket", sender_id="u", chat_id="c", content="x"),
        initial_messages=[{"role": "system"}],
        history=[],
        user_persisted_early=False,
    )
    prepare_save_boundary(ctx)
    assert ctx.save_skip == len(ctx.initial_messages)


def test_clear_internal_continuation_state_direct_call():
    metadata = {"_sustained_goal_continuation_rounds": 5}
    clear_internal_continuation_state(metadata)
    assert "_sustained_goal_continuation_rounds" not in metadata

    metadata_active = {
        GOAL_STATE_KEY: {"status": "active", "objective": "x"},
        "_sustained_goal_continuation_rounds": 5,
    }
    clear_internal_continuation_state(metadata_active)
    assert metadata_active["_sustained_goal_continuation_rounds"] == 5


# --- _goal_continuation_prompt --------------------------------------------------


def test_goal_continuation_prompt_includes_objective_when_present():
    metadata = {GOAL_STATE_KEY: {"status": "active", "objective": "Ship it.", "ui_summary": "ship"}}
    prompt = _goal_continuation_prompt(metadata)
    assert "Ship it." in prompt
    assert "complete_goal" in prompt


def test_goal_continuation_prompt_falls_back_without_runtime_lines():
    # Nessun goal attivo -> goal_state_runtime_lines torna vuoto -> prompt generico.
    prompt = _goal_continuation_prompt({})
    assert "Continue from the saved context" in prompt
    assert "Ship it." not in prompt


# --- _internal_continuation_metadata --------------------------------------------


def test_internal_continuation_metadata_strips_stream_and_resume_keys():
    source = {
        "_stream_id": "s1",
        "_stream_delta": True,
        "_stream_end": True,
        "_resuming": True,
        INTERNAL_CONTINUATION_PENDING_META: True,
        "keep_me": "yes",
    }
    result = _internal_continuation_metadata(source, run_started_at=99.0)
    assert result[INTERNAL_CONTINUATION_META] is True
    assert result["_internal_continuation_run_started_at"] == 99.0
    assert result["keep_me"] == "yes"
    for stripped_key in ("_stream_id", "_stream_delta", "_stream_end", "_resuming", INTERNAL_CONTINUATION_PENDING_META):
        assert stripped_key not in result


def test_internal_continuation_metadata_omits_run_started_at_when_not_given():
    result = _internal_continuation_metadata(None)
    assert "_internal_continuation_run_started_at" not in result
    assert result[INTERNAL_CONTINUATION_META] is True


# --- _strip_terminal_assistant ---------------------------------------------------


def test_strip_terminal_assistant_returns_empty_list_unchanged():
    assert _strip_terminal_assistant([], "x") == []


def test_strip_terminal_assistant_keeps_messages_when_last_role_not_assistant():
    messages = [{"role": "user", "content": "hi"}]
    assert _strip_terminal_assistant(messages, "hi") == messages


def test_strip_terminal_assistant_keeps_messages_when_final_content_mismatch():
    messages = [{"role": "assistant", "content": "actual"}]
    assert _strip_terminal_assistant(messages, "different") == messages


def test_strip_terminal_assistant_keeps_messages_when_final_content_none():
    messages = [{"role": "assistant", "content": "actual"}]
    assert _strip_terminal_assistant(messages, None) == messages


def test_strip_terminal_assistant_keeps_messages_with_tool_calls():
    messages = [{"role": "assistant", "content": "actual", "tool_calls": [{"id": "1"}]}]
    assert _strip_terminal_assistant(messages, "actual") == messages


def test_strip_terminal_assistant_drops_matching_terminal_message():
    messages = [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "paused"},
    ]
    result = _strip_terminal_assistant(messages, "paused")
    assert result == messages[:-1]
