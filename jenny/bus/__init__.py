"""Message bus module for decoupled channel-agent communication."""

from jenny.bus.events import InboundMessage, OutboundMessage
from jenny.bus.queue import MessageBus

__all__ = ["MessageBus", "InboundMessage", "OutboundMessage"]
