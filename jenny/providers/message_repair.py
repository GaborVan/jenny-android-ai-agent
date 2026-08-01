"""Normalizzazione/riparazione dei messaggi per le richieste ai provider
(estratto da LLMProvider).

Trasformazioni pure sulla lista di messaggi, indipendenti dal provider:
sanitizzazione del contenuto vuoto/meta, enforcement dell'alternanza dei ruoli,
sostituzione dei blocchi immagine con placeholder testuale.
"""

from __future__ import annotations

from typing import Any

SYNTHETIC_USER_CONTENT = "(conversation continued)"


def sanitize_empty_content(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Sanitize message content: fix empty blocks, strip internal _meta fields."""
    result: list[dict[str, Any]] = []
    for msg in messages:
        content = msg.get("content")

        if isinstance(content, str) and not content:
            clean = dict(msg)
            clean["content"] = (
                None if (msg.get("role") == "assistant" and msg.get("tool_calls")) else "(empty)"
            )
            result.append(clean)
            continue

        if isinstance(content, list):
            new_items: list[Any] = []
            changed = False
            for item in content:
                if (
                    isinstance(item, dict)
                    and item.get("type") in ("text", "input_text", "output_text")
                    and not item.get("text")
                ):
                    changed = True
                    continue
                if isinstance(item, dict) and "_meta" in item:
                    new_items.append({k: v for k, v in item.items() if k != "_meta"})
                    changed = True
                else:
                    new_items.append(item)
            if changed:
                clean = dict(msg)
                if new_items:
                    clean["content"] = new_items
                elif msg.get("role") == "assistant" and msg.get("tool_calls"):
                    clean["content"] = None
                else:
                    clean["content"] = "(empty)"
                result.append(clean)
                continue

        if isinstance(content, dict):
            clean = dict(msg)
            clean["content"] = [content]
            result.append(clean)
            continue

        result.append(msg)
    return result


def enforce_role_alternation(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Merge consecutive same-role messages and drop trailing assistant messages.

    Some OpenAI-compatible providers reject requests where the last message is
    'assistant' (prefill not supported) or two consecutive non-system messages
    share the same role.
    """
    if not messages:
        return messages

    merged: list[dict[str, Any]] = []
    for msg in messages:
        role = msg.get("role")
        if (
            merged
            and role != "system"
            and role not in ("tool",)
            and merged[-1].get("role") == role
            and role in ("user", "assistant")
        ):
            prev = merged[-1]
            if role == "assistant":
                prev_has_tools = bool(prev.get("tool_calls"))
                curr_has_tools = bool(msg.get("tool_calls"))
                if curr_has_tools:
                    merged[-1] = dict(msg)
                    continue
                if prev_has_tools:
                    continue
            prev_content = prev.get("content") or ""
            curr_content = msg.get("content") or ""
            if isinstance(prev_content, str) and isinstance(curr_content, str):
                prev["content"] = (prev_content + "\n\n" + curr_content).strip()
            else:
                merged[-1] = dict(msg)
        else:
            merged.append(dict(msg))

    last_popped = None
    while merged and merged[-1].get("role") == "assistant":
        last_popped = merged.pop()

    # If removing trailing assistant messages left only system messages, the
    # request would be invalid for most providers (e.g. Zhipu/GLM error 1214).
    # Recover by converting the last popped assistant message to a user message.
    if (
        merged
        and last_popped is not None
        and not any(m.get("role") in ("user", "tool") for m in merged)
    ):
        recovered = dict(last_popped)
        recovered["role"] = "user"
        merged.append(recovered)

    # Safety net: ensure the first non-system message is not a bare assistant
    # message (GLM rejects system→assistant with 1214). Can happen when upstream
    # truncation drops the only user message. Insert a synthetic user message.
    for i, msg in enumerate(merged):
        if msg.get("role") != "system":
            if msg.get("role") == "assistant" and not msg.get("tool_calls"):
                merged.insert(i, {"role": "user", "content": SYNTHETIC_USER_CONTENT})
            break

    return merged


def _stripped_image_placeholder(path: str) -> str:
    """Placeholder per un'immagine rimossa perché il modello l'ha rifiutata.

    Diverso dal generico ``[image: path]``: qui il modello attivo NON può
    vedere immagini, quindi il testo lo dice esplicitamente e scoraggia i
    tentativi di "leggere" i byte dell'immagine coi tool (loop inutile
    osservato coi modelli text-only).
    """
    where = f" ({path})" if path else ""
    return (
        f"[The user attached an image{where}, but the active model does not "
        "support image input, so it was removed. Do NOT try to read the image "
        "bytes with tools — tell the user the current model cannot view images.]"
    )


def strip_image_content(messages: list[dict[str, Any]]) -> list[dict[str, Any]] | None:
    """Replace image_url blocks with text placeholder. Returns None if no images."""
    found = False
    result = []
    for msg in messages:
        content = msg.get("content")
        if isinstance(content, list):
            new_content = []
            for b in content:
                if isinstance(b, dict) and b.get("type") == "image_url":
                    path = (b.get("_meta") or {}).get("path", "")
                    new_content.append(
                        {"type": "text", "text": _stripped_image_placeholder(path)}
                    )
                    found = True
                else:
                    new_content.append(b)
            result.append({**msg, "content": new_content})
        else:
            result.append(msg)
    return result if found else None


def strip_image_content_inplace(messages: list[dict[str, Any]]) -> bool:
    """Replace image_url blocks with text placeholder *in-place*.

    Mutates the content lists of the original message dicts so callers holding
    references to those dicts also see the stripped version.
    """
    found = False
    for msg in messages:
        content = msg.get("content")
        if isinstance(content, list):
            for i, b in enumerate(content):
                if isinstance(b, dict) and b.get("type") == "image_url":
                    path = (b.get("_meta") or {}).get("path", "")
                    content[i] = {
                        "type": "text",
                        "text": _stripped_image_placeholder(path),
                    }
                    found = True
    return found
