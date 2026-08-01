"""Shared session key constants and helpers."""

from __future__ import annotations

UNIFIED_SESSION_KEY = "unified:default"


def session_key_for_channel(channel: str, chat_id: str) -> str:
    """Return the session key for a channel/chat pair.

    Every channel/chat maps onto the single unified conversation; explicit
    ``session_key_override`` values (internal keys) bypass this helper.
    """
    return UNIFIED_SESSION_KEY
