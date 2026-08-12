"""Message tool for sending messages to users."""

from contextvars import ContextVar
from pathlib import Path
from typing import Any, Awaitable, Callable

from loguru import logger

from jenny.agent.tools.base import Tool, tool_parameters
from jenny.agent.tools.context import ContextAware, RequestContext
from jenny.agent.tools.path_utils import resolve_workspace_path
from jenny.agent.tools.schema import ArraySchema, StringSchema, tool_parameters_schema
from jenny.bus.events import OutboundMessage
from jenny.config.paths import get_workspace_path
from jenny.security.workspace_access import current_tool_workspace
from jenny.security.workspace_policy import _safe_expanduser
from jenny.session.turn_visibility import is_silent_turn

# Sentinel di default per i flag per-turno: condiviso, mai mutato. Un turno vero
# riceve il suo dict fresco da ``start_turn()``.
_NO_TURN_FLAGS: dict[str, bool] = {}


@tool_parameters(
    tool_parameters_schema(
        content=StringSchema(
            "Message content for proactive or cross-channel delivery. "
            "Do not use this for a normal reply in the current chat."
        ),
        channel=StringSchema(
            "Optional target channel for cross-channel/proactive delivery. "
            "Do not set this to the current runtime channel for a normal reply."
        ),
        chat_id=StringSchema(
            "Optional target chat/user ID for cross-channel/proactive delivery. "
            "On WebSocket/WebUI turns: omit chat_id to use the server's conversation id "
            "(never pass client_id values like anon-…). "
            "Do not set this to the current runtime chat for a normal reply."
        ),
        media=ArraySchema(
            StringSchema(""),
            description="Optional list of existing file paths to attach.",
        ),
        buttons=ArraySchema(
            ArraySchema(StringSchema("Button label")),
            description="Optional: inline keyboard buttons as list of rows, each row is list of button labels.",
        ),
        required=["content"],
    )
)
class MessageTool(Tool, ContextAware):
    """Tool to send messages to users on chat channels."""

    _scopes = {"core", "orchestrator"}

    def __init__(
        self,
        send_callback: Callable[[OutboundMessage], Awaitable[None]] | None = None,
        default_channel: str = "",
        default_chat_id: str = "",
        default_message_id: str | None = None,
        workspace: str | Path | None = None,
        restrict_to_workspace: bool = False,
    ):
        self._send_callback = send_callback
        self._workspace = (
            _safe_expanduser(workspace) if workspace is not None else get_workspace_path()
        )
        self._restrict_to_workspace = restrict_to_workspace
        self._default_channel: ContextVar[str] = ContextVar(
            "message_default_channel", default=default_channel
        )
        self._default_chat_id: ContextVar[str] = ContextVar(
            "message_default_chat_id", default=default_chat_id
        )
        self._default_message_id: ContextVar[str | None] = ContextVar(
            "message_default_message_id",
            default=default_message_id,
        )
        self._default_metadata: ContextVar[dict[str, Any]] = ContextVar(
            "message_default_metadata",
            default={},
        )
        # I flag per-turno vivono DENTRO un dict mutabile tenuto dalla ContextVar,
        # non come valore della ContextVar stessa. Il tool viene eseguito in un
        # task figlio (``asyncio.wait_for``/``gather`` in tool_execution) che
        # riceve una *copia* del context: un ``ContextVar.set()`` fatto lì dentro
        # non risalirebbe mai al turno, e la soppressione della risposta finale in
        # ``AgentLoop._assemble_outbound`` non scatterebbe (utente = messaggio
        # doppio). Mutando l'oggetto condiviso il turno vede la scrittura, e
        # l'isolamento fra turni concorrenti resta garantito perché ogni turno
        # installa un dict nuovo nel proprio context. Stesso idioma di
        # ``file_state._current_file_states``.
        self._turn_flags: ContextVar[dict[str, bool]] = ContextVar(
            "message_turn_flags",
            default=_NO_TURN_FLAGS,
        )
        self._suppress_delivery_var: ContextVar[bool] = ContextVar(
            "message_suppress_delivery",
            default=False,
        )

    @classmethod
    def create(cls, ctx: Any) -> Tool:
        send_callback = ctx.bus.publish_outbound if ctx.bus else None
        return cls(
            send_callback=send_callback,
            workspace=ctx.workspace,
            restrict_to_workspace=ctx.config.restrict_to_workspace,
        )

    def set_context(self, ctx: RequestContext) -> None:
        """Set the current message context."""
        self._default_channel.set(ctx.channel)
        self._default_chat_id.set(ctx.chat_id)
        self._default_message_id.set(ctx.message_id)
        self._default_metadata.set(dict(ctx.metadata or {}))

    def set_send_callback(self, callback: Callable[[OutboundMessage], Awaitable[None]]) -> None:
        """Set the callback for sending messages."""
        self._send_callback = callback

    def start_turn(self) -> None:
        """Reset per-turn send tracking.

        Installa un contenitore nuovo nel context del turno: le scritture dei
        tool (che girano in task figli) mutano *questo* oggetto e restano
        visibili al turno che l'ha creato.
        """
        self._turn_flags.set({"sent_in_turn": False})

    def set_suppress_delivery(self, active: bool):
        """Acknowledge but don't deliver tool sends (heartbeat internal check)."""
        return self._suppress_delivery_var.set(active)

    def reset_suppress_delivery(self, token) -> None:
        """Restore previous delivery-suppression state."""
        self._suppress_delivery_var.reset(token)

    @property
    def _sent_in_turn(self) -> bool:
        return self._turn_flags.get().get("sent_in_turn", False)

    @_sent_in_turn.setter
    def _sent_in_turn(self, value: bool) -> None:
        flags = self._turn_flags.get()
        if flags is _NO_TURN_FLAGS:
            # Nessuno ``start_turn()`` per questo context (uso diretto del tool,
            # fuori da un turno): la scrittura resta locale al context invece di
            # mutare il sentinel condiviso da tutte le istanze.
            flags = {}
            self._turn_flags.set(flags)
        flags["sent_in_turn"] = value

    @property
    def name(self) -> str:
        return "message"

    @property
    def description(self) -> str:
        return (
            "Proactively send a message to a user/channel, optionally with file attachments. "
            "Use this for reminders, cross-channel delivery, or explicit proactive sends. "
            "Do not use this for the normal reply in the current chat: answer naturally instead. "
            "If channel/chat_id would target the current runtime conversation, do not call this tool "
            "unless the user explicitly asked you to proactively send an existing file attachment. "
            "For proactive attachment delivery, use the 'media' parameter with file paths. "
            "Do NOT use read_file to send files — that only reads content for your own analysis."
        )

    def _resolve_media(self, media: list[str]) -> list[str]:
        """Resolve local media attachments and enforce workspace restriction when enabled."""
        resolved: list[str] = []
        access = current_tool_workspace(
            self._workspace,
            restrict_to_workspace=self._restrict_to_workspace,
        )
        workspace = access.project_path or self._workspace
        for p in media:
            if p.startswith(("http://", "https://")):
                resolved.append(p)
            elif not access.restrict_to_workspace:
                try:
                    path = _safe_expanduser(p)
                except (RuntimeError, OSError):
                    path = Path(p)
                resolved.append(p if path.is_absolute() else str(workspace / path))
            else:
                resolved.append(str(resolve_workspace_path(p, workspace, access.allowed_root)))
        return resolved

    async def execute(
        self,
        content: str,
        channel: str | None = None,
        chat_id: str | None = None,
        message_id: str | None = None,
        media: list[str] | None = None,
        buttons: list[list[str]] | None = None,
        **kwargs: Any,
    ) -> str:
        from jenny.utils.helpers import strip_think

        content = strip_think(content)

        if buttons is not None:
            if not isinstance(buttons, list) or any(
                not isinstance(row, list) or any(not isinstance(label, str) for label in row)
                for row in buttons
            ):
                return "Error: buttons must be a list of list of strings"
        default_channel = self._default_channel.get()
        default_chat_id = self._default_chat_id.get()
        channel = channel or default_channel
        explicit_chat_id = chat_id
        if (
            default_channel == "websocket"
            and channel == "websocket"
            and explicit_chat_id is not None
            and str(explicit_chat_id).strip() != ""
            and str(explicit_chat_id).strip() != str(default_chat_id).strip()
        ):
            return (
                "Error: chat_id does not match the active WebSocket conversation. "
                "Omit chat_id (and usually channel) so delivery uses the current "
                "conversation id from context — WebSocket client_id strings "
                "(e.g. anon-…) are not chat ids."
            )
        chat_id = chat_id or default_chat_id
        # Only inherit default message_id when targeting the same channel+chat.
        # Cross-chat sends must not carry the original message_id, because
        # some channels use it to determine the target conversation via their
        # Reply API, which would route the message to the wrong chat entirely.
        same_target = channel == default_channel and chat_id == default_chat_id
        if same_target:
            message_id = message_id or self._default_message_id.get()
        else:
            message_id = None

        if not channel or not chat_id:
            return "Error: No target channel/chat specified"

        # Un ciclo silenzioso manda AL MASSIMO un avviso. Non e' una quota
        # arbitraria: un turno silenzioso non e' una conversazione, e' un avviso —
        # e un avviso e' uno. Misurato sul dispositivo: senza questo tetto un
        # singolo ciclo heartbeat ha consegnato cinque messaggi di fila
        # ("sto aspettando", "ok basta, mi zitto", "🙄"), perche' il modello
        # trattava la chat come un canale di pensiero. Il prompt lo vieta, ma il
        # prompt non garantisce: qui il secondo tentativo non parte, e la stringa
        # di ritorno dice al modello cosa fare invece (accorpare).
        if self._sent_in_turn and is_silent_turn(self._default_metadata.get()):
            logger.info("MessageTool: second send suppressed on a silent turn")
            return (
                "Error: you already sent the one alert this scheduled run is allowed. "
                "A silent check delivers at most one message per run. Do not try again: "
                "if something is missing, it belongs in that single message — say it in "
                "the next run instead."
            )

        if not self._send_callback:
            return "Error: Message sending not configured"

        if media:
            try:
                media = self._resolve_media(media)
            except (OSError, PermissionError, ValueError) as e:
                return f"Error: media path is not allowed: {str(e)}"

        metadata = dict(self._default_metadata.get()) if same_target else {}
        if message_id:
            metadata["message_id"] = message_id
        # Un invio proattivo è l'unica cosa che il modello dice all'utente da
        # FUORI la conversazione: gira su una sessione interna (heartbeat, cron,
        # Dream), quindi il suo testo finisce nella history di *quella* sessione
        # e non in quella unificata. Senza questa registrazione l'utente vede
        # l'avviso (transcript WebUI + notifica Android) e al turno dopo il
        # modello non ne ha traccia. Misurato sul dispositivo il 2026-08-12:
        # avviso "hps non è raggiungibile" alle 18:33 dall'heartbeat, "sicura?"
        # alle 18:39 su ``unified:default`` — risposto come se non fosse mai
        # stato detto, perché nel contesto di quel turno non c'era.
        # La registrazione segue l'intento proattivo, che è la ragione vera:
        # prima dipendeva dalla presenza di un allegato (che la attivava per il
        # motivo diverso di conservare la copia dei media in cronologia).
        proactive = not same_target or is_silent_turn(self._default_metadata.get())
        if proactive or media:
            metadata["_record_channel_delivery"] = True
        # Fan-out cross-canale SOLO per un invio davvero proattivo/cross-channel.
        # Un allegato o messaggio nella conversazione corrente (``same_target``)
        # resta sul canale d'origine e non viene diffuso agli altri canali
        # accoppiati (es. Telegram): il ChannelDeliverer diffonde solo con
        # questo flag (o con ``proactive=True`` dai chiamanti cron/heartbeat).
        # Eccezione: un turno SILENZIOSO (cron monitor, heartbeat, lavoro interno
        # in genere). Li ``same_target`` e vero (canale/chat d'origine) ma
        # l'utente NON e in conversazione: sta arrivando un avviso, non una
        # risposta, e deve raggiungere anche i canali accoppiati (WebUI +
        # Telegram). E' la stessa consegna proattiva che prima faceva a mano il
        # cron dispatcher per l'heartbeat. La condizione si legge dai metadata del
        # turno gia propagati in ``set_context``: nessuno stato nuovo sul tool e
        # nessun call site in piu da tenere allineato.
        if proactive:
            metadata["_proactive_fanout"] = True

        msg = OutboundMessage(
            channel=channel,
            chat_id=chat_id,
            content=content,
            media=media or [],
            buttons=buttons or [],
            metadata=metadata,
        )

        if self._suppress_delivery_var.get():
            logger.debug("MessageTool: delivery suppressed during internal check")
            return f"Message acknowledged for {channel}:{chat_id} (not delivered)"

        try:
            await self._send_callback(msg)
            if channel == default_channel and chat_id == default_chat_id:
                self._sent_in_turn = True
            media_info = f" with {len(media)} attachments" if media else ""
            button_info = f" with {sum(len(row) for row in buttons)} button(s)" if buttons else ""
            return f"Message sent to {channel}:{chat_id}{media_info}{button_info}"
        except Exception as e:
            return f"Error sending message: {str(e)}"


# Registrazione esplicita dei tool di questo modulo (Fase 5.3): il
# ToolLoader legge questa lista invece della reflection dir(). Un nuovo
# tool va aggiunto qui esplicitamente.
TOOLS = [MessageTool]
