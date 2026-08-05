"""Contratto di filo dell'attività dei subagent: forma, tetti, registro.

Il modulo sotto test è puro (nessun socket, nessun task), quindi qui si pinnano
le due cose che un bug renderebbe invisibile a runtime: che ``gap`` sia sempre
riderivato dall'unica regola (quindi un troncamento non possa passare per
finestra integra) e che il registro dei watcher non possa crescere né
sopravvivere alla connessione che l'ha creato.
"""

from __future__ import annotations

from typing import Any

import pytest

from jenny.channels.subagent_activity_wire import (
    ACTIVITY_FRAME_EVENT,
    MAX_FRAME_EVENTS,
    MAX_WATCHES_PER_CONNECTION,
    SubagentWatchRegistry,
    activity_frame,
    digest_payload,
    empty_window_payload,
    normalize_since,
    normalize_task_id,
    slice_for_cursor,
    window_payload,
)


def _event(seq: int, **extra: Any) -> dict[str, Any]:
    event = {
        "seq": seq,
        "ts": 1785841304.0 + seq,
        "kind": "tool_start",
        "name": "grep",
        "status": None,
        "summary": f"grepping for pattern {seq}",
        "duration_ms": None,
    }
    event.update(extra)
    return event


class _Window:
    """Doppio di ``ActivityWindow``: stessa superficie (``to_dict``), zero import."""

    def __init__(self, events, *, since_seq=0, latest_seq=0, dropped=0) -> None:
        self._events = list(events)
        self._since = since_seq
        self._latest = latest_seq or (self._events[-1]["seq"] if self._events else 0)
        self._dropped = dropped

    def to_dict(self) -> dict[str, Any]:
        first = self._events[0]["seq"] if self._events else 0
        last = self._events[-1]["seq"] if self._events else 0
        return {
            "events": self._events,
            "since_seq": self._since,
            "first_seq": first,
            "last_seq": last,
            "latest_seq": self._latest,
            "dropped": self._dropped,
            "gap": first > self._since + 1,
        }


# -- normalizzazione degli input del client ----------------------------------


@pytest.mark.parametrize("raw", ["d2ee4342", "a", "a" * 64, "with-dash_and_underscore"])
def test_valid_task_ids_pass(raw: str) -> None:
    assert normalize_task_id(raw) == raw


@pytest.mark.parametrize(
    "raw",
    ["", "a" * 65, "a/b", "a\\b", "with space", "../etc/passwd", "ta\tb", None, 12, {}],
)
def test_invalid_task_ids_are_refused(raw: Any) -> None:
    assert normalize_task_id(raw) is None


def test_task_id_is_trimmed_not_sanitized() -> None:
    assert normalize_task_id("  d2ee4342  ") == "d2ee4342"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [(0, 0), (7, 7), ("7", 7), (" 7 ", 7), (None, 0), (-1, 0), ("x", 0), (True, 0), (10**12, 0)],
)
def test_since_normalization(raw: Any, expected: int) -> None:
    assert normalize_since(raw) == expected


# -- forma della finestra -----------------------------------------------------


def test_window_payload_is_the_identity_on_a_well_formed_window() -> None:
    window = _Window([_event(1), _event(2)], since_seq=0, latest_seq=2)
    assert window_payload(window) == window.to_dict()


def test_window_payload_accepts_a_plain_mapping() -> None:
    """Il percorso bus consegna un dict, non un ActivityWindow."""
    source = _Window([_event(5)], since_seq=4, latest_seq=5).to_dict()
    assert window_payload(source) == source


def test_window_payload_rejects_non_windows() -> None:
    assert window_payload(None) is None
    assert window_payload("nope") is None
    assert window_payload(["events"]) is None


def test_nothing_yet_is_not_a_gap() -> None:
    payload = window_payload(_Window([], since_seq=0))
    assert payload is not None
    assert payload["events"] == []
    assert payload["latest_seq"] == 0
    assert payload["gap"] is False


def test_eviction_from_the_ring_crosses_the_wire_as_a_gap() -> None:
    # Il client è a 0, il primo evento ancora nel ring è il 90: 89 eventi persi.
    payload = window_payload(_Window([_event(90), _event(91)], since_seq=0, dropped=89))
    assert payload is not None
    assert payload["gap"] is True
    assert payload["dropped"] == 89
    assert payload["latest_seq"] == 91


def test_truncation_by_the_frame_cap_also_shows_up_as_a_gap() -> None:
    """Il tetto del frame non può produrre una finestra che si dichiara integra."""
    events = [_event(i) for i in range(1, MAX_FRAME_EVENTS + 21)]
    payload = window_payload(_Window(events, since_seq=0), limit=MAX_FRAME_EVENTS)
    assert payload is not None
    assert len(payload["events"]) == MAX_FRAME_EVENTS
    # Tenuti i più recenti, e il buco è dichiarato.
    assert payload["last_seq"] == events[-1]["seq"]
    assert payload["gap"] is True
    assert payload["latest_seq"] == events[-1]["seq"]


def test_events_without_a_usable_seq_are_dropped() -> None:
    payload = window_payload({
        "events": [_event(1), {"kind": "phase"}, "garbage", {"seq": "2"}, _event(3)],
        "since_seq": 0, "latest_seq": 3, "dropped": 0, "gap": False,
    })
    assert payload is not None
    assert [e["seq"] for e in payload["events"]] == [1, 3]


def test_optional_call_id_survives_verbatim() -> None:
    payload = window_payload(_Window([_event(1, call_id="toolu_01")]))
    assert payload is not None
    assert payload["events"][0]["call_id"] == "toolu_01"


def test_empty_window_payload_keeps_the_client_cursor() -> None:
    assert empty_window_payload(12) == {
        "events": [], "since_seq": 12, "first_seq": 0, "last_seq": 0,
        "latest_seq": 0, "dropped": 0, "gap": False,
    }


# -- fette per-watcher --------------------------------------------------------


def test_slice_for_cursor_returns_only_what_is_new() -> None:
    payload = window_payload(_Window([_event(1), _event(2), _event(3)]))
    assert payload is not None
    fragment = slice_for_cursor(payload, 1)
    assert fragment is not None
    assert [e["seq"] for e in fragment["events"]] == [2, 3]
    assert fragment["since_seq"] == 1
    assert fragment["first_seq"] == 2
    assert fragment["last_seq"] == 3
    assert fragment["gap"] is False


def test_slice_for_cursor_is_none_when_nothing_is_new() -> None:
    payload = window_payload(_Window([_event(1), _event(2)]))
    assert payload is not None
    assert slice_for_cursor(payload, 2) is None
    assert slice_for_cursor(payload, 99) is None


def test_slice_keeps_the_gap_relative_to_its_own_cursor() -> None:
    payload = window_payload(_Window([_event(50), _event(51)], since_seq=0, dropped=49))
    assert payload is not None
    # Chi era a 49 non ha buchi; chi era a 0 sì, dalla stessa lettura del ring.
    behind = slice_for_cursor(payload, 0)
    aligned = slice_for_cursor(payload, 49)
    assert behind is not None and behind["gap"] is True
    assert aligned is not None and aligned["gap"] is False
    assert aligned["dropped"] == 49


# -- frame --------------------------------------------------------------------


def test_frame_is_flat_and_never_looks_like_a_chat_bubble() -> None:
    payload = window_payload(_Window([_event(1)]))
    assert payload is not None
    frame = activity_frame("d2ee4342", "default", payload)
    assert frame["event"] == ACTIVITY_FRAME_EVENT
    assert frame["chat_id"] == "default"
    assert frame["task_id"] == "d2ee4342"
    assert frame["events"] == payload["events"]
    for chat_field in ("text", "kind", "media", "media_urls"):
        assert chat_field not in frame
    assert "initial" not in frame


def test_initial_is_present_only_on_the_watch_reply() -> None:
    frame = activity_frame("d2ee4342", "default", empty_window_payload(0), initial=True)
    assert frame["initial"] is True


# -- digest -------------------------------------------------------------------


def test_digest_payload_reports_its_source() -> None:
    assert digest_payload([_event(1)], "digest")["source"] == "digest"
    assert digest_payload([_event(1)], "live")["count"] == 1


def test_an_empty_digest_is_never_labelled_with_a_source() -> None:
    """Zero eventi = niente blocco da espandere, qualunque cosa dica il chiamante."""
    assert digest_payload([], "digest") == {"events": [], "count": 0, "source": "none"}
    assert digest_payload("garbage", "live")["source"] == "none"


# -- registro dei watcher -----------------------------------------------------


class TestWatchRegistry:
    def test_starts_inert(self) -> None:
        registry = SubagentWatchRegistry()
        assert registry.active is False
        assert registry.tasks() == []
        assert registry.cursors("t1") == []
        assert registry.min_cursor("t1") == 0

    def test_watch_and_unwatch(self) -> None:
        registry = SubagentWatchRegistry()
        conn = object()
        assert registry.watch(conn, "t1") == []
        assert registry.active is True
        assert registry.is_watching(conn, "t1") is True
        assert registry.unwatch(conn, "t1") is True
        assert registry.active is False
        # Idempotente: una seconda unwatch non è un errore.
        assert registry.unwatch(conn, "t1") is False

    def test_a_second_watch_updates_the_cursor_instead_of_duplicating(self) -> None:
        registry = SubagentWatchRegistry()
        conn = object()
        registry.watch(conn, "t1", cursor=3)
        registry.watch(conn, "t1", cursor=9)
        assert registry.watch_count() == 1
        assert registry.cursors("t1") == [(conn, 9)]

    def test_two_connections_on_one_task_keep_separate_cursors(self) -> None:
        registry = SubagentWatchRegistry()
        a, b = object(), object()
        registry.watch(a, "t1", cursor=10)
        registry.watch(b, "t1", cursor=0)
        assert registry.min_cursor("t1") == 0
        assert dict(registry.cursors("t1")) == {a: 10, b: 0}
        # Una sola voce nel giro del pump, non due.
        assert registry.tasks() == ["t1"]

    def test_cursor_only_moves_forward(self) -> None:
        registry = SubagentWatchRegistry()
        conn = object()
        registry.watch(conn, "t1", cursor=5)
        registry.advance(conn, "t1", 9)
        registry.advance(conn, "t1", 2)
        assert registry.cursors("t1") == [(conn, 9)]

    def test_advance_on_an_unwatched_task_is_a_no_op(self) -> None:
        registry = SubagentWatchRegistry()
        conn = object()
        registry.advance(conn, "gone", 5)
        assert registry.active is False

    def test_the_per_connection_cap_evicts_the_oldest(self) -> None:
        registry = SubagentWatchRegistry()
        conn = object()
        for index in range(MAX_WATCHES_PER_CONNECTION):
            assert registry.watch(conn, f"t{index}") == []
        evicted = registry.watch(conn, "newest")
        assert evicted == ["t0"]
        assert registry.watch_count() == MAX_WATCHES_PER_CONNECTION
        assert registry.is_watching(conn, "newest") is True
        assert registry.is_watching(conn, "t0") is False
        # Il task sfrattato esce anche dal giro del pump.
        assert "t0" not in registry.tasks()

    def test_the_cap_never_evicts_the_watch_just_requested(self) -> None:
        registry = SubagentWatchRegistry(max_per_connection=1)
        conn = object()
        registry.watch(conn, "first")
        assert registry.watch(conn, "second") == ["first"]
        assert registry.is_watching(conn, "second") is True

    def test_forget_removes_everything_of_one_connection_only(self) -> None:
        registry = SubagentWatchRegistry()
        gone, alive = object(), object()
        registry.watch(gone, "shared")
        registry.watch(gone, "private")
        registry.watch(alive, "shared")

        assert sorted(registry.forget(gone)) == ["private", "shared"]

        assert registry.is_watching(gone, "shared") is False
        assert registry.cursors("shared") == [(alive, 0)]
        assert "private" not in registry.tasks()
        assert registry.active is True
        # Una seconda forget (cleanup chiamato due volte) non solleva.
        assert registry.forget(gone) == []

    def test_clear_makes_the_pump_gate_close(self) -> None:
        registry = SubagentWatchRegistry()
        registry.watch(object(), "t1")
        registry.clear()
        assert registry.active is False
        assert registry.watch_count() == 0
