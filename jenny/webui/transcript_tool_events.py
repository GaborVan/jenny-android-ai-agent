"""Processing/normalizzazione dei tool-event per la replay del transcript
(estratto da transcript.py). Funzioni pure su dict di eventi; nessuna dipendenza
da store/paging/replay.
"""

from __future__ import annotations

import json
from typing import Any

from jenny.webui.transcript_markdown import media_kind_from_name as _media_kind_from_name

_FILE_EDIT_TOOL_NAMES: frozenset[str] = frozenset({
    "write_file",
    "edit_file",
    "apply_patch",
})


def _format_tool_call_trace(call: Any) -> str | None:
    if not call or not isinstance(call, dict):
        return None
    fn = call.get("function")
    name = fn.get("name") if isinstance(fn, dict) else None
    if not isinstance(name, str) or not name:
        raw_name = call.get("name")
        name = raw_name if isinstance(raw_name, str) else ""
    if not name:
        return None
    args = (fn.get("arguments") if isinstance(fn, dict) else None) or call.get("arguments")
    if isinstance(args, str) and args.strip():
        return f"{name}({args})"
    if args and isinstance(args, dict):
        return f"{name}({json.dumps(args, ensure_ascii=False)})"
    return f"{name}()"


def _tool_trace_lines_from_events(events: Any) -> list[str]:
    if not isinstance(events, list):
        return []
    lines: list[str] = []
    seen: set[str] = set()
    for event in events:
        if not event or not isinstance(event, dict):
            continue
        if event.get("phase") not in {"start", "end", "error"}:
            continue
        call_id = event.get("call_id")
        if isinstance(call_id, str) and call_id:
            if call_id in seen:
                continue
            seen.add(call_id)
        t = _format_tool_call_trace(event)
        if t:
            lines.append(t)
    return lines


_PHASE_RANK = {"start": 1, "end": 2, "error": 3}


def _normalize_tool_events(events: Any) -> list[dict[str, Any]]:
    if not isinstance(events, list):
        return []
    out: list[dict[str, Any]] = []
    for event in events:
        if not event or not isinstance(event, dict):
            continue
        if event.get("phase") not in {"start", "end", "error"}:
            continue
        if not isinstance(event.get("name"), str):
            fn = event.get("function")
            if not (isinstance(fn, dict) and isinstance(fn.get("name"), str)):
                continue
        out.append(dict(event))
    return out


def _tool_event_key(event: dict[str, Any]) -> str:
    call_id = event.get("call_id")
    if isinstance(call_id, str) and call_id:
        return f"call:{call_id}"
    return _format_tool_call_trace(event) or json.dumps(event, sort_keys=True, ensure_ascii=False)


def _tool_event_file_edit_key(event: dict[str, Any]) -> str | None:
    call_id = event.get("call_id")
    if not isinstance(call_id, str) or not call_id:
        return None
    name = event.get("name")
    if not isinstance(name, str) or not name:
        fn = event.get("function")
        name = fn.get("name") if isinstance(fn, dict) else ""
    if not isinstance(name, str) or name not in _FILE_EDIT_TOOL_NAMES:
        return None
    return f"{call_id}|{name}"


def _merge_tool_events(previous: Any, incoming: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not isinstance(previous, list) or not previous:
        return incoming
    if not incoming:
        return [dict(event) for event in previous if isinstance(event, dict)]
    merged = [dict(event) for event in previous if isinstance(event, dict)]
    index_by_key = {_tool_event_key(event): idx for idx, event in enumerate(merged)}
    for event in incoming:
        key = _tool_event_key(event)
        existing_index = index_by_key.get(key)
        if existing_index is None:
            index_by_key[key] = len(merged)
            merged.append(event)
            continue
        existing = merged[existing_index]
        incoming_rank = _PHASE_RANK.get(str(event.get("phase")), 0)
        existing_rank = _PHASE_RANK.get(str(existing.get("phase")), 0)
        if incoming_rank >= existing_rank:
            merged[existing_index] = {**existing, **event}
    return merged


def _file_edit_key(edit: dict[str, Any]) -> str:
    call_id = str(edit.get("call_id") or "")
    tool = str(edit.get("tool") or "")
    if call_id:
        return f"{call_id}|{tool}"
    return f"{tool}|{edit.get('path') or ''}"


def _message_has_file_edit_for_tool_event(
    message: dict[str, Any],
    event: dict[str, Any],
) -> bool:
    key = _tool_event_file_edit_key(event)
    if not key:
        return False
    edits = message.get("fileEdits")
    if not isinstance(edits, list):
        return False
    return any(isinstance(edit, dict) and _file_edit_key(edit) == key for edit in edits)


def _filter_covered_file_edit_tool_events(
    messages: list[dict[str, Any]],
    events: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if not events:
        return events
    return [
        event
        for event in events
        if not any(_message_has_file_edit_for_tool_event(message, event) for message in messages)
    ]


def _strip_covered_file_edit_tool_hints(
    message: dict[str, Any],
    edits: list[dict[str, Any]],
) -> dict[str, Any]:
    incoming_keys = {
        _file_edit_key(edit)
        for edit in edits
        if isinstance(edit, dict)
    }
    events = message.get("toolEvents")
    if not incoming_keys or not isinstance(events, list):
        return message

    kept_events: list[dict[str, Any]] = []
    removed_trace_lines: set[str] = set()
    changed = False
    for event in events:
        if not isinstance(event, dict):
            continue
        key = _tool_event_file_edit_key(event)
        if key and key in incoming_keys:
            changed = True
            removed_trace_lines.update(_tool_trace_lines_from_events([event]))
            continue
        kept_events.append(event)
    if not changed:
        return message

    raw_traces = message.get("traces")
    if isinstance(raw_traces, list):
        previous_traces = [trace for trace in raw_traces if isinstance(trace, str)]
    else:
        content = message.get("content")
        previous_traces = [content] if isinstance(content, str) and content else []
    next_traces = [trace for trace in previous_traces if trace not in removed_trace_lines]
    next_message = {
        **message,
        "traces": next_traces,
        "content": next_traces[-1] if next_traces else "",
    }
    if kept_events:
        next_message["toolEvents"] = kept_events
    else:
        next_message.pop("toolEvents", None)
    return next_message


def _merge_unique_tool_trace_lines(
    previous_traces: list[str],
    lines: list[str],
) -> tuple[list[str], bool]:
    seen_lines = set(previous_traces)
    traces = list(previous_traces)
    added = False
    for line in lines:
        if line in seen_lines:
            continue
        seen_lines.add(line)
        traces.append(line)
        added = True
    return traces, added


def _media_from_signed_urls(value: Any) -> list[dict[str, Any]]:
    media: list[dict[str, Any]] = []
    urls = value if isinstance(value, list) else []
    for m in urls:
        if isinstance(m, dict) and m.get("url"):
            name = str(m.get("name") or "")
            entry: dict[str, Any] = {
                "kind": _media_kind_from_name(name),
                "url": str(m["url"]),
                "name": name,
            }
            # Path locale (se persistito dal live): serve al client per
            # l'apertura nativa dei file non renderizzabili inline.
            path = m.get("path")
            if isinstance(path, str) and path:
                entry["path"] = path
            media.append(entry)
    return media
