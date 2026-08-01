"""Tipi del turno per l'``AgentLoop``: stato FSM + contesto del turno.

Estratti da ``loop.py`` in un modulo leaf così che i mixin del loop
(`turn_states`, ecc.) possano importarli a runtime senza creare un ciclo con
``loop``. ``loop`` li re-esporta per retro-compatibilità.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import TYPE_CHECKING, Any, Awaitable, Callable

if TYPE_CHECKING:
    from jenny.agent.tools.registry import ToolRegistry
    from jenny.agent.turn_epochs import TurnToken
    from jenny.bus.events import InboundMessage, OutboundMessage
    from jenny.session.manager import Session


class TurnState(Enum):
    RESTORE = auto()
    COMPACT = auto()
    COMMAND = auto()
    BUILD = auto()
    RUN = auto()
    SAVE = auto()
    RESPOND = auto()
    DONE = auto()


@dataclass
class StateTraceEntry:
    state: TurnState
    started_at: float
    duration_ms: float
    event: str
    error: str | None = None


@dataclass
class TurnContext:
    msg: InboundMessage
    session_key: str
    state: TurnState
    turn_id: str
    session: Session | None = None

    history: list[dict[str, Any]] = field(default_factory=list)
    initial_messages: list[dict[str, Any]] = field(default_factory=list)

    final_content: str | None = None
    tools_used: list[str] = field(default_factory=list)
    all_messages: list[dict[str, Any]] = field(default_factory=list)
    stop_reason: str = ""
    had_injections: bool = False

    user_persisted_early: bool = False
    save_skip: int = 0

    outbound: OutboundMessage | None = None
    suppress_response: bool = False

    on_progress: Callable[..., Awaitable[None]] | None = None
    on_stream: Callable[[str], Awaitable[None]] | None = None
    on_stream_end: Callable[..., Awaitable[None]] | None = None
    on_retry_wait: Callable[[str], Awaitable[None]] | None = None

    pending_queue: asyncio.Queue | None = None
    pending_summary: str | None = None

    ephemeral: bool = False
    tools: ToolRegistry | None = None
    # Token di epoch del turno (None per i chiamanti non governati).
    turn_token: TurnToken | None = None

    turn_wall_started_at: float = field(default_factory=time.time)
    visible_run_started_at: float | None = None
    turn_latency_ms: int | None = None

    trace: list[StateTraceEntry] = field(default_factory=list)
