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
    from jenny.agent.dream_review import ReviewOutcome
    from jenny.agent.gardener import GardenerOutcome
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
        "/gardener",
        "Run the gardener",
        (
            "Turn this project's new journal lines into pages and update its map. Inside a "
            "project it works on that one; elsewhere name it: '/gardener <project>'."
        ),
        "sprout",
        "[project]",
    ),
    BuiltinCommandSpec(
        "/skill",
        "List skills",
        "List enabled skills and their descriptions.",
        "puzzle",
    ),
    BuiltinCommandSpec(
        "/init",
        "Write this project's instructions",
        "Inside a project: read the wiki and write its AGENTS.md.",
        "file-pen",
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
    # La conversazione e' vuota, quindi non contiene piu' il contenuto di nessun
    # file: il dedup delle letture va dimenticato insieme ai messaggi, o la prima
    # lettura della sessione nuova torna «invariato dall'ultima lettura» a chi non
    # ha mai letto niente.
    loop.forget_file_reads(session.key)
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

        # Prologo ed epilogo del ciclo sono gli stessi del job cron, e stanno in
        # un modulo solo: erano due copie della stessa sequenza, e ogni volta che
        # una cresceva l'altra restava indietro in silenzio — il guard del budget,
        # il gauge, i contatori del review sono arrivati qui tre commit dopo che
        # erano di là. Qui resta ciò che è davvero di questo percorso: il turno
        # incrementale e la frase da dire a chi ha lanciato il comando.
        from jenny.agent.dream_cycle import (
            NO_ENTRIES,
            batch_was_not_consolidated,
            begin_dream_cycle,
            finish_dream_cycle,
            take_dream_snapshot,
        )
        from jenny.agent.memory import MemoryStore
        from jenny.agent.memory_budget import render_gauge
        from jenny.config.loader import load_config

        dream_session_key = MemoryStore.dream_session_key
        prune_dream_sessions = MemoryStore.prune_dream_sessions

        store = loop.context.memory
        # Il checkpoint pre-Dream lo possiede il container, che lo appende al
        # loop accanto al dispatcher cron. ``getattr`` perché il loop può non
        # averlo — un test, un percorso che costruisce l'agente da sé — e la sua
        # assenza ha già una traduzione sola e giusta in ``take_dream_snapshot``:
        # ``snapshotted=False``, cioè "le tue cancellazioni sono definitive".
        #
        # Prima di ``04de3cc`` questo run faceva solo consolidamento
        # incrementale, e girare senza rete era già un buco. Ora può far partire
        # un review pass, che è esplicitamente autorizzato a ristrutturare e
        # cancellare (``agent/dream_review.md``): è un'altra cosa.
        snapshot_cb = getattr(loop, "snapshot_before_dream", None)
        content = ""
        review_note = ""
        resp = None
        t0 = time.monotonic()
        try:
            # Le due metà di `/dream` devono raccontare la stessa cosa, e per
            # tre commit non lo facevano. `/dream budget` legge lo stesso
            # ``budget_report`` e stampa all'utente dimensioni, tetti e
            # percentuali: chi li ha appena letti e poi lancia `/dream`
            # conclude — ragionevolmente — che quel tetto valga per il run che
            # sta avviando. Senza il guard montato qui non valeva per niente, e
            # non era "una feature che non gira": era un limite annunciato da
            # una metà del comando e ignorato dall'altra, cioè la porta di
            # servizio con cui si aggirava l'enforcement lanciando Dream a
            # mano. Il gauge segue la stessa logica: il modello non può
            # rispettare uno spazio di cui non gli si dice la misura.
            cfg = load_config().agents.defaults.dream
            prologue = await begin_dream_cycle(
                loop, store=store, cfg=cfg, take_snapshot=snapshot_cb,
            )
            if prologue.review is not None:
                review_note = _format_dream_review_note(prologue.review)

            # ``advanced=None`` vuol dire "il turno non ha mancato niente": è il
            # valore del ramo senza storia, e anche quello di un turno che è
            # crashato. Non ``False``, che incrementerebbe ``stuck`` e quindi
            # dichiarerebbe un livelock del budget dove c'è stata un'eccezione.
            advanced: bool | None = None
            try:
                result = store.build_dream_prompt(gauge=render_gauge(prologue.report))
                if result is None:
                    await loop.bus.publish_outbound(OutboundMessage(
                        channel=msg.channel, chat_id=msg.chat_id,
                        content=_prefix_review_note(
                            review_note, _format_dream_no_input_message()
                        ),
                        metadata={"render_as": "text"},
                    ))
                    return
                prompt, last_cursor = result
                if prologue.review is None:
                    # Un solo checkpoint per ciclo: se il review è appena girato
                    # lo snapshot è già stato preso e copre anche il turno
                    # incrementale che segue, mentre un secondo "pre_dream"
                    # archiviato dopo il review non sarebbe pre-niente.
                    await take_dream_snapshot(snapshot_cb)
                key = dream_session_key()
                dream_tools = store.build_dream_tools(write_size_guard=prologue.guard)
                # Legato prima del turno: il ``finally`` qui sotto lo legge, e un
                # ``process_direct`` che solleva lascerebbe altrimenti il nome non
                # definito — cioè un ``NameError`` dentro il ``finally``, che si
                # porterebbe via la chiusura del ciclo. È lo stesso guasto che
                # quel ``finally`` esiste per chiudere, reintrodotto un livello
                # più in basso.
                dream_file_states = getattr(dream_tools, "file_states", None)
                resp = await loop.process_direct(
                    prompt,
                    session_key=key,
                    ephemeral=True,
                    tools=dream_tools,
                    on_progress=_silent,
                )
                elapsed = time.monotonic() - t0
                advanced = MemoryStore.dream_should_advance_cursor(resp, dream_file_states)
                # Stessa domanda in più del percorso cron, e per la stessa
                # ragione: "ha scritto" non è "il batch è atterrato". Chi lancia
                # `/dream` a mano deve vedere lo stesso esito del job, o il
                # comando torna a essere la porta di servizio del meccanismo.
                # Il tool per voci del run appena concluso. ``getattr`` con un default
                # perché ``build_dream_tools`` è sostituito nei test da doppi che non lo
                # espongono: un run senza quel tool è un run con zero voci, non un errore.
                entries = getattr(dream_tools, "memory_entries", None) or NO_ENTRIES
                held_batch = advanced and batch_was_not_consolidated(
                    before=prologue.report,
                    history_text=MemoryStore.dream_prompt_history(prompt),
                    stuck=prologue.stuck + prologue.nothing_new,
                    # L'esito in voci del run, dal tool esposto sul registry: sono questi
                    # numeri a rendere la domanda una verifica invece di una stima.
                    added=entries.entries_added,
                    replaced=entries.entries_replaced,
                    already_present=entries.entries_already_present,
                    # Se non ha tentato nessuna scrittura non ha mancato niente:
                    # ha deciso che non c'era da scrivere. V. il docstring.
                    attempted=getattr(dream_file_states, "writes_attempted", 0),
                )
                if held_batch:
                    advanced = False
                if advanced:
                    store.set_last_dream_cursor(last_cursor)
                    content = f"Dream completed in {elapsed:.1f}s."
                elif held_batch:
                    content = (
                        f"Dream completed in {elapsed:.1f}s but consolidated nothing "
                        "from its batch (no memory file grew); memory cursor was not "
                        "advanced, so those entries come back next run."
                    )
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
                content = _prefix_review_note(review_note, content)
            finally:
                # Anti-livelock, con la stessa regola del percorso cron perché è
                # la stessa funzione (la spiegazione sta lì). Contare anche da qui
                # è ciò che impedisce a un utente che lancia Dream a mano di
                # restare fuori dal meccanismo: un batch rifiutato dal budget
                # tornerebbe altrimenti identico a ogni `/dream`, per sempre.
                #
                # In un ``finally``, e vale per entrambi i percorsi: come ultima
                # istruzione del try, un turno che solleva la saltava del tutto —
                # quindi ``runs_since_review`` non avanzava e un Dream che
                # fallisce sempre non arrivava mai a un review pass.
                finish_dream_cycle(
                    store,
                    advanced=advanced,
                    runs_since_review=prologue.runs_since_review,
                    stuck=prologue.stuck,
                    nothing_new=prologue.nothing_new,
                    # La causa, che è ciò che decide quale dei due contatori sale:
                    # un rifiuto rimasto aperto significa "manca spazio" e un review
                    # può liberarlo; senza rifiuti non c'è niente da liberare.
                    refused=_int_or_zero(
                        getattr(dream_file_states, "unrecovered_refusals", 0)
                    ),
                )
        except Exception as e:
            elapsed = time.monotonic() - t0
            content = _prefix_review_note(
                review_note, f"Dream failed after {elapsed:.1f}s: {e}"
            )
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


def _prefix_review_note(note: str, content: str) -> str:
    """Antepone la riga sul review pass alla risposta, se c'è stato un review."""
    return f"{note}\n\n{content}" if note else content


def _format_dream_review_note(outcome: "ReviewOutcome") -> str:
    """Riga sul review pass, da anteporre alla risposta di `/dream`.

    Il review è un turno LLM in più, partito dentro un comando che l'utente ha
    lanciato a mano e che gli ha risposto "Dreaming...": tacerlo gli farebbe
    pagare token e attesa senza dirgli per cosa, e un turno in più partito in
    silenzio è una sorpresa, non una feature. Il percorso cron lo scrive solo
    nel log perché lì non c'è nessuno in ascolto; qui c'è.

    ``freed`` è il delta dei **tre file misurati**, non del workspace: un review
    che sposta una task spec da USER.md a una ``skills/<name>/SKILL.md`` — cosa
    che il suo prompt chiede esplicitamente — la conta come liberata. La frase
    lo dice, perché quel numero serve a tarare quei tre tetti e non a stimare
    quanto è dimagrito il disco.
    """
    from jenny.agent.dream_review import STATUS_FAILED

    files = "MEMORY.md, USER.md, SOUL.md"
    if outcome.status == STATUS_FAILED:
        return (
            "A memory review pass ran first but did not complete cleanly; "
            f"{outcome.freed:,} chars freed across {files}."
        )
    if outcome.freed > 0:
        return (
            f"A memory review pass ran first and freed {outcome.freed:,} chars "
            f"across {files}."
        )
    # Zero o negativo. Un review che non trova niente da potare è un esito
    # valido — il suo prompt lo dice al modello — e il negativo è il caso in cui
    # ha ristrutturato spostando testo *fra* i tre file misurati.
    return f"A memory review pass ran first; nothing was freed across {files}."


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


# Sotto questa cadenza il review pass smette di essere manutenzione e comincia a
# cancellare. Un consiglio e non un vincolo: lo schema ammette ``ge=1``, che è
# corretto in senso stretto (un review ogni run *è* una configurazione), e un
# ``config.json`` scritto a mano è una scelta dell'utente. Ma è il solo numero di
# questa feature che sotto soglia perde dati invece di limitarne la crescita, e
# chi lo abbassa da chat deve leggere la misura prima di scoprirla dai file.
#
# Sei e non dodici: dodici è il default e "sotto il default" non è un allarme.
# Il difetto misurato è la passata *consecutiva*, e sotto sei run — con
# ``interval_h`` al default, mezza giornata — le passate cominciano a incontrarsi.
_REVIEW_CADENCE_ADVISED_FLOOR = 6

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


def _int_or_zero(value: object) -> int:
    """Un contatore che arriva da un doppio può non essere un intero."""
    return value if isinstance(value, int) else 0


def _format_dream_budget_report(
    report: Sequence[FileBudget],
    *,
    review_every_runs: int,
    runs_since_review: int,
    stuck_runs: int,
    nothing_new_runs: int = 0,
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

    Oltre ``STUCK_IS_ALARMING`` run bloccati il conteggio non basta più: un
    numero in coda a una riga non dice che Dream ha *smesso* di consolidare né
    cosa farci. Questa è la vista in cui si atterra dopo l'alert di sistema
    (``dream_cycle._alert_stuck``), quindi qui va la diagnosi per esteso e la via
    d'uscita — la stessa frase dell'alert, da un'unica stesura, più il comando
    che la chiude.
    """
    from jenny.agent.dream_cycle import STUCK_IS_ALARMING, format_stuck_alarm

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
    if any(item.enforced for item in report):
        # Chi legge questa vista sta scegliendo dei numeri e deve sapere su chi
        # cadono: il guard è montato solo sui tool di Dream (v.
        # ``agent/tools/filesystem.py``, dove l'assenza sull'agente principale è
        # una scelta motivata). Senza questa riga i tetti sembrano valere per
        # tutti, e un file arrivato al cap durante una conversazione resta un
        # fatto inspiegato — proprio nella vista dove si viene a spiegarselo.
        # Condizionata all'enforcement perché con tutti e tre i budget a `0` non
        # c'è nessun vincolo di cui dire a chi si applica.
        lines.extend([
            "",
            "Enforced on Dream's own writes only. A chat turn is never refused by these "
            "numbers, so a conversation can fill a file to its cap — and it is Dream that "
            "then finds no room.",
        ])
    lines.extend([
        "",
        (
            f"Review pass: every {review_every_runs} Dream runs. "
            f"{runs_since_review} runs since the last one, {stuck_runs} stuck runs."
        ),
    ])
    if nothing_new_runs:
        # Contatore separato perché ha un rimedio diverso, e dirlo qui è metà del
        # punto: chi legge "stuck runs: 0" e vede comunque Dream fermo andrebbe a
        # cercare un tetto da alzare, che è la cosa sbagliata. Compare solo se è
        # > 0: su un'installazione sana sarebbe una riga di zero informazione.
        lines.append(
            f"Plus {nothing_new_runs} runs where nothing landed and nothing was refused "
            "— no cap is in the way, so a review pass has nothing to free."
        )
    if stuck_runs >= STUCK_IS_ALARMING:
        over = [item.label for item in report if item.over]
        # Il nome del file è quello che il rifiuto sta colpendo, e senza di esso
        # la riga direbbe "alza un budget" senza dire quale. Se nessun file
        # risulta sopra soglia il blocco è altrove (una policy, un turno che non
        # completa) e promettere un tetto da alzare sarebbe una pista falsa.
        if over:
            fix = (
                f"Raise the budget of {', '.join(f'`{label}`' for label in over)} above "
                "its current size, or set it to `0` to stop enforcing it."
            )
        else:
            fix = (
                "No file is over budget, so the writes are being stopped by something "
                "else — check the logs for the refused write."
            )
        lines.extend(["", f"**Dream is blocked.** {format_stuck_alarm(stuck_runs)} {fix}"])
    lines.extend(["", _dream_usage()])
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
        lines = [f"{field.label}: every {before} → every {after} {field.unit}."]
        if field.attr == "review_every_runs" and after < _REVIEW_CADENCE_ADVISED_FLOOR:
            # Scritto comunque — è una manopola dell'utente — ma detto, perché
            # questo è l'unico numero della feature che sotto una certa soglia
            # **cancella dati** invece di limitarne la crescita.
            #
            # Misurato il 2026-08-16 sul Titan 2: una prima passata di review è
            # esemplare, la seconda di fila arriva a un file già potato e continua
            # a cercare cose da togliere — sono finiti i fatti personali. Il
            # prompt del review dichiara valido un run che non cambia niente, ma
            # il modello obbedisce all'istruzione di rimpicciolire.
            lines.append(
                f"Note: below {_REVIEW_CADENCE_ADVISED_FLOOR} runs the review pass starts "
                "landing on files a previous pass has already pruned, and measured on this "
                "device the second consecutive pass deletes personal facts rather than "
                "redundancy. It is written — this is your call — but the safe range starts "
                f"at {_REVIEW_CADENCE_ADVISED_FLOOR}."
            )
        return "\n".join(lines)
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
            nothing_new_runs = memory.get_nothing_new_runs()
            return reply(_format_dream_budget_report(
                report,
                review_every_runs=dream.review_every_runs,
                runs_since_review=runs_since_review,
                stuck_runs=stuck_runs,
                nothing_new_runs=nothing_new_runs,
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


async def cmd_gardener(ctx: CommandContext) -> OutboundMessage:
    """Run one gardener pass on a project, now.

    Il comando esiste per due ragioni, e la seconda è la vera. La prima: un
    utente che vuole vedere le pagine adesso invece di fra sei ore. La seconda:
    **senza di lui il passo non è collaudabile**. I tre orologi dell'innesco
    (delta, trenta minuti di fermo, sei ore di distanza) rendono la strada
    naturale impossibile da percorrere in una sessione di prova, ed è la stessa
    ragione per cui ``/atlas`` e ``/dream`` esistono.
    """
    from jenny.session.keys import PROJECT_SESSION_PREFIX, is_project_session_key

    loop = ctx.loop
    msg = ctx.msg
    named = ctx.args.strip()
    if named:
        target = named
    elif is_project_session_key(ctx.key):
        target = ctx.key[len(PROJECT_SESSION_PREFIX):]
    else:
        # Un rifiuto che dice **dove**, non solo che qui non si può: la lezione
        # dei rifiuti del passo 6, e la stessa forma del rifiuto di
        # ``journal_append`` fuori da un progetto.
        return _reply(msg, _gardener_no_target())

    async def _run():
        from jenny.agent.gardener import GardenerStore, run_gardener
        from jenny.config.loader import load_config

        try:
            config = load_config()
            wikis_dir = getattr(getattr(config, "wiki", None), "wikis_dir", "wikis") or "wikis"
            store = GardenerStore.for_project(
                config.workspace_path, target, wikis_dir_name=wikis_dir
            )
            if store is None:
                content = (
                    f"`{target}` is not a project I can garden: I look for "
                    f"`{wikis_dir}/{target}/wiki/`, and there is nothing there."
                )
            else:
                content = _format_gardener_outcome(target, await run_gardener(loop, store))
        except Exception as e:
            content = f"The gardener failed: {e}"
        await loop.bus.publish_outbound(OutboundMessage(
            channel=msg.channel, chat_id=msg.chat_id, content=content,
            metadata={"render_as": "text"},
        ))

    asyncio.create_task(_run())
    return OutboundMessage(
        channel=msg.channel, chat_id=msg.chat_id,
        content=f"Gardening {target}...",
    )


def _reply(msg, content: str) -> OutboundMessage:
    return OutboundMessage(
        channel=msg.channel, chat_id=msg.chat_id, content=content,
        metadata={"render_as": "text"},
    )


def _gardener_no_target() -> str:
    return (
        "The gardener works on one project at a time, and this conversation is not a project.\n\n"
        "Run it from inside a project, or name one: `/gardener <project>`."
    )


def _format_gardener_outcome(name: str, outcome: "GardenerOutcome") -> str:
    """Messaggio utente per una passata.

    Gli esiti "non ho fatto niente" hanno frasi distinte, come per Atlas: un
    comando che risponde "fatto" senza aver fatto niente è peggio di uno che dice
    perché — e qui i modi di non fare niente sono tre, e vogliono dire cose molto
    diverse.
    """
    elapsed = f"{outcome.elapsed:.1f}s"
    if outcome.status == "skipped_no_delta":
        return (
            f"Nothing new in {name}'s journal since the last pass, so there was nothing to "
            "promote — no tokens spent."
        )
    if outcome.status == "written":
        return (
            f"The gardener read {outcome.lines} journal lines in {name} and wrote "
            f"{outcome.writes} times, in {elapsed}. See `wiki/index.md` and today's `log/`."
        )
    if outcome.status == "nothing_to_promote":
        return (
            f"The gardener read {outcome.lines} journal lines in {name} in {elapsed} and "
            "judged that none of them earned a page. The journal is marked as read."
        )
    if outcome.status == "no_write":
        return (
            f"The gardener finished in {elapsed} without writing (attempts blocked or refused); "
            "the journal was left unread, so the next pass will try again."
        )
    if outcome.status == "incomplete":
        return f"The gardener did not finish after {elapsed}; nothing was changed."
    return f"The gardener failed after {elapsed}: {outcome.detail}"


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
    router.exact("/gardener", cmd_gardener)
    router.prefix("/gardener ", cmd_gardener)
    router.exact("/skill", cmd_skill)
    router.exact("/help", cmd_help)
