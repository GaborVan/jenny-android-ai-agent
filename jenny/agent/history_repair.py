"""Riparazione/normalizzazione della cronologia messaggi (modulo "leaf").

Casa unica per la logica di riparazione della history condivisa tra runner,
session manager e memory: drop degli orfani (tool result senza tool_call),
backfill dei tool_use non soddisfatti, microcompact dei vecchi risultati tool,
e il taglio del prefisso orfano basato su ``find_legal_message_start``.

Leaf-level: dipende solo da stdlib + ``utils.helpers`` (nessun import verso
``agent.runner``/``session``), così runner/session/memory possono importarlo
senza cicli.
"""

from __future__ import annotations

from typing import Any

# find_legal_message_start vive in utils.helpers; qui serve solo a
# ``trim_orphan_prefix`` (uso interno), non è parte dell'API pubblica del modulo.

__all__ = [
    "drop_orphan_tool_results",
    "backfill_missing_tool_results",
    "microcompact",
    "BACKFILL_CONTENT",
    "COMPACTABLE_TOOLS",
    "MICROCOMPACT_KEEP_RECENT",
    "MICROCOMPACT_MIN_CHARS",
]

MICROCOMPACT_KEEP_RECENT = 10
MICROCOMPACT_MIN_CHARS = 500
COMPACTABLE_TOOLS = frozenset(
    {
        "read_file",
        "python_exec",
        "grep",
        "find_files",
        "web_search",
        "web_fetch",
        "list_dir",
        "list_exec_sessions",
    }
)
BACKFILL_CONTENT = "[Tool result unavailable — call was interrupted or lost]"


def drop_orphan_tool_results(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Drop tool results that have no matching assistant tool_call earlier."""
    declared: set[str] = set()
    updated: list[dict[str, Any]] | None = None
    for idx, msg in enumerate(messages):
        role = msg.get("role")
        if role == "assistant":
            for tc in msg.get("tool_calls") or []:
                if isinstance(tc, dict) and tc.get("id"):
                    declared.add(str(tc["id"]))
        if role == "tool":
            tid = msg.get("tool_call_id")
            if tid and str(tid) not in declared:
                if updated is None:
                    updated = [dict(m) for m in messages[:idx]]
                continue
        if updated is not None:
            updated.append(dict(msg))

    if updated is None:
        return messages
    return updated


def backfill_missing_tool_results(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Insert synthetic error results for orphaned tool_use blocks."""
    declared: list[tuple[int, str, str]] = []  # (assistant_idx, call_id, name)
    fulfilled: set[str] = set()
    for idx, msg in enumerate(messages):
        role = msg.get("role")
        if role == "assistant":
            for tc in msg.get("tool_calls") or []:
                if isinstance(tc, dict) and tc.get("id"):
                    name = ""
                    func = tc.get("function")
                    if isinstance(func, dict):
                        name = func.get("name", "")
                    declared.append((idx, str(tc["id"]), name))
        elif role == "tool":
            tid = msg.get("tool_call_id")
            if tid:
                fulfilled.add(str(tid))

    missing = [(ai, cid, name) for ai, cid, name in declared if cid not in fulfilled]
    if not missing:
        return messages

    updated = list(messages)
    offset = 0
    for assistant_idx, call_id, name in missing:
        insert_at = assistant_idx + 1 + offset
        while insert_at < len(updated) and updated[insert_at].get("role") == "tool":
            insert_at += 1
        updated.insert(
            insert_at,
            {
                "role": "tool",
                "tool_call_id": call_id,
                "name": name,
                "content": BACKFILL_CONTENT,
            },
        )
        offset += 1
    return updated


def microcompact(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Replace old compactable tool results with one-line summaries."""
    compactable_indices: list[int] = []
    for idx, msg in enumerate(messages):
        if msg.get("role") == "tool" and msg.get("name") in COMPACTABLE_TOOLS:
            compactable_indices.append(idx)

    if len(compactable_indices) <= MICROCOMPACT_KEEP_RECENT:
        return messages

    stale = compactable_indices[: len(compactable_indices) - MICROCOMPACT_KEEP_RECENT]
    updated: list[dict[str, Any]] | None = None
    for idx in stale:
        msg = messages[idx]
        content = msg.get("content")
        if not isinstance(content, str) or len(content) < MICROCOMPACT_MIN_CHARS:
            continue
        name = msg.get("name", "tool")
        summary = f"[{name} result omitted from context]"
        if updated is None:
            updated = [dict(m) for m in messages]
        updated[idx]["content"] = summary

    return updated if updated is not None else messages
