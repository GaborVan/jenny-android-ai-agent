"""Convert Chat Completions messages/tools to Responses API format."""

from __future__ import annotations

import json
from typing import Any

from jenny.providers.base import tool_arguments_json_for_replay


def convert_messages(messages: list[dict[str, Any]]) -> tuple[str, list[dict[str, Any]]]:
    """Convert Chat Completions messages to Responses API input items.

    Returns ``(system_prompt, input_items)`` where *system_prompt* is extracted
    from any ``system`` role message and *input_items* is the Responses API
    ``input`` array.
    """
    system_prompt = ""
    input_items: list[dict[str, Any]] = []
    used_item_ids: set[str] = set()

    for idx, msg in enumerate(messages):
        role = msg.get("role")
        content = msg.get("content")

        if role == "system":
            system_prompt = content if isinstance(content, str) else ""
            continue

        if role == "user":
            input_items.append(convert_user_message(content))
            continue

        if role == "assistant":
            if isinstance(content, str) and content:
                message_id = _unique_item_id(f"msg_{idx}", used_item_ids)
                input_items.append({
                    "type": "message", "role": "assistant",
                    "content": [{"type": "output_text", "text": content}],
                    "status": "completed", "id": message_id,
                })
            for tool_call in msg.get("tool_calls", []) or []:
                fn = tool_call.get("function") or {}
                call_id, item_id = split_tool_call_id(tool_call.get("id"))
                response_item_id = _unique_item_id(item_id or f"fc_{idx}", used_item_ids)
                input_items.append({
                    "type": "function_call",
                    "id": response_item_id,
                    "call_id": call_id or f"call_{idx}",
                    "name": fn.get("name"),
                    "arguments": tool_arguments_json_for_replay(fn.get("arguments")),
                })
            continue

        if role == "tool":
            call_id, _ = split_tool_call_id(msg.get("tool_call_id"))
            output_text = _tool_output_text(content)
            input_items.append({"type": "function_call_output", "call_id": call_id, "output": output_text})

    return system_prompt, input_items


def _tool_output_text(content: Any) -> str:
    """Render a tool message's content as ``function_call_output`` text.

    Unlike Anthropic's ``tool_result``, the Responses API's
    ``function_call_output`` only accepts plain text — it has no native image
    block. A list containing ``image_url`` items (e.g. ``read_file`` on an
    image, see ``build_image_content_blocks``) would otherwise be
    JSON-stringified, dumping raw base64 into the prompt with no chance the
    model can use it. Replace it with a text reference to the file instead,
    which is already on disk since ``build_image_content_blocks`` always
    stamps the source path in ``_meta``.
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list) and any(
        isinstance(item, dict) and item.get("type") == "image_url" for item in content
    ):
        return _image_tool_output_note(content)
    return json.dumps(content, ensure_ascii=False)


def _image_tool_output_note(content: list[dict[str, Any]]) -> str:
    paths = [
        (item.get("_meta") or {}).get("path")
        for item in content
        if isinstance(item, dict) and item.get("type") == "image_url"
    ]
    paths = [p for p in paths if p]
    texts = [
        item.get("text")
        for item in content
        if isinstance(item, dict) and item.get("type") == "text" and item.get("text")
    ]
    location = ", ".join(paths) if paths else "the workspace"
    note = (
        f"(Image saved to {location}. This provider (OpenAI Responses API) cannot embed "
        "images inside tool results, so you cannot see this file's visual content here. "
        "Say so rather than guessing, or ask the user to describe/share it directly.)"
    )
    return "\n".join([*texts, note])


def convert_user_message(content: Any) -> dict[str, Any]:
    """Convert a user message's content to Responses API format.

    Handles plain strings, ``text`` blocks -> ``input_text``, and
    ``image_url`` blocks -> ``input_image``.
    """
    if isinstance(content, str):
        return {"role": "user", "content": [{"type": "input_text", "text": content}]}
    if isinstance(content, list):
        converted: list[dict[str, Any]] = []
        for item in content:
            if not isinstance(item, dict):
                continue
            if item.get("type") == "text":
                converted.append({"type": "input_text", "text": item.get("text", "")})
            elif item.get("type") == "image_url":
                url = (item.get("image_url") or {}).get("url")
                if url:
                    converted.append({"type": "input_image", "image_url": url, "detail": "auto"})
        if converted:
            return {"role": "user", "content": converted}
    return {"role": "user", "content": [{"type": "input_text", "text": ""}]}


def convert_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convert OpenAI function-calling tool schema to Responses API flat format."""
    converted: list[dict[str, Any]] = []
    for tool in tools:
        fn = (tool.get("function") or {}) if tool.get("type") == "function" else tool
        name = fn.get("name")
        if not name:
            continue
        params = fn.get("parameters") or {}
        converted.append({
            "type": "function",
            "name": name,
            "description": fn.get("description") or "",
            "parameters": params if isinstance(params, dict) else {},
        })
    return converted


def _unique_item_id(item_id: str, used: set[str]) -> str:
    """Return a Responses input item id that is unique within one request."""
    if item_id not in used:
        used.add(item_id)
        return item_id

    suffix = 2
    while f"{item_id}_{suffix}" in used:
        suffix += 1
    unique = f"{item_id}_{suffix}"
    used.add(unique)
    return unique


def split_tool_call_id(tool_call_id: Any) -> tuple[str, str | None]:
    """Split a compound ``call_id|item_id`` string.

    Returns ``(call_id, item_id)`` where *item_id* may be ``None``.
    """
    if isinstance(tool_call_id, str) and tool_call_id:
        if "|" in tool_call_id:
            call_id, item_id = tool_call_id.split("|", 1)
            return call_id, item_id or None
        return tool_call_id, None
    return "call_0", None
