"""Il ciclo di un run di Dream, nella parte che i suoi due percorsi condividono.

Un run di Dream parte da due posti: il job cron (``jenny/runtime/cron_dispatch.py``)
e lo slash command ``/dream`` (``jenny/command/builtin.py``). Erano due
implementazioni parallele della stessa cosa, e ogni volta che una cresceva
l'altra restava indietro in silenzio — il guard del budget montato solo sul
cron, il gauge assente dal prompt manuale, i contatori del review che non
avanzavano lanciando Dream a mano. Nessuna di quelle era una feature spenta: la
prima era l'enforcement che si aggirava usando il comando, l'ultima un'installazione
in cui il review pass non sarebbe partito mai. Sono state trovate una alla volta
e allineate a mano, ed è l'allineamento a mano la ragione per cui ne sarebbe
arrivata un'altra.

Qui stanno il prologo — misura, riga di log, trigger del review, checkpoint,
ricostruzione delle misure dopo il review — e l'epilogo, cioè l'aritmetica dei
contatori: la parte che per costruzione deve essere la stessa. Ai chiamanti
resta ciò che è davvero loro: costruire il prompt, il turno incrementale, lo
snapshot pre-turno, la contabilità token, ``compact_history`` più il pruning, e
la traduzione dell'esito — una riga di log per il cron, una frase in chat per il
comando, che è il motivo per cui ``DreamPrologue.review`` viaggia fino a loro
invece di essere consumato qui.

Il modulo vive sotto ``jenny/agent`` e non sotto ``jenny/runtime`` di proposito:
``jenny/command`` importava da ``jenny/runtime/cron_dispatch`` la costante del
trigger, ed era la traccia visibile del problema. Da qui nessuno dei due
pacchetti importa l'altro.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from loguru import logger

from jenny.agent import dream_review
from jenny.agent.memory_budget import budget_report, make_write_size_guard

if TYPE_CHECKING:
    from jenny.agent.memory import MemoryStore
    from jenny.agent.memory_budget import FileBudget, WriteSizeGuard
    from jenny.config.schema import DreamConfig

# Numero di run consecutivi senza avanzamento del cursore oltre il quale il
# review pass viene forzato. Due e non uno: un singolo run che non avanza è
# ordinario (una scrittura bloccata dalla policy, un turno andato storto) e
# pagare un review pass ogni volta costerebbe più del problema. Due di fila
# invece è una configurazione che si ripete, ed è quella che si autoalimenta.
STUCK_FORCES_REVIEW = 2

# Oltre questa soglia il livelock non è più un'ipotesi: il review è già stato
# forzato (a 2) e non è bastato. Log a ERROR, perché da qui in poi ogni run è
# un turno LLM che non consolida nulla.
#
# Perché ``stuck`` NON viene azzerato dal review, che sarebbe la cosa istintiva:
# azzerandolo il contatore oscillava 1,2,1,2 e questa soglia non si raggiungeva
# mai — l'allarme era codice morto, e per giunta l'unico allarme che questo
# meccanismo abbia. Un contatore che il review azzera risponde a "da quanto
# aspetto il prossimo review", che è una domanda a cui ``runs_since_review``
# risponde già. Quella a cui deve rispondere ``stuck`` è "da quanti run di fila
# Dream non consolida", e a quella un review che non ha risolto niente non è una
# risposta. Lo azzera solo un cursore che avanza, cioè il problema che finisce.
STUCK_IS_ALARMING = 4

# Fra quanti run si ritenta un review pass che è **fallito**.
#
# Un review che dichiara ``STATUS_FAILED`` non ha fatto la manutenzione, quindi
# azzerare la cadenza come se l'avesse fatta gli regala l'intero intervallo (con
# il default, dodici run: circa un giorno). Ma nemmeno "riprova subito": il
# fallimento più probabile è una migrazione che il budget ha troncato, e
# ritentarla a ogni run è un turno LLM ogni due ore — la stessa spesa a vuoto che
# il modulo esiste per evitare. Due run è il compromesso, e non è un numero
# nuovo: è la stessa distanza con cui ``STUCK_FORCES_REVIEW`` reagisce a un
# problema che si ripete.
REVIEW_RETRY_AFTER_RUNS = 2


def format_stuck_alarm(stuck: int) -> str:
    """La frase che descrive il livelock, condivisa dalle superfici che lo dicono.

    Una stesura sola perché sono due: l'alert di sistema che parte da
    :func:`finish_dream_cycle` e la vista di ``/dream budget``, che è dove si va
    a guardare dopo averlo letto. Se divergessero, la seconda smentirebbe la
    prima nel momento peggiore.

    Nessuna cifra dei file qui dentro: da ``finish_dream_cycle`` le misure non
    sono a portata — il report è del prologo, un turno LLM fa — e rifarle
    vorrebbe dire rileggere tre file per comporre una frase. Chi ha bisogno dei
    numeri li trova nella vista che questa frase gli dice di aprire.
    """
    return (
        f"Dream has not consolidated anything for {stuck} runs in a row: writes to "
        "long-term memory keep being refused by their character budget."
    )


def _alert_stuck(stuck: int) -> None:
    """Porta l'allarme fuori dal log, su una superficie che qualcuno vede.

    Il ``logger.error`` di ``finish_dream_cycle`` è, su Android, un allarme che
    non suona: nessuno legge logcat, e questo è precisamente lo stato in cui
    Jenny smette di ricordare senza che niente lo dica. Restava solo la lettura
    su richiesta (``/dream budget``), che risponde a chi è già venuto a
    chiedere.

    ``notify_delivery`` è la stessa primitiva con cui il canale WS posta gli
    alert di consegna: fire-and-forget, zero token, e no-op fuori da Android o
    senza event loop — quindi anche nei test. Non è il tool ``message``
    dell'escalation dell'heartbeat, di proposito: quello costa un turno LLM e
    dipende dal modello che sceglie di chiamarlo, e il modello è esattamente la
    parte che in questo scenario non sta funzionando.

    Riparte a **ogni** run oltre soglia, non solo all'attraversamento. Il tag che
    ne deriva (``cron:Dream``) fa sostituire l'alert precedente invece di
    sommarlo, quindi un livelock lungo lascia sul telefono una notifica sola e
    sempre aggiornata; e chi l'ha scartata la rivede al giro dopo, che è il
    comportamento voluto per un allarme che significa "la memoria è ferma". Il
    tag è suo: con quello di default (``message``) andrebbe a coprire la
    notifica di un messaggio vero.

    Import locale come in ``runtime/cron_dispatch.py``: nel grafo dei moduli
    ``jenny/agent`` non dipende da ``jenny/runtime``, e una riga di allarme non
    è una buona ragione per cominciare.
    """
    from jenny.runtime.notifier import notify_delivery
    from jenny.webui.metadata import WEBUI_MESSAGE_SOURCE_METADATA_KEY

    notify_delivery(
        f"{format_stuck_alarm(stuck)} Run /dream budget to see the sizes.",
        {WEBUI_MESSAGE_SOURCE_METADATA_KEY: {"kind": "cron", "label": "Dream"}},
    )


def format_budget(report: Sequence["FileBudget"]) -> str:
    """Riassumi il report di budget in una riga sola di log.

    Non riusa ``render_gauge``: quello è multiriga e scritto per il modello
    (con l'istruzione su cosa fare al 80%), qui serve una riga grezza che stia
    in logcat e si possa grep-are nel tempo per tarare i tetti.
    """
    if not report:
        return "no files"
    parts = []
    for item in report:
        if item.enforced:
            parts.append(f"{item.label} {item.chars}/{item.budget} ({item.pct}%)")
        else:
            parts.append(f"{item.label} {item.chars} (no budget)")
    return ", ".join(parts)


async def take_dream_snapshot(
    take_snapshot: Callable[[], Awaitable[bool]] | None,
) -> bool:
    """Checkpoint pre-Dream, fail-open, che dichiara se è davvero avvenuto.

    Dream può riscrivere MEMORY/SOUL/USER e le skill: uno snapshot prima
    rende ogni sua modifica reversibile. Fail-open perché un checkpoint
    guasto non deve impedire il consolidamento — ma l'esito **non** si
    perde: ritorna ``False``, e il review pass ne fa una frase diversa nel
    proprio prompt invece di promettere al modello una rete che non c'è.

    Un callback assente conta come checkpoint non avvenuto, e la traduzione sta
    qui perché nessun chiamante debba scriverla: un percorso che non ha modo di
    chiedere lo snapshot deve dire ``False``, non dimenticare la domanda.
    """
    if take_snapshot is None:
        return False
    try:
        return bool(await take_snapshot())
    except Exception:
        logger.exception("Pre-dream snapshot failed")
        return False


def _measure(store: "MemoryStore", cfg: "DreamConfig") -> list["FileBudget"]:
    """Report di budget dei tre file di memoria, con i tetti di *cfg*."""
    return budget_report(
        store,
        memory_chars=cfg.memory_budget_chars,
        user_chars=cfg.user_budget_chars,
        soul_chars=cfg.soul_budget_chars,
    )


@dataclass(frozen=True, slots=True)
class DreamPrologue:
    """Ciò che il prologo ha prodotto, quando il chiamante riprende il controllo.

    ``report`` e ``guard`` sono le misure **valide adesso**: se il review pass è
    girato sono state rifatte dopo, perché quelle di prima descrivono file che
    non esistono più in quella forma.

    ``runs_since_review`` e ``stuck`` sono i contatori già letti da disco (e
    azzerati, se il review è girato) da passare a :func:`finish_dream_cycle`
    invece di rileggerli: fra i due momenti c'è un turno LLM, e rileggerli
    significherebbe contare su uno stato che nel frattempo può essere stato
    riscritto da un altro run.

    ``review`` è ``None`` quando il review pass non è girato. Non è un dettaglio
    di comodo: è il gancio con cui ciascun chiamante racconta il fatto a modo
    suo — il cron l'ha già scritto nel log, il comando ne fa una frase per
    l'utente che sta aspettando in chat — e insieme il modo di sapere se lo
    snapshot del ciclo è già stato preso.
    """

    report: list["FileBudget"]
    guard: "WriteSizeGuard"
    runs_since_review: int
    stuck: int
    review: "dream_review.ReviewOutcome | None"


async def begin_dream_cycle(
    agent: Any,
    *,
    store: "MemoryStore",
    cfg: "DreamConfig",
    take_snapshot: Callable[[], Awaitable[bool]] | None = None,
) -> DreamPrologue:
    """Tutto ciò che precede il turno incrementale di Dream, per entrambi i percorsi.

    Misura i file, monta il guard, legge i contatori, decide se il review pass
    è dovuto e — se lo è — prende il checkpoint, lo esegue, azzera i contatori e
    **rimisura**.

    *take_snapshot* è il callback del checkpoint; ``None`` è un percorso che non
    ha modo di prenderlo e vale ``snapshotted=False`` (v.
    :func:`take_dream_snapshot`).
    """
    report = _measure(store, cfg)
    guard = make_write_size_guard(report)
    runs_since_review, stuck = store.get_review_state()
    # Loggato a OGNI run, non solo quando qualcosa scatta. Con i tre
    # budget a 0 — il default di spedizione — questa riga è letteralmente
    # l'unica cosa che la feature produce, e sono i numeri da cui si
    # sceglieranno i tetti veri (la roadmap propone 4-6 kB, che è una
    # proposta, non una misura). Toglierla renderebbe la taratura una
    # stima a occhio, cioè lo stato da cui si è partiti.
    logger.info(
        "Dream memory budget: {} | runs since review: {}, stuck runs: {}",
        format_budget(report), runs_since_review, stuck,
    )

    # Due modi di arrivare al review pass. ``review_every_runs`` è la
    # manutenzione periodica, che deve girare anche su file sani: è
    # l'unico momento in cui qualcuno guarda il file *intero* invece
    # della voce di storia del momento. ``stuck`` è l'uscita di
    # emergenza dal livelock (v. il commento in ``finish_dream_cycle``).
    #
    # Un terzo trigger — "un file ha sforato il budget" — è stato tolto
    # di proposito, ed è la parte che vale spiegare. Sembra il più
    # ovvio dei tre e invece è l'unico che non sa fermarsi: un file può
    # restare sopra la soglia dopo un review che ha già fatto tutto il
    # possibile (il resto è roba che le regole marcano "never delete"),
    # e il prompt del review dichiara *valido* un run che non cambia
    # niente. La condizione resterebbe quindi vera per sempre e
    # farebbe partire un turno LLM ogni due ore, a vuoto, senza che
    # nessun contatore lo limiti — lo specchio esatto del livelock che
    # tutto questo lavoro esiste per chiudere, e per giunta su una
    # feature il cui scopo è contenere i costi.
    #
    # Non si perde niente di importante, perché ``stuck`` copre già il
    # caso in cui essere sopra budget fa *danno*: se il tetto blocca una
    # scrittura, il cursore non avanza, ``stuck`` sale e due cicli dopo
    # il review parte. Ed è un bersaglio migliore — un file sopra
    # soglia che non sta bloccando nessuna scrittura non è un'urgenza,
    # e può aspettare il giro periodico.
    #
    # Il ramo ``stuck`` usa il modulo e non ``>=``: il contatore non viene
    # azzerato dal review (v. ``STUCK_IS_ALARMING``), quindi senza il modulo un
    # livelock farebbe partire un review a *ogni* run da lì in poi. Con il
    # modulo la cadenza è la stessa di prima — un review ogni due run bloccati —
    # ma il contatore continua a salire, ed è ciò che rende raggiungibile
    # l'allarme.
    #
    # E il modulo da solo non basta, correzione del 2026-08-17: ``stuck`` resta
    # **fermo** quando non c'era storia da consolidare (``advanced is None`` in
    # ``finish_dream_cycle``), cioè su ogni installazione in pari. Fermo su un
    # multiplo della soglia, quella condizione è vera a ogni run: un review ogni
    # due ore per sempre, a vuoto, su file che nessuno ha toccato — lo specchio
    # del livelock, e su una feature il cui scopo è contenere i costi. Serve
    # quindi anche sapere a che valore si è già forzato: si riforza solo quando
    # ``stuck`` è cambiato, cioè quando Dream ha mancato un altro consolidamento.
    forced_at = store.get_review_forced_at_stuck()
    livelock_due = stuck > 0 and stuck % STUCK_FORCES_REVIEW == 0 and stuck != forced_at
    review_due = runs_since_review >= cfg.review_every_runs or livelock_due
    if not review_due:
        return DreamPrologue(
            report=report,
            guard=guard,
            runs_since_review=runs_since_review,
            stuck=stuck,
            review=None,
        )

    snapshotted = await take_dream_snapshot(take_snapshot)
    outcome = await dream_review.run_dream_review(
        agent,
        store=store,
        report=report,
        snapshotted=snapshotted,
        write_size_guard=guard,
    )
    # Si azzera solo ``runs_since_review``: il review è avvenuto, la cadenza
    # periodica riparte. ``stuck`` resta dov'è, perché dice un'altra cosa — da
    # quanti run di fila Dream non consolida — e un review appena girato non è
    # una risposta a quella domanda. Lo azzera ``finish_dream_cycle``, e solo
    # quando il cursore avanza davvero.
    #
    # Ma non si azzera a 0 se il review ha **fallito**, ed è la correzione del
    # 2026-08-17: la riga di prima si comprava dodici run di tregua qualunque
    # fosse l'esito, compreso lo ``STATUS_FAILED`` che segnala una migrazione
    # troncata — cioè il caso in cui tornare presto conta di più. Si riparte
    # invece a due run dalla prossima occasione: prima di dodici, e non "a ogni
    # run", che è la protezione di costo per cui l'azzeramento incondizionato era
    # stato scritto.
    if outcome.status == dream_review.STATUS_FAILED:
        runs_after = max(0, cfg.review_every_runs - REVIEW_RETRY_AFTER_RUNS)
    else:
        runs_after = 0
    store.set_review_state(
        runs_since_review=runs_after,
        stuck_runs=stuck,
        # Il valore a cui questo review è stato forzato, quando è il livelock ad
        # averlo chiesto: è ciò che impedisce di riforzarlo su uno ``stuck`` che
        # non si muove più. Un review periodico non lo tocca (``None``), perché
        # del livelock non dice niente.
        forced_at_stuck=stuck if livelock_due else None,
    )
    # ``freed`` è il delta dei **tre file misurati**, non del
    # workspace. Un review che sposta una task spec da USER.md a una
    # ``skills/<name>/SKILL.md`` — cosa che il suo prompt chiede
    # esplicitamente — la conta come liberata, perché le skill non
    # stanno nel report. È il numero giusto per tarare i budget (sono
    # quei tre file ad averne uno) e quello sbagliato per dire di
    # quanto è dimagrito il disco.
    logger.info(
        "Dream review pass: {} (snapshotted={}), {} chars freed across the "
        "budgeted files",
        outcome.status, snapshotted, outcome.freed,
    )
    # Report e guard si RICOSTRUISCONO, non si riusano: il review ha
    # appena riscritto quei file. Il gauge del turno incrementale
    # mostrerebbe altrimenti al modello un riempimento che il review
    # ha già smontato — cioè gli chiederebbe di far spazio che è già
    # stato fatto. Il guard rilegge comunque la dimensione da disco a
    # ogni scrittura, ma va rifatto insieme al report perché i due
    # restino derivati dalla stessa misura invece che da due momenti
    # diversi.
    report = _measure(store, cfg)
    return DreamPrologue(
        report=report,
        guard=make_write_size_guard(report),
        runs_since_review=0,
        stuck=stuck,
        review=outcome,
    )


def finish_dream_cycle(
    store: "MemoryStore",
    *,
    advanced: bool | None,
    runs_since_review: int,
    stuck: int,
) -> tuple[int, int]:
    """Aggiorna i contatori del review dopo il turno incrementale.

    *advanced* è l'esito di ``dream_should_advance_cursor``, con un terzo stato:
    ``None`` significa **non c'era storia da consolidare**, quindi il turno
    incrementale non è nemmeno partito. È diverso da ``False`` e va tenuto
    distinto, perché ``stuck`` conta i run in cui Dream *non è riuscito* a
    consolidare — e un run che non aveva niente da fare non ha fallito niente.

    ``runs_since_review`` invece avanza in tutti e tre i casi, ed è la ragione
    per cui questo terzo stato esiste. Il review pass è manutenzione sui *file*;
    farlo dipendere dall'arrivo di nuova *storia* lega due cose scorrelate, e
    sulla combinazione peggiore le lega male: su un'installazione in pari — dove
    Dream ha già digerito tutto e i file stanno fermi — il contatore non saliva
    mai e il review non partiva **mai**, che è esattamente lo stato in cui la
    manutenzione periodica avrebbe più senso. Misurato sul Titan 2 il
    2026-08-16: cursore a 88, storia a 23 voci, `.dream_review` inesistente
    dopo un `/dream` completo.

    Ritorna i contatori scritti, perché il chiamante possa dirne qualcosa senza
    rileggere il disco.
    """
    # Anti-livelock, ed è la ragione per cui ``stuck`` esiste.
    # ``_resolve_write`` conta il tentativo PRIMA di risolvere il path
    # (``agent/tools/filesystem.py``, ``record_write_attempt``) e
    # ``internal_run_should_commit`` avanza solo con ``writes_ok > 0``
    # oppure ``writes_attempted == 0``. Un run in cui il budget rifiuta
    # ogni scrittura ha quindi ``attempted > 0, ok == 0``: il cursore non
    # avanza, al run dopo torna lo stesso batch, che viene rifiutato di
    # nuovo. Un turno LLM completo ogni due ore, per sempre.
    #
    # Non avanzare È la semantica corretta — il fatto non è stato scritto
    # e avanzare lo perderebbe — quindi la via d'uscita è forzare il review,
    # non allentare il commit.
    #
    # Con una correzione, del 2026-08-17: quel che *non* si allenta è il caso
    # in cui il contenuto non è atterrato. Un modello che obbedisce al
    # messaggio di rifiuto, pota e riscrive portandosi dentro il fatto ora
    # commette, perché il rifiuto si chiude quando il contenuto arriva su
    # disco (``FileStates.record_write_refused``). Prima no, e quel run — che
    # ha fatto esattamente il lavoro — era la sorgente di livelock più
    # probabile di tutte.
    #
    # E il conto non è solo in token. ``compact_history`` gira comunque a
    # fine run, nel chiamante, e tiene le ultime ``max_history_entries`` voci
    # SENZA guardare il cursore (``agent/memory.py``): un livelock abbastanza
    # lungo non spreca soltanto chiamate, perde storia che non è mai stata
    # consolidata.
    if advanced is not None:
        stuck = 0 if advanced else stuck + 1
    runs_since_review += 1
    store.set_review_state(runs_since_review=runs_since_review, stuck_runs=stuck)
    if stuck >= STUCK_IS_ALARMING:
        logger.error(
            "Dream has not advanced its cursor for {} consecutive runs; the forced "
            "review pass is not freeing enough space (cursor still at {})",
            stuck, store.get_last_dream_cursor(),
        )
        # Il log resta (è dove si legge il cursore), ma non è l'allarme: v.
        # ``_alert_stuck``.
        _alert_stuck(stuck)
    return runs_since_review, stuck
