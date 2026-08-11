"""Copertura per ``jenny.session.webui_turns``.

``tests/webui/test_webui_turn_helpers.py`` copre solo ``publish_turn_run_status``
(strip di timing). Qui si copre il resto del modulo: marcatura sessione WebUI,
pulizia titolo generato, selezione input per il titolo, generazione titolo end-to-end
(incluse le uscite anticipate), e ``WebuiTurnCoordinator`` — il fan-out degli eventi
runtime verso i messaggi WebSocket della WebUI.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from jenny.bus.events import InboundMessage
from jenny.bus.queue import MessageBus
from jenny.bus.runtime_events import (
    RuntimeEventBus,
    RuntimeEventContext,
    RuntimeModelChanged,
    SessionTurnStarted,
    TurnCompleted,
    TurnRunStatusChanged,
)
from jenny.cron.session_turns import CRON_HISTORY_META
from jenny.providers.base import LLMResponse
from jenny.session import webui_turns as wt
from jenny.session.keys import HEARTBEAT_SESSION_KEY
from jenny.session.manager import Session, SessionManager
from jenny.session.turn_visibility import silent_turn_metadata
from jenny.utils.llm_runtime import LLMRuntime

# --- mark_webui_session --------------------------------------------------------


def test_mark_webui_session_sets_metadata_when_opted_in():
    session = Session(key="websocket:c1")
    assert wt.mark_webui_session(session, {"webui": True}) is True
    assert session.metadata["webui"] is True


def test_mark_webui_session_noop_when_not_opted_in():
    session = Session(key="websocket:c1")
    assert wt.mark_webui_session(session, {"webui": False}) is False
    assert wt.mark_webui_session(session, {}) is False
    assert "webui" not in session.metadata


# --- clean_generated_title ------------------------------------------------------


def test_clean_generated_title_empty_input():
    assert wt.clean_generated_title(None) == ""
    assert wt.clean_generated_title("   ") == ""


def test_clean_generated_title_strips_prefix_and_quotes():
    assert wt.clean_generated_title('Title: "Fix the bug"') == "Fix the bug"
    assert wt.clean_generated_title("标题：修复缺陷") == "修复缺陷"


def test_clean_generated_title_collapses_whitespace_and_trailing_punctuation():
    assert wt.clean_generated_title("Plan   the   trip!") == "Plan the trip"
    assert wt.clean_generated_title("Fix bug.") == "Fix bug"


def test_clean_generated_title_truncates_long_titles():
    raw = "x" * 100
    cleaned = wt.clean_generated_title(raw)
    assert len(cleaned) == wt.TITLE_MAX_CHARS
    assert cleaned.endswith("…")


def test_clean_generated_title_strips_think_blocks():
    raw = "<think>internal reasoning</think>Final title"
    assert wt.clean_generated_title(raw) == "Final title"


# --- _title_inputs ---------------------------------------------------------------


def test_title_inputs_picks_first_user_and_assistant_text():
    session = Session(key="websocket:c1")
    session.messages = [
        {"role": "user", "content": "first question"},
        {"role": "assistant", "content": "first answer"},
        {"role": "user", "content": "second question"},
    ]
    user_text, assistant_text = wt._title_inputs(session)
    assert user_text == "first question"
    assert assistant_text == "first answer"


def test_title_inputs_skips_commands_and_cron_turns():
    session = Session(key="websocket:c1")
    session.messages = [
        {"role": "user", "content": "/stop", "_command": True},
        {"role": "user", "content": "cron ping", CRON_HISTORY_META: True},
        {"role": "user", "content": "real question"},
        {"role": "assistant", "content": "real answer"},
    ]
    user_text, assistant_text = wt._title_inputs(session)
    assert user_text == "real question"
    assert assistant_text == "real answer"


def test_title_inputs_ignores_non_string_or_blank_content():
    session = Session(key="websocket:c1")
    session.messages = [
        {"role": "user", "content": None},
        {"role": "user", "content": "   "},
        {"role": "user", "content": "actual text"},
    ]
    user_text, _assistant_text = wt._title_inputs(session)
    assert user_text == "actual text"


def test_title_inputs_returns_empty_when_no_messages():
    session = Session(key="websocket:c1")
    assert wt._title_inputs(session) == ("", "")


# --- maybe_generate_webui_title --------------------------------------------------


def _make_provider(content: str | None, *, raises: bool = False) -> MagicMock:
    provider = MagicMock()
    if raises:
        provider.chat_with_retry = AsyncMock(side_effect=RuntimeError("boom"))
    else:
        provider.chat_with_retry = AsyncMock(
            return_value=LLMResponse(content=content, finish_reason="stop"),
        )
    return provider


async def test_maybe_generate_title_false_when_not_webui_session(tmp_path):
    sessions = SessionManager(tmp_path)
    session = sessions.get_or_create("websocket:c1")
    session.messages = [{"role": "user", "content": "hi"}]
    provider = _make_provider("A title")

    generated = await wt.maybe_generate_webui_title(
        sessions=sessions,
        session_key="websocket:c1",
        provider=provider,
        model="m",
    )
    assert generated is False
    provider.chat_with_retry.assert_not_awaited()


async def test_maybe_generate_title_respects_user_edited_flag(tmp_path):
    sessions = SessionManager(tmp_path)
    session = sessions.get_or_create("websocket:c1")
    session.metadata[wt.WEBUI_SESSION_METADATA_KEY] = True
    session.metadata[wt.WEBUI_TITLE_USER_EDITED_METADATA_KEY] = True
    session.messages = [{"role": "user", "content": "hi"}]
    provider = _make_provider("A title")

    generated = await wt.maybe_generate_webui_title(
        sessions=sessions,
        session_key="websocket:c1",
        provider=provider,
        model="m",
    )
    assert generated is False
    provider.chat_with_retry.assert_not_awaited()


async def test_maybe_generate_title_false_and_unchanged_when_already_clean(tmp_path):
    sessions = SessionManager(tmp_path)
    session = sessions.get_or_create("websocket:c1")
    session.metadata[wt.WEBUI_SESSION_METADATA_KEY] = True
    session.metadata[wt.WEBUI_TITLE_METADATA_KEY] = "Already clean title"
    session.messages = [{"role": "user", "content": "hi"}]
    provider = _make_provider("ignored")

    generated = await wt.maybe_generate_webui_title(
        sessions=sessions,
        session_key="websocket:c1",
        provider=provider,
        model="m",
    )
    assert generated is False
    provider.chat_with_retry.assert_not_awaited()
    assert session.metadata[wt.WEBUI_TITLE_METADATA_KEY] == "Already clean title"


async def test_maybe_generate_title_normalizes_dirty_current_title_without_llm(tmp_path):
    sessions = SessionManager(tmp_path)
    session = sessions.get_or_create("websocket:c1")
    session.metadata[wt.WEBUI_SESSION_METADATA_KEY] = True
    session.metadata[wt.WEBUI_TITLE_METADATA_KEY] = "Title: messy title!!"
    session.messages = [{"role": "user", "content": "hi"}]
    provider = _make_provider("ignored")

    generated = await wt.maybe_generate_webui_title(
        sessions=sessions,
        session_key="websocket:c1",
        provider=provider,
        model="m",
    )
    assert generated is False
    provider.chat_with_retry.assert_not_awaited()
    assert session.metadata[wt.WEBUI_TITLE_METADATA_KEY] == "messy title"


async def test_maybe_generate_title_false_without_user_text(tmp_path):
    sessions = SessionManager(tmp_path)
    session = sessions.get_or_create("websocket:c1")
    session.metadata[wt.WEBUI_SESSION_METADATA_KEY] = True
    session.messages = []
    provider = _make_provider("A title")

    generated = await wt.maybe_generate_webui_title(
        sessions=sessions,
        session_key="websocket:c1",
        provider=provider,
        model="m",
    )
    assert generated is False
    provider.chat_with_retry.assert_not_awaited()


async def test_maybe_generate_title_success_persists_cleaned_title(tmp_path):
    sessions = SessionManager(tmp_path)
    session = sessions.get_or_create("websocket:c1")
    session.metadata[wt.WEBUI_SESSION_METADATA_KEY] = True
    session.messages = [
        {"role": "user", "content": "how do I fix this bug"},
        {"role": "assistant", "content": "here is the fix"},
    ]
    provider = _make_provider('"Fix the bug."')

    generated = await wt.maybe_generate_webui_title(
        sessions=sessions,
        session_key="websocket:c1",
        provider=provider,
        model="m",
    )
    assert generated is True
    provider.chat_with_retry.assert_awaited_once()
    assert session.metadata[wt.WEBUI_TITLE_METADATA_KEY] == "Fix the bug"
    # Persistito su disco: una nuova SessionManager legge lo stesso titolo.
    reloaded = SessionManager(tmp_path).get_or_create("websocket:c1")
    assert reloaded.metadata[wt.WEBUI_TITLE_METADATA_KEY] == "Fix the bug"


async def test_maybe_generate_title_false_on_provider_exception(tmp_path):
    sessions = SessionManager(tmp_path)
    session = sessions.get_or_create("websocket:c1")
    session.metadata[wt.WEBUI_SESSION_METADATA_KEY] = True
    session.messages = [{"role": "user", "content": "hi"}]
    provider = _make_provider(None, raises=True)

    generated = await wt.maybe_generate_webui_title(
        sessions=sessions,
        session_key="websocket:c1",
        provider=provider,
        model="m",
    )
    assert generated is False


async def test_maybe_generate_title_false_when_response_is_error_like(tmp_path):
    sessions = SessionManager(tmp_path)
    session = sessions.get_or_create("websocket:c1")
    session.metadata[wt.WEBUI_SESSION_METADATA_KEY] = True
    session.messages = [{"role": "user", "content": "hi"}]
    provider = _make_provider("Error calling LLM: boom")

    generated = await wt.maybe_generate_webui_title(
        sessions=sessions,
        session_key="websocket:c1",
        provider=provider,
        model="m",
    )
    assert generated is False


async def test_maybe_generate_title_false_when_response_empty(tmp_path):
    sessions = SessionManager(tmp_path)
    session = sessions.get_or_create("websocket:c1")
    session.metadata[wt.WEBUI_SESSION_METADATA_KEY] = True
    session.messages = [{"role": "user", "content": "hi"}]
    provider = _make_provider("   ")

    generated = await wt.maybe_generate_webui_title(
        sessions=sessions,
        session_key="websocket:c1",
        provider=provider,
        model="m",
    )
    assert generated is False


# --- maybe_generate_webui_title_after_turn ---------------------------------------


async def test_maybe_generate_title_after_turn_false_for_non_websocket_channel(tmp_path):
    sessions = SessionManager(tmp_path)
    session = sessions.get_or_create("cli:c1")
    session.metadata[wt.WEBUI_SESSION_METADATA_KEY] = True
    provider = _make_provider("A title")

    generated = await wt.maybe_generate_webui_title_after_turn(
        channel="cli",
        metadata={"webui": True},
        sessions=sessions,
        session_key="cli:c1",
        provider=provider,
        model="m",
    )
    assert generated is False
    provider.chat_with_retry.assert_not_awaited()


async def test_maybe_generate_title_after_turn_false_when_metadata_not_webui(tmp_path):
    sessions = SessionManager(tmp_path)
    provider = _make_provider("A title")

    generated = await wt.maybe_generate_webui_title_after_turn(
        channel="websocket",
        metadata={"webui": False},
        sessions=sessions,
        session_key="websocket:c1",
        provider=provider,
        model="m",
    )
    assert generated is False


async def test_maybe_generate_title_after_turn_delegates_when_eligible(tmp_path):
    sessions = SessionManager(tmp_path)
    session = sessions.get_or_create("websocket:c1")
    session.metadata[wt.WEBUI_SESSION_METADATA_KEY] = True
    session.messages = [{"role": "user", "content": "hello there"}]
    provider = _make_provider("Generated title")

    generated = await wt.maybe_generate_webui_title_after_turn(
        channel="websocket",
        metadata={"webui": True},
        sessions=sessions,
        session_key="websocket:c1",
        provider=provider,
        model="m",
    )
    assert generated is True
    provider.chat_with_retry.assert_awaited_once()


# --- WebuiTurnCoordinator ----------------------------------------------------------


def _coordinator(tmp_path) -> tuple[wt.WebuiTurnCoordinator, MessageBus, list]:
    bus = MagicMock()
    bus.publish_outbound = AsyncMock()
    sessions = SessionManager(tmp_path)
    scheduled: list = []

    def schedule_background(coro):
        scheduled.append(coro)

    coordinator = wt.WebuiTurnCoordinator(
        bus=bus,
        sessions=sessions,
        schedule_background=schedule_background,
    )
    return coordinator, bus, scheduled


def test_subscribe_registers_and_unsubscribe_removes_all_handlers(tmp_path):
    coordinator, _bus, _scheduled = _coordinator(tmp_path)
    runtime_events = RuntimeEventBus()

    unsubscribe = coordinator.subscribe(runtime_events)
    assert len(runtime_events._handlers) == 4

    unsubscribe()
    assert runtime_events._handlers == []


async def test_handle_session_turn_started_marks_webui_session(tmp_path):
    coordinator, _bus, _scheduled = _coordinator(tmp_path)
    ctx = RuntimeEventContext(
        channel="websocket",
        chat_id="c1",
        session_key="websocket:c1",
        metadata={"webui": True},
    )
    await coordinator._handle_session_turn_started(SessionTurnStarted(context=ctx))
    session = coordinator.sessions.get_or_create("websocket:c1")
    assert session.metadata["webui"] is True


async def test_handle_session_turn_started_ignores_non_websocket(tmp_path):
    coordinator, _bus, _scheduled = _coordinator(tmp_path)
    ctx = RuntimeEventContext(
        channel="cli",
        chat_id="c1",
        session_key="cli:c1",
        metadata={"webui": True},
    )
    await coordinator._handle_session_turn_started(SessionTurnStarted(context=ctx))
    # Nessuna sessione creata per un canale non websocket.
    assert "cli:c1" not in coordinator.sessions._cache


async def test_handle_run_status_changed_publishes_for_websocket_only(tmp_path):
    coordinator, bus, _scheduled = _coordinator(tmp_path)
    ctx = RuntimeEventContext(channel="websocket", chat_id="c1", session_key="websocket:c1")
    await coordinator._handle_run_status_changed(
        TurnRunStatusChanged(context=ctx, status="running", started_at=10.0),
    )
    bus.publish_outbound.assert_awaited_once()

    bus.publish_outbound.reset_mock()
    ctx_other = RuntimeEventContext(channel="cli", chat_id="c1", session_key="cli:c1")
    await coordinator._handle_run_status_changed(
        TurnRunStatusChanged(context=ctx_other, status="running"),
    )
    bus.publish_outbound.assert_not_awaited()


async def test_handle_runtime_model_changed_broadcasts(tmp_path):
    coordinator, bus, _scheduled = _coordinator(tmp_path)
    await coordinator._handle_runtime_model_changed(
        RuntimeModelChanged(model="m2", model_preset="fast", provider="prov-x"),
    )
    bus.publish_outbound.assert_awaited_once()
    call = bus.publish_outbound.await_args[0][0]
    assert call.chat_id == "*"
    assert call.metadata["model"] == "m2"
    assert call.metadata["model_preset"] == "fast"
    assert call.metadata["provider"] == "prov-x"


async def test_handle_turn_end_publishes_turn_end(tmp_path):
    coordinator, bus, _scheduled = _coordinator(tmp_path)
    msg = InboundMessage(channel="websocket", sender_id="u", chat_id="c1", content="hi")
    await coordinator.handle_turn_end(msg, latency_ms=120)

    bus.publish_outbound.assert_awaited_once()
    call = bus.publish_outbound.await_args[0][0]
    assert call.metadata["_turn_end"] is True
    assert call.metadata["latency_ms"] == 120


async def test_handle_turn_end_noop_for_non_websocket_channel(tmp_path):
    coordinator, bus, _scheduled = _coordinator(tmp_path)
    msg = InboundMessage(channel="cli", sender_id="u", chat_id="c1", content="hi")
    await coordinator.handle_turn_end(msg, latency_ms=None)
    bus.publish_outbound.assert_not_awaited()


async def test_handle_turn_completed_event_schedules_title_from_event_runtime(tmp_path):
    coordinator, bus, scheduled = _coordinator(tmp_path)

    session = coordinator.sessions.get_or_create("websocket:c1")
    session.metadata[wt.WEBUI_SESSION_METADATA_KEY] = True
    session.messages = [{"role": "user", "content": "hello there"}]

    provider = _make_provider("Event Title")
    runtime = LLMRuntime(provider=provider, model="m")
    ctx = RuntimeEventContext(
        channel="websocket",
        chat_id="c1",
        session_key="websocket:c1",
        metadata={"webui": True},
    )
    event = TurnCompleted(context=ctx, latency_ms=75, runtime=runtime)

    await coordinator._handle_turn_completed_event(event)

    assert len(scheduled) == 1
    await scheduled[0]
    assert session.metadata[wt.WEBUI_TITLE_METADATA_KEY] == "Event Title"

    # La notifica outbound al client deve annunciare l'aggiornamento della metadata di sessione.
    notify = bus.publish_outbound.await_args_list[-1][0][0]
    assert notify.metadata["_session_updated"] is True
    assert notify.metadata["_session_update_scope"] == "metadata"


async def test_handle_turn_completed_event_ignores_non_websocket(tmp_path):
    coordinator, bus, scheduled = _coordinator(tmp_path)
    ctx = RuntimeEventContext(channel="cli", chat_id="c1", session_key="cli:c1")
    event = TurnCompleted(context=ctx, latency_ms=None, runtime=None)

    await coordinator._handle_turn_completed_event(event)
    bus.publish_outbound.assert_not_awaited()
    assert scheduled == []


async def test_schedule_title_update_from_event_skips_when_runtime_not_llm_runtime(tmp_path):
    coordinator, bus, scheduled = _coordinator(tmp_path)
    ctx = RuntimeEventContext(
        channel="websocket",
        chat_id="c1",
        session_key="websocket:c1",
        metadata={"webui": True},
    )
    event = TurnCompleted(context=ctx, latency_ms=10, runtime="not-a-runtime")

    await coordinator._handle_turn_completed_event(event)
    assert scheduled == []


# --- proiezione dei turni esterni sulla vista WebUI --------------------------------


def _telegram_ctx(metadata: dict | None = None) -> RuntimeEventContext:
    return RuntimeEventContext(
        channel="telegram",
        chat_id="42",
        session_key="unified:default",
        metadata=metadata if metadata is not None else {"webui_turn_id": "t1"},
    )


def test_webui_view_target_routing():
    ws = RuntimeEventContext(channel="websocket", chat_id="c1", session_key="unified:default")
    assert wt.webui_view_target(ws) == ("websocket", "c1")
    assert wt.webui_view_target(_telegram_ctx()) == ("websocket", "default")
    internal = RuntimeEventContext(
        channel="internal", chat_id="x", session_key="unified:default"
    )
    assert wt.webui_view_target(internal) is None
    non_unified = RuntimeEventContext(
        channel="telegram", chat_id="42", session_key="cron:job1"
    )
    assert wt.webui_view_target(non_unified) is None


def test_a_silent_turn_has_no_webui_projection():
    """Il canale d'origine non basta a decidere.

    Un heartbeat o un cron monitor gira *su* ``websocket:default`` — è il target a
    cui consegnerà se una condizione scatta — ma spinner e ``_turn_end``
    appartengono alla conversazione dell'utente, non a un controllo che non ha
    chiesto. Senza questo gate ogni ciclo lasciava i propri marcatori in chat.
    """
    heartbeat = RuntimeEventContext(
        channel="websocket", chat_id="default", session_key=HEARTBEAT_SESSION_KEY
    )
    assert wt.webui_view_target(heartbeat) is None

    monitor = RuntimeEventContext(
        channel="websocket", chat_id="chat-1", session_key="cron:job-m"
    )
    assert wt.webui_view_target(monitor) is None

    marked = RuntimeEventContext(
        channel="websocket",
        chat_id="c1",
        session_key="unified:default",
        metadata=silent_turn_metadata(),
    )
    assert wt.webui_view_target(marked) is None


async def test_a_silent_turn_publishes_no_run_status(tmp_path):
    coordinator, bus, _scheduled = _coordinator(tmp_path)
    ctx = RuntimeEventContext(
        channel="websocket", chat_id="default", session_key=HEARTBEAT_SESSION_KEY
    )

    await coordinator._handle_run_status_changed(
        TurnRunStatusChanged(context=ctx, status="running", started_at=10.0),
    )
    await coordinator._handle_turn_completed_event(
        TurnCompleted(context=ctx, latency_ms=12, runtime=None),
    )

    bus.publish_outbound.assert_not_awaited()


async def test_external_turn_start_publishes_user_echo(tmp_path):
    coordinator, bus, _scheduled = _coordinator(tmp_path)
    event = SessionTurnStarted(context=_telegram_ctx(), content="ciao dal telefono")

    await coordinator._handle_session_turn_started(event)

    bus.publish_outbound.assert_awaited_once()
    call = bus.publish_outbound.await_args[0][0]
    assert call.channel == "websocket"
    assert call.chat_id == "default"
    assert call.content == "ciao dal telefono"
    assert call.metadata["_user_echo"] is True
    assert call.metadata["origin_channel"] == "telegram"
    assert call.metadata["webui_turn_id"] == "t1"


async def test_external_turn_start_skips_stop_and_empty(tmp_path):
    coordinator, bus, _scheduled = _coordinator(tmp_path)
    await coordinator._handle_session_turn_started(
        SessionTurnStarted(context=_telegram_ctx(), content="/stop")
    )
    await coordinator._handle_session_turn_started(
        SessionTurnStarted(context=_telegram_ctx(), content="   ")
    )
    bus.publish_outbound.assert_not_awaited()


async def test_external_turn_start_skips_internal_continuation(tmp_path):
    coordinator, bus, _scheduled = _coordinator(tmp_path)
    await coordinator._handle_session_turn_started(
        SessionTurnStarted(
            context=_telegram_ctx({"_internal_continuation": True}),
            content="continua il lavoro",
        )
    )
    bus.publish_outbound.assert_not_awaited()


async def test_external_turn_start_skips_internal_and_non_unified(tmp_path):
    coordinator, bus, _scheduled = _coordinator(tmp_path)
    internal = RuntimeEventContext(
        channel="internal", chat_id="x", session_key="unified:default"
    )
    await coordinator._handle_session_turn_started(
        SessionTurnStarted(context=internal, content="lavoro interno")
    )
    non_unified = RuntimeEventContext(
        channel="telegram", chat_id="42", session_key="cron:job1"
    )
    await coordinator._handle_session_turn_started(
        SessionTurnStarted(context=non_unified, content="x")
    )
    bus.publish_outbound.assert_not_awaited()


async def test_external_turn_completed_publishes_turn_end_on_view(tmp_path):
    coordinator, bus, _scheduled = _coordinator(tmp_path)
    event = TurnCompleted(context=_telegram_ctx(), latency_ms=99, runtime=None)

    await coordinator._handle_turn_completed_event(event)

    bus.publish_outbound.assert_awaited_once()
    call = bus.publish_outbound.await_args[0][0]
    assert call.channel == "websocket"
    assert call.chat_id == "default"
    assert call.metadata["_turn_end"] is True
    assert call.metadata["latency_ms"] == 99
    assert call.metadata["webui_turn_id"] == "t1"


async def test_external_run_status_projected_on_view(tmp_path):
    coordinator, bus, _scheduled = _coordinator(tmp_path)
    await coordinator._handle_run_status_changed(
        TurnRunStatusChanged(context=_telegram_ctx(), status="running", started_at=10.0),
    )
    bus.publish_outbound.assert_awaited_once()
    call = bus.publish_outbound.await_args[0][0]
    assert call.channel == "websocket"
    assert call.chat_id == "default"
    assert call.metadata["_goal_status"] is True
    assert call.metadata["goal_status"] == "running"


