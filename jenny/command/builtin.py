"""Built-in slash command handlers."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable, Sequence
from contextlib import suppress
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from loguru import logger

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
            "Turn this project's new journal lines into pages and update its map — or, with "
            "nothing new to promote, bring an oversized map back under its ceiling. Inside a "
            "project it works on that one; elsewhere name it: '/gardener <project>'. Add "
            "'settings' to read the periodic pass, or 'off' to stop it."
        ),
        "sprout",
        # Corto di proposito: è il suggerimento accanto al comando nella palette,
        # non la sua documentazione. Le forme per esteso stanno in
        # ``_gardener_usage()``, che `/gardener settings` stampa in coda.
        "[project|settings]",
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

    from jenny.agent.dream_cycle import (
        DREAM_ALREADY_RUNNING,
        claim_dream_cycle,
        release_dream_cycle,
    )

    async def _dream_cycle():
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

    async def _run_dream():
        # Il ``finally`` sta **un livello fuori** dal lavoro, così non c'è un
        # cammino dentro ``_dream_cycle`` che possa saltarlo: ritorno, eccezione e
        # cancellazione passano tutti da qui. Una presa che resta presa spegnerebbe
        # Dream fino al riavvio del processo, e in silenzio.
        try:
            await _dream_cycle()
        finally:
            release_dream_cycle()

    # La presa si prende **qui**, sincrona, prima di ``create_task``: il corpo del
    # task non parte fino al primo punto di sospensione, quindi controllarla là
    # dentro lascerebbe passare due ``/dream`` di fila. E prendendola prima, la
    # risposta immediata al comando può dire la verità invece di promettere
    # "Dreaming..." a un ciclo che non partirà.
    if not claim_dream_cycle():
        return OutboundMessage(
            channel=msg.channel, chat_id=msg.chat_id, content=DREAM_ALREADY_RUNNING,
        )
    try:
        asyncio.create_task(_run_dream())
    except BaseException:
        # Se il task non arriva nemmeno a esistere, il suo ``finally`` non girerà:
        # la presa va restituita qui o resta appesa a un ciclo che non c'è.
        release_dream_cycle()
        raise
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

    E ``freed`` da solo **non basta a dire cosa è successo**: sono due domande
    diverse, "quanto spazio" e "quali fatti", e la seconda è quella che l'utente
    riconosce. Un review che sposta sei voci personali da ``USER.md``
    all'archivio — cosa che ``dream_review.md`` autorizza esplicitamente —
    poteva rispondere "nothing was freed" se nello stesso passaggio un altro
    file era cresciuto: il numero era vero e la frase era falsa. Le degradazioni
    hanno quindi una riga loro, sempre, con gli id da citare a ``recall``.

    Terza notizia, e la sola su cui l'utente possa fare qualcosa subito: i
    **rifiuti di budget rimasti aperti**. Una degradazione si ritrova con
    ``recall``; una scrittura rifiutata non è in nessun file, e resta rifiutata a
    ogni run finché il tetto non si alza o il file non si pota. Sta subito dopo la
    riga dei caratteri perché è la sua *spiegazione*: "nothing was freed" con un
    rifiuto aperto non è un run che non aveva niente da fare, è un run che non è
    riuscito a farlo.
    """
    from jenny.agent.dream_review import STATUS_FAILED

    files = "MEMORY.md, USER.md, SOUL.md"
    if outcome.status == STATUS_FAILED:
        note = (
            "A memory review pass ran first but did not complete cleanly; "
            f"{outcome.freed:,} chars freed across {files}."
        )
    elif outcome.freed > 0:
        note = (
            f"A memory review pass ran first and freed {outcome.freed:,} chars "
            f"across {files}."
        )
    else:
        # Zero o negativo. Un review che non trova niente da potare è un esito
        # valido — il suo prompt lo dice al modello — e il negativo è il caso in
        # cui ha ristrutturato spostando testo *fra* i tre file misurati.
        note = f"A memory review pass ran first; nothing was freed across {files}."
    extra = [_format_dream_refusals(outcome), _format_dream_demotions(outcome)]
    return " ".join([note, *(part for part in extra if part)])


def _format_dream_refusals(outcome: "ReviewOutcome") -> str:
    """Le scritture che il budget ha rifiutato e che non sono mai atterrate.

    ``unresolved_refusals`` conta **contenuto**, non tentativi: un file rifiutato
    e poi riscritto con dentro il fatto è stato recuperato e non arriva qui
    (v. ``FileStates.record_write_refused``). Quel che resta è un fatto che non è
    in nessun file, e nessuno degli altri numeri della nota lo dice — con lo
    status ``no-change`` è indistinguibile da "non c'era niente da potare".

    La frase dice il numero e la mossa, non la diagnosi: da qui non si sa *quale*
    dei tre tetti ha rifiutato (l'esito porta un conteggio, non i percorsi), e
    ``/dream budget`` è esattamente il comando che lo mostra. Non promette invece
    quel che non può sapere: se il fatto era in viaggio da un file all'altro,
    lo status è già ``failed`` e la riga sopra lo dice.
    """
    # ``getattr`` per la stessa ragione di ``memory_entries`` qui sopra: i doppi di
    # ``run_dream_review`` nei test costruiscono l'esito a mano e non espongono
    # tutti i campi. Il contratto vero non è affidato a questo default — lo fissa
    # un test che fa girare ``run_dream_review`` davvero e legge la nota che ne
    # esce, quindi un campo che sparisse dal dataclass farebbe rosso lì.
    refused = getattr(outcome, "unresolved_refusals", 0)
    if refused <= 0:
        return ""
    return (
        f"{refused} write(s) were refused by their size budget and never landed — "
        "`/dream budget` shows which file is full: raise its cap or prune it, then "
        "run `/dream` again."
    )


# Quanti id di voci degradate si nominano nella risposta di `/dream`. Il tetto non
# serve al costo — otto caratteri per id — ma a impedire che una passata patologica
# trasformi la nota in un muro di hash. Oltre il tetto la via è comunque aperta:
# ``recall`` senza argomenti elenca l'archivio intero dalla voce più recente, e le
# voci di questa passata sono in testa a quell'elenco.
_DEMOTIONS_NAMED_IN_NOTE = 10


def _format_dream_demotions(outcome: "ReviewOutcome") -> str:
    """Cosa il review ha spostato in archivio, e come richiamarlo.

    Il numero da solo non è azionabile, e la reazione a "sei fatti in meno" senza
    un modo di guardarli è la stessa che a una cancellazione — cioè l'effetto che
    l'archivio esiste per non produrre. Gli id sono ciò che il tool ``recall``
    accetta, quindi la frase è già l'istruzione.
    """
    ids = outcome.demoted_ids
    if not ids:
        return ""
    shown = ids[:_DEMOTIONS_NAMED_IN_NOTE]
    more = len(ids) - len(shown)
    tail = f", and {more} more" if more else ""
    return (
        f"It also moved {len(ids)} fact(s) into `memory/archive/` instead of "
        f"deleting them — ask me to recall {', '.join(shown)}{tail} to read them back."
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
    l'utente legge quando lo sfora. Duplicare qui il vincolo non è ridondanza, ma
    non per la ragione che stava scritta qui: la prima stesura diceva che
    assegnare un valore fuori range dentro la callback di ``mutate`` alzerebbe un
    ``ValidationError``. Non lo alza. ``pydantic_compat.BaseModel.__setattr__``
    inoltra a ``object.__setattr__`` per ogni campo noto **senza validare**
    (``pydantic_compat/core.py``), quindi il valore fuori range viene *scritto* su
    `config.json` e alza alla lettura successiva — cioè all'avvio dopo, addosso a
    un utente che non sta più guardando. Il controllo qui è l'unico che esista.
    """

    attr: str
    label: str
    unit: str
    minimum: int
    too_low: str
    # True se ``label`` è anche il nome di un file nel report di budget: solo
    # per quelli ha senso confrontare il valore appena scritto con una misura.
    measured: bool


# Sotto questa cadenza le passate di review cominciano a incontrarsi, e la
# seconda arriva su un file che la prima ha già potato continuando a cercare cose
# da togliere. Le misure, sul Titan 2:
#
#   * 2026-08-16 (``roadmap/memory-budget.md``): due passate di review, `USER.md`
#     3.524 → 1.626 caratteri, "−31% on the second pass alone".
#   * 2026-08-18 (``.agent/memory-plan.md``, difetto **D4**): "The review pass
#     deletes real facts on a second consecutive forced pass — lost five entries:
#     two open questions, a plan, a biographical detail and one insight; the
#     ``[permanent]`` one survived. −564 bytes".
#
# **Dodici e non sei**, e la ragione è cambiata insieme al codice. Il sei
# documentato nasceva dalla cancellazione: la fase 2 del piano l'ha chiusa —
# ``make_entry_archiver`` è montato al confine di scrittura di tutti i tool di
# Dream, e la verifica su device del 2026-08-19 (``reviewEveryRuns: 1``, due run,
# dieci voci in ``memory/archive/``) dice "nothing was lost". Quindi sotto soglia
# oggi non si perdono fatti: si spendono token, ogni run, senza nessuno a
# guardare. Il numero che resta è quello che il piano tiene: *"keep
# ``reviewEveryRuns`` at 12 and treat forced reviews as the rare path they are
# meant to be"* (``.agent/memory-plan.md``), e scendere sotto dodici è l'item
# **6.1**, deliberatamente non fatto — "no run has asked for it".
#
# Lo schema resta ``ge=1`` di proposito: un restore deve poter riscrivere
# qualunque valore storico. Il pavimento vive qui, dove c'è una persona a cui
# spiegarlo.
_REVIEW_CADENCE_FLOOR = 12

# La via d'uscita, e non è un ornamento: le misure di questo progetto si fanno
# con ``reviewEveryRuns: 1`` (fase 2 verificata così, e l'item 6.1 dovrà farlo di
# nuovo), e sul telefono non c'è una shell di root — se il comando rifiutasse
# senza scampo, il protocollo di misura del progetto diventerebbe irraggiungibile
# proprio dallo strumento nato per raggiungerlo.
#
# Una frase e non un ``--force``: un flag corto e convenzionale è esattamente ciò
# che un modello aggiunge per essere d'aiuto, mentre questa è una frase in prima
# persona che afferma qualcosa a nome dell'utente. Non rende la conferma
# impossibile da emettere a un modello — nessun token stampato nel rifiuto lo
# sarebbe — ma la sposta da "flag plausibile" a "asserzione riconoscibile", e
# costa comunque **due turni**: il rifiuto prima, la frase dopo.
_REVIEW_CADENCE_OVERRIDE = "i-accept-back-to-back-reviews"

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
        f"- `/dream budget review <runs>` — Dream runs between review passes "
        f"(minimum {_REVIEW_CADENCE_FLOOR})",
    ])


def _review_cadence_refusal(value: int, *, override: str) -> str:
    """Perché ``review`` sotto soglia non passa, e cosa si digita se si insiste.

    Ritorna la stringa da mostrare, oppure ``""`` se la scrittura può procedere.
    Chiamata **prima** di ``mutate``: un valore rifiutato non tocca il file e non
    ruota il `.bak`.
    """
    if override and override.lower() != _REVIEW_CADENCE_OVERRIDE:
        # Un terzo token sbagliato non si ignora: chi l'ha scritto voleva
        # confermare, e mangiarselo in silenzio scriverebbe (o rifiuterebbe) senza
        # dire che la conferma non è arrivata.
        return (
            f"`{override}` is not the confirmation phrase. To set a review cadence below "
            f"{_REVIEW_CADENCE_FLOOR} runs the phrase is exactly "
            f"`{_REVIEW_CADENCE_OVERRIDE}`.\n\n{_dream_usage()}"
        )
    if value >= _REVIEW_CADENCE_FLOOR or override:
        return ""
    return "\n\n".join([
        f"A review cadence of {value} runs is below the floor of {_REVIEW_CADENCE_FLOOR}, "
        "so `config.json` was not written.",
        "Below it the review passes start landing on files a previous pass has already "
        "pruned, and the pass keeps looking for things to remove. Measured on this device: "
        "two passes took `USER.md` from 3,524 to 1,626 characters, 31% of it on the second "
        "pass alone, and a forced pass removed five real entries — two open questions, a "
        "plan, a biographical detail and one insight.",
        "That no longer loses anything: every entry that leaves `USER.md` or "
        "`memory/MEMORY.md` is archived under `memory/archive/` before the write lands. What "
        "a faster cadence still costs is tokens, on every Dream run, unattended.",
        "If you want it anyway — measuring on a real device is why this path exists — repeat "
        f"the command with the confirmation phrase:\n\n`/dream budget review {value} "
        f"{_REVIEW_CADENCE_OVERRIDE}`",
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
        if field.attr == "review_every_runs" and after < _REVIEW_CADENCE_FLOOR:
            # Qui si arriva solo con la frase di conferma già digitata, quindi la
            # misura è già stata letta nel rifiuto: questa riga non la ripete, dice
            # cosa resta acceso e come si torna indietro.
            lines.append(
                f"Note: below {_REVIEW_CADENCE_FLOOR} runs the review passes land "
                "back-to-back on files a previous pass has already pruned. Entries they "
                "drop are archived under `memory/archive/`, so nothing is lost — but the "
                "churn is paid on every Dream run. Back to the default with "
                f"`/dream budget review {_REVIEW_CADENCE_FLOOR}`."
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
    # Un terzo token lo accetta solo `review`, ed è la frase di conferma del
    # pavimento di cadenza. Staccato qui e non dentro il ramo perché il resto del
    # comando resta a due argomenti esatti.
    override = ""
    if len(rest) == 3 and rest[0].lower() == "review":
        override = rest[2]
        rest = rest[:2]
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
        if field.attr == "review_every_runs":
            refusal = _review_cadence_refusal(value, override=override)
            if refusal:
                return reply(refusal)

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

    Con una delle parole riservate al posto del nome del progetto (v.
    ``_GARDENER_SETTINGS_WORDS``) il comando non lancia niente e tara la passata
    periodica: quel ramo risponde nello stesso turno, come `/dream budget`.
    """
    from jenny.session.keys import PROJECT_SESSION_PREFIX, is_project_session_key

    loop = ctx.loop
    msg = ctx.msg
    named = ctx.args.strip()
    parts = named.split()
    if parts and parts[0].lower() in _GARDENER_SETTINGS_WORDS:
        return await _gardener_settings_command(ctx, parts)
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

    Ai modi di non fare niente si aggiungono due modi di farlo **a metà**
    (``partial_write``, ``commit_failed``), e sono i due che vanno detti con più
    cura: le pagine sono su disco ma il diario è rimasto da rileggere. Senza un
    ramo proprio cadevano nel fondo, cioè si raccontavano come un fallimento —
    che è falso — e la frase di ``no_write`` («finished without writing») sarebbe
    falsa allo stesso modo, al contrario.

    Poi ci sono i due esiti che non parlano del lavoro ma di **chi altro c'era**.
    ``already_running`` è una passata che non è partita perché un'altra era in
    volo sullo stesso progetto; ``aborted_user_active`` è una passata che si è
    fermata perché l'utente è tornato su quel progetto mentre scriveva — e questa
    ha bisogno di una frase sua per la stessa ragione delle due a metà: può avere
    lasciato pagine su disco, quindi «finished without writing» sarebbe falso.

    Infine, **trasversale a quasi tutti**, la passata girata per la mappa
    (``outcome.map_pass``, T3.5): lì le righe di diario sono zero per costruzione, e
    ogni frase che le conta diventa falsa. Il ramo sta **prima** di tutti gli esiti
    che le nominano, e resta fuori solo per i due che parlano d'altro
    (``failed``/``incomplete``). V. ``_gardener_map_pass_line``.
    """
    elapsed = f"{outcome.elapsed:.1f}s"
    if outcome.status == "skipped_no_delta":
        return (
            f"Nothing new in {name}'s journal since the last pass, so there was nothing to "
            "promote — no tokens spent."
        )
    if outcome.map_pass and outcome.status not in ("failed", "incomplete"):
        return _gardener_map_pass_line(name, outcome, elapsed)
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
    if outcome.status == "partial_write":
        return (
            f"The gardener wrote {outcome.writes} pages in {name} in {elapsed}, but some writes "
            "were refused, so the journal was left unread — the next pass will see those lines "
            "again."
        )
    if outcome.status == "commit_failed":
        return (
            f"The gardener wrote {outcome.writes} pages in {name} in {elapsed}, but could not "
            f"record how far it had read: {outcome.detail}. The pages are on disk and the "
            "journal was left unread, so the next pass will see those lines again."
        )
    if outcome.status == "aborted_user_active":
        return (
            f"You came back to {name} while the gardener was working there, so it stood down "
            f"after {elapsed} rather than write over you ({outcome.writes} pages had already "
            "landed). The journal was left unread, so the next pass will see those lines again."
        )
    if outcome.status == "already_running":
        return (
            f"A pass on {name} is already running, so this one did not start — the gardener "
            "works on one project one pass at a time, or two passes overwrite each other's "
            "pages. Try again once it has finished."
        )
    if outcome.status == "no_write":
        return (
            f"The gardener finished in {elapsed} without writing (attempts blocked or refused); "
            "the journal was left unread, so the next pass will try again."
        )
    if outcome.status == "incomplete":
        return f"The gardener did not finish after {elapsed}; nothing was changed."
    return f"The gardener failed after {elapsed}: {outcome.detail}"


def _gardener_map_pass_line(name: str, outcome: "GardenerOutcome", elapsed: str) -> str:
    """Messaggio di una passata girata **per la mappa** e non per il diario.

    Serve un ramo suo perché tutte le frasi qui sopra contano righe di diario, e su
    una passata così sono zero: «read 0 journal lines and judged that none of them
    earned a page» è la risposta di un comando che ha smesso di dire la verità. Il
    numero che questa passata esiste per muovere è un altro, e va detto.

    E va detto anche il **freno**: la mappa che resta sopra il tetto non si
    ritenta, perché una ragione che resta vera dopo la passata è un livelock (v.
    ``GardenerState.map_left_at``). Un utente che rilancia il comando e non vede
    partire niente deve poter sapere perché da qui, non dai log di un telefono.
    """
    from jenny.agent.gardener import MAP_TARGET_CHARS

    lede = f"Nothing new in {name}'s journal, so the gardener went in for the map alone"
    if outcome.map_after < outcome.map_before:
        moved = (
            f"`wiki/index.md` went from {outcome.map_before} to {outcome.map_after} characters "
            f"in {elapsed}, against a ceiling of {MAP_TARGET_CHARS}"
        )
        if outcome.map_after <= MAP_TARGET_CHARS:
            return f"{lede}: {moved} — it fits now, so every turn sees all of it again."
        return (
            f"{lede}: {moved} — still over, and it will not be tried again until the map grows "
            "past that."
        )
    return (
        f"{lede}, and after {elapsed} it is still {outcome.map_after} characters against a "
        f"ceiling of {MAP_TARGET_CHARS}: nothing was moved out of it. It will not be tried "
        "again until the map grows past that."
    )


# ---------------------------------------------------------------------------
# /gardener settings — leggere e tarare la passata periodica
# ---------------------------------------------------------------------------
#
# Prima di questo blocco niente in ``jenny/webui/`` o ``jenny/command/`` leggeva o
# scriveva ``agents.defaults.gardener``, e la conseguenza non era "una manopola
# scomoda": ``enabled=False`` — la via d'uscita documentata, e la ragione per cui
# esiste il cancello di dispatch in ``CronDispatcher._run_gardener`` — non era
# raggiungibile da nessuna superficie. L'unico modo di spegnere il giardiniere era
# una shell di root sul telefono, e ``compactProjectsWhenIdle`` ci era arrivato
# così: scritto a mano fuori da ``store.mutate()``, e spento con un ``sed -i`` che
# ha rotto l'etichetta SELinux del file.


@dataclass(frozen=True)
class _GardenerNumber:
    """Un numero tarabile della passata periodica.

    Il range **non** è duplicato qui: lo si legge da
    ``GardenerConfig.model_fields`` (v. :func:`_gardener_range`), perché un range
    scritto due volte diventa due range appena uno dei due si muove — ed è
    esattamente ciò che questo comando racconta all'utente nei suoi rifiuti.

    ``attr`` è il campo di ``GardenerConfig``; ``means`` è la riga della vista di
    lettura; ``out_of_range`` è la frase di chi ha appena sforato, che deve dire
    *perché* quel tetto e quale sia l'alternativa reversibile; ``effect`` traduce
    il valore appena scritto in quel che cambia.
    """

    attr: str
    label: str
    unit: str
    usage: str
    means: str
    out_of_range: str
    effect: Callable[[int], str]


def _gardener_range(attr: str) -> tuple[int, int]:
    """Il range che lo schema impone a *attr*, letto dallo schema stesso."""
    from jenny.config.schema import GardenerConfig

    finfo = GardenerConfig.model_fields[attr]
    return int(finfo.ge or 0), int(finfo.le or 0)


def _interval_effect(value: int) -> str:
    return f"It now looks for a project to garden every {value}min."


def _idle_effect(value: int) -> str:
    if value == 0:
        return (
            "A pass will now start even while you are talking in that project — it can "
            "promote half of a conversation, and rewrite the map under you while you read it."
        )
    return f"A pass now waits for {value}min of silence in that project's conversation."


def _distance_effect(value: int) -> str:
    if value == 0:
        return (
            "It can now come back to the same project immediately. That is the measured "
            "failure mode of Dream written as a number: a second close pass on one subject "
            "reworks what the first wrote instead of adding to it."
        )
    return f"It now waits {value}h before coming back to the same project."


_GARDENER_NUMBERS: dict[str, _GardenerNumber] = {
    "interval": _GardenerNumber(
        attr="interval_min",
        label="Interval",
        unit="min",
        usage="how often it looks for work",
        means=(
            "how often it looks for work. A tick that finds nothing spends no tokens, so "
            "looking often is cheap — what decides is the silence below"
        ),
        out_of_range=(
            "The interval must be between {low} and {high} minutes. Zero would be a tick with "
            "no gap; past a day the periodic pass has stopped being periodic, and "
            "`/gardener off` is the reversible way to say never."
        ),
        effect=_interval_effect,
    ),
    "idle": _GardenerNumber(
        attr="idle_min",
        label="Required silence",
        unit="min",
        usage="silence required in that project before a pass",
        means=(
            "how long that project's conversation must have been silent before a pass starts. "
            "The gardener works on cold material; `0` lets it in while you are talking"
        ),
        out_of_range=(
            "The required silence must be between {low} and {high} minutes. Past a day no "
            "live project ever reaches it, which is an off switch in disguise — "
            "`/gardener off` says that reversibly, and says it where you can read it."
        ),
        effect=_idle_effect,
    ),
    "distance": _GardenerNumber(
        attr="min_hours_between_passes",
        label="Distance between passes",
        unit="h",
        usage="minimum gap between two passes on one project",
        means=(
            "how long before it comes back to the same project. Per project, because the "
            "degradation it guards against is per subject"
        ),
        out_of_range=(
            "The distance must be between {low} and {high} hours (a year). Past that it no "
            "longer means distance, it means never — and `/gardener off` says never "
            "reversibly."
        ),
        effect=_distance_effect,
    ),
}

# Le parole che al posto del nome di un progetto significano "non lanciare niente".
# Un progetto che si chiamasse davvero così viene oscurato, e la lista di
# ``_gardener_usage`` lo dice: il rimedio è ``/gardener`` da dentro il progetto,
# che non passa dal nome.
_GARDENER_SETTINGS_WORDS = ("settings", "off", "on", "compact", *_GARDENER_NUMBERS)


def _gardener_usage() -> str:
    """Le forme valide del comando, con i range dentro.

    In coda alla vista di lettura e a ogni rifiuto, non alle conferme: è l'unico
    posto in cui si scopre che ``distance`` e ``compact`` esistono, e chi ha appena
    sbagliato la sintassi ne ha bisogno. Chi ha appena scritto un valore no.
    """
    lines = [
        "Valid forms:",
        "- `/gardener` — run a pass on this project now",
        "- `/gardener <project>` — run a pass on a named project",
        "- `/gardener settings` — show what the periodic pass is set to",
        "- `/gardener off` / `/gardener on` — stop or start the periodic pass",
    ]
    for name, field in _GARDENER_NUMBERS.items():
        low, high = _gardener_range(field.attr)
        lines.append(
            f"- `/gardener {name} <{field.unit}>` — {field.usage} ({low}–{high})"
        )
    lines.extend([
        "- `/gardener compact on|off` — archive a project's chat history once it goes idle",
        "",
        "A project actually named `"
        + "`, `".join(_GARDENER_SETTINGS_WORDS)
        + "` is shadowed by these forms; `/gardener` from inside it still works.",
    ])
    return "\n".join(lines)


def _gardener_off_line() -> str:
    """Cosa resta possibile a giardiniere spento. Detto ogni volta che si spegne.

    Spegnere non è disinstallare, e un utente che legge solo "off" non ha modo di
    saperlo: la strada a mano è precisamente quella che ha reso collaudabile la
    feature, e resta aperta.
    """
    return (
        "The periodic pass will not run; `/gardener` and `/gardener <project>` still work "
        "by hand."
    )


def _format_gardener_settings(cfg: Any, compact_projects: bool) -> str:
    """Vista di lettura: com'è tarata la passata periodica, adesso.

    ``/gardener`` senza argomento **lancia una passata**, quindi la lettura ha
    bisogno di una parola sua: un comando che scrive dei numeri e non ha modo di
    rileggerli manda l'utente a cercarli in `config.json`, che è il problema da cui
    è nato questo blocco.
    """
    if cfg.enabled:
        head = f"**On** — {cfg.describe_schedule()}."
    else:
        head = f"**Off** — {_gardener_off_line()}"
    lines = ["## Gardener", "", head, ""]
    for name, field in _GARDENER_NUMBERS.items():
        low, high = _gardener_range(field.attr)
        value = int(getattr(cfg, field.attr))
        lines.append(
            f"- `{name}` — {value} {field.unit} ({low}–{high}): {field.means}."
        )
    lines.extend(["", _format_compact_projects_state(compact_projects), "", _gardener_usage()])
    return "\n".join(lines)


def _format_compact_projects_state(enabled: bool) -> str:
    """Stato di ``compactProjectsWhenIdle``, con cosa costa e cosa no.

    Sta nella vista del giardiniere perché è la stessa decisione vista dall'altro
    lato: il recinto che tiene la cronologia di un progetto si può togliere quando
    la verità sta nelle pagine, e chi le pagine le produce è il giardiniere.
    """
    if enabled:
        return (
            "Project history compaction: **on** — a project's conversation is archived once "
            "it goes idle, like the personal one. The agent then has in context what was "
            "*written* in the wiki, not what was said. The visible transcript is untouched, "
            "so a person can still read back: the amnesia is the agent's, not the record's."
        )
    return (
        "Project history compaction: **off** — a project's conversation is never archived "
        "for sitting idle. It can sit for three weeks and pick up where it was."
    )


# Il flag di P4 si legge quando l'agente si costruisce (``AgentLoop`` lo passa ad
# ``AutoCompact``), quindi scriverlo non lo applica al processo in corso. Detto,
# non taciuto: una manopola che sembra fatta e non è fatta è peggio di una che
# dichiara quando ha effetto.
_COMPACT_TAKES_EFFECT = (
    "This one is read when the agent starts, so it takes effect from the next gateway start."
)


def _parse_gardener_value(raw: str, name: str, field: _GardenerNumber) -> tuple[int | None, str]:
    """Interpreta il valore richiesto, o spiega perché non si può.

    Ritorna ``(valore, "")`` oppure ``(None, messaggio)``. Nel secondo caso il
    chiamante **non entra in** ``mutate``: un input sbagliato non tocca il file,
    non ruota il `.bak` e non prende il lock.

    Il rifiuto vive qui e non nello schema per una ragione asimmetrica: lo schema
    deve poter *leggere* qualunque valore storico — un ``le=`` che boccia manda in
    quarantena il `config.json` di chi aggiorna (v. ``GardenerConfig.clamp_raw``) —
    mentre un numero appena battuto a mano non ha nessuna storia da rispettare.
    Lo schema è il tetto di ciò che può esistere, il comando è il tetto di ciò che
    si può chiedere.
    """
    low, high = _gardener_range(field.attr)
    try:
        value = int(raw)
    except ValueError:
        return None, (
            f"`{raw}` is not a whole number.\n\n"
            f"Usage: `/gardener {name} <{field.unit}>`\n\n"
            f"{_gardener_usage()}"
        )
    if not low <= value <= high:
        return None, f"{field.out_of_range.format(low=low, high=high)}\n\n{_gardener_usage()}"
    return value, ""


def _rearm_gardener_job(ctx: CommandContext, saved: Any) -> str | None:
    """Fa vedere al cron la pianificazione appena scritta, senza riavvio.

    ``interval_min`` non vive nel ``Config`` letto a ogni tick: è diventato lo
    ``schedule`` del ``CronJob`` nello store del cron. E su un gateway partito col
    giardiniere spento il job non è nemmeno registrato, quindi ``/gardener on``
    scriverebbe un ``enabled=True`` che nessuno va a leggere. Entrambi i casi li
    chiude ``refresh_gardener_job``.

    Ritorna la pianificazione armata, o ``None``: senza servizio cron in mano — un
    test, un loop costruito a parte — non c'è niente da riarmare, e non è un
    errore.
    """
    cron = getattr(ctx.loop, "cron_service", None)
    if cron is None:
        return None
    from jenny.runtime.cron_dispatch import refresh_gardener_job

    try:
        return refresh_gardener_job(cron, config=saved)
    except Exception as e:  # noqa: BLE001 — il valore è scritto: questo è il contorno
        logger.warning("Could not re-arm the gardener cron job: {}", e)
        return None


async def _gardener_settings_command(ctx: CommandContext, parts: list[str]) -> OutboundMessage:
    """Gestisci ``/gardener settings|off|on|interval|idle|distance|compact``."""
    metadata = {**dict(ctx.msg.metadata or {}), "render_as": "text"}

    def reply(content: str) -> OutboundMessage:
        return OutboundMessage(
            channel=ctx.msg.channel, chat_id=ctx.msg.chat_id,
            content=content, metadata=metadata,
        )

    word = parts[0].lower()
    rest = parts[1:]
    try:
        if word in ("settings", "off", "on"):
            if rest:
                return reply(
                    f"`/gardener {word}` takes no value.\n\n{_gardener_usage()}"
                )
            if word == "settings":
                return reply(await _gardener_settings_view())
            return reply(await _set_gardener_enabled(ctx, word == "on"))
        if word == "compact":
            return reply(await _set_compact_projects(rest))
        field = _GARDENER_NUMBERS[word]
        if not rest:
            return reply(f"`/gardener {word}` is missing a value.\n\n{_gardener_usage()}")
        if len(rest) > 1:
            return reply(f"`/gardener {word}` takes one value.\n\n{_gardener_usage()}")
        value, error = _parse_gardener_value(rest[0], word, field)
        if value is None:
            return reply(error)
        return reply(await _set_gardener_number(ctx, word, field, value))
    except Exception as e:
        # Come ``/dream budget``: un comando che muore in silenzio lascia la chat
        # senza risposta. Se a sollevare è stata ``mutate``, il file non è stato
        # scritto — la sua callback o completa o non salva.
        return reply(f"Could not read or write the gardener settings: {e}")


async def _gardener_settings_view() -> str:
    from jenny.config.loader import load_config

    config = load_config()
    return _format_gardener_settings(
        config.agents.defaults.gardener,
        config.agents.defaults.compact_projects_when_idle,
    )


async def _set_gardener_enabled(ctx: CommandContext, enabled: bool) -> str:
    """``/gardener off`` e ``/gardener on``.

    ``off`` è la via d'uscita, e deve funzionare anche da una config che lo schema
    di oggi boccerebbe: chi ha un ``intervalMin`` fuori range scritto da una
    versione precedente passa comunque da qui, perché ``GardenerConfig.clamp_raw``
    riporta quei numeri dentro i tetti al parse invece di far fallire la lettura.
    Senza quella clemenza ``mutate`` rileggerebbe un file in quarantena e questa
    scrittura ripartirebbe dai default, cioè lo spegnimento cancellerebbe il
    provider.
    """
    from jenny.config import store as config_store

    seen: dict[str, bool] = {}

    def _apply(config) -> bool:
        cfg = config.agents.defaults.gardener
        seen["before"] = bool(cfg.enabled)
        if cfg.enabled == enabled:
            # ``False``: il file non viene toccato e il `.bak` non ruota per nulla.
            return False
        cfg.enabled = enabled
        return True

    saved = await config_store.mutate(_apply)
    cfg = saved.agents.defaults.gardener
    if seen.get("before") == enabled:
        state = "on" if enabled else "off"
        return (
            f"The gardener is already {state}; `config.json` was not rewritten."
        )
    if not enabled:
        return f"The gardener is off. {_gardener_off_line()}"
    lines = [f"The gardener is on: {cfg.describe_schedule()}."]
    if _rearm_gardener_job(ctx, saved):
        lines.append("The periodic job is armed for that schedule now — no restart needed.")
    return "\n".join(lines)


async def _set_gardener_number(
    ctx: CommandContext, name: str, field: _GardenerNumber, value: int
) -> str:
    """Scrive uno dei tre numeri e dice cosa cambia."""
    from jenny.config import store as config_store

    # ``before`` lo cattura la callback e non una config letta prima: ``mutate``
    # rilegge il file dentro il proprio lock, quindi solo lì il valore corrente è
    # quello vero al momento della scrittura.
    seen: dict[str, int] = {}

    def _apply(config) -> bool:
        cfg = config.agents.defaults.gardener
        before = int(getattr(cfg, field.attr))
        seen["before"] = before
        if before == value:
            return False
        setattr(cfg, field.attr, value)
        return True

    saved = await config_store.mutate(_apply)
    before = seen.get("before", value)
    if before == value:
        return (
            f"{field.label} is already {value} {field.unit}; "
            "`config.json` was not rewritten."
        )
    lines = [
        f"{field.label}: {before} → {value} {field.unit}.",
        field.effect(value),
    ]
    cfg = saved.agents.defaults.gardener
    if not cfg.enabled:
        # Scritto comunque — è un numero della passata periodica anche mentre è
        # ferma — ma un utente che tara una cosa spenta e non vede effetti deve
        # sapere da qui che manca l'interruttore, non dai log di un telefono.
        lines.append(
            "Note: the gardener is off, so nothing is looking. `/gardener on` starts it."
        )
    elif field.attr == "interval_min" and _rearm_gardener_job(ctx, saved):
        lines.append("The periodic job is armed on the new interval now — no restart needed.")
    return "\n".join(lines)


async def _set_compact_projects(rest: list[str]) -> str:
    """``/gardener compact on|off`` — l'interruttore di P4, il più pesante dei due.

    Non è un campo di ``GardenerConfig`` (sta in ``agents.defaults``), ma è la
    stessa decisione vista dall'altro lato, e soprattutto è **il valore che è
    arrivato acceso sul device passando fuori da** ``store.mutate()``. Averlo qui è
    il rimedio a quella classe di incidente: la strada a mano era un ``sed -i`` che
    ha rotto l'etichetta SELinux del file.
    """
    from jenny.config import store as config_store

    if len(rest) != 1 or rest[0].lower() not in ("on", "off"):
        return f"`/gardener compact` needs `on` or `off`.\n\n{_gardener_usage()}"
    enabled = rest[0].lower() == "on"
    seen: dict[str, bool] = {}

    def _apply(config) -> bool:
        defaults = config.agents.defaults
        seen["before"] = bool(defaults.compact_projects_when_idle)
        if defaults.compact_projects_when_idle == enabled:
            return False
        defaults.compact_projects_when_idle = enabled
        return True

    await config_store.mutate(_apply)
    if seen.get("before") == enabled:
        state = "on" if enabled else "off"
        return (
            f"Project history compaction is already {state}; "
            "`config.json` was not rewritten."
        )
    return f"{_format_compact_projects_state(enabled)}\n\n{_COMPACT_TAKES_EFFECT}"


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
