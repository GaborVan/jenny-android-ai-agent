"""Minimal command routing table for slash commands."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Awaitable, Callable

if TYPE_CHECKING:
    from jenny.bus.events import InboundMessage, OutboundMessage
    from jenny.session.manager import Session

Handler = Callable[["CommandContext"], Awaitable["OutboundMessage | None"]]


@dataclass
class CommandContext:
    """Everything a command handler needs to produce a response."""

    msg: InboundMessage
    session: Session | None
    key: str
    raw: str
    args: str = ""
    loop: Any = None


class CommandRouter:
    """Pure dict-based command dispatch.

    Three tiers checked in order:
      1. *priority* — exact-match commands handled before the dispatch lock
         (e.g. /stop, /restart).
      2. *exact* — exact-match commands handled inside the dispatch lock.
      3. *prefix* — longest-prefix-first match (e.g. "/team ").

    Piu' un cancello, iniettato: :attr:`availability`. Il router resta senza
    vocabolario — non sa cosa sia un progetto — e chiede a chi lo sa se questa
    riga, in questa sessione, ha un soggetto. Chi risponde e'
    :mod:`jenny.command.scope`, montato da ``register_builtin_commands``.
    """

    def __init__(self) -> None:
        self._priority: dict[str, Handler] = {}
        self._exact: dict[str, Handler] = {}
        self._prefix: list[tuple[str, Handler]] = []
        # ``(ctx) -> OutboundMessage | None``: la risposta di rifiuto, o ``None``
        # se il comando puo' partire. Torna un messaggio gia' composto e non un
        # testo perche' cosi' questo modulo non deve costruire ``OutboundMessage``
        # ne' sapere quali metadati vuole un canale.
        self.availability: Callable[["CommandContext"], Any] | None = None

    def priority(self, cmd: str, handler: Handler) -> None:
        self._priority[cmd] = handler

    def exact(self, cmd: str, handler: Handler) -> None:
        self._exact[cmd] = handler

    def prefix(self, pfx: str, handler: Handler) -> None:
        self._prefix.append((pfx, handler))
        self._prefix.sort(key=lambda p: len(p[0]), reverse=True)

    def is_priority(self, text: str) -> bool:
        return text.strip().lower() in self._priority

    def is_dispatchable_command(self, text: str) -> bool:
        """Check whether *text* matches any non-priority command tier (exact or prefix).

        Does NOT check priority tier.
        If this returns True, ``dispatch()`` is guaranteed to match a handler.

        **Non guarda lo scope, e non e' una dimenticanza.** Un comando che qui non
        ha un soggetto deve comunque essere *intercettato*: la risposta e' il
        rifiuto di :attr:`availability`, non il testo che passa al modello come
        messaggio.
        """
        cmd = text.strip().lower()
        if cmd in self._exact:
            return True
        for pfx, _ in self._prefix:
            if cmd.startswith(pfx):
                return True
        return False

    def _refused(self, ctx: CommandContext) -> "OutboundMessage | None":
        """Il rifiuto di scope, se questa riga non ha un soggetto in questa sessione."""
        if self.availability is None:
            return None
        return self.availability(ctx)

    async def dispatch_priority(self, ctx: CommandContext) -> OutboundMessage | None:
        """Dispatch a priority command. Called from run() without the lock."""
        handler = self._priority.get(ctx.raw.lower())
        if handler:
            return self._refused(ctx) or await handler(ctx)
        return None

    async def dispatch(self, ctx: CommandContext) -> OutboundMessage | None:
        """Try exact, then prefix handlers. Returns None if unhandled."""
        cmd = ctx.raw.lower()

        if handler := self._exact.get(cmd):
            return self._refused(ctx) or await handler(ctx)

        for pfx, handler in self._prefix:
            if cmd.startswith(pfx):
                # Il cancello **prima** di ``ctx.args``: un rifiuto non deve
                # lasciare il contesto mezzo preparato per un handler che non
                # verra' chiamato.
                if refused := self._refused(ctx):
                    return refused
                ctx.args = ctx.raw[len(pfx):]
                return await handler(ctx)

        return None
