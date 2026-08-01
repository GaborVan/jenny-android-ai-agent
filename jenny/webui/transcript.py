"""Append-only WebUI display transcript (JSONL), separate from agent session."""

from __future__ import annotations

import base64
import binascii
import json
from typing import Any, Callable, NamedTuple

from jenny.cron.session_turns import CRON_HISTORY_META

# Re-export per gli importatori esterni (media_gateway, ecc.). L'alias ridondante
# segnala a ruff che è un re-export intenzionale (non un import inutilizzato).
from jenny.webui.transcript_markdown import (
    rewrite_local_markdown_images as rewrite_local_markdown_images,
)
from jenny.webui.transcript_recorder import (
    WebUITranscriptRecorder as WebUITranscriptRecorder,
)

# Recorder layer. `_build_user_transcript_event` ha un chiamante interno
# (`_session_user_event`); `WebUITranscriptRecorder` è re-export per i channel.
from jenny.webui.transcript_recorder import (
    _build_user_transcript_event,
)
from jenny.webui.transcript_replay import replay_transcript_to_ui_messages

# Store layer (persistenza JSONL/segmenti). Re-export: gli importatori esterni e i
# test continuano a leggere questi simboli da ``webui.transcript``. L'alias ridondante
# marca i re-export che non hanno un chiamante interno (ruff non li pota).
from jenny.webui.transcript_store import (
    _TRANSCRIPT_ACTIVE_CHUNK_ID,
    _is_user_transcript_row,
    _read_chunk_turns,
    _read_segment_manifest_entries,
    _rebuild_segment_manifest,
    _rotate_active_transcript_if_needed,
    _split_transcript_turns,
    read_transcript_lines,
    webui_transcript_path,
)
from jenny.webui.transcript_store import (
    append_transcript_object as append_transcript_object,
)
from jenny.webui.transcript_store import (
    delete_webui_transcript as delete_webui_transcript,
)
from jenny.webui.transcript_store import (
    webui_transcript_segments_dir as webui_transcript_segments_dir,
)

WEBUI_TRANSCRIPT_SCHEMA_VERSION = 3
_WEBUI_FORK_MARKER_EVENT = "fork_marker"
_DEFAULT_TRANSCRIPT_PAGE_LIMIT = 160
_MAX_TRANSCRIPT_PAGE_LIMIT = 1000
_TURN_DISPLAY_EVENTS: frozenset[str] = frozenset({
    "reasoning_delta",
    "reasoning_end",
    "delta",
    "stream_end",
    "message",
    "file_edit",
    "turn_end",
})



class _TranscriptTurnRef(NamedTuple):
    ordinal: int
    records: list[dict[str, Any]]


class _TranscriptChunkRef(NamedTuple):
    chunk_id: str
    start_ordinal: int
    turn_count: int
    user_count: int



def _encode_page_cursor(before_turn_ordinal: int) -> str:
    raw = json.dumps(
        {"before_turn": before_turn_ordinal},
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _decode_page_cursor(value: str | None) -> int | None:
    if not value:
        return None
    try:
        padded = value + "=" * (-len(value) % 4)
        data = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8"))
    except (binascii.Error, json.JSONDecodeError, UnicodeDecodeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    before_turn = data.get("before_turn")
    if (
        isinstance(before_turn, bool)
        or not isinstance(before_turn, int)
        or before_turn < 0
    ):
        return None
    return before_turn


def _coerce_page_limit(limit: int | None) -> int:
    if limit is None:
        return _DEFAULT_TRANSCRIPT_PAGE_LIMIT
    return max(1, min(_MAX_TRANSCRIPT_PAGE_LIMIT, int(limit)))


def _chunk_turn_refs(session_key: str) -> list[_TranscriptChunkRef]:
    _rotate_active_transcript_if_needed(session_key)
    refs: list[_TranscriptChunkRef] = []
    ordinal = 0
    for entry in _read_segment_manifest_entries(session_key):
        chunk_id = str(entry["id"])
        turn_count = int(entry["turn_count"])
        if turn_count <= 0:
            continue
        refs.append(_TranscriptChunkRef(chunk_id, ordinal, turn_count, int(entry["user_count"])))
        ordinal += turn_count
    if webui_transcript_path(session_key).is_file():
        active_turns = _read_chunk_turns(session_key, _TRANSCRIPT_ACTIVE_CHUNK_ID)
        active_turn_count = len(active_turns)
        if active_turn_count > 0:
            refs.append(
                _TranscriptChunkRef(
                    _TRANSCRIPT_ACTIVE_CHUNK_ID,
                    ordinal,
                    active_turn_count,
                    sum(1 for turn in active_turns for row in turn if _is_user_transcript_row(row)),
                ),
            )
    return refs


def _count_user_messages_before_ordinal(
    session_key: str,
    chunks: list[_TranscriptChunkRef],
    before_ordinal: int,
) -> int:
    total = 0
    for chunk in chunks:
        if before_ordinal <= chunk.start_ordinal:
            break
        local_end = min(chunk.turn_count, before_ordinal - chunk.start_ordinal)
        if local_end <= 0:
            continue
        if local_end >= chunk.turn_count:
            total += chunk.user_count
            continue
        turns = _read_chunk_turns(session_key, chunk.chunk_id)
        total += sum(
            1
            for turn in turns[:local_end]
            for row in turn
            if _is_user_transcript_row(row)
        )
    return total


def _select_transcript_page(
    session_key: str,
    *,
    limit: int | None,
    before: str | None,
    _manifest_rebuilt: bool = False,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    page_limit = _coerce_page_limit(limit)
    chunks = _chunk_turn_refs(session_key)
    total_turns = sum(chunk.turn_count for chunk in chunks)
    before_ordinal = _decode_page_cursor(before)
    upper_ordinal = total_turns if before_ordinal is None else min(before_ordinal, total_turns)
    selected: list[_TranscriptTurnRef] = []
    selected_message_count = 0

    for chunk in reversed(chunks):
        if chunk.start_ordinal >= upper_ordinal:
            continue
        local_upper = min(chunk.turn_count, upper_ordinal - chunk.start_ordinal)
        if local_upper <= 0:
            continue
        turns = _read_chunk_turns(session_key, chunk.chunk_id)
        if (
            chunk.chunk_id != _TRANSCRIPT_ACTIVE_CHUNK_ID
            and len(turns) != chunk.turn_count
            and not _manifest_rebuilt
        ):
            _rebuild_segment_manifest(session_key)
            return _select_transcript_page(
                session_key,
                limit=limit,
                before=before,
                _manifest_rebuilt=True,
            )
        local_upper = min(local_upper, len(turns))
        for turn_index in range(local_upper - 1, -1, -1):
            ordinal = chunk.start_ordinal + turn_index
            turn = turns[turn_index]
            selected.append(_TranscriptTurnRef(ordinal, turn))
            selected_message_count += len(replay_transcript_to_ui_messages(turn))
            if selected_message_count >= page_limit:
                break
        if selected_message_count >= page_limit:
            break

    selected_chronological = list(reversed(selected))
    lines = [record for ref in selected_chronological for record in ref.records]
    if not selected_chronological:
        return [], {
            "before_cursor": None,
            "has_more_before": False,
            "loaded_message_count": 0,
            "user_message_offset": 0,
        }

    first_ref = selected_chronological[0]
    has_more = first_ref.ordinal > 0
    page = {
        "before_cursor": _encode_page_cursor(first_ref.ordinal) if has_more else None,
        "has_more_before": has_more,
        "loaded_message_count": 0,
        "user_message_offset": _count_user_messages_before_ordinal(
            session_key,
            chunks,
            first_ref.ordinal,
        ),
    }
    return lines, page














def _session_user_event(
    session_key: str,
    message: dict[str, Any],
) -> dict[str, Any] | None:
    if message.get("role") != "user":
        return None
    if message.get(CRON_HISTORY_META) is True:
        return None
    content = message.get("content")
    text = content if isinstance(content, str) else ""
    media = message.get("media")
    chat_id = session_key.split(":", 1)[1] if ":" in session_key else session_key
    return _build_user_transcript_event(
        chat_id,
        text,
        media_paths=media if isinstance(media, list) else None,
    )


def _assistant_text_signature(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _session_backfill_turns(
    session_key: str,
    session_messages: list[dict[str, Any]],
) -> list[tuple[dict[str, Any], tuple[str, ...]]]:
    turns: list[tuple[dict[str, Any], tuple[str, ...]]] = []
    current_user: dict[str, Any] | None = None
    assistant_texts: list[str] = []

    def flush() -> None:
        if current_user is None:
            return
        signature = tuple(text for text in assistant_texts if text)
        if signature:
            turns.append((current_user, signature))

    for message in session_messages:
        role = message.get("role")
        if role == "user":
            flush()
            current_user = _session_user_event(session_key, message)
            assistant_texts = []
            continue
        if role == "assistant" and current_user is not None:
            text = _assistant_text_signature(message.get("content"))
            if text:
                assistant_texts.append(text)
    flush()
    return turns




def _transcript_turn_signature(records: list[dict[str, Any]]) -> tuple[str, ...]:
    texts: list[str] = []
    for message in replay_transcript_to_ui_messages(records):
        if message.get("role") != "assistant" or message.get("kind") == "trace":
            continue
        text = _assistant_text_signature(message.get("content"))
        if text:
            texts.append(text)
    return tuple(texts)


def _find_unique_session_turn(
    session_turns: list[tuple[dict[str, Any], tuple[str, ...]]],
    signature: tuple[str, ...],
    start: int,
) -> int | None:
    if not signature:
        return None
    found: int | None = None
    for index in range(start, len(session_turns)):
        if session_turns[index][1] != signature:
            continue
        if found is not None:
            return None
        found = index
    return found


def _with_backfilled_user(
    records: list[dict[str, Any]],
    user_event: dict[str, Any],
) -> list[dict[str, Any]]:
    for index, rec in enumerate(records):
        if rec.get("event") in _TURN_DISPLAY_EVENTS:
            return [*records[:index], dict(user_event), *records[index:]]
    return records


def _build_synthetic_transcript(
    session_key: str,
    session_messages: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Build synthetic transcript lines from session messages when the WebUI
    transcript file doesn't exist yet (e.g. migrated session, unified session
    with missing user events)."""
    chat_id = session_key.split(":", 1)[1] if ":" in session_key else session_key
    lines: list[dict[str, Any]] = []
    turn_counter = 0
    for msg in session_messages:
        role = msg.get("role")
        if role == "user":
            event = _session_user_event(session_key, msg)
            if event:
                event["turn_id"] = f"synthetic:{turn_counter}"
                event["turn_phase"] = "user"
                lines.append(event)
        elif role == "assistant":
            content = msg.get("content")
            text = content if isinstance(content, str) else ""
            if text.strip():
                lines.append({
                    "event": "message",
                    "chat_id": chat_id,
                    "text": text,
                    "kind": "message",
                    "turn_id": f"synthetic:{turn_counter}",
                    "turn_phase": "answer",
                })
                lines.append({
                    "event": "turn_end",
                    "chat_id": chat_id,
                    "turn_id": f"synthetic:{turn_counter}",
                    "turn_phase": "complete",
                })
                turn_counter += 1
    return lines


def _inject_missing_user_events_from_session(
    session_key: str,
    lines: list[dict[str, Any]],
    session_messages: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    """Backfill user rows for legacy WebUI transcripts that only stored assistant streams."""
    if not lines or not session_messages:
        return lines
    session_turns = _session_backfill_turns(session_key, session_messages)
    if not session_turns:
        return lines

    out: list[dict[str, Any]] = []
    session_cursor = 0
    for turn in _split_transcript_turns(lines):
        has_user = any(rec.get("event") == "user" for rec in turn)
        signature = _transcript_turn_signature(turn)
        match_index = _find_unique_session_turn(session_turns, signature, session_cursor)
        if match_index is None:
            out.extend(turn)
            continue
        out.extend(turn if has_user else _with_backfilled_user(turn, session_turns[match_index][0]))
        session_cursor = match_index + 1
    return out


def _fork_boundary_message_count(lines: list[dict[str, Any]]) -> int | None:
    """Return the replayed UI message count before the first fork marker, if any."""
    for idx, rec in enumerate(lines):
        if rec.get("event") != _WEBUI_FORK_MARKER_EVENT:
            continue
        return len(replay_transcript_to_ui_messages(lines[:idx]))
    return None


def _has_pending_tool_calls(lines: list[dict[str, Any]]) -> bool:
    """Return True when the selected transcript tail looks like an unfinished turn."""
    for rec in reversed(lines):
        ev = rec.get("event")
        if ev == "turn_end":
            return False
        if ev == "user":
            return False
        if ev == "message":
            return rec.get("kind") in {"tool_hint", "progress", "reasoning"}
        if ev in {
            "delta",
            "stream_end",
            "reasoning_delta",
            "reasoning_end",
            "file_edit",
        }:
            return True
        if ev in {_WEBUI_FORK_MARKER_EVENT}:
            continue
    return False


def build_webui_thread_response(
    session_key: str,
    *,
    augment_user_media: Callable[[list[str]], list[dict[str, Any]]] | None = None,
    augment_assistant_media: Callable[[list[str]], list[dict[str, Any]]] | None = None,
    augment_assistant_text: Callable[[str], str] | None = None,
    session_messages: list[dict[str, Any]] | None = None,
    limit: int | None = None,
    before: str | None = None,
) -> dict[str, Any] | None:
    """Return a payload compatible with ``WebuiThreadPersistedPayload``."""
    paginated = limit is not None or before is not None
    page: dict[str, Any] | None = None
    if paginated:
        lines, page = _select_transcript_page(session_key, limit=limit, before=before)
    else:
        lines = read_transcript_lines(session_key)
    if not lines and session_messages:
        lines = _build_synthetic_transcript(session_key, session_messages)
    if not lines:
        return None
    lines = _inject_missing_user_events_from_session(session_key, lines, session_messages)
    fork_boundary = _fork_boundary_message_count(lines)
    msgs = replay_transcript_to_ui_messages(
        lines,
        augment_user_media=augment_user_media,
        augment_assistant_media=augment_assistant_media,
        augment_assistant_text=augment_assistant_text,
    )
    payload = {
        "schemaVersion": WEBUI_TRANSCRIPT_SCHEMA_VERSION,
        "sessionKey": session_key,
        "messages": msgs,
        "has_pending_tool_calls": _has_pending_tool_calls(lines),
    }
    if page is not None:
        page["loaded_message_count"] = len(msgs)
        payload["page"] = page
    if fork_boundary is not None:
        payload["fork_boundary_message_count"] = fork_boundary
    return payload
