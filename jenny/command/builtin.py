"""Built-in slash command handlers."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Sequence
from contextlib import suppress
from dataclasses import dataclass
from typing import TYPE_CHECKING

from jenny import __version__
from jenny.bus.events import OutboundMessage
from jenny.command.router import CommandContext, CommandRouter
from jenny.utils.helpers import build_status_content

if TYPE_CHECKING:
    from jenny.agent.atlas import AtlasOutcome
    from jenny.agent.memory_budget import FileBudget


@dataclass(frozen=True)
class BuiltinCommandSpec:
    command: str
    title: str
    description: str
    icon: str
    arg_hint: str = ""

    def as_dict(self) -> dict[str, str]:
        return {
            "command": self.command,
            "title": self.title,
            "description": self.description,
            "icon": self.icon,
            "arg_hint": self.arg_hint,
        }


BUILTIN_COMMAND_SPECS: tuple[BuiltinCommandSpec, ...] = (
    BuiltinCommandSpec(
        "/new",
        "New chat",
        "Stop the current task and start a fresh conversation.",
        "square-pen",
    ),
    BuiltinCommandSpec(
        "/stop",
        "Stop current task",
        "Cancel the active agent turn for this chat.",
        "square",
    ),
    BuiltinCommandSpec(
        "/status",
        "Show status",
        "Display runtime, provider, and channel status.",
        "activity",
    ),
    BuiltinCommandSpec(
        "/model",
        "Switch model preset",
        "Show or switch the active model preset.",
        "brain",
        "[preset]",
    ),
    BuiltinCommandSpec(
        "/history",
        "Show conversation history",
        "Print the last N persisted conversation messages.",
        "history",
        "[n]",
    ),
    BuiltinCommandSpec(
        "/goal",
        "Start long-running goal",
        "Tell the agent to treat the request as a long-running goal.",
        "activity",
        "<goal>",
    ),
    BuiltinCommandSpec(
        "/dream",
        "Run Dream",
        (
            "Manually trigger memory consolidation. Add 'budget' to read the memory file "
            "sizes, or 'budget <name> <n>' to set one."
        ),
        "sparkles",
        "[budget [name n]]",
    ),
    BuiltinCommandSpec(
        "/atlas",
        "Run Atlas",
        "Rebuild the wiki directory in memory/WIKI.md. Add 'force' to skip the change check.",
        "map",
        "[force]",
    ),
    BuiltinCommandSpec(
        "/skill",
        "List skills",
        "List enabled skills and their descriptions.",
        "puzzle",
    ),
    BuiltinCommandSpec(
        "/help",
        "Show help",
        "List available slash commands.",
        "circle-help",
    ),
)


async def cmd_stop(ctx: CommandContext) -> OutboundMessage:
    """Cancel all active tasks and subagents for the session."""
    loop = ctx.loop
    msg = ctx.msg
    total = await loop._cancel_active_tasks(ctx.key)
    if total:
        # Il turno ripudiato salta il proprio restore/turn_end: li emette /stop,
        # in modo sincrono e deterministico (la UI riceve sempre turn_end).
        loop._restore_cancelled_turn(ctx.key)
        await loop._emit_stop_turn_end(msg, ctx.key)
    content = f"Stopped {total} task(s)." if total else "No active task to stop."
    return OutboundMessage(
        channel=msg.channel, chat_id=msg.chat_id, content=content,
        metadata=dict(msg.metadata or {})
    )


async def cmd_status(ctx: CommandContext) -> OutboundMessage:
    """Build an outbound status message for a session."""
    loop = ctx.loop
    session = ctx.session or loop.sessions.get_or_create(ctx.key)
    ctx_est = 0
    with suppress(Exception):
        ctx_est, _ = loop.consolidator.estimate_session_prompt_tokens(session)
    if ctx_est <= 0:
        ctx_est = loop._last_usage.get("prompt_tokens", 0)

    active_tasks = loop._active_tasks.get(ctx.key, [])
    task_count = sum(1 for t in active_tasks if not t.done())
    with suppress(Exception):
        task_count += loop.subagents.get_running_count_by_session(ctx.key)
    return OutboundMessage(
        channel=ctx.msg.channel,
        chat_id=ctx.msg.chat_id,
        content=build_status_content(
            version=__version__, model=loop.model,
            start_time=loop._start_time, last_usage=loop._last_usage,
            context_window_tokens=loop.context_window_tokens,
            session_msg_count=len(session.get_history(max_messages=0)),
            context_tokens_estimate=ctx_est,
            active_task_count=task_count,
            max_completion_tokens=getattr(
                getattr(loop.provider, "generation", None), "max_tokens", 8192
            ),
        ),
        metadata={**dict(ctx.msg.metadata or {}), "render_as": "text"},
    )


async def cmd_new(ctx: CommandContext) -> OutboundMessage:
    """Stop active task and start a fresh session."""
    loop = ctx.loop
    cancelled = await loop._cancel_active_tasks(ctx.key)
    if cancelled:
        # Materializza il lavoro parziale del turno fermato PRIMA dello
        # snapshot, così finisce nell'archivio invece di andare perso.
        loop._restore_cancelled_turn(ctx.key)
        await loop._emit_stop_turn_end(ctx.msg, ctx.key)
    session = ctx.session or loop.sessions.get_or_create(ctx.key)
    snapshot = session.messages[session.last_consolidated:]
    session.clear()
    loop.sessions.save(session)
    loop.sessions.invalidate(session.key)
    if snapshot:
        loop._schedule_background(loop.consolidator.archive(snapshot, session_key=ctx.key))
    return OutboundMessage(
        channel=ctx.msg.channel, chat_id=ctx.msg.chat_id,
        content="New session started.",
        # `_session_boundary` dice ai client di rendere questa conferma come
        # separatore invece che come bolla. Il transcript NON viene toccato da
        # /new (si azzera il contesto del modello, non il registro visibile):
        # senza un confine esplicito la conversazione prosegue a schermo come
        # se il comando fosse stato ignorato.
        metadata={**dict(ctx.msg.metadata or {}), "_session_boundary": True},
    )


def _format_preset_names(names: list[str]) -> str:
    return ", ".join(f"`{name}`" for name in names) if names else "(none configured)"


def _model_preset_names(loop) -> list[str]:
    return sorted(loop.model_presets)


def _active_model_preset_name(loop) -> str:
    return loop.model_preset or "(none)"


def _command_error_message(exc: Exception) -> str:
    return str(exc.args[0]) if isinstance(exc, KeyError) and exc.args else str(exc)


def _model_command_status(loop) -> str:
    names = _model_preset_names(loop)
    active = _active_model_preset_name(loop)
    return "\n".join([
        "## Model",
        f"- Current model: `{loop.model}`",
        f"- Current preset: `{active}`",
        f"- Available presets: {_format_preset_names(names)}",
    ])


async def cmd_model(ctx: CommandContext) -> OutboundMessage:
    """Show or switch model presets."""
    loop = ctx.loop
    args = ctx.args.strip()
    metadata = {**dict(ctx.msg.metadata or {}), "render_as": "text"}

    if not args:
        return OutboundMessage(
            channel=ctx.msg.channel,
            chat_id=ctx.msg.chat_id,
            content=_model_command_status(loop),
            metadata=metadata,
        )

    parts = args.split()
    if len(parts) != 1:
        return OutboundMessage(
            channel=ctx.msg.channel,
            chat_id=ctx.msg.chat_id,
            content="Usage: `/model [preset]`",
            metadata=metadata,
        )

    name = parts[0]
    try:
        await loop.set_model_preset(name)
    except (KeyError, ValueError) as exc:
        names = _model_preset_names(loop)
        return OutboundMessage(
            channel=ctx.msg.channel,
            chat_id=ctx.msg.chat_id,
            content=(
                f"Could not switch model preset: {_command_error_message(exc)}\n\n"
                f"Available presets: {_format_preset_names(names)}"
            ),
            metadata=metadata,
        )

    max_tokens = getattr(getattr(loop.provider, "generation", None), "max_tokens", None)
    lines = [
        f"Switched model preset to `{loop.model_preset}`.",
        f"- Model: `{loop.model}`",
        f"- Context window: {loop.context_window_tokens}",
    ]
    if max_tokens is not None:
        lines.append(f"- Max output tokens: {max_tokens}")
    return OutboundMessage(
        channel=ctx.msg.channel,
        chat_id=ctx.msg.chat_id,
        content="\n".join(lines),
        metadata=metadata,
    )


async def cmd_dream(ctx: CommandContext) -> OutboundMessage:
    """Manually trigger a Dream consolidation run, or read/tune the memory budgets."""
    loop = ctx.loop
    msg = ctx.msg

    args = ctx.args.strip()
    if args:
        # Il ramo con argomento risponde nello stesso turno invece di accodare
        # un task: legge tre file piccoli e al più riscrive `config.json`, non
        # chiama il provider. Il ramo senza argomento resta intatto sotto.
        return await _dream_budget_command(ctx, args)

    async def _run_dream():
        async def _silent(*_args, **_kwargs):
            pass

        from jenny.agent.memory import MemoryStore

        dream_session_key = MemoryStore.dream_session_key
        prune_dream_sessions = MemoryStore.prune_dream_sessions

        store = loop.context.memory
        content = ""
        resp = None
        t0 = time.monotonic()
        try:
            result = store.build_dream_prompt()
            if result is None:
                await loop.bus.publish_outbound(OutboundMessage(
                    channel=msg.channel, chat_id=msg.chat_id,
                    content=_format_dream_no_input_message(),
                    metadata={"render_as": "text"},
                ))
                return
            prompt, last_cursor = result
            key = dream_session_key()
            dream_tools = store.build_dream_tools()
            resp = await loop.process_direct(
                prompt,
                session_key=key,
                ephemeral=True,
                tools=dream_tools,
                on_progress=_silent,
            )
            elapsed = time.monotonic() - t0
            # ``getattr``: il registry Dream espone ``file_states``, ma il
            # contratto resta tollerante verso registry di altra provenienza.
            dream_file_states = getattr(dream_tools, "file_states", None)
            if MemoryStore.dream_should_advance_cursor(resp, dream_file_states):
                store.set_last_dream_cursor(last_cursor)
                content = f"Dream completed in {elapsed:.1f}s."
            elif MemoryStore.dream_run_completed(resp):
                content = (
                    f"Dream completed in {elapsed:.1f}s but wrote nothing "
                    "(attempts blocked/refused); memory cursor was not advanced."
                )
            else:
                content = (
                    f"Dream did not complete after {elapsed:.1f}s; "
                    "memory cursor was not advanced."
                )
        except Exception as e:
            elapsed = time.monotonic() - t0
            content = f"Dream failed after {elapsed:.1f}s: {e}"
        finally:
            from jenny.agent.token_usage import record_response_token_usage

            record_response_token_usage(
                resp,
                source="dream",
                timezone_name=getattr(loop.context, "timezone", None),
            )
            await asyncio.to_thread(store.compact_history)
            pruned_keys = prune_dream_sessions(loop.sessions.sessions_dir)
            if pruned_keys:
                loop.evict_pruned_sessions(pruned_keys)
        await loop.bus.publish_outbound(OutboundMessage(
            channel=msg.channel, chat_id=msg.chat_id, content=content,
        ))

    asyncio.create_task(_run_dream())
    return OutboundMessage(
        channel=msg.channel, chat_id=msg.chat_id, content="Dreaming...",
    )


def _format_dream_no_input_message() -> str:
    return "\n".join([
        "Dream has no conversation history to process yet.",
        "",
        "Dream reads new entries from `memory/history.jsonl` after the current Dream cursor.",
        (
            "Short chats only reach that file after token compaction or idle auto-compact, "
            "so a fresh or short WebUI chat may leave Dream with no input."
        ),
        "",
        "Next steps:",
        "- Enable `agents.defaults.idleCompactAfterMinutes` so completed chats become Dream input automatically.",
        "- Compact the current chat into memory once that manual action is available.",
        "- If you expected history to exist, check whether `memory/history.jsonl` has new entries after the Dream cursor.",
    ])


# ---------------------------------------------------------------------------
# /dream budget — leggere le misure e tarare i budget della memoria lunga
# ---------------------------------------------------------------------------
#
# I budget nascono a 0 ("misurato ma non applicato") e i numeri veri si scelgono
# dalle dimensioni che il device riporta davvero. Ma `loader.py` serializza senza
# `exclude_defaults`, quindi alla prima scrittura di config lo 0 finisce *dentro*
# `config.json` sul telefono: alzare il default in Python da lì in poi non lo
# raggiunge più. Senza questo comando l'unico modo di leggere le misure e
# scrivere i tetti sarebbe una shell di root sul dispositivo.


@dataclass(frozen=True)
class _DreamBudgetField:
    """Un campo tarabile di ``DreamConfig``, con il vincolo che lo schema gli impone.

    ``minimum`` rispecchia il ``ge=`` dello schema e ``too_low`` è la frase che
    l'utente legge quando lo sfora. Duplicare qui il vincolo non è ridondanza:
    assegnare un valore fuori range dentro la callback di ``mutate`` alzerebbe un
    ``ValidationError`` che risale come guasto di scrittura, indistinguibile da
    un `config.json` non scrivibile, per quello che è solo un errore di battitura.
    """

    attr: str
    label: str
    unit: str
    minimum: int
    too_low: str
    # True se ``label`` è anche il nome di un file nel report di budget: solo
    # per quelli ha senso confrontare il valore appena scritto con una misura.
    measured: bool


_DREAM_BUDGET_FIELDS: dict[str, _DreamBudgetField] = {
    "memory": _DreamBudgetField(
        attr="memory_budget_chars",
        label="MEMORY.md",
        unit="chars",
        minimum=0,
        too_low=(
            "A character budget cannot be negative. Use `0` to keep MEMORY.md measured "
            "without enforcing anything."
        ),
        measured=True,
    ),
    "user": _DreamBudgetField(
        attr="user_budget_chars",
        label="USER.md",
        unit="chars",
        minimum=0,
        too_low=(
            "A character budget cannot be negative. Use `0` to keep USER.md measured "
            "without enforcing anything."
        ),
        measured=True,
    ),
    "soul": _DreamBudgetField(
        attr="soul_budget_chars",
        label="SOUL.md",
        unit="chars",
        minimum=0,
        too_low=(
            "A character budget cannot be negative. Use `0` to keep SOUL.md measured "
            "without enforcing anything."
        ),
        measured=True,
    ),
    "review": _DreamBudgetField(
        attr="review_every_runs",
        label="Dream review pass",
        unit="runs",
        minimum=1,
        too_low=(
            "The review cadence must be at least 1 run: a review pass every zero runs "
            "is not a schedule."
        ),
        measured=False,
    ),
}


def _dream_usage() -> str:
    """Le forme valide del comando.

    Va in coda alla vista di lettura e a ogni risposta d'errore, non alle
    conferme: la lista dei quattro nomi è l'unico posto in cui si scopre che
    `soul` e `review` esistono, e chi sta leggendo o ha appena sbagliato la
    sintassi ne ha bisogno. Chi ha appena scritto un valore no — lì sarebbe solo
    rumore addosso alla frase che conta.
    """
    return "\n".join([
        "Valid forms:",
        "- `/dream` — run memory consolidation now",
        "- `/dream budget` — show the current sizes, budgets, and review state",
        "- `/dream budget <memory|user|soul> <chars>` — set a size budget (`0` = measure only)",
        "- `/dream budget review <runs>` — Dream runs between review passes (minimum 1)",
    ])


def _format_dream_budget_report(
    report: Sequence[FileBudget],
    *,
    review_every_runs: int,
    runs_since_review: int,
    stuck_runs: int,
) -> str:
    """Vista utente del report di budget.

    Non riusa ``render_gauge``: quel testo è scritto *per il modello* ("over
    budget the write is refused, so free space in the same turn"), cioè
    un'istruzione su cosa fare mentre Dream gira. Qui chi legge è la persona che
    deve scegliere i numeri, e la domanda è un'altra — quanto è grande il file,
    quanto gli è concesso, quanto manca.

    Lo stato del review pass sta nella stessa risposta perché fa parte della
    stessa domanda: "cosa sta facendo questa cosa adesso" non si risponde con i
    soli tetti, visto che è il review pass a farli rispettare nel tempo.
    """
    lines = ["## Long-term memory budget", ""]
    for item in report:
        if item.enforced:
            over = f" — **over budget by {item.chars - item.budget:,}**" if item.over else ""
            lines.append(
                f"- `{item.label}` — {item.chars:,} / {item.budget:,} chars "
                f"({item.pct}%){over}"
            )
        else:
            # Un file senza budget si mostra, non si omette: `0` è lo stato di
            # default di tutti e tre ed è precisamente il numero che chi lancia
            # questo comando è venuto a leggere.
            lines.append(
                f"- `{item.label}` — {item.chars:,} chars, no budget "
                "(measured, not enforced)"
            )
    lines.extend([
        "",
        (
            f"Review pass: every {review_every_runs} Dream runs. "
            f"{runs_since_review} runs since the last one, {stuck_runs} stuck runs."
        ),
        "",
        _dream_usage(),
    ])
    return "\n".join(lines)


def _parse_dream_budget_value(
    raw: str, name: str, field: _DreamBudgetField
) -> tuple[int | None, str]:
    """Interpreta il valore richiesto, o spiega perché non si può.

    Ritorna ``(valore, "")`` oppure ``(None, messaggio)``. Nel secondo caso il
    chiamante non deve entrare in ``mutate``: un input sbagliato non tocca il
    file, non ruota il `.bak` e non alza niente.
    """
    try:
        value = int(raw)
    except ValueError:
        return None, (
            f"`{raw}` is not a whole number.\n\n"
            f"Usage: `/dream budget {name} <{field.unit}>`\n\n"
            f"{_dream_usage()}"
        )
    if value < field.minimum:
        return None, f"{field.too_low}\n\n{_dream_usage()}"
    return value, ""


def _format_dream_budget_change(
    field: _DreamBudgetField,
    *,
    before: int,
    after: int,
    report: Sequence[FileBudget],
) -> str:
    """Conferma di una scrittura, con il prima e il dopo."""
    if not field.measured:
        return f"{field.label}: every {before} → every {after} {field.unit}."
    lines = [f"`{field.label}` budget: {before:,} → {after:,} {field.unit}."]
    if after == 0:
        lines.append(
            "Enforcement is off: the file is still measured and still shown in Dream's "
            "gauge, but no write to it will be refused. Nothing on disk changed."
        )
    else:
        current = next((item.chars for item in report if item.label == field.label), None)
        if current is not None and current > after:
            # Il caso normale sul device, non un errore: MEMORY.md è già fuori
            # misura oggi, e un tetto scelto dalle misure reali nasce quasi
            # sempre sotto la dimensione attuale. Dirlo subito evita di far
            # scoprire all'utente ore dopo, da un log, che il review pass ha
            # lavoro arretrato.
            lines.append(
                f"Note: the file is {current:,} chars today, already {current - after:,} "
                "over the new budget. Nothing was deleted — the next Dream review pass "
                "will work it down, and a write that shrinks the file is always accepted "
                "in the meantime."
            )
    return "\n".join(lines)


async def _dream_budget_command(ctx: CommandContext, args: str) -> OutboundMessage:
    """Gestisci `/dream budget [...]`: mostra le misure o scrive un tetto."""
    metadata = {**dict(ctx.msg.metadata or {}), "render_as": "text"}

    def reply(content: str) -> OutboundMessage:
        return OutboundMessage(
            channel=ctx.msg.channel,
            chat_id=ctx.msg.chat_id,
            content=content,
            metadata=metadata,
        )

    parts = args.split()
    if parts[0].lower() != "budget":
        return reply(f"Unknown `/dream` argument `{parts[0]}`.\n\n{_dream_usage()}")
    rest = parts[1:]
    if len(rest) == 1:
        return reply(f"`/dream budget {rest[0]}` is missing a value.\n\n{_dream_usage()}")
    if len(rest) > 2:
        return reply(
            "`/dream budget` takes at most a name and a value.\n\n" + _dream_usage()
        )

    try:
        from jenny.agent.memory_budget import budget_report
        from jenny.config import store as config_store
        from jenny.config.loader import load_config

        memory = ctx.loop.context.memory
        dream = load_config().agents.defaults.dream
        # Le misure si leggono **prima** di entrare in ``mutate``: quel lock
        # resta preso per tutta la durata della callback, e tre letture di file
        # là dentro lo terrebbero fermo su I/O che non riguarda la scrittura.
        report = budget_report(
            memory,
            memory_chars=dream.memory_budget_chars,
            user_chars=dream.user_budget_chars,
            soul_chars=dream.soul_budget_chars,
        )

        if not rest:
            runs_since_review, stuck_runs = memory.get_review_state()
            return reply(_format_dream_budget_report(
                report,
                review_every_runs=dream.review_every_runs,
                runs_since_review=runs_since_review,
                stuck_runs=stuck_runs,
            ))

        name = rest[0].lower()
        field = _DREAM_BUDGET_FIELDS.get(name)
        if field is None:
            valid = ", ".join(f"`{key}`" for key in _DREAM_BUDGET_FIELDS)
            return reply(
                f"Unknown budget `{rest[0]}`. Valid names: {valid}.\n\n{_dream_usage()}"
            )
        value, error = _parse_dream_budget_value(rest[1], name, field)
        if value is None:
            return reply(error)

        # ``before`` lo cattura la callback e non la config letta qui sopra:
        # ``mutate`` rilegge il file dentro il proprio lock, quindi solo lì il
        # valore corrente è quello vero al momento della scrittura.
        seen: dict[str, int] = {}

        def _apply(config) -> bool:
            target = config.agents.defaults.dream
            before = int(getattr(target, field.attr))
            seen["before"] = before
            if before == value:
                # Niente da cambiare: ``False`` lascia il file intatto, così un
                # `/dream budget memory 6000` ribattuto non riscrive
                # `config.json` né ruota il `.bak` per nulla.
                return False
            setattr(target, field.attr, value)
            return True

        await config_store.mutate(_apply)
        before = seen.get("before", value)
        if before == value:
            # Backtick solo sui tre nomi di file: "`Dream review pass`" farebbe
            # sembrare un identificatore quello che è una frase.
            subject = f"`{field.label}`" if field.measured else field.label
            return reply(
                f"{subject} is already set to {value:,} {field.unit}; "
                "`config.json` was not rewritten."
            )
        return reply(_format_dream_budget_change(
            field, before=before, after=value, report=report
        ))
    except Exception as e:
        # Come ``cmd_atlas``: un comando che muore in silenzio lascia la chat
        # senza risposta e l'utente senza idea del perché. Il motivo va nel
        # messaggio. Se a sollevare è stata ``mutate``, il file non è stato
        # scritto — la sua callback o completa o non salva.
        return reply(f"Could not read or write the memory budget: {e}")


async def cmd_atlas(ctx: CommandContext) -> OutboundMessage:
    """Manually trigger an Atlas run (rebuild the wiki directory)."""
    loop = ctx.loop
    msg = ctx.msg
    force = ctx.args.strip().lower() == "force"

    async def _run():
        from jenny.agent.atlas import AtlasStore, run_atlas
        from jenny.config.loader import load_config

        try:
            config = load_config()
            store = AtlasStore.from_config(config.workspace_path, config)
            outcome = await run_atlas(loop, store=store, force=force)
            content = _format_atlas_outcome(outcome)
        except Exception as e:
            content = f"Atlas failed: {e}"
        await loop.bus.publish_outbound(OutboundMessage(
            channel=msg.channel, chat_id=msg.chat_id, content=content,
            metadata={"render_as": "text"},
        ))

    asyncio.create_task(_run())
    return OutboundMessage(
        channel=msg.channel, chat_id=msg.chat_id, content="Mapping the wiki...",
    )


def _format_atlas_outcome(outcome: "AtlasOutcome") -> str:
    """Messaggio utente per un run Atlas.

    Gli esiti "non ho fatto niente" hanno messaggi distinti apposta: un comando
    che risponde "fatto" senza aver fatto nulla è peggio di uno che dice perché.
    """
    elapsed = f"{outcome.elapsed:.1f}s"
    if outcome.status == "skipped_no_wikis":
        return (
            "Atlas found no wikis to map.\n\n"
            "It reads `workspace/wikis/<name>/wiki/`. Ask me to create a wiki first, "
            "then run `/atlas` again."
        )
    if outcome.status == "skipped_unchanged":
        return (
            "The wiki hasn't changed since the last Atlas run, so `memory/WIKI.md` is "
            "already current — no tokens spent. Use `/atlas force` to rebuild it anyway."
        )
    if outcome.status == "written":
        return f"Atlas updated `memory/WIKI.md` in {elapsed}."
    if outcome.status == "no_write":
        return (
            f"Atlas finished in {elapsed} without writing (attempts blocked or refused); "
            "the wiki fingerprint was not advanced, so the next run will retry."
        )
    if outcome.status == "incomplete":
        return f"Atlas did not complete after {elapsed}; the directory was left untouched."
    return f"Atlas failed after {elapsed}: {outcome.detail}"


_HISTORY_DEFAULT_COUNT = 10
_HISTORY_MAX_COUNT = 50
_HISTORY_MAX_CONTENT_CHARS = 200


def _format_history_message(msg: dict) -> str | None:
    """Format a single history message for display. Returns None to skip."""
    role = msg.get("role")
    if role not in ("user", "assistant"):
        return None
    content = msg.get("content") or ""
    if isinstance(content, list):
        parts = [b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"]
        content = " ".join(parts)
    content = str(content).strip()
    if not content:
        return None
    if len(content) > _HISTORY_MAX_CONTENT_CHARS:
        content = content[:_HISTORY_MAX_CONTENT_CHARS] + "…"
    label = "👤 You" if role == "user" else "🤖 Bot"
    return f"{label}: {content}"


async def cmd_history(ctx: CommandContext) -> OutboundMessage:
    """Show the last N messages of the current session (default 10, max 50).

    Usage: /history [count]
    """
    count = _HISTORY_DEFAULT_COUNT
    if ctx.args.strip():
        try:
            count = max(1, min(int(ctx.args.strip()), _HISTORY_MAX_COUNT))
        except ValueError:
            return OutboundMessage(
                channel=ctx.msg.channel, chat_id=ctx.msg.chat_id,
                content="Usage: /history [count] — e.g. /history 5 (default: 10, max: 50)",
                metadata=dict(ctx.msg.metadata or {}),
            )

    session = ctx.session or ctx.loop.sessions.get_or_create(ctx.key)
    history = session.get_history(max_messages=0)
    visible = [_format_history_message(m) for m in history]
    visible = [m for m in visible if m is not None]
    recent = visible[-count:]

    if not recent:
        return OutboundMessage(
            channel=ctx.msg.channel, chat_id=ctx.msg.chat_id,
            content="No conversation history yet.",
            metadata=dict(ctx.msg.metadata or {}),
        )

    header = f"Last {len(recent)} message(s):\n"
    return OutboundMessage(
        channel=ctx.msg.channel, chat_id=ctx.msg.chat_id,
        content=header + "\n".join(recent),
        metadata={**dict(ctx.msg.metadata or {}), "render_as": "text"},
    )


_GOAL_PROMPT_TEMPLATE = """The user declared a sustained objective for this thread.

Inspect or clarify if needed, then call `long_task` with the refined objective (and optional short ui_summary). Work proceeds as normal assistant turns using your usual tools. When the objective is fully done and verified, call `complete_goal` with a brief recap. If the user later cancels or changes direction, still call `complete_goal` with an honest recap (then `long_task` again only after there is no active goal). Do not use `long_task` / `complete_goal` for trivial one-shot answers.

Goal:
{goal}
"""


async def cmd_goal(ctx: CommandContext) -> OutboundMessage | None:
    """Rewrite /goal into a normal agent turn that nudges long_task use."""
    goal = ctx.args.strip()
    if not goal:
        return OutboundMessage(
            channel=ctx.msg.channel,
            chat_id=ctx.msg.chat_id,
            content="Usage: /goal <long-running task description>",
            metadata={**dict(ctx.msg.metadata or {}), "render_as": "text"},
        )
    current_task = asyncio.current_task()
    active_tasks = ctx.loop._active_tasks.get(ctx.key, [])
    running = sum(1 for t in active_tasks if t is not current_task and not t.done())
    running += ctx.loop.subagents.get_running_count_by_session(ctx.key)
    if running > 0:
        return OutboundMessage(
            channel=ctx.msg.channel,
            chat_id=ctx.msg.chat_id,
            content=(
                "A task is already running for this chat. "
                "Use `/stop` first, then send `/goal <long-running task description>` again."
            ),
            metadata={**dict(ctx.msg.metadata or {}), "render_as": "text"},
        )

    ctx.msg.metadata = {
        **dict(ctx.msg.metadata or {}),
        "original_command": "/goal",
        "goal_started_at": time.time(),
    }
    ctx.msg.content = _GOAL_PROMPT_TEMPLATE.format(goal=goal)
    return None


async def cmd_skill(ctx: CommandContext) -> OutboundMessage:
    """List all enabled skills (name and description only)."""
    loop = ctx.loop
    skills = loop.context.skills.list_skills(filter_unavailable=False)
    if not skills:
        content = "No skills available."
    else:
        lines = [f"Available skills ({len(skills)}):", ""]
        for entry in skills:
            desc = loop.context.skills._get_skill_description(entry["name"])
            lines.append(f"- **{entry['name']}** — {desc}")
        content = "\n".join(lines)
    return OutboundMessage(
        channel=ctx.msg.channel,
        chat_id=ctx.msg.chat_id,
        content=content,
        metadata=dict(ctx.msg.metadata or {}),
    )

async def cmd_help(ctx: CommandContext) -> OutboundMessage:
    """Return available slash commands."""
    return OutboundMessage(
        channel=ctx.msg.channel,
        chat_id=ctx.msg.chat_id,
        content=build_help_text(),
        metadata={**dict(ctx.msg.metadata or {}), "render_as": "text"},
    )


def build_help_text() -> str:
    """Build canonical help text shared across channels."""
    lines = ["✿ jenny commands:"]
    for spec in BUILTIN_COMMAND_SPECS:
        command = spec.command
        if spec.arg_hint:
            command = f"{command} {spec.arg_hint}"
        lines.append(f"{command} — {spec.description}")
    return "\n".join(lines)


def register_builtin_commands(router: CommandRouter) -> None:
    """Register the default set of slash commands."""
    router.priority("/stop", cmd_stop)
    router.priority("/status", cmd_status)
    router.exact("/new", cmd_new)
    router.exact("/model", cmd_model)
    router.prefix("/model ", cmd_model)
    router.exact("/history", cmd_history)
    router.prefix("/history ", cmd_history)
    router.exact("/goal", cmd_goal)
    router.prefix("/goal ", cmd_goal)
    router.exact("/dream", cmd_dream)
    router.prefix("/dream ", cmd_dream)
    router.exact("/atlas", cmd_atlas)
    router.prefix("/atlas ", cmd_atlas)
    router.exact("/skill", cmd_skill)
    router.exact("/help", cmd_help)
