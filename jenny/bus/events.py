"""Event types for the message bus."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

# Optional ``OutboundMessage.metadata`` key for structured, channel-agnostic UI
# payloads. Value is JSON-serializable with at least ``kind``; rich clients may
# render it and other channels may ignore unknown keys.
OUTBOUND_META_AGENT_UI = "_agent_ui"

# Channel name for messages that stay inside the agent (cron, Dream, heartbeat,
# subagents) and are never delivered to a real chat channel. Defined here — a
# leaf module with no internal deps — so core (loop/cron/delivery) can import it
# without depending on the gateway entry-point.
INTERNAL_CHANNEL = "internal"

# Flag di metadata che marcano un outbound come coordinamento/streaming: tutto
# ciò che NON è un messaggio finale user-visible. Sorgente unica condivisa dal
# dispatcher (che vi aggiunge ``_mirror``) e dal canale Telegram (webui-only),
# così l'aggiunta di un nuovo flag di coordinamento non può più divergere fra
# le due liste.
COORDINATION_FLAGS = (
    "_progress", "_stream_delta", "_stream_end", "_streamed",
    "_reasoning_delta", "_reasoning_end", "_retry_wait",
    "_turn_end", "_file_edit_events", "_goal_status",
    "_session_updated", "_runtime_model_updated", "_app_data_changed",
    "_apps_list_changed", "_user_echo",
)


@dataclass
class InboundMessage:
    """Message received from a chat channel."""

    channel: str  # e.g. "websocket"
    sender_id: str  # User identifier
    chat_id: str  # Chat/channel identifier
    content: str  # Message text
    timestamp: datetime = field(default_factory=datetime.now)
    media: list[str] = field(default_factory=list)  # Media URLs
    metadata: dict[str, Any] = field(default_factory=dict)  # Channel-specific data
    session_key_override: str | None = None  # Optional override for thread-scoped sessions

    @property
    def session_key(self) -> str:
        """Unique key for session identification."""
        return self.session_key_override or f"{self.channel}:{self.chat_id}"


@dataclass
class OutboundMessage:
    """Message to send to a chat channel.

    ``metadata`` can carry routing (``message_id``, …), trace flags (``_progress``),
    and optional ``OUTBOUND_META_AGENT_UI`` blobs for rich clients; non-WebUI
    channels may ignore unknown keys.
    """

    channel: str
    chat_id: str
    content: str
    media: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    buttons: list[list[str]] = field(default_factory=list)
