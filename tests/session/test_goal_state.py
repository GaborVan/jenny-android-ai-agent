"""Tests for ``goal_state`` session metadata helpers."""

from __future__ import annotations

from datetime import datetime, timedelta

from jenny.session.goal_state import (
    GOAL_STATE_KEY,
    cancel_active_goal,
    expire_stale_goal,
    goal_state_runtime_lines,
    note_goal_turn,
    parse_goal_state,
    runner_wall_llm_timeout_s,
    sustained_goal_active,
)
from jenny.session.manager import SessionManager


def test_runtime_lines_empty_when_no_metadata():
    assert goal_state_runtime_lines(None) == []
    assert goal_state_runtime_lines({}) == []


def test_runtime_lines_empty_when_completed():
    meta = {
        GOAL_STATE_KEY: {"status": "completed", "objective": "was doing X"},
    }
    assert goal_state_runtime_lines(meta) == []


def test_runtime_lines_include_objective_when_active():
    meta = {
        GOAL_STATE_KEY: {
            "status": "active",
            "objective": "Ship the fix.",
            "ui_summary": "fix",
        },
    }
    lines = goal_state_runtime_lines(meta)
    assert "Goal (active):" in lines
    assert "Ship the fix." in lines
    assert any("Summary: fix" in ln for ln in lines)


def test_parse_goal_state_accepts_json_string():
    assert parse_goal_state('{"status":"active","objective":"x"}') == {
        "status": "active",
        "objective": "x",
    }


def test_sustained_goal_active_false_when_missing_or_completed():
    assert sustained_goal_active(None) is False
    assert sustained_goal_active({}) is False
    assert sustained_goal_active({GOAL_STATE_KEY: {"status": "completed", "objective": "x"}}) is False


def test_sustained_goal_active_true_when_active():
    meta = {GOAL_STATE_KEY: {"status": "active", "objective": "Run long task."}}
    assert sustained_goal_active(meta) is True


def test_runner_wall_llm_timeout_uses_metadata_override(tmp_path):
    sm = SessionManager(tmp_path)
    assert (
        runner_wall_llm_timeout_s(
            sm,
            "internal:test",
            metadata={GOAL_STATE_KEY: {"status": "active", "objective": "x"}},
        )
        == 0.0
    )
    assert runner_wall_llm_timeout_s(sm, "internal:test", metadata={}) is None


def test_runner_wall_llm_timeout_reads_session_when_metadata_missing(tmp_path):
    sm = SessionManager(tmp_path)
    sess = sm.get_or_create("c:d")
    sess.metadata = {GOAL_STATE_KEY: {"status": "active", "objective": "z"}}
    assert runner_wall_llm_timeout_s(sm, "c:d") == 0.0
    sess.metadata = {}
    assert runner_wall_llm_timeout_s(sm, "c:d") is None


def test_cancel_active_goal_resets_active_to_cancelled():
    meta = {GOAL_STATE_KEY: {"status": "active", "objective": "x"}}
    updated = cancel_active_goal(meta)
    assert updated is not None
    assert updated["status"] == "cancelled"
    assert "cancelled_at" in updated
    # Objective/other fields are preserved for later recap/debugging.
    assert updated["objective"] == "x"
    assert meta[GOAL_STATE_KEY] == updated
    assert sustained_goal_active(meta) is False


def test_cancel_active_goal_noop_when_not_active():
    assert cancel_active_goal(None) is None
    assert cancel_active_goal({}) is None
    meta = {GOAL_STATE_KEY: {"status": "completed", "objective": "x"}}
    assert cancel_active_goal(meta) is None
    assert meta[GOAL_STATE_KEY]["status"] == "completed"


def test_cancel_active_goal_reenables_timeout(tmp_path):
    sm = SessionManager(tmp_path)
    sess = sm.get_or_create("c:e")
    sess.metadata = {GOAL_STATE_KEY: {"status": "active", "objective": "x"}}
    assert runner_wall_llm_timeout_s(sm, "c:e") == 0.0

    cancel_active_goal(sess.metadata)

    assert runner_wall_llm_timeout_s(sm, "c:e") is None


def test_expire_stale_goal_marks_inactive_goal_expired(tmp_path):
    now = datetime(2026, 7, 16, 12, 0, 0)
    stale = (now - timedelta(hours=13)).isoformat()
    meta = {GOAL_STATE_KEY: {"status": "active", "objective": "x", "started_at": stale}}
    # Zombie goal disables the wall-timeout before repair.
    assert runner_wall_llm_timeout_s(SessionManager(tmp_path), None, metadata=meta) == 0.0

    updated = expire_stale_goal(meta, ttl_h=12.0, now=now)

    assert updated is not None
    assert updated["status"] == "expired"
    assert "expired_at" in updated
    assert updated["objective"] == "x"
    assert meta[GOAL_STATE_KEY]["status"] == "expired"
    assert sustained_goal_active(meta) is False
    # Wall-timeout falls back to the non-goal value (None => JENNY_LLM_TIMEOUT_S).
    assert runner_wall_llm_timeout_s(SessionManager(tmp_path), None, metadata=meta) is None


def test_expire_stale_goal_keeps_recent_goal_intact():
    now = datetime(2026, 7, 16, 12, 0, 0)
    started = (now - timedelta(hours=30)).isoformat()  # old start, but recent activity
    recent_turn = (now - timedelta(minutes=2)).isoformat()
    meta = {
        GOAL_STATE_KEY: {
            "status": "active",
            "objective": "x",
            "started_at": started,
            "last_turn_at": recent_turn,
        }
    }
    assert expire_stale_goal(meta, ttl_h=12.0, now=now) is None
    assert meta[GOAL_STATE_KEY]["status"] == "active"
    assert sustained_goal_active(meta) is True


def test_expire_stale_goal_expires_legacy_blob_without_timestamps():
    now = datetime(2026, 7, 16, 12, 0, 0)
    meta = {GOAL_STATE_KEY: {"status": "active", "objective": "x"}}
    updated = expire_stale_goal(meta, ttl_h=12.0, now=now)
    assert updated is not None
    assert updated["status"] == "expired"
    assert sustained_goal_active(meta) is False


def test_expire_stale_goal_noop_when_not_active():
    now = datetime(2026, 7, 16, 12, 0, 0)
    assert expire_stale_goal(None, ttl_h=12.0, now=now) is None
    assert expire_stale_goal({}, ttl_h=12.0, now=now) is None
    meta = {GOAL_STATE_KEY: {"status": "completed", "objective": "x"}}
    assert expire_stale_goal(meta, ttl_h=12.0, now=now) is None
    assert meta[GOAL_STATE_KEY]["status"] == "completed"


def test_note_goal_turn_stamps_last_turn_at_and_defers_expiry():
    now = datetime(2026, 7, 16, 12, 0, 0)
    stale = (now - timedelta(hours=13)).isoformat()
    meta = {GOAL_STATE_KEY: {"status": "active", "objective": "x", "started_at": stale}}

    # A completed goal turn refreshes activity...
    updated = note_goal_turn(meta, now=now)
    assert updated is not None
    assert updated["last_turn_at"] == now.isoformat()

    # ...so the goal is no longer considered stale on the next turn.
    assert expire_stale_goal(meta, ttl_h=12.0, now=now + timedelta(minutes=1)) is None
    assert sustained_goal_active(meta) is True


def test_note_goal_turn_noop_when_not_active():
    assert note_goal_turn(None) is None
    meta = {GOAL_STATE_KEY: {"status": "completed", "objective": "x"}}
    assert note_goal_turn(meta) is None
    assert "last_turn_at" not in meta[GOAL_STATE_KEY]
